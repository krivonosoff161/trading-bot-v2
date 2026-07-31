from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
import time
from types import MappingProxyType
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


class CanaryMonitorHardFailure(RuntimeError):
    """Latched fail-closed monitoring failure that adapters must escalate."""


@dataclass(frozen=True)
class CanaryLaneSample:
    lane: str
    state: str
    payload: Mapping[str, object]
    error_type: str | None
    watchdog: CanaryWatchdogAssessment


@dataclass(frozen=True)
class CanaryMonitoringLaneSpec:
    name: str
    max_sample_gap_seconds: float
    permits_database_snapshot: bool


CANONICAL_MONITORING_LANES: tuple[CanaryMonitoringLaneSpec, ...] = (
    CanaryMonitoringLaneSpec(
        name="fast_safety",
        max_sample_gap_seconds=15.0,
        permits_database_snapshot=False,
    ),
    CanaryMonitoringLaneSpec(
        name="deep_database",
        max_sample_gap_seconds=300.0,
        permits_database_snapshot=True,
    ),
)


def build_monitoring_lane_watchdogs(
    *, started_at: float
) -> Mapping[str, "CanaryFastSampleWatchdog"]:
    """Build independent freshness clocks for fast and deep monitor lanes."""

    return MappingProxyType(
        {
            spec.name: CanaryFastSampleWatchdog(
                started_at=started_at,
                max_fast_sample_gap_seconds=spec.max_sample_gap_seconds,
            )
            for spec in CANONICAL_MONITORING_LANES
        }
    )


class CanaryMonitoringCoordinator:
    """Keep fast safety and deep database probes on independent clocks.

    A single probe exception is observable but does not fabricate a completed
    sample. The lane fails only when its monotonic freshness budget is
    exhausted. A slow/degraded deep database probe therefore cannot suppress
    or invalidate successful process/authority samples.
    """

    def __init__(self, *, started_at: float) -> None:
        self.watchdogs = build_monitoring_lane_watchdogs(started_at=started_at)

    def sample(
        self,
        lane: str,
        probe: Callable[[], Mapping[str, object]],
        *,
        now: float,
    ) -> CanaryLaneSample:
        try:
            payload = dict(probe())
        except Exception as exc:  # safe metadata only; never persist the value
            return self.record_error(lane, type(exc).__name__, now=now)
        return self.record_success(lane, payload, now=now)

    def record_success(
        self,
        lane: str,
        payload: Mapping[str, object],
        *,
        now: float,
    ) -> CanaryLaneSample:
        assessment = self.watchdogs[lane].record_fast_sample(now=now)
        return CanaryLaneSample(
            lane=lane,
            state="failed" if assessment.failure_reason is not None else "healthy",
            payload=MappingProxyType(payload),
            error_type=None,
            watchdog=assessment,
        )

    def record_error(
        self,
        lane: str,
        error_type: str,
        *,
        now: float,
    ) -> CanaryLaneSample:
        assessment = self.watchdogs[lane].assess(now=now)
        return CanaryLaneSample(
            lane=lane,
            state="failed" if assessment.failure_reason is not None else "degraded",
            payload=MappingProxyType({}),
            error_type=str(error_type)[:120],
            watchdog=assessment,
        )

    def require_lane(self, lane: str, *, now: float) -> None:
        require_healthy_watchdog(self.watchdogs[lane].assess(now=now))


class CanonicalCanaryRuntimeWatchdog:
    """Validate complete steady-state samples without inventing failed fields.

    Operational evidence adapters sample more slowly than the RCC's internal
    five-second safety lane.  A bounded listener/process inventory can fail
    transiently on Windows, so an ordinary probe exception is evidence that
    the sample is unavailable, not evidence that the lease supervisor is
    missing.  Explicit safety violations and invalid *complete* samples still
    fail immediately.
    """

    lane = "external_runtime"

    def __init__(
        self,
        *,
        started_at: float,
        baseline_owner_hash: str,
        baseline_fence: int,
        max_probe_gap_seconds: float = 90.0,
    ) -> None:
        if not baseline_owner_hash:
            raise ValueError("baseline owner hash is required")
        if baseline_fence <= 0:
            raise ValueError("baseline fence must be positive")
        self.baseline_owner_hash = str(baseline_owner_hash)
        self.baseline_fence = int(baseline_fence)
        self.watchdog = CanaryFastSampleWatchdog(
            started_at=started_at,
            max_fast_sample_gap_seconds=max_probe_gap_seconds,
        )

    def sample(
        self,
        probe: Callable[[], Mapping[str, object]],
        *,
        now: float,
    ) -> CanaryLaneSample:
        try:
            payload = dict(probe())
        except CanaryMonitorHardFailure as exc:
            return self._hard_failure(
                str(exc) or "runtime_probe_hard_failure",
                type(exc).__name__,
                now=now,
            )
        except Exception as exc:  # safe type only; never persist exception values
            assessment = self.watchdog.assess(now=now)
            return CanaryLaneSample(
                lane=self.lane,
                state=(
                    "failed"
                    if assessment.failure_reason is not None
                    else "degraded"
                ),
                payload=MappingProxyType({}),
                error_type=type(exc).__name__,
                watchdog=assessment,
            )
        try:
            self._validate_complete_sample(payload)
        except CanaryMonitorHardFailure as exc:
            return self._hard_failure(
                str(exc) or "runtime_sample_hard_failure",
                type(exc).__name__,
                now=now,
            )
        except (TypeError, ValueError):
            return self._hard_failure(
                "runtime_sample_invalid",
                "RuntimeSampleValidationError",
                now=now,
            )
        assessment = self.watchdog.record_fast_sample(now=now)
        return CanaryLaneSample(
            lane=self.lane,
            state="failed" if assessment.failure_reason is not None else "healthy",
            payload=MappingProxyType(payload),
            error_type=None,
            watchdog=assessment,
        )

    def _hard_failure(
        self,
        reason: str,
        error_type: str,
        *,
        now: float,
    ) -> CanaryLaneSample:
        assessment = self.watchdog.fail(str(reason)[:160], now=now)
        return CanaryLaneSample(
            lane=self.lane,
            state="failed",
            payload=MappingProxyType({}),
            error_type=str(error_type)[:120],
            watchdog=assessment,
        )

    def _validate_complete_sample(self, payload: Mapping[str, object]) -> None:
        reasons = payload.get("hard_fail_reasons")
        if isinstance(reasons, (list, tuple)) and reasons:
            raise CanaryMonitorHardFailure(
                "runtime:" + str(reasons[0])[:140]
            )
        if payload.get("ready") is not True:
            raise CanaryMonitorHardFailure("canonical_runtime_not_ready")

        owner = payload.get("owner")
        if not isinstance(owner, Mapping):
            raise CanaryMonitorHardFailure("canonical_owner_unavailable")
        if owner.get("green") is not True:
            raise CanaryMonitorHardFailure("canonical_owner_not_ready")
        if int(owner.get("distinct_process_authorities") or 0) != 1:
            raise CanaryMonitorHardFailure("canonical_owner_cardinality_changed")
        if owner.get("process_bound_to_rcc") is not True:
            raise CanaryMonitorHardFailure("canonical_owner_left_rcc_tree")
        if str(owner.get("owner_hash") or "") != self.baseline_owner_hash:
            raise CanaryMonitorHardFailure("canonical_owner_identity_changed")
        if int(owner.get("fence") or 0) != self.baseline_fence:
            raise CanaryMonitorHardFailure("canonical_owner_fence_changed")

        supervisor = payload.get("process_lease_supervisor")
        if not isinstance(supervisor, Mapping) or supervisor.get("ready") is not True:
            raise CanaryMonitorHardFailure("process_lease_supervisor_not_ready")
        if supervisor.get("state") != "running":
            raise CanaryMonitorHardFailure("process_lease_supervisor_not_running")
        if supervisor.get("fresh_generation") is not True:
            raise CanaryMonitorHardFailure(
                "process_lease_supervisor_generation_changed"
            )
        if supervisor.get("paper_only") is not True:
            raise CanaryMonitorHardFailure(
                "process_lease_supervisor_paper_only_drift"
            )
        if supervisor.get("execution_allowed") is not False:
            raise CanaryMonitorHardFailure(
                "process_lease_supervisor_execution_authority_drift"
            )
        if supervisor.get("identity_matches") is not True:
            raise CanaryMonitorHardFailure(
                "process_lease_supervisor_identity_changed"
            )
        if supervisor.get("fence_matches") is not True:
            raise CanaryMonitorHardFailure("process_lease_supervisor_fence_changed")


LaneSampleCallback = Callable[[CanaryLaneSample], None]
LaneFailureCallback = Callable[[str, CanaryWatchdogAssessment], None]


class CanaryMonitoringService:
    """Run fast and deep canary probes on independent supervised threads.

    The service never performs an operational stop itself.  It reports one
    latched failure to its adapter, which must invoke the documented RCC
    graceful-stop path.  A blocked deep SQLite probe therefore cannot suppress
    fast process/authority observations or hide its own freshness expiry.
    """

    def __init__(
        self,
        *,
        fast_probe: Callable[[], Mapping[str, object]],
        deep_probe: Callable[[], Mapping[str, object]],
        on_sample: LaneSampleCallback,
        on_failure: LaneFailureCallback,
        started_at: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        fast_interval_seconds: float = 5.0,
        deep_interval_seconds: float = 60.0,
        supervisor_interval_seconds: float = 0.25,
    ) -> None:
        if min(
            fast_interval_seconds,
            deep_interval_seconds,
            supervisor_interval_seconds,
        ) <= 0:
            raise ValueError("monitoring intervals must be positive")
        self._monotonic = monotonic
        initial = float(monotonic() if started_at is None else started_at)
        self.coordinator = CanaryMonitoringCoordinator(started_at=initial)
        self._probes = {
            "fast_safety": fast_probe,
            "deep_database": deep_probe,
        }
        self._intervals = {
            "fast_safety": float(fast_interval_seconds),
            "deep_database": float(deep_interval_seconds),
        }
        self._supervisor_interval = float(supervisor_interval_seconds)
        self._on_sample = on_sample
        self._on_failure = on_failure
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._failure_lane: str | None = None
        self._threads = {
            lane: threading.Thread(
                target=self._run_lane,
                args=(lane,),
                name=f"canary-monitor-{lane}",
                daemon=True,
            )
            for lane in self._probes
        }
        self._supervisor = threading.Thread(
            target=self._run_supervisor,
            name="canary-monitor-supervisor",
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            raise RuntimeError("canary monitoring service already started")
        self._started = True
        for thread in self._threads.values():
            thread.start()
        self._supervisor.start()

    def stop(self, *, timeout: float = 2.0) -> tuple[str, ...]:
        """Stop bounded monitor control threads and report any blocked probe lane."""

        self._stop.set()
        deadline = time.monotonic() + max(0.0, float(timeout))
        for thread in (*self._threads.values(), self._supervisor):
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        return tuple(
            lane
            for lane, thread in self._threads.items()
            if thread.is_alive()
        )

    def _run_lane(self, lane: str) -> None:
        probe = self._probes[lane]
        interval = self._intervals[lane]
        while not self._stop.is_set():
            try:
                payload = dict(probe())
            except CanaryMonitorHardFailure as exc:
                now = float(self._monotonic())
                with self._state_lock:
                    prior = self.coordinator.watchdogs[lane].assess(now=now)
                    assessment = CanaryWatchdogAssessment(
                        failure_reason=str(exc)[:160] or "probe_hard_failure",
                        alert_count=prior.alert_count + 1,
                        last_fast_sample_at=prior.last_fast_sample_at,
                        fast_sample_age_seconds=prior.fast_sample_age_seconds,
                    )
                    sample = CanaryLaneSample(
                        lane=lane,
                        state="failed",
                        payload=MappingProxyType({}),
                        error_type=type(exc).__name__,
                        watchdog=assessment,
                    )
                self._on_sample(sample)
                self._fail_once(lane, assessment)
                return
            except Exception as exc:
                now = float(self._monotonic())
                with self._state_lock:
                    sample = self.coordinator.record_error(
                        lane,
                        type(exc).__name__,
                        now=now,
                    )
            else:
                now = float(self._monotonic())
                with self._state_lock:
                    sample = self.coordinator.record_success(
                        lane,
                        payload,
                        now=now,
                    )
            self._on_sample(sample)
            if sample.watchdog.failure_reason is not None:
                self._fail_once(lane, sample.watchdog)
                return
            if self._stop.wait(interval):
                return

    def _run_supervisor(self) -> None:
        while not self._stop.wait(self._supervisor_interval):
            now = float(self._monotonic())
            with self._state_lock:
                assessments = {
                    lane: watchdog.assess(now=now)
                    for lane, watchdog in self.coordinator.watchdogs.items()
                }
            for lane, assessment in assessments.items():
                if assessment.failure_reason is not None:
                    self._fail_once(lane, assessment)

    def _fail_once(
        self,
        lane: str,
        assessment: CanaryWatchdogAssessment,
    ) -> None:
        with self._state_lock:
            if self._failure_lane is not None:
                return
            self._failure_lane = lane
        self._stop.set()
        self._on_failure(lane, assessment)


def require_healthy_watchdog(assessment: CanaryWatchdogAssessment) -> None:
    """Raise immediately when a watchdog assessment contains a latched failure."""

    if assessment.failure_reason is not None:
        raise CanaryMonitorHardFailure(assessment.failure_reason)


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
        max_fast_sample_gap_seconds: float = 15.0,
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
        self._evaluate_freshness(current)
        if self.failure_reason is None:
            self.last_fast_sample_at = current
        return self._snapshot(current)

    def fail(self, reason: str, *, now: float) -> CanaryWatchdogAssessment:
        current = self._check_time(now)
        if self.failure_reason is None:
            self.failure_reason = reason
            self.alert_count = 1
        return self.assess(now=current)

    def assess(self, *, now: float) -> CanaryWatchdogAssessment:
        current = self._check_time(now)
        self._evaluate_freshness(current)
        return self._snapshot(current)

    def _evaluate_freshness(self, current: float) -> None:
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

    def _snapshot(self, current: float) -> CanaryWatchdogAssessment:
        basis = self.last_fast_sample_at
        age = current - (basis if basis is not None else self.started_at)
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
