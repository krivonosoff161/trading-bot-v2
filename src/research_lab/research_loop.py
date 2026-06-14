# -*- coding: utf-8 -*-
"""Report IO for the time-bounded research loop (heartbeat + iteration log).

A loop is a controlled, time-bounded sequence of research-cycle iterations — NOT a
daemon. It runs until a wall-clock duration or an iteration cap, sleeping between
iterations and respecting the worker throttle (a deferred worker is recorded and the
loop waits, it does not storm). This module only persists the heartbeat + final report
under the private root; the orchestration lives in `scripts/strategy_lab/research_loop.py`.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from src.research_lab.paths import resolve_private_root


def _loop_dir(private_root: Path) -> Path:
    return private_root / "state" / "research_loop"


def heartbeat_path(private_root: Path) -> Path:
    return _loop_dir(private_root) / "heartbeat.json"


def loop_report_path(private_root: Path) -> Path:
    return _loop_dir(private_root) / "latest.json"


def write_heartbeat(private_root: Path, payload: dict[str, Any], *, allow_public_output: bool = False) -> Path:
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    path = heartbeat_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), **payload}
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_loop_report(private_root: Path, report: dict[str, Any], *, allow_public_output: bool = False) -> Path:
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    path = loop_report_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "strategy_lab_research_loop.v1",
               "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), **report}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_loop_report(private_root: Path) -> dict[str, Any]:
    path = loop_report_path(private_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def loop_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Public-safe compact summary for status/dashboard (counts/labels only)."""
    if not report:
        return {"available": False}
    totals = report.get("totals") or {}
    log = report.get("iteration_log") or []
    last = log[-1] if log else {}
    last_llm = last.get("llm") or {}
    return {
        "available": True,
        "mode": report.get("mode", ""),
        "generated_at": report.get("generated_at", ""),
        "iterations": int(report.get("iterations", 0) or 0),
        "duration_minutes": report.get("duration_minutes", 0),
        "proposals_queued": int(totals.get("proposals_queued", 0) or 0),
        "llm_validated": int(totals.get("llm_validated", 0) or 0),
        "llm_rejected": int(totals.get("llm_rejected", 0) or 0),
        "skipped_missing_data": int(totals.get("skipped_missing_data", 0) or 0),
        "worker_completed": int(totals.get("worker_completed", 0) or 0),
        "worker_deferred": int(totals.get("worker_deferred", 0) or 0),
        "worker_failed": int(totals.get("worker_failed", 0) or 0),
        "llm_cost_rub": report.get("llm_cost_rub", 0.0),
        # last-iteration detail (operator wants: what did the LLM/worker just do?)
        "last_llm_status": str(last_llm.get("status", "")),
        "last_llm_reject_reasons": dict(last_llm.get("reject_reasons", {}) or {}),
        "last_worker": dict(last.get("worker", {}) or {}),
        "next_command": report.get("next_command", ""),
        "stop_requested": bool(report.get("stop_requested")),
    }
