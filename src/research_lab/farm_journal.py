# -*- coding: utf-8 -*-
"""Structured append-only logs for the continuous farm — explain state, don't spam.

Three legacy JSONL streams under ``<private_root>/logs/farm/``. Automatic storage
maintenance may report their size, but does not rotate or truncate them:

  * ``cycle_log.jsonl``       — one row per coordinator cycle (pivot, counters, states);
  * ``task_transitions.jsonl``— one row per task state change (the audit trail that the
    in-place ``tasks`` row otherwise loses);
  * ``errors.jsonl``          — worker / cycle errors that need human attention.

These are the durable, queryable explanation of "what the farm did and why". Raw stdout
stays for live watching; humans read structured state via farm_status_report / cockpit,
not by tailing logs. The separate segmented-store adapter is synthetic-only and must be
passed explicitly; this module never activates, imports, or dual-writes it. Pure file IO,
no network, no order path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA = "farm_journal.v1"


def farm_log_dir(private_root: Path) -> Path:
    return Path(private_root) / "logs" / "farm"


def cycle_log_path(private_root: Path) -> Path:
    return farm_log_dir(private_root) / "cycle_log.jsonl"


def transitions_path(private_root: Path) -> Path:
    return farm_log_dir(private_root) / "task_transitions.jsonl"


def errors_path(private_root: Path) -> Path:
    return farm_log_dir(private_root) / "errors.jsonl"


def farm_log_paths(private_root: Path) -> list[Path]:
    """All farm log files for report-only storage status."""
    return [cycle_log_path(private_root), transitions_path(private_root), errors_path(private_root)]


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"schema": SCHEMA, **row}, ensure_ascii=False, sort_keys=True) + "\n")


def log_cycle(
    private_root: Path,
    *,
    ts: float,
    mode: str,
    result: dict[str, Any],
    stages: dict[str, Any] | None = None,
    discovery: dict[str, Any] | None = None,
) -> None:
    """Persist one cycle summary (the dict returned by run_coordinator_cycle).

    ``stages`` records which closing-the-loop stages (worker/validation/paper/enrich)
    were active and, when skipped, why — so an operator can later see that the loop only
    queued work but never computed/validated it. ``discovery`` records the universe
    snapshot freshness (fresh/refreshed/stale_no_refresh/missing) so a stale universe is
    visible instead of silently degrading to blocked:no_eligible.
    """
    counters = {k: v for k, v in (result.get("counters") or {}).items()
                if isinstance(v, int) and v}
    status = result.get("status") or {}
    row: dict[str, Any] = {
        "ts": round(float(ts), 3), "mode": mode, "pivot": result.get("pivot"),
        "active_tasks": result.get("active_tasks"), "counters": counters,
        "by_state": status.get("by_state"), "blocked_reasons": status.get("blocked_reasons"),
        "deferred_reasons": status.get("deferred_reasons"),
    }
    if stages is not None:
        row["stages"] = stages
    if discovery is not None:
        row["discovery"] = discovery
    paper = result.get("paper") or {}
    paper_counters = {k: v for k, v in (paper.get("counters") or {}).items()
                      if isinstance(v, int) and v}
    if paper_counters:
        row["paper_counters"] = paper_counters
    readiness = paper.get("readiness") or {}
    if readiness:
        row["paper_ready"] = readiness.get("paper_forward_ready")
    _append(cycle_log_path(private_root), row)


def _read_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError):
                continue
    return rows[-limit:] if limit and limit > 0 else rows


def read_recent_cycles(private_root: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Most recent cycle rows (oldest-first) — for status/dashboard, not raw tailing."""
    return _read_tail(cycle_log_path(private_root), limit)


def read_recent_errors(private_root: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Most recent error rows (oldest-first)."""
    return _read_tail(errors_path(private_root), limit)


def skipped_stages(cycle_row: dict[str, Any]) -> list[str]:
    """Names of critical stages that were OFF in a cycle row (empty if none/unknown)."""
    stages = cycle_row.get("stages") or {}
    return [name for name, s in stages.items()
            if isinstance(s, dict) and s.get("critical") and not s.get("enabled")]


def make_transition_sink(private_root: Path):
    """A callback for FarmTasksDB.on_transition that appends each state change."""
    path = transitions_path(private_root)

    def sink(record: dict[str, Any]) -> None:
        _append(path, record)

    return sink


def log_error(private_root: Path, *, where: str, error: str, ts: float, **extra: Any) -> None:
    _append(errors_path(private_root), {"ts": round(float(ts), 3), "where": where,
                                        "error": str(error)[:500], **extra})
