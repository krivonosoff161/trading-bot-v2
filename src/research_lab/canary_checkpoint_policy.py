from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping


class IntegrityEvidenceMode(str, Enum):
    """Database evidence permitted at a canary checkpoint."""

    FULL_PRE_T0 = "full_pre_t0"
    BOUNDED_HEALTH = "bounded_health"
    FULL_QUIESCENT = "full_quiescent"


@dataclass(frozen=True)
class CanaryCheckpointSpec:
    name: str
    due_seconds: float
    integrity_mode: IntegrityEvidenceMode


ACTIVE_CHECKPOINT_SECONDS: tuple[tuple[str, float], ...] = (
    ("2m", 120.0),
    ("5m", 300.0),
    ("15m", 900.0),
    ("30m", 1_800.0),
    ("60m", 3_600.0),
    ("90m", 5_400.0),
    ("120m", 7_200.0),
    ("150m", 9_000.0),
    ("180m", 10_800.0),
    ("210m", 12_600.0),
    ("240m", 14_400.0),
    ("270m", 16_200.0),
    ("300m", 18_000.0),
)


CANONICAL_CANARY_CHECKPOINTS: tuple[CanaryCheckpointSpec, ...] = (
    CanaryCheckpointSpec("immediate", 0.0, IntegrityEvidenceMode.FULL_PRE_T0),
    *(
        CanaryCheckpointSpec(name, due, IntegrityEvidenceMode.BOUNDED_HEALTH)
        for name, due in ACTIVE_CHECKPOINT_SECONDS
    ),
)

FINAL_QUIESCENT_CHECKPOINT = CanaryCheckpointSpec(
    "final_quiescent",
    18_000.0,
    IntegrityEvidenceMode.FULL_QUIESCENT,
)


@dataclass(frozen=True)
class CanaryWatchdogAssessment:
    failure_reason: str | None
    alert_count: int
    last_fast_sample_at: float | None
    fast_sample_age_seconds: float | None


class CanaryFastSampleWatchdog:
    """Fail-closed monotonic freshness policy independent of checkpoint work.

    Operational adapters must call :meth:`record_fast_sample` only after a
    complete bounded sample.  Active-run checkpoint collection is deliberately
    excluded from this class so it cannot postpone the freshness decision.
    """

    def __init__(
        self,
        *,
        started_at: float,
        max_fast_sample_gap_seconds: float = 45.0,
    ) -> None:
        if max_fast_sample_gap_seconds <= 0:
            raise ValueError("fast-sample gap must be positive")
        self.started_at = float(started_at)
        self.max_fast_sample_gap_seconds = float(max_fast_sample_gap_seconds)
        self.last_fast_sample_at: float | None = None
        self.failure_reason: str | None = None
        self.alert_count = 0
        self._last_now = self.started_at

    def _check_time(self, now: float) -> float:
        value = float(now)
        if value < self._last_now:
            raise ValueError("monotonic time moved backwards")
        self._last_now = value
        return value

    def record_fast_sample(self, *, now: float) -> CanaryWatchdogAssessment:
        current = self._check_time(now)
        if self.failure_reason is None:
            self.last_fast_sample_at = current
        return self.assess(now=current)

    def fail(self, reason: str, *, now: float) -> CanaryWatchdogAssessment:
        current = self._check_time(now)
        if self.failure_reason is None:
            self.failure_reason = reason
            self.alert_count = 1
        return self.assess(now=current)

    def assess(self, *, now: float) -> CanaryWatchdogAssessment:
        current = self._check_time(now)
        basis = self.last_fast_sample_at
        age = current - (basis if basis is not None else self.started_at)
        if self.failure_reason is None and age > self.max_fast_sample_gap_seconds:
            reason = (
                "monitor_fast_sample_initial_deadline_exhausted"
                if basis is None
                else "monitor_fast_sample_freshness_lost"
            )
            self.failure_reason = reason
            self.alert_count = 1
        return CanaryWatchdogAssessment(
            failure_reason=self.failure_reason,
            alert_count=self.alert_count,
            last_fast_sample_at=self.last_fast_sample_at,
            fast_sample_age_seconds=age,
        )


def due_active_checkpoints(
    *,
    elapsed_seconds: float,
    completed: set[str] | frozenset[str],
) -> tuple[CanaryCheckpointSpec, ...]:
    """Return active checkpoints due at a monotonic elapsed time."""

    if elapsed_seconds < 0:
        raise ValueError("elapsed time must not be negative")
    return tuple(
        spec
        for spec in CANONICAL_CANARY_CHECKPOINTS
        if spec.name != "immediate"
        and spec.name not in completed
        and elapsed_seconds >= spec.due_seconds
    )


def collect_checkpoint_integrity_evidence(
    spec: CanaryCheckpointSpec,
    *,
    bounded_health_probe: Callable[[], Mapping[str, object]],
    full_integrity_probe: Callable[[], Mapping[str, object]],
) -> Mapping[str, object]:
    """Dispatch only the integrity evidence allowed for a checkpoint.

    A potentially long full ``PRAGMA integrity_check`` is never called while
    the five-hour timer is active.  Active checkpoints consume already-bounded
    read-only health signals instead.  Full scans remain mandatory before T+0
    and after canonical processes, owners and ports are quiescent.
    """

    if spec.integrity_mode is IntegrityEvidenceMode.BOUNDED_HEALTH:
        return bounded_health_probe()
    return full_integrity_probe()
