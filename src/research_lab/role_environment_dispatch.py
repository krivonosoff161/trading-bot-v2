"""Dispatch accepted adaptive role requests into bounded deterministic work.

This bridge never treats an LLM recommendation as applied policy.  It maps the
immutable RoleTaskSpec to an existing paper/research owner and records a private
dispatch result.  Final environment acceptance still requires the separate
deterministic gate and untouched evaluation contract.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import json
from pathlib import Path
import re
from typing import Any

from src.research_lab.farm_tasks_db import FarmTasksDB
from src.research_lab.lineage_contract import utc_now
from src.research_lab.paths import resolve_private_child
from src.research_lab.paper_projection_reader import read_projection_view
from src.research_lab.trade_thesis_supervisor import replay_symbol_fsm


SCHEMA = "RoleEnvironmentDispatch.v1"
_ENVIRONMENT_ID_RE = re.compile(r"env_[0-9a-f]{24}\Z")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _dispatch_path(private_root: Path, recipient: str, environment_id: str) -> Path:
    return resolve_private_child(
        private_root, "state", "role_work_queue", recipient, f"{environment_id}.json"
    )


def _accepted_rows(
    private_root: Path,
    recipient: str,
    *,
    expected_generation_run_id: str | None = None,
    environment_ids: Iterable[str] | None = None,
    check_active: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    base = resolve_private_child(private_root, "state", "role_environments", recipient)
    state_dir = base / "_state"
    rows: list[dict[str, Any]] = []
    if not base.exists() or not state_dir.exists():
        return rows
    if environment_ids is None:
        paths = sorted(base.glob("env_*.json"))
    else:
        paths = []
        seen: set[str] = set()
        for raw_id in environment_ids:
            environment_id = str(raw_id or "")
            if environment_id in seen or not _ENVIRONMENT_ID_RE.fullmatch(environment_id):
                continue
            seen.add(environment_id)
            path = base / f"{environment_id}.json"
            if path.is_file():
                paths.append(path)
    for path in paths:
        if check_active is not None:
            check_active()
        state = _read_json(state_dir / path.name)
        candidate = _read_json(path)
        if not isinstance(state, dict) or not isinstance(candidate, dict):
            continue
        if state.get("status") != "request_accepted":
            continue
        task_spec = candidate.get("task_spec")
        if (
            not isinstance(task_spec, dict)
            or task_spec.get("schema") != "RoleTaskSpec.v1"
        ):
            # Historical request acknowledgements predate executable typed work.
            # Keep them as evidence, but never guess a task from them.
            continue
        if expected_generation_run_id and str(
            task_spec.get("paper_generation_run_id") or ""
        ) != str(expected_generation_run_id):
            continue
        rows.append({**candidate, **state, "schema": candidate.get("schema")})
    return rows


def _retest_specs(private_root: Path) -> list[dict[str, Any]]:
    data = _read_json(
        resolve_private_child(
            private_root, "state", "derived", "outcome_retest_specs.json"
        )
    )
    if not isinstance(data, dict):
        return []
    return [row for row in (data.get("items") or []) if isinstance(row, dict)]


def _paper_rows(
    private_root: Path,
    *,
    evidence_database_path: Path | str | None = None,
    expected_generation_run_id: str | None = None,
) -> list[dict[str, Any]]:
    if expected_generation_run_id:
        generation = read_projection_view(
            private_root,
            "trades",
            legacy_snapshot=resolve_private_child(
                private_root, "state", "derived", "main_paper_trades.json"
            ),
            evidence_database_path=evidence_database_path,
        )
        if generation.get("current") is not True or str(
            generation.get("paper_generation_run_id") or ""
        ) != str(expected_generation_run_id):
            return []
        return [row for row in generation.get("items") or [] if isinstance(row, dict)]
    data = _read_json(
        resolve_private_child(
            private_root, "state", "derived", "paper_product_trades.json"
        )
    )
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("trades", "items", "rows"):
            if isinstance(data.get(key), list):
                return [row for row in data[key] if isinstance(row, dict)]
    return []


def _base_result(row: dict[str, Any], recipient: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "environment_id": str(row.get("environment_id") or ""),
        "feedback_id": str(row.get("feedback_id") or ""),
        "adaptive_trial_id": str(row.get("adaptive_trial_id") or ""),
        "recipient": recipient,
        "task_spec": dict(row.get("task_spec") or {}),
        "status": "waiting",
        "reason": "",
        "work_ref": "",
        "updated_at": utc_now(),
        "paper_only": True,
        "execution_allowed": False,
    }


def _dispatch_farm(
    private_root: Path, tasks: FarmTasksDB, row: dict[str, Any]
) -> dict[str, Any]:
    result = _base_result(row, "farm")
    source_ref = str((row.get("task_spec") or {}).get("source_ref") or "")
    spec = next(
        (
            item
            for item in _retest_specs(private_root)
            if str(item.get("source_ref") or "") == source_ref
        ),
        None,
    )
    if not spec or not bool(spec.get("queueable")):
        result.update(status="waiting", reason="retest_spec_not_ready")
        return result
    task_id, created = tasks.enqueue_task(
        task_type="schedule_retest",
        task_key=f"analyst_retest::{row['environment_id']}",
        priority=55,
        symbol=str(spec.get("symbol") or ""),
        timeframe=str(spec.get("timeframe") or ""),
        family=str(spec.get("family") or ""),
        source_event_id=str(row.get("feedback_id") or ""),
        payload={
            "retest_spec": spec,
            "role_environment_id": row["environment_id"],
            "feedback_id": row.get("feedback_id"),
            "followup_depth": 0,
            "paper_only": True,
            "execution_allowed": False,
        },
    )
    result.update(
        status="queued" if created else "deduped",
        reason="bounded_retest_scheduled",
        work_ref=f"farm_tasks:{task_id}",
    )
    return result


def _dispatch_validator(tasks: FarmTasksDB, row: dict[str, Any]) -> dict[str, Any]:
    result = _base_result(row, "validator")
    subject = (row.get("task_spec") or {}).get("subject") or {}
    candidate_id = str(subject.get("candidate_id") or "")
    candidate = next(
        (
            item
            for item in tasks.latest_unique_candidates(limit=5000)
            if candidate_id and str(item.get("candidate_id") or "") == candidate_id
        ),
        None,
    )
    if not candidate:
        result.update(status="waiting", reason="candidate_from_farm_not_ready")
        return result
    uc_key = str(candidate.get("uc_key") or "")
    task_id, created = tasks.enqueue_task(
        task_type="export_validation",
        task_key=f"analyst_validation::{row['environment_id']}::{uc_key}",
        priority=50,
        symbol=str(candidate.get("symbol") or ""),
        timeframe=str(candidate.get("timeframe") or ""),
        family=str(candidate.get("family") or ""),
        source_event_id=str(row.get("feedback_id") or ""),
        payload={
            "uc_key": uc_key,
            "candidate_id": candidate_id,
            "role_environment_id": row["environment_id"],
            "feedback_id": row.get("feedback_id"),
            "paper_only": True,
            "execution_allowed": False,
        },
    )
    result.update(
        status="queued" if created else "deduped",
        reason="untouched_validation_scheduled",
        work_ref=f"farm_tasks:{task_id}",
    )
    return result


def _dispatch_trader(
    private_root: Path,
    row: dict[str, Any],
    *,
    evidence_database_path: Path | str | None = None,
    expected_generation_run_id: str | None = None,
) -> dict[str, Any]:
    result = _base_result(row, "trader")
    subject = (row.get("task_spec") or {}).get("subject") or {}
    symbol = str(subject.get("symbol") or "")
    group = [
        item
        for item in _paper_rows(
            private_root,
            evidence_database_path=evidence_database_path,
            expected_generation_run_id=expected_generation_run_id,
        )
        if str(item.get("symbol") or item.get("pair") or "") == symbol
    ]
    if not group:
        result.update(status="waiting", reason="paper_observation_not_ready")
        return result
    replay = replay_symbol_fsm(group)
    replay_path = resolve_private_child(
        private_root,
        "state",
        "role_work_results",
        "trader",
        f"{row['environment_id']}.json",
    )
    _write_json(
        replay_path,
        {
            "schema": "TraderRoleReplayResult.v1",
            "environment_id": row["environment_id"],
            "feedback_id": row.get("feedback_id"),
            "symbol": symbol,
            "replay": replay,
            "paper_only": True,
            "execution_allowed": False,
        },
    )
    result.update(
        status="completed", reason="paper_replay_completed", work_ref=str(replay_path)
    )
    return result


def dispatch_role_environments(
    private_root: Path,
    tasks: FarmTasksDB,
    *,
    apply: bool,
    limit_per_role: int = 20,
    expected_generation_run_id: str | None = None,
    evidence_database_path: Path | str | None = None,
    environment_ids_by_role: Mapping[str, Iterable[str]] | None = None,
    check_active: Callable[[], None] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "schema": "RoleEnvironmentDispatchSummary.v1",
        "by_role": {},
        "environment_ids": {},
        "paper_only": True,
        "execution_allowed": False,
        "apply": bool(apply),
        "paper_generation_run_id": str(expected_generation_run_id or ""),
        "current_generation_compatible": bool(expected_generation_run_id),
    }
    for recipient in ("farm", "validator", "trader"):
        counters = {"seen": 0, "queued": 0, "deduped": 0, "completed": 0, "waiting": 0}
        processed_ids: list[str] = []
        for row in _accepted_rows(
            private_root,
            recipient,
            expected_generation_run_id=expected_generation_run_id,
            environment_ids=(
                environment_ids_by_role.get(recipient, ())
                if environment_ids_by_role is not None
                else None
            ),
            check_active=check_active,
        )[: max(0, int(limit_per_role))]:
            if check_active is not None:
                check_active()
            counters["seen"] += 1
            if not apply:
                counters["waiting"] += 1
                continue
            if recipient == "farm":
                result = _dispatch_farm(private_root, tasks, row)
            elif recipient == "validator":
                result = _dispatch_validator(tasks, row)
            else:
                result = _dispatch_trader(
                    private_root,
                    row,
                    evidence_database_path=evidence_database_path,
                    expected_generation_run_id=expected_generation_run_id,
                )
            _write_json(
                _dispatch_path(private_root, recipient, row["environment_id"]), result
            )
            processed_ids.append(str(row["environment_id"]))
            counters[result["status"]] = counters.get(result["status"], 0) + 1
        summary["by_role"][recipient] = counters
        summary["environment_ids"][recipient] = processed_ids
    return summary


def reconcile_role_work_results(
    private_root: Path,
    tasks: FarmTasksDB,
    *,
    apply: bool,
    expected_generation_run_id: str | None = None,
    environment_ids_by_role: Mapping[str, Iterable[str]] | None = None,
    check_active: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Project completed recipient work into a bounded System Analyst inbox."""
    inbox: list[dict[str, Any]] = []
    dispatch_root = resolve_private_child(private_root, "state", "role_work_queue")
    for recipient in ("farm", "validator", "trader"):
        directory = dispatch_root / recipient
        if not directory.exists():
            continue
        if environment_ids_by_role is None:
            paths = sorted(directory.glob("env_*.json"))
        else:
            paths = []
            seen: set[str] = set()
            for raw_id in environment_ids_by_role.get(recipient, ()):
                environment_id = str(raw_id or "")
                if (
                    environment_id in seen
                    or not _ENVIRONMENT_ID_RE.fullmatch(environment_id)
                ):
                    continue
                seen.add(environment_id)
                path = directory / f"{environment_id}.json"
                if path.is_file():
                    paths.append(path)
        for path in paths:
            if check_active is not None:
                check_active()
            dispatch = _read_json(path)
            if not isinstance(dispatch, dict):
                continue
            raw_task_spec = dispatch.get("task_spec")
            task_spec = raw_task_spec if isinstance(raw_task_spec, dict) else {}
            if expected_generation_run_id and str(
                task_spec.get("paper_generation_run_id") or ""
            ) != str(expected_generation_run_id):
                continue
            environment_id = str(dispatch.get("environment_id") or "")
            result: dict[str, Any] | None = None
            if recipient == "trader":
                result_path = resolve_private_child(
                    private_root,
                    "state",
                    "role_work_results",
                    "trader",
                    f"{environment_id}.json",
                )
                raw = _read_json(result_path)
                if isinstance(raw, dict):
                    result = raw
            else:
                rows = tasks.tasks_for_role_environment(environment_id)
                wanted = "run_sweep" if recipient == "farm" else "export_validation"
                terminal = [
                    row
                    for row in rows
                    if row.get("task_type") == wanted
                    and row.get("state") in {"completed", "failed", "skipped"}
                ]
                if terminal:
                    task = terminal[-1]
                    result = {
                        "schema": "RoleWorkResult.v1",
                        "environment_id": environment_id,
                        "feedback_id": dispatch.get("feedback_id"),
                        "recipient": recipient,
                        "status": str(task.get("state") or ""),
                        "reason": str(task.get("machine_reason") or ""),
                        "task_id": int(task.get("task_id") or 0),
                        "task_type": wanted,
                        "result_ref": str(
                            task.get("last_result_ref")
                            or task.get("run_dir_label")
                            or ""
                        ),
                        "task_spec": dispatch.get("task_spec") or {},
                        "paper_only": True,
                        "execution_allowed": False,
                    }
            if not isinstance(result, dict):
                continue
            normalized = {
                "schema": "SystemAnalystResultInput.v1",
                "result_id": f"role_result::{environment_id}::{recipient}",
                "environment_id": environment_id,
                "feedback_id": dispatch.get("feedback_id"),
                "adaptive_trial_id": dispatch.get("adaptive_trial_id"),
                "recipient": recipient,
                "result": result,
                "task_spec": dispatch.get("task_spec") or {},
                "paper_only": True,
                "execution_allowed": False,
            }
            inbox.append(normalized)
    inbox.sort(key=lambda row: row["result_id"])
    if apply:
        out_path = resolve_private_child(
            private_root, "state", "derived", "system_analyst_result_inbox.jsonl"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in inbox
            ),
            encoding="utf-8",
        )
    return {
        "schema": "RoleWorkResultReconciliation.v1",
        "results": len(inbox),
        "by_recipient": {
            recipient: sum(1 for row in inbox if row["recipient"] == recipient)
            for recipient in ("farm", "validator", "trader")
        },
        "paper_only": True,
        "execution_allowed": False,
        "apply": bool(apply),
        "paper_generation_run_id": str(expected_generation_run_id or ""),
        "current_generation_compatible": bool(expected_generation_run_id),
    }
