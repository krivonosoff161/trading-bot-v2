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
from src.research_lab.hard_validation_export import export_requests
from src.research_lab.honest_backtest_bridge import bridge_available, run_validation_batch
from src.research_lab.validation_handoff import refresh_from_artifacts


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


def run_due_validations(tasks: FarmTasksDB, private_root: Path, *, apply: bool,
                        limit: int = 10, now: float | None = None) -> dict[str, Any]:
    """Execute export + validation + stamp-back for queued export_validation tasks."""
    counters = {"export_tasks": 0, "exported": 0, "validated": 0, "stamped_db": 0,
                "stamped_unique": 0, "bridge_ok": all(bridge_available().values())}
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

    summary = export_requests(private_root, dry_run=False, limit=max(limit, len(export_tasks)),
                              include_regime_specific=True)
    counters["exported"] = int(summary.get("exported") or 0)
    requests_dir = Path(private_root) / "hard_validation" / "requests"
    val = run_validation_batch(requests_dir, private_root, dry_run=False, limit=max(limit, len(export_tasks)))
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
    for cid, hard_status in _verdict_map(private_root).items():
        if hard_status:
            counters["stamped_unique"] += tasks.set_candidate_hard_status(cid, hard_status, now=now)

    for task in export_tasks:
        tasks.complete_task(task["task_id"], reason="validated", now=now)
    return counters
