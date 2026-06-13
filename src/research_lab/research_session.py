# -*- coding: utf-8 -*-
"""Models + report IO for a research SESSION (a cycle + LLM advisory, one pass).

A session is one operator-initiated, capped pass: it may build/send (gated) an LLM
proposal request, then runs one readiness-aware research cycle. This module only
holds the JSON-friendly report and its private-root IO; orchestration lives in
`scripts/strategy_lab/research_session.py`. No daemon, no network, no paid LLM here.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.paths import resolve_private_root


@dataclass
class SessionReport:
    mode: str
    cycle: dict[str, Any] = field(default_factory=dict)        # research_cycle summary
    llm: dict[str, Any] = field(default_factory=dict)          # loop config + gate decision + pack label
    readiness: dict[str, int] = field(default_factory=dict)    # ready/skipped counts
    next_command: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "strategy_lab_research_session.v1",
            "generated_at": self.generated_at,
            "mode": self.mode,
            "cycle": self.cycle,
            "llm": self.llm,
            "readiness": self.readiness,
            "next_command": self.next_command,
        }


def session_report_path(private_root: Path) -> Path:
    return private_root / "state" / "research_session" / "latest.json"


def write_session_report(private_root: Path, report: SessionReport, *, allow_public_output: bool = False) -> Path:
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    path = session_report_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    payload["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_session_report(private_root: Path) -> dict[str, Any]:
    path = session_report_path(private_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def session_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Public-safe compact summary for status/dashboard (counts + labels only)."""
    if not report:
        return {"available": False}
    cycle = report.get("cycle") or {}
    llm = report.get("llm") or {}
    readiness = report.get("readiness") or {}
    return {
        "available": True,
        "mode": report.get("mode", ""),
        "generated_at": report.get("generated_at", ""),
        "proposals_queued": int(cycle.get("proposals_queued", 0) or 0),
        "ready_jobs": int(readiness.get("ready_jobs", 0) or 0),
        "skipped_missing_data": int(readiness.get("skipped_missing_data", 0) or 0),
        "worker_completed": int(cycle.get("worker_completed", 0) or 0),
        "worker_deferred": int(cycle.get("worker_deferred", 0) or 0),
        "llm_mode": llm.get("mode", "export_only"),
        "llm_enabled": bool(llm.get("enabled", False)),
        "next_command": report.get("next_command", ""),
    }


def asdict_safe(obj: Any) -> dict[str, Any]:
    return asdict(obj) if hasattr(obj, "__dataclass_fields__") else dict(obj)
