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
from typing import Any

from src.research_lab.farm_tasks_db import FarmTasksDB
from src.research_lab.hard_validation_contract import HardValidationReport
from src.research_lab.hard_validation_export import export_requests, validation_id_for_unique_candidate
from src.research_lab.honest_backtest_bridge import (
    _artifact_stem,
    bridge_available,
    run_validation_batch,
)
from src.research_lab.setup_library import build_setup_card, write_setup_library
from src.research_lab.validation_handoff import refresh_from_artifacts
from src.research_lab.validation_feedback import generate_feedback, write_feedback


def _verdict_map(private_root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    vdir = Path(private_root) / "hard_validation" / "verdicts"
    if not vdir.exists():
        return out
    for path in vdir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out[str(data.get("candidate_id") or path.stem)] = str(data.get("hard_status") or "")
    return out


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _request_map(private_root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    req_dir = Path(private_root) / "hard_validation" / "requests"
    if not req_dir.exists():
        return out
    for path in req_dir.glob("*.json"):
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


def _stamp_farm_results_from_contexts(private_root: Path, verdicts: dict[str, str]) -> int:
    """Stamp compute DB using request provenance, not just validation candidate_id."""
    from src.research_lab.state_db import connect, default_db_path, init_db

    reqs = _request_map(private_root)
    conn = connect(default_db_path(private_root))
    init_db(conn)
    stamped = 0
    try:
        for validation_id, hard_status in verdicts.items():
            req = reqs.get(validation_id) or {}
            metrics = req.get("metrics") if isinstance(req.get("metrics"), dict) else {}
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


def _write_setup_cards(private_root: Path, candidate_ids: list[str]) -> int:
    reports_dir = Path(private_root) / "hard_validation" / "reports"
    reqs = _request_map(private_root)
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


def run_due_validations(tasks: FarmTasksDB, private_root: Path, *, apply: bool,
                        limit: int = 10, now: float | None = None) -> dict[str, Any]:
    """Execute export + validation + stamp-back for queued export_validation tasks."""
    counters = {"export_tasks": 0, "exported": 0, "validated": 0, "stamped_db": 0,
                "stamped_unique": 0, "setup_cards": 0, "feedback_written": 0,
                "bridge_ok": all(bridge_available().values())}
    export_tasks: list[dict] = []
    while len(export_tasks) < limit:
        task = tasks.claim_next_task(task_types=("export_validation",), now=now)
        if task is None:
            break
        export_tasks.append(task)
    if not export_tasks:
        return counters
    counters["export_tasks"] = len(export_tasks)

    if not apply:
        for task in export_tasks:
            tasks.complete_task(task["task_id"], reason="export_dry_run", now=now)
        return counters

    uc_keys = [str(_read_payload(t).get("uc_key") or "") for t in export_tasks]
    uc_keys = [k for k in uc_keys if k]
    summary = export_requests(private_root, dry_run=False, limit=max(limit, len(export_tasks)),
                              include_regime_specific=True, source="farm_tasks",
                              uc_keys=uc_keys or None)
    counters["exported"] = int(summary.get("exported") or 0)
    exported_ids = [str(x) for x in summary.get("exported_ids") or []]
    requests_dir = Path(private_root) / "hard_validation" / "requests"
    val = run_validation_batch(requests_dir, private_root, dry_run=False,
                               limit=max(limit, len(export_tasks)),
                               candidate_ids=exported_ids or None)
    counters["validated"] = int(val.get("validated") or 0)

    # auto stamp-back into farm_results (was the orphaned refresh_validation_handoff step)
    from src.research_lab.state_db import connect, default_db_path, init_db
    conn = connect(default_db_path(private_root))
    init_db(conn)
    try:
        handoff = refresh_from_artifacts(conn, private_root)
        counters["stamped_db"] = int(handoff.get("rows_stamped_verdict") or 0)
    finally:
        conn.close()

    # mirror verdicts into the coordinator's unique_candidates view
    verdicts_all = _verdict_map(private_root)
    current_ids = exported_ids or [_hard_id_for_task(t) for t in export_tasks]
    verdicts = {cid: verdicts_all[cid] for cid in current_ids if cid in verdicts_all}
    reqs = _request_map(private_root)
    counters["stamped_db"] += _stamp_farm_results_from_contexts(Path(private_root), verdicts)
    counters["setup_cards"] = _write_setup_cards(Path(private_root), current_ids)
    reports_dir = Path(private_root) / "hard_validation" / "reports"
    for cid in current_ids:
        report_data = _read_json(reports_dir / f"{_artifact_stem(cid)}.json")
        if not report_data:
            continue
        try:
            feedback = generate_feedback(HardValidationReport.from_dict(report_data))
        except (KeyError, TypeError, ValueError):
            feedback = None
        if feedback is not None and write_feedback(Path(private_root), feedback, dry_run=False):
            counters["feedback_written"] += 1
    for cid, hard_status in verdicts.items():
        if hard_status:
            req = reqs.get(cid) or {}
            metrics = req.get("metrics") if isinstance(req.get("metrics"), dict) else {}
            uc_key = str(metrics.get("uc_key") or "")
            if uc_key:
                counters["stamped_unique"] += tasks.set_unique_hard_status(uc_key, hard_status, now=now)
            else:
                counters["stamped_unique"] += tasks.set_candidate_hard_status(cid, hard_status, now=now)

    for task in export_tasks:
        hid = _hard_id_for_task(task)
        if hid and hid in verdicts:
            tasks.complete_task(task["task_id"], reason="validated", now=now)
        else:
            tasks.defer_task(task["task_id"], until=(now or 0) + 300, reason="validation_no_verdict", now=now)
    return counters
