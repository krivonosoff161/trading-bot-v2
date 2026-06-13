# -*- coding: utf-8 -*-
"""Resource policy loader: keep a shared, limited desktop quiet.

The policy bounds worker count, queue size, autopilot generation, job cadence,
and whether heavy or 1m jobs are allowed. `night_mode` is an opt-in relaxation
that overrides only the keys it lists; everything else stays as the quiet default.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.research_lab.config_io import CONFIG_DIR, load_yaml_mapping, require

DEFAULT_PATH = CONFIG_DIR / "resource_policy.yaml"
_REQUIRED = (
    "mode",
    "max_workers",
    "max_queue_size",
    "max_variants_per_job",
    "autopilot_refill_when_queue_below",
    "autopilot_generate_max",
    "min_seconds_between_jobs",
    "max_jobs_per_hour",
    "allow_heavy_jobs",
    "allow_1m_jobs",
    "process_priority",
    "llm_auto_execute",
)
_NIGHT_INT_KEYS = (
    "max_queue_size",
    "max_variants_per_job",
    "autopilot_generate_max",
    "min_seconds_between_jobs",
    "max_jobs_per_hour",
)
_NIGHT_BOOL_KEYS = ("allow_heavy_jobs",)


def _norm_1m(value: Any) -> str:
    """Normalize allow_1m_jobs to a token: 'true' / 'false' / 'trigger_only'."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().lower()


@dataclass(frozen=True)
class ResourcePolicy:
    mode: str
    max_workers: int
    max_queue_size: int
    max_variants_per_job: int
    autopilot_refill_when_queue_below: int
    autopilot_generate_max: int
    min_seconds_between_jobs: int
    max_jobs_per_hour: int
    allow_heavy_jobs: bool
    allow_1m_jobs: str
    process_priority: str
    llm_auto_execute: bool

    def allows_1m(self, resource_class: str = "event_1m") -> bool:
        """True if a 1m job of this resource class is permitted by the policy."""
        token = self.allow_1m_jobs
        if token in ("false", "none", "no"):
            return False
        if token in ("true", "all", "yes"):
            return True
        # trigger_only: only the bounded event microscope is allowed.
        return resource_class == "event_1m"

    def with_night_mode(self, night: dict[str, Any]) -> "ResourcePolicy":
        """Return a copy with the listed night_mode keys overridden."""
        overrides: dict[str, Any] = {}
        for key in _NIGHT_INT_KEYS:
            if key in night:
                overrides[key] = int(night[key])
        for key in _NIGHT_BOOL_KEYS:
            if key in night:
                overrides[key] = bool(night[key])
        if "allow_1m_jobs" in night:
            overrides["allow_1m_jobs"] = _norm_1m(night["allow_1m_jobs"])
        return replace(self, **overrides)


def load_resource_policy(path: str | Path = DEFAULT_PATH, *, night_mode: bool = False) -> ResourcePolicy:
    data = load_yaml_mapping(path, what="resource_policy")
    for key in _REQUIRED:
        require(data, key, what="resource_policy")
    policy = ResourcePolicy(
        mode=str(data["mode"]),
        max_workers=int(data["max_workers"]),
        max_queue_size=int(data["max_queue_size"]),
        max_variants_per_job=int(data["max_variants_per_job"]),
        autopilot_refill_when_queue_below=int(data["autopilot_refill_when_queue_below"]),
        autopilot_generate_max=int(data["autopilot_generate_max"]),
        min_seconds_between_jobs=int(data["min_seconds_between_jobs"]),
        max_jobs_per_hour=int(data["max_jobs_per_hour"]),
        allow_heavy_jobs=bool(data["allow_heavy_jobs"]),
        allow_1m_jobs=_norm_1m(data["allow_1m_jobs"]),
        process_priority=str(data["process_priority"]),
        llm_auto_execute=bool(data["llm_auto_execute"]),
    )
    if night_mode:
        night = data.get("night_mode") or {}
        if not isinstance(night, dict):
            raise ValueError("resource_policy 'night_mode' must be a mapping when present")
        policy = policy.with_night_mode(night)
    return policy
