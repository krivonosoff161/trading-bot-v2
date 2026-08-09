# -*- coding: utf-8 -*-
"""Automated honest-validation step for the farm lifecycle (no manual file carry).

When ``classify`` produced ``export_validation`` tasks (eligible FORWARD_PAPER /
REGIME_SPECIFIC candidates), this:
  1) exports validation requests from the candidate registry (gated),
  2) runs the honest-backtest bridge in-process,
  3) STAMPS the verdicts back into farm_results AND the unique_candidates table —
     closing the loop that ``refresh_validation_handoff`` used to leave orphaned.

If the honest-backtest package is unavailable the bridge writes a NEEDS_MORE_DATA
verdict (honest degradation, never a fake pass) and that fact is reported. No
network beyond the existing bridge, no order path, no .env, no Telegram.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

from src.research_lab.farm_tasks_db import FarmTasksDB
from src.research_lab.hard_validation_contract import HardValidationReport
from src.research_lab.hard_validation_export import (
    prepare_requests,
    validation_id_for_unique_candidate,
    write_prepared_requests,
)
from src.research_lab.honest_backtest_bridge import (
    _artifact_stem,
    bridge_available,
    run_validation_batch,
)
from src.research_lab.setup_library import build_setup_card, write_setup_library
from src.research_lab.validation_handoff import refresh_from_artifacts
from src.research_lab.validation_feedback import generate_feedback, write_feedback
from src.research_lab.validation_generation import (
    current_generation_manifest_status,
    write_current_generation,
    write_pending_generation,
)


ProgressCallback = Callable[[str, int, int], None]
ActiveCheck = Callable[[], None]
MAX_VALIDATION_ATTEMPTS = 3
MIN_VALIDATION_SCAN_LIMIT = 32
MAX_VALIDATION_SCAN_LIMIT = 256


def _check_active(check_active: ActiveCheck | None) -> None:
    if check_active is not None:
        check_active()


def _completed_progress(
    progress: ProgressCallback | None,
    check_active: ActiveCheck | None,
    stage: str,
    completed: int,
    total: int,
) -> None:
    """Publish only a completed milestone and fail closed around publication."""
    _check_active(check_active)
    if progress is not None:
        progress(stage, int(completed), int(total))
    _check_active(check_active)


def _artifact_paths(
    directory: Path,
    candidate_ids: Iterable[str] | None,
) -> Iterable[Path]:
    if candidate_ids is None:
        return directory.glob("*.json")
    return (
        directory / f"{_artifact_stem(candidate_id)}.json"
        for candidate_id in dict.fromkeys(
            str(candidate_id) for candidate_id in candidate_ids if str(candidate_id)
        )
    )


def _verdict_map(
    private_root: Path,
    candidate_ids: Iterable[str] | None = None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    vdir = Path(private_root) / "hard_validation" / "verdicts"
    if not vdir.exists():
        return out
    for path in _artifact_paths(vdir, candidate_ids):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out[str(data.get("candidate_id") or path.stem)] = str(
            data.get("hard_status") or ""
        )
    return out


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _request_map(
    private_root: Path,
    candidate_ids: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    req_dir = Path(private_root) / "hard_validation" / "requests"
    if not req_dir.exists():
        return out
    for path in _artifact_paths(req_dir, candidate_ids):
        if not path.is_file():
            continue
        data = _read_json(path)
        cid = str(data.get("candidate_id") or path.stem)
        if cid:
            out[cid] = data
    return out


def _hard_id_for_task(task: dict[str, Any]) -> str:
    payload = _read_payload(task)
    if payload.get("validation_candidate_id"):
        return str(payload["validation_candidate_id"])
    if payload.get("uc_key"):
        return validation_id_for_unique_candidate({"uc_key": str(payload["uc_key"])})
    return str(payload.get("candidate_id") or task.get("candidate_id") or "")


def _read_payload(task: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(task.get("payload_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _stamp_farm_results_from_contexts(
    private_root: Path,
    verdicts: dict[str, str],
    *,
    requests: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Stamp compute DB using request provenance, not just validation candidate_id."""
    from src.research_lab.state_db import connect, default_db_path, init_db

    reqs = requests if requests is not None else _request_map(private_root, verdicts)
    conn = connect(default_db_path(private_root))
    init_db(conn)
    stamped = 0
    try:
        for validation_id, hard_status in verdicts.items():
            req = reqs.get(validation_id) or {}
            metrics = (
                raw_metrics
                if isinstance(raw_metrics := req.get("metrics"), dict)
                else {}
            )
            original_candidate = str(metrics.get("source_candidate_id") or "")
            run_id = Path(str(req.get("source_run_id") or "").replace("\\", "/")).name
            if run_id and original_candidate:
                cur = conn.execute(
                    """UPDATE farm_results
                       SET validation_exported=1, hard_status=?
                       WHERE run_id=? AND candidate_id=?""",
                    (hard_status, run_id, original_candidate),
                )
                stamped += int(cur.rowcount or 0)
            cur = conn.execute(
                """UPDATE farm_results
                   SET validation_exported=1, hard_status=?
                   WHERE candidate_id=?""",
                (hard_status, validation_id),
            )
            stamped += int(cur.rowcount or 0)
        conn.commit()
    finally:
        conn.close()
    return stamped


def _write_setup_cards(
    private_root: Path,
    candidate_ids: list[str],
    *,
    requests: dict[str, dict[str, Any]] | None = None,
) -> int:
    reports_dir = Path(private_root) / "hard_validation" / "reports"
    reqs = (
        requests
        if requests is not None
        else _request_map(private_root, candidate_ids)
    )
    cards = []
    for cid in candidate_ids:
        report = _read_json(reports_dir / f"{_artifact_stem(cid)}.json")
        if not report:
            continue
        cards.append(build_setup_card(report, reqs.get(cid) or {}))
    if not cards:
        return 0
    summary = write_setup_library(Path(private_root), cards, dry_run=False)
    return int(summary.get("cards_written") or 0)


def run_due_validations(
    tasks: FarmTasksDB,
    private_root: Path,
    *,
    apply: bool,
    limit: int = 10,
    now: float | None = None,
    progress: ProgressCallback | None = None,
    check_active: ActiveCheck | None = None,
) -> dict[str, Any]:
    """Execute export + validation + stamp-back for queued export_validation tasks."""
    counters: dict[str, Any] = {
        "export_tasks": 0,
        "tasks_examined": 0,
        "exported": 0,
        "validated": 0,
        "stamped_db": 0,
        "stamped_unique": 0,
        "setup_cards": 0,
        "feedback_written": 0,
        "orphan_tasks_skipped": 0,
        "ineligible_tasks_skipped": 0,
        "visibility_tasks_deferred": 0,
        "retry_exhausted_skipped": 0,
        "unexportable_candidates": 0,
        "artifact_batches": 0,
        "artifact_ready_tasks": 0,
        "fair_scan_exhausted": 0,
        "generation_empty_published": 0,
        "generation_status_before": "unchecked",
        "generation_unchanged": 0,
        "bridge_ok": all(bridge_available().values()),
    }
    limit = max(0, int(limit))
    export_tasks: list[dict] = []
    prepared_by_id: dict[str, Any] = {}
    scan_limit = max(
        int(limit),
        min(
            MAX_VALIDATION_SCAN_LIMIT,
            max(MIN_VALIDATION_SCAN_LIMIT, int(limit) * 32),
        ),
    )
    queue_exhausted = False
    while len(export_tasks) < limit and counters["tasks_examined"] < scan_limit:
        probe_tasks: list[dict[str, Any]] = []
        probe_capacity = min(
            limit - len(export_tasks),
            scan_limit - counters["tasks_examined"],
        )
        while (
            len(probe_tasks) < probe_capacity
            and counters["tasks_examined"] < scan_limit
        ):
            _check_active(check_active)
            task = tasks.claim_next_task(task_types=("export_validation",), now=now)
            if task is None:
                queue_exhausted = True
                break
            counters["tasks_examined"] += 1
            counters["export_tasks"] += 1
            disposition = tasks.classify_export_validation_task(task, now=now)
            action = disposition["action"]
            reason = disposition["reason"]
            if action == "skip":
                tasks.skip_task(task["task_id"], reason, now=now)
                if reason == "validation_orphan_missing_unique_candidate":
                    counters["orphan_tasks_skipped"] += 1
                else:
                    counters["ineligible_tasks_skipped"] += 1
                _completed_progress(
                    progress,
                    check_active,
                    "task_dispositioned",
                    counters["tasks_examined"],
                    scan_limit,
                )
                continue
            if action == "defer":
                tasks.defer_task(
                    task["task_id"],
                    until=(now or 0) + 300,
                    reason=reason,
                    now=now,
                )
                counters["visibility_tasks_deferred"] += 1
                _completed_progress(
                    progress,
                    check_active,
                    "task_dispositioned",
                    counters["tasks_examined"],
                    scan_limit,
                )
                continue
            probe_tasks.append(task)

        if not probe_tasks:
            if queue_exhausted:
                break
            continue

        if not apply:
            export_tasks.extend(probe_tasks)
            continue

        uc_keys = [str(_read_payload(task).get("uc_key") or "") for task in probe_tasks]
        uc_keys = [key for key in uc_keys if key]
        _check_active(check_active)
        summary, prepared_batch = prepare_requests(
            private_root,
            limit=len(probe_tasks),
            include_regime_specific=True,
            source="farm_tasks",
            uc_keys=uc_keys,
            progress=progress,
            check_active=check_active,
        )
        counters["artifact_batches"] += 1
        counters["unexportable_candidates"] += int(
            summary.get("skipped_no_artifact") or 0
        )
        expected_ids = {
            _hard_id_for_task(task)
            for task in probe_tasks
            if str(_read_payload(task).get("uc_key") or "")
        }
        expected_ids.discard("")
        batch_by_id = {
            str(candidate.candidate_id): candidate
            for candidate in prepared_batch
            if str(candidate.candidate_id) in expected_ids
        }
        for task in probe_tasks:
            hard_id = _hard_id_for_task(task)
            if hard_id in batch_by_id:
                export_tasks.append(task)
                prepared_by_id[hard_id] = batch_by_id[hard_id]
                counters["artifact_ready_tasks"] += 1
                continue
            _check_active(check_active)
            if int(task.get("attempts") or 0) >= MAX_VALIDATION_ATTEMPTS:
                tasks.skip_task(
                    task["task_id"],
                    "validation_artifact_unavailable_retry_exhausted",
                    now=now,
                )
                counters["retry_exhausted_skipped"] += 1
            else:
                tasks.defer_task(
                    task["task_id"],
                    until=(now or 0) + 300,
                    reason="validation_artifact_unavailable",
                    now=now,
                )
            _completed_progress(progress, check_active, "task_unexportable", 1, 1)

        if queue_exhausted:
            break

    if counters["tasks_examined"] >= scan_limit and len(export_tasks) < limit:
        counters["fair_scan_exhausted"] = 1
    _completed_progress(
        progress,
        check_active,
        "tasks_claimed",
        counters["tasks_examined"],
        scan_limit,
    )

    if not export_tasks:
        generation_status = current_generation_manifest_status(Path(private_root))
        counters["generation_status_before"] = generation_status
        if apply and generation_status == "code_stale":
            _check_active(check_active)
            write_current_generation(
                Path(private_root),
                tasks=[],
                exported_ids=[],
                completed_ids=[],
                producer_time=now,
            )
            _completed_progress(
                progress,
                check_active,
                "empty_generation_published",
                1,
                1,
            )
            counters["generation_empty_published"] = 1
            return counters
        counters["generation_unchanged"] = 1
        return counters

    if not apply:
        for task in export_tasks:
            _check_active(check_active)
            tasks.complete_task(task["task_id"], reason="export_dry_run", now=now)
            _completed_progress(progress, check_active, "task_completed", 1, 1)
        return counters
    prepared = [prepared_by_id[_hard_id_for_task(task)] for task in export_tasks]

    # Only a proven exportable batch may revoke the prior generation.  The
    # pending manifest still precedes every request/report/verdict/card write.
    _check_active(check_active)
    write_pending_generation(
        Path(private_root),
        tasks=export_tasks,
        producer_time=now,
    )
    _completed_progress(
        progress,
        check_active,
        "pending_generation_published",
        len(export_tasks),
        len(export_tasks),
    )
    exported_ids = write_prepared_requests(
        private_root,
        prepared,
        progress=progress,
        check_active=check_active,
    )
    counters["exported"] = len(exported_ids)
    _completed_progress(
        progress,
        check_active,
        "requests_exported",
        len(exported_ids),
        len(export_tasks),
    )
    requests_dir = Path(private_root) / "hard_validation" / "requests"
    if exported_ids:
        _check_active(check_active)
        val = run_validation_batch(
            requests_dir,
            private_root,
            dry_run=False,
            limit=max(limit, len(export_tasks)),
            candidate_ids=exported_ids,
            progress=progress,
            check_active=check_active,
        )
    else:
        val = {"total": 0, "validated": 0, "errors": 0, "results": []}
    counters["validated"] = int(val.get("validated") or 0)
    exported_set = set(exported_ids)
    current_ids = list(
        dict.fromkeys(
            str(result.get("candidate_id") or "")
            for result in (val.get("results") or [])
            if isinstance(result, dict)
            and result.get("hard_status")
            and str(result.get("candidate_id") or "") in exported_set
        )
    )
    _completed_progress(
        progress,
        check_active,
        "validations_completed",
        len(current_ids),
        len(exported_ids),
    )

    # Empty or failed current batches must not scan historical artifact trees.
    # Publish the bounded incomplete generation and defer only the claimed tasks.
    if not current_ids:
        _check_active(check_active)
        write_current_generation(
            Path(private_root),
            tasks=export_tasks,
            exported_ids=exported_ids,
            completed_ids=[],
            producer_time=now,
        )
        _completed_progress(
            progress,
            check_active,
            "generation_published",
            0,
            len(exported_ids),
        )
        for completed, task in enumerate(export_tasks, start=1):
            _check_active(check_active)
            if int(task.get("attempts") or 0) >= MAX_VALIDATION_ATTEMPTS:
                tasks.skip_task(
                    task["task_id"],
                    "validation_no_verdict_retry_exhausted",
                    now=now,
                )
                counters["retry_exhausted_skipped"] += 1
            else:
                tasks.defer_task(
                    task["task_id"],
                    until=(now or 0) + 300,
                    reason="validation_no_verdict",
                    now=now,
                )
            _completed_progress(
                progress,
                check_active,
                "task_terminalized",
                completed,
                len(export_tasks),
            )
        return counters

    # auto stamp-back into farm_results (was the orphaned refresh_validation_handoff step)
    from src.research_lab.state_db import connect, default_db_path, init_db

    _check_active(check_active)
    conn = connect(default_db_path(private_root))
    init_db(conn)
    try:
        handoff = refresh_from_artifacts(conn, private_root, candidate_ids=current_ids)
        counters["stamped_db"] = int(handoff.get("rows_stamped_verdict") or 0)
    finally:
        conn.close()
    _completed_progress(
        progress,
        check_active,
        "validation_handoff_stamped",
        int(counters["stamped_db"]),
        len(current_ids),
    )

    # mirror verdicts into the coordinator's unique_candidates view
    _check_active(check_active)
    verdicts_all = _verdict_map(private_root, current_ids)
    verdicts = {cid: verdicts_all[cid] for cid in current_ids if cid in verdicts_all}
    reqs = _request_map(private_root, current_ids)
    _completed_progress(
        progress,
        check_active,
        "validation_artifacts_loaded",
        len(verdicts),
        len(current_ids),
    )
    _check_active(check_active)
    counters["stamped_db"] += _stamp_farm_results_from_contexts(
        Path(private_root), verdicts, requests=reqs
    )
    _completed_progress(
        progress,
        check_active,
        "validation_contexts_stamped",
        int(counters["stamped_db"]),
        len(verdicts),
    )
    _check_active(check_active)
    counters["setup_cards"] = _write_setup_cards(
        Path(private_root), current_ids, requests=reqs
    )
    _completed_progress(
        progress,
        check_active,
        "setup_cards_written",
        int(counters["setup_cards"]),
        len(current_ids),
    )
    reports_dir = Path(private_root) / "hard_validation" / "reports"
    for completed, cid in enumerate(current_ids, start=1):
        _check_active(check_active)
        report_data = _read_json(reports_dir / f"{_artifact_stem(cid)}.json")
        if report_data:
            try:
                feedback = generate_feedback(HardValidationReport.from_dict(report_data))
            except (KeyError, TypeError, ValueError):
                feedback = None
            if feedback is not None:
                _check_active(check_active)
                if write_feedback(Path(private_root), feedback, dry_run=False):
                    counters["feedback_written"] += 1
        _completed_progress(
            progress,
            check_active,
            "validation_feedback_processed",
            completed,
            len(current_ids),
        )
    for completed, (cid, hard_status) in enumerate(verdicts.items(), start=1):
        _check_active(check_active)
        if hard_status:
            req = reqs.get(cid) or {}
            metrics = (
                raw_metrics
                if isinstance(raw_metrics := req.get("metrics"), dict)
                else {}
            )
            uc_key = str(metrics.get("uc_key") or "")
            if uc_key:
                counters["stamped_unique"] += tasks.set_unique_hard_status(
                    uc_key, hard_status, now=now
                )
            else:
                counters["stamped_unique"] += tasks.set_candidate_hard_status(
                    cid, hard_status, now=now
                )
        _completed_progress(
            progress,
            check_active,
            "unique_candidate_stamped",
            completed,
            len(verdicts),
        )

    # Publish final authority while the claimed tasks still provide a recoverable
    # running marker.  If publication fails, startup orphan reconciliation can
    # requeue the tasks and the pending manifest remains fail-closed.
    _check_active(check_active)
    write_current_generation(
        Path(private_root),
        tasks=export_tasks,
        exported_ids=exported_ids,
        completed_ids=current_ids,
        producer_time=now,
    )
    _completed_progress(
        progress,
        check_active,
        "generation_published",
        len(current_ids),
        len(exported_ids),
    )
    for completed, task in enumerate(export_tasks, start=1):
        _check_active(check_active)
        hid = _hard_id_for_task(task)
        if hid and hid in verdicts:
            tasks.complete_task(task["task_id"], reason="validated", now=now)
        elif int(task.get("attempts") or 0) >= MAX_VALIDATION_ATTEMPTS:
            tasks.skip_task(
                task["task_id"],
                "validation_no_verdict_retry_exhausted",
                now=now,
            )
            counters["retry_exhausted_skipped"] += 1
        else:
            tasks.defer_task(
                task["task_id"],
                until=(now or 0) + 300,
                reason="validation_no_verdict",
                now=now,
            )
        _completed_progress(
            progress,
            check_active,
            "task_terminalized",
            completed,
            len(export_tasks),
        )
    return counters
