"""Public-safe health classification for the fenced numeric compute contour.

The worker and priority loop already publish bounded status artifacts.  This
module turns those artifacts into one deterministic operator signal without
reading task payloads, candidate rows, credentials, or runtime configuration.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

_FATAL_PRIORITY_STAGES = {"claim_failed", "worker_failed"}
_ACTIVE_PRIORITY_STAGES = {"running_slot", "busy"}
_IDLE_PRIORITY_STAGES = {"idle"}
_KNOWN_PRIORITY_STAGES = (
    _FATAL_PRIORITY_STAGES
    | _ACTIVE_PRIORITY_STAGES
    | _IDLE_PRIORITY_STAGES
    | {"stopped", "error"}
)
_FATAL_WORKER_REASONS = {
    "expired_alive_conflict",
    "identity_mismatch",
    "process_identity_mismatch",
    "worker_process_lease_release_failed",
}
_KNOWN_WORKER_STATES = {"running", "completed", "failed", "deferred", "queue_empty"}
_KNOWN_WORKER_REASONS = _FATAL_WORKER_REASONS | {
    "active_worker_owner",
    "legacy_worker_lock_present",
}


def _epoch(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _age(value: Any, now: float) -> float | None:
    timestamp = _epoch(value)
    if timestamp <= 0:
        return None
    return round(max(0.0, now - timestamp), 3)


def assess_compute_pipeline(
    *,
    priority_status: dict[str, Any] | None,
    worker_status: dict[str, Any] | None,
    farm_running: bool,
    farm_started_at: float | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Return one redacted health view for RCC, health reports, and monitors."""

    current = time.time() if now is None else float(now)
    priority = priority_status if isinstance(priority_status, dict) else {}
    worker = worker_status if isinstance(worker_status, dict) else {}
    raw_priority_stage = str(priority.get("stage") or "")
    priority_stage = (
        raw_priority_stage if raw_priority_stage in _KNOWN_PRIORITY_STAGES else ""
    )
    raw_worker_state = str(worker.get("status") or "")
    worker_state = raw_worker_state if raw_worker_state in _KNOWN_WORKER_STATES else ""
    raw_worker_reason = str(worker.get("reason_code") or "")
    worker_reason = (
        raw_worker_reason
        if raw_worker_reason in _KNOWN_WORKER_REASONS
        else "unclassified_failure"
        if raw_worker_reason
        else ""
    )
    priority_age = _age(priority.get("updated_at"), current)
    worker_age = _age(worker.get("updated_at"), current)
    priority_updated_at = _epoch(priority.get("updated_at"))
    worker_updated_at = _epoch(worker.get("updated_at"))
    farm_start = float(farm_started_at or 0.0)
    priority_failure_current = bool(
        priority_stage in _FATAL_PRIORITY_STAGES
        and (farm_start <= 0 or priority_updated_at >= farm_start - 1.0)
    )
    worker_failure_current = bool(
        worker_state == "failed"
        and worker_reason != ""
        and (farm_start <= 0 or worker_updated_at >= farm_start - 1.0)
    )

    hard_fail = bool(
        farm_running
        and (priority_failure_current or worker_failure_current)
    )
    if hard_fail:
        state = "failed"
        reason = (
            f"priority_{priority_stage}"
            if priority_failure_current
            else "worker_lease_lifecycle"
        )
    elif not farm_running:
        state = "stopped"
        reason = "farm_not_running"
    elif priority_stage in _ACTIVE_PRIORITY_STAGES:
        state = "working"
        reason = priority_stage
    elif priority_stage in _IDLE_PRIORITY_STAGES:
        state = "idle"
        reason = priority_stage
    elif priority_stage == "stopped":
        state = "starting"
        reason = "priority_worker_not_started"
    elif priority_stage in _FATAL_PRIORITY_STAGES:
        state = "starting"
        reason = "stale_failure_artifact"
    else:
        state = "starting"
        reason = "priority_status_pending"

    return {
        "schema": "ComputePipelineHealth.v1",
        "state": state,
        "reason": reason,
        "hard_fail": hard_fail,
        "farm_running": bool(farm_running),
        "priority_stage": priority_stage or "unknown",
        "priority_status_age_seconds": priority_age,
        "worker_status": worker_state or "unknown",
        "worker_reason_code": worker_reason,
        "worker_status_age_seconds": worker_age,
        "paper_only": True,
        "execution_allowed": False,
    }
