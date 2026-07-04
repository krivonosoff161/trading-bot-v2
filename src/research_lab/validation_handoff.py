# -*- coding: utf-8 -*-
"""Read hard-validation artifacts back into farm_results (decision-machine handoff).

After candidates are exported (hard_validation/requests/<candidate_id>.json) and the
honest-backtest bridge writes verdicts (hard_validation/verdicts/<candidate_id>.json),
this marks each farm_results row exported and stamps its hard_status. candidate_id ==
the run's stable run_id, so the join is exact. A derived validation_state turns the raw
verdict into VALIDATION_EXPORTED / VALIDATION_PASSED / VALIDATION_FAILED / NEEDS_MORE_DATA.
No network, no orders; reads private artifacts and writes only the research DB.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

HARD_PASS = "PAPER_FORWARD_READY"
HARD_FAIL = {"HARD_REJECT", "FAILED_OVERFIT", "FAILED_COSTS", "FAILED_FRAGILITY",
             "FAILED_OOS", "FAILED_DATA_QUALITY", "REGIME_ONLY"}


def _ids_from_dir(directory: Path) -> set[str]:
    return {p.stem for p in directory.glob("*.json")} if directory.exists() else set()


def _verdicts(directory: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not directory.exists():
        return out
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cid = str(data.get("candidate_id") or path.stem)
        out[cid] = str(data.get("hard_status") or "")
    return out


def refresh_from_artifacts(conn: sqlite3.Connection, private_root: Path) -> dict[str, Any]:
    """Stamp farm_results.validation_exported + hard_status from the hard-validation dirs."""
    base = Path(private_root) / "hard_validation"
    exported = _ids_from_dir(base / "requests")
    verdicts = _verdicts(base / "verdicts")
    marked_exported = 0
    marked_verdict = 0
    for cid in exported:
        cur = conn.execute("UPDATE farm_results SET validation_exported = 1 WHERE candidate_id = ?", (cid,))
        marked_exported += int(cur.rowcount or 0)
    for cid, status in verdicts.items():
        cur = conn.execute("UPDATE farm_results SET hard_status = ? WHERE candidate_id = ?", (status, cid))
        marked_verdict += int(cur.rowcount or 0)
    conn.commit()
    return {
        "request_files": len(exported),
        "verdict_files": len(verdicts),
        "rows_marked_exported": marked_exported,
        "rows_stamped_verdict": marked_verdict,
    }


def validation_state(validation_status: str, hard_status: str, exported: bool) -> str:
    """Combine lite status + hard verdict + export into one decision-machine label."""
    if hard_status == HARD_PASS:
        return "VALIDATION_PASSED"
    if hard_status in HARD_FAIL:
        return "VALIDATION_FAILED"
    if hard_status == "NEEDS_MORE_DATA":
        return "NEEDS_MORE_DATA"
    if exported:
        return "VALIDATION_EXPORTED"
    return validation_status or "PENDING"
