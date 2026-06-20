# -*- coding: utf-8 -*-
"""Read-only completion verdict + two-DB reconciliation (no writes, no compute).

Distinguishes a genuinely DRAINED research cycle from one merely PAUSED with claimable
work, and reconciles the two result stores (farm_tasks.sqlite brain vs strategy_lab.sqlite
worker ledger) whose status vocabularies diverge. It opens both DBs read-only, writes
nothing back, and re-labels stale NEEDS_OI_DATA rows as oi_unmeasured ONLY in the derived
view (the source is never mutated). No money path, no compute, no apply.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.state_db import default_db_path


def _ro(path: Path) -> sqlite3.Connection | None:
    if not Path(path).exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _counts(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    try:
        return {str(r[0] if r[0] is not None else ""): int(r[1]) for r in conn.execute(sql)}
    except sqlite3.Error:
        return {}


def completion_verdict(private_root: Path, *, now: float | None = None) -> dict[str, Any]:
    """DRAINED / PAUSED_WITH_WORK / BLOCKED_BOUNDED from the farm_tasks brain (read-only)."""
    now = time.time() if now is None else now
    conn = _ro(tasks_db_path(private_root))
    if conn is None:
        return {"state": "NO_DB", "reasons": {}, "available": False}
    try:
        by_state = _counts(conn, "SELECT state, COUNT(*) FROM tasks GROUP BY state")
        eligible = int(conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE (state='queued' OR (state='deferred' AND deferred_until<=?)) "
            "AND (depends_on IS NULL OR depends_on IN (SELECT task_id FROM tasks WHERE state='completed'))",
            (now,)).fetchone()[0])
        running = int(by_state.get("running", 0))
        deferred_due = int(conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE state='deferred' AND deferred_until<=?", (now,)).fetchone()[0])
        deferred_future = int(by_state.get("deferred", 0)) - deferred_due
        blocked = _counts(conn, "SELECT machine_reason, COUNT(*) FROM tasks WHERE state='blocked' GROUP BY machine_reason")
        blocked_unreasoned = int(conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE state='blocked' AND (machine_reason IS NULL OR machine_reason='')"
        ).fetchone()[0])
    finally:
        conn.close()
    reasons = {
        "eligible_now": eligible, "running": running,
        "deferred_due": deferred_due, "deferred_future": deferred_future,
        "blocked": blocked, "blocked_unreasoned": blocked_unreasoned,
    }
    if eligible > 0 or running > 0:
        state = "PAUSED_WITH_WORK"
    elif deferred_future > 0 or sum(blocked.values()) > 0:
        state = "BLOCKED_BOUNDED"
    else:
        state = "DRAINED"
    # Even BLOCKED_BOUNDED is only honest if every blocked task carries a machine_reason.
    if state != "PAUSED_WITH_WORK" and blocked_unreasoned > 0:
        state = "UNOWNED_RESIDUE"
    return {"state": state, "reasons": reasons, "available": True}


def reconcile_dbs(private_root: Path) -> dict[str, Any]:
    """Compare strategy_lab.sqlite (worker ledger) vs farm_tasks.unique_candidates (brain).

    Surfaces statuses present in the ledger but with no owner in the brain, and derives an
    oi_unmeasured relabel for stale NEEDS_OI_DATA rows. Read-only — the source DBs are not
    written; oi_unmeasured exists only in this view.
    """
    ledger = _ro(default_db_path(private_root))
    brain = _ro(tasks_db_path(private_root))
    out: dict[str, Any] = {"available": bool(ledger and brain), "orphans": {}, "oi_relabel": {}}
    if ledger is not None:
        try:
            fr_decision = _counts(ledger, "SELECT decision, COUNT(*) FROM farm_results GROUP BY decision")
            cand_validation = _counts(ledger, "SELECT validation_status, COUNT(*) FROM candidates GROUP BY validation_status")
            out["ledger_decisions"] = fr_decision
            needs_oi = int(fr_decision.get("NEEDS_OI_DATA", 0))
            legacy_unscored = int(cand_validation.get("", 0))
        finally:
            ledger.close()
    else:
        needs_oi = legacy_unscored = 0
    enrich_oi_tasks = 0
    if brain is not None:
        try:
            enrich_oi_tasks = int(brain.execute(
                "SELECT COUNT(*) FROM tasks WHERE task_type='enrich_oi'").fetchone()[0])
            uc_oi = int(brain.execute(
                "SELECT COUNT(*) FROM unique_candidates WHERE decision='NEEDS_OI_DATA'").fetchone()[0])
        finally:
            brain.close()
    else:
        uc_oi = 0
    # NEEDS_OI_DATA lives only in the ledger and has no brain owner (no enrich_oi task planned).
    if needs_oi and uc_oi == 0:
        out["orphans"]["NEEDS_OI_DATA"] = {
            "count": needs_oi, "in_ledger_only": True, "enrich_oi_tasks": enrich_oi_tasks,
            "owner": "enrich_oi" if enrich_oi_tasks else "UNOWNED",
            "relabel": "oi_unmeasured (derived; OI families are not planned by the current loop)",
        }
        # Derived re-stamp (view only, source untouched): NEEDS_OI_DATA -> oi_unmeasured.
        out["oi_relabel"] = {"NEEDS_OI_DATA": needs_oi, "as": "oi_unmeasured", "persisted": False}
    if legacy_unscored:
        out["orphans"]["LEGACY_UNSCORED"] = {
            "count": legacy_unscored, "in_ledger_only": True, "owner": "classify_incomplete",
            "note": "empty validation_status in legacy candidates table (was surfaced as UNKNOWN)",
        }
    return out
