# -*- coding: utf-8 -*-
"""Structured append-only logs for the continuous farm — explain state, don't spam.

Three JSONL streams under ``<private_root>/logs/farm/`` (mirrors the scanner_journal
pattern so storage_policy.maintain can rotate them):

  * ``cycle_log.jsonl``       — one row per coordinator cycle (pivot, counters, states);
  * ``task_transitions.jsonl``— one row per task state change (the audit trail that the
    in-place ``tasks`` row otherwise loses);
  * ``errors.jsonl``          — worker / cycle errors that need human attention.

These are the durable, queryable explanation of "what the farm did and why". Raw stdout
stays for live watching; humans read structured state via farm_status_report / cockpit,
not by tailing logs. Pure file IO, no network, no order path.
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
    """All farm log files — hand these to storage_policy.maintain for rotation."""
    return [cycle_log_path(private_root), transitions_path(private_root), errors_path(private_root)]


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"schema": SCHEMA, **row}, ensure_ascii=False, sort_keys=True) + "\n")


def log_cycle(private_root: Path, *, ts: float, mode: str, result: dict[str, Any]) -> None:
    """Persist one cycle summary (the dict returned by run_coordinator_cycle)."""
    counters = {k: v for k, v in (result.get("counters") or {}).items()
                if isinstance(v, int) and v}
    status = result.get("status") or {}
    _append(cycle_log_path(private_root), {
        "ts": round(float(ts), 3), "mode": mode, "pivot": result.get("pivot"),
        "active_tasks": result.get("active_tasks"), "counters": counters,
        "by_state": status.get("by_state"), "blocked_reasons": status.get("blocked_reasons"),
        "deferred_reasons": status.get("deferred_reasons"),
    })


def make_transition_sink(private_root: Path):
    """A callback for FarmTasksDB.on_transition that appends each state change."""
    path = transitions_path(private_root)

    def sink(record: dict[str, Any]) -> None:
        _append(path, record)

    return sink


def log_error(private_root: Path, *, where: str, error: str, ts: float, **extra: Any) -> None:
    _append(errors_path(private_root), {"ts": round(float(ts), 3), "where": where,
                                        "error": str(error)[:500], **extra})
