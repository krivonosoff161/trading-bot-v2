"""Fail-closed owner-authority state for the canonical paper RCC.

The monitor deliberately separates startup absence from authority loss.  An
old expired row is not a writer, so startup may wait for the canonical farm to
acquire a fresh lease.  Once one exact owner/fence generation has been
observed, any disappearance, expiry, competing identity, unexpected resource,
or generation change is a hard failure.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from src.research_lab.canary_checkpoint_policy import CanaryMonitorHardFailure
from src.research_lab.ownership import (
    CanonicalAuthorityAssessment,
    IdentityProbe,
    ProcessIdentity,
    assess_canonical_farm_authority,
)


AuthorityRowsReader = Callable[[], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class CanonicalOwnerSafetySample:
    state: str
    ready: bool
    owner_id: str | None
    canonical_fence: int | None
    process_identity: ProcessIdentity | None
    resources: tuple[str, ...]


@dataclass(frozen=True)
class CanonicalRccProcessIdentity:
    """Immutable RCC identity published by its liveness heartbeat."""

    pid: int
    started_at: float


@dataclass(frozen=True)
class CanonicalRccFinalizerDecision:
    """Identity-bound action for an external graceful-stop finalizer."""

    action: str
    shutdown_state: str
    process_identity: ProcessIdentity
    reason_code: str | None


def parse_rcc_heartbeat_process_identity(
    heartbeat: Mapping[str, Any],
) -> CanonicalRccProcessIdentity:
    """Parse the fail-closed PID/start tuple from an RCC v3 heartbeat."""

    if heartbeat.get("schema") != "ResearchControlCenterHeartbeat.v3":
        raise CanaryMonitorHardFailure("rcc_heartbeat_identity:schema_mismatch")
    if heartbeat.get("paper_only") is not True:
        raise CanaryMonitorHardFailure("rcc_heartbeat_identity:paper_boundary_missing")
    if heartbeat.get("execution_allowed") is not False:
        raise CanaryMonitorHardFailure(
            "rcc_heartbeat_identity:execution_boundary_missing"
        )
    pid_value = heartbeat.get("pid")
    started_at_value = heartbeat.get("started_at")
    if (
        isinstance(pid_value, bool)
        or not isinstance(pid_value, int)
        or pid_value <= 0
    ):
        raise CanaryMonitorHardFailure("rcc_heartbeat_identity:pid_missing")
    if isinstance(started_at_value, bool) or not isinstance(
        started_at_value, (int, float)
    ):
        raise CanaryMonitorHardFailure("rcc_heartbeat_identity:start_missing")
    started_at = float(started_at_value)
    if not math.isfinite(started_at) or started_at <= 0:
        raise CanaryMonitorHardFailure("rcc_heartbeat_identity:start_invalid")
    return CanonicalRccProcessIdentity(pid=pid_value, started_at=started_at)


def verify_rcc_heartbeat_process_identity(
    heartbeat: Mapping[str, Any],
    *,
    identity_probe: IdentityProbe,
) -> ProcessIdentity:
    """Bind an RCC heartbeat to the same currently live process generation."""

    expected = parse_rcc_heartbeat_process_identity(heartbeat)
    actual = identity_probe(expected.pid)
    if actual is None:
        raise CanaryMonitorHardFailure("rcc_heartbeat_identity:process_not_live")
    if actual.pid != expected.pid or actual.started_at != expected.started_at:
        raise CanaryMonitorHardFailure("rcc_heartbeat_identity:generation_mismatch")
    return actual


def decide_rcc_finalizer_action(
    heartbeat: Mapping[str, Any],
    *,
    identity_probe: IdentityProbe,
) -> CanonicalRccFinalizerDecision:
    """Choose a stop action without racing an RCC-internal hard-fail stop.

    The heartbeat is evidence, never authority: the caller must independently
    possess process-control authority.  This helper only binds that caller to
    the exact live RCC generation and prevents a second WM_CLOSE request while
    dependency-ordered shutdown is already in progress.
    """

    identity = verify_rcc_heartbeat_process_identity(
        heartbeat,
        identity_probe=identity_probe,
    )
    shutdown = heartbeat.get("shutdown")
    if not isinstance(shutdown, Mapping):
        raise CanaryMonitorHardFailure("rcc_finalizer:shutdown_state_missing")
    state = shutdown.get("state")
    if state not in {"running", "stopping", "stop_failed"}:
        raise CanaryMonitorHardFailure("rcc_finalizer:shutdown_state_invalid")
    reason_value = shutdown.get("reason_code")
    if reason_value is not None and not isinstance(reason_value, str):
        raise CanaryMonitorHardFailure("rcc_finalizer:shutdown_reason_invalid")
    reason_code = str(reason_value)[:80] if reason_value else None
    started_at = shutdown.get("started_at")
    if state == "running":
        if started_at is not None:
            raise CanaryMonitorHardFailure(
                "rcc_finalizer:running_shutdown_timestamp_present"
            )
        action = "request_graceful_stop"
    else:
        if isinstance(started_at, bool) or not isinstance(
            started_at, (int, float)
        ):
            raise CanaryMonitorHardFailure(
                "rcc_finalizer:shutdown_timestamp_missing"
            )
        timestamp = float(started_at)
        if not math.isfinite(timestamp) or timestamp <= 0:
            raise CanaryMonitorHardFailure(
                "rcc_finalizer:shutdown_timestamp_invalid"
            )
        action = (
            "wait_for_quiescence" if state == "stopping" else "fail_closed"
        )
    return CanonicalRccFinalizerDecision(
        action=action,
        shutdown_state=str(state),
        process_identity=identity,
        reason_code=reason_code,
    )


class CanonicalOwnerSafetyMonitor:
    """Track one canonical farm generation across startup and steady state."""

    def __init__(
        self,
        *,
        rows_reader: AuthorityRowsReader,
        identity_probe: IdentityProbe,
        startup_budget_seconds: float = 600.0,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if startup_budget_seconds <= 0:
            raise ValueError("positive owner startup budget is required")
        self._rows_reader = rows_reader
        self._identity_probe = identity_probe
        self._startup_budget_seconds = float(startup_budget_seconds)
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._started_at = float(monotonic())
        self._owner_id: str | None = None
        self._fences: dict[str, int] = {}
        self._identity: ProcessIdentity | None = None

    @property
    def ready(self) -> bool:
        return self._owner_id is not None

    def sample(self) -> CanonicalOwnerSafetySample:
        wall_now = float(self._wall_clock())
        active_rows = tuple(
            row
            for row in self._rows_reader()
            if self._is_active_row(row, now=wall_now)
        )
        assessment = assess_canonical_farm_authority(
            active_rows,
            identity_probe=self._identity_probe,
            now=wall_now,
            prior_canonical_owner_id=self._owner_id,
            prior_fences=self._fences if self._owner_id is not None else None,
        )
        if assessment.green:
            self._accept_initial_or_verify(assessment, active_rows)
            return CanonicalOwnerSafetySample(
                state="ready",
                ready=True,
                owner_id=assessment.canonical_owner_id,
                canonical_fence=assessment.canonical_fence,
                process_identity=assessment.process_identity,
                resources=assessment.resources,
            )

        if self._owner_id is None and not active_rows:
            elapsed = max(0.0, float(self._monotonic()) - self._started_at)
            if elapsed < self._startup_budget_seconds:
                return CanonicalOwnerSafetySample(
                    state="process_starting",
                    ready=False,
                    owner_id=None,
                    canonical_fence=None,
                    process_identity=None,
                    resources=(),
                )
            raise CanaryMonitorHardFailure("owner_startup_timeout")

        reason = ",".join(assessment.errors) or "owner_authority_unknown"
        raise CanaryMonitorHardFailure(f"owner_authority:{reason}")

    @staticmethod
    def _is_active_row(row: Mapping[str, Any], *, now: float) -> bool:
        try:
            return bool(row["owner_id"]) and float(row["lease_expires_at"]) > now
        except (KeyError, TypeError, ValueError):
            # Corrupt rows must reach the canonical assessment and fail closed.
            return True

    def _accept_initial_or_verify(
        self,
        assessment: CanonicalAuthorityAssessment,
        rows: Sequence[Mapping[str, Any]],
    ) -> None:
        owner_id = assessment.canonical_owner_id
        if owner_id is None:
            raise CanaryMonitorHardFailure("owner_authority:canonical_owner_missing")
        current_fences = {
            str(row["resource_id"]): int(row["next_fence"])
            for row in rows
        }
        if self._owner_id is None:
            self._owner_id = owner_id
            self._fences = current_fences
            self._identity = assessment.process_identity
            return
        if assessment.process_identity != self._identity:
            raise CanaryMonitorHardFailure(
                "owner_authority:process_generation_changed"
            )
        for resource_id, fence in current_fences.items():
            self._fences[resource_id] = max(
                fence,
                self._fences.get(resource_id, fence),
            )
