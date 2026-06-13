# -*- coding: utf-8 -*-
"""Runtime enforcement of the Strategy Lab resource policy.

The schema gates (sweep_spec.py) decide whether a spec is *allowed*. This module
decides whether a worker may run *right now* and how large a job may be, so the
policy actually throttles a busy desktop instead of only describing limits.

Pure decision functions take an explicit `now` and the recent job-start times so
they are fully testable without sleeping. Only the small cadence file IO touches
disk, under the private research root.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.research_lab.resource_policy import ResourcePolicy

_RETAIN_SECONDS = 7200  # keep ~2h of starts so the hourly window is always covered


def cadence_path(private_root: Path) -> Path:
    return private_root / "state" / "job_cadence.json"


def worker_status_path(private_root: Path) -> Path:
    return private_root / "state" / "worker_status.json"


def write_worker_status(path: Path, **fields: Any) -> None:
    """Persist the latest worker outcome (status/reason/...) for the dashboard."""
    payload = {"updated_at": dt.datetime.now(dt.timezone.utc).isoformat(), **fields}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_worker_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class CadenceDecision:
    allowed: bool
    wait_seconds: int
    reason: str


def evaluate_cadence(
    policy: ResourcePolicy,
    recent_starts: list[float],
    now: float,
) -> CadenceDecision:
    """Decide if a new job may start now, given prior job-start epoch seconds."""
    starts = sorted(s for s in recent_starts if s <= now)
    if starts:
        since_last = now - starts[-1]
        gap = int(policy.min_seconds_between_jobs)
        if since_last < gap:
            return CadenceDecision(False, max(1, int(gap - since_last)), "min_seconds_between_jobs")
    window = [s for s in starts if now - s < 3600]
    if len(window) >= int(policy.max_jobs_per_hour):
        wait = int(3600 - (now - window[0]))
        return CadenceDecision(False, max(1, wait), "max_jobs_per_hour")
    return CadenceDecision(True, 0, "ok")


def effective_variant_cap(policy: ResourcePolicy, spec_max_runs: int) -> tuple[int, bool]:
    """Return (effective_cap, was_capped) for a job's variant budget."""
    cap = max(1, int(policy.max_variants_per_job))
    if spec_max_runs and spec_max_runs > 0:
        return min(spec_max_runs, cap), spec_max_runs > cap
    # spec_max_runs == 0 means "unlimited" -> always clamp to the policy cap.
    return cap, True


def load_recent_starts(path: Path) -> list[float]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[float] = []
    for item in data:
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            continue
    return out


def record_start(path: Path, now: float) -> None:
    """Append a job-start timestamp, trimming entries older than the retain window."""
    starts = [s for s in load_recent_starts(path) if now - s < _RETAIN_SECONDS]
    starts.append(float(now))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(starts)), encoding="utf-8")
