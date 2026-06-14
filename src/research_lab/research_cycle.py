# -*- coding: utf-8 -*-
"""Models + report IO for the controlled event-driven research cycle.

This module holds only JSON-friendly dataclasses and the private-root report
writer/reader. The orchestration that ties proposals -> data check -> prepare ->
queue -> worker lives in `scripts/strategy_lab/research_cycle.py`. Nothing here
fetches, queues, or trades; it just describes a cycle and persists its result as a
harmless status file under the private root.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.paths import resolve_private_root

NETWORK_PROVIDERS = {"okx-public"}  # synthetic is offline; null never fetches

# Aggregate count keys (always present, default 0)
COUNT_KEYS = (
    "proposals_generated",
    "proposals_validated",
    "proposals_queued",
    "ready_jobs",
    "skipped_missing_data",
    "skipped_too_short",
    "skipped_malformed",
    "data_requirements",
    "data_missing",
    "data_prepared",
    "worker_completed",
    "worker_deferred",
    "worker_failed",
)


@dataclass(frozen=True)
class ResearchCycleConfig:
    apply: bool = False
    generate_proposals: bool = True
    max_proposals: int = 10
    queue: bool = True
    max_queue: int | None = None
    priority: int = 72
    run_worker: bool = True
    max_worker_jobs: int = 1
    prepare_1m: bool = False
    prepare_1m_apply: bool = False
    provider: str = "null"
    allow_synthetic: bool = False
    night_mode: bool = False

    @property
    def mode(self) -> str:
        return "apply" if self.apply else "dry_run"

    @property
    def llm_send_allowed(self) -> bool:
        return False  # never; the cycle has no LLM send path

    @property
    def network_fetch_allowed(self) -> bool:
        """True only when the cycle would make a real network fetch."""
        return bool(self.apply and self.prepare_1m and self.prepare_1m_apply
                    and self.provider in NETWORK_PROVIDERS)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(mode=self.mode, llm_send_allowed=self.llm_send_allowed,
                 network_fetch_allowed=self.network_fetch_allowed)
        return d


@dataclass(frozen=True)
class CycleStepResult:
    name: str
    status: str  # executed | skipped | blocked | deferred | failed
    detail: str = ""
    counts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail, "counts": dict(self.counts)}


@dataclass
class ResearchCycleResult:
    mode: str
    provider: str
    steps: list[CycleStepResult] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: {k: 0 for k in COUNT_KEYS})
    safety: dict[str, Any] = field(default_factory=dict)
    next_command: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "strategy_lab_research_cycle.v1",
            "generated_at": self.generated_at,
            "mode": self.mode,
            "provider": self.provider,
            "counts": dict(self.counts),
            "steps": [s.to_dict() for s in self.steps],
            "safety": dict(self.safety),
            "next_command": self.next_command,
        }


def cycle_report_path(private_root: Path) -> Path:
    return private_root / "state" / "research_cycle" / "latest.json"


def write_cycle_report(private_root: Path, result: ResearchCycleResult, *, allow_public_output: bool = False) -> Path:
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    path = cycle_report_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    payload["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_cycle_report(private_root: Path) -> dict[str, Any]:
    path = cycle_report_path(private_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def cycle_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Public-safe compact summary for status/dashboard (counts + labels only)."""
    if not report:
        return {"available": False}
    counts = report.get("counts") or {}
    return {
        "available": True,
        "mode": report.get("mode", ""),
        "provider": report.get("provider", "null"),
        "generated_at": report.get("generated_at", ""),
        "proposals_generated": int(counts.get("proposals_generated", 0) or 0),
        "proposals_queued": int(counts.get("proposals_queued", 0) or 0),
        "data_missing": int(counts.get("data_missing", 0) or 0),
        "data_prepared": int(counts.get("data_prepared", 0) or 0),
        "worker_completed": int(counts.get("worker_completed", 0) or 0),
        "worker_deferred": int(counts.get("worker_deferred", 0) or 0),
        "worker_failed": int(counts.get("worker_failed", 0) or 0),
        "next_command": report.get("next_command", ""),
    }
