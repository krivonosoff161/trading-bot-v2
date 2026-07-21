from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class StartupState(str, Enum):
    """Observable lifecycle states used before and after the RCC readiness gate."""

    NOT_STARTED = "not_started"
    PROCESS_STARTING = "process_starting"
    LISTENER_STARTING = "listener_starting"
    MODEL_LOADING = "model_loading"
    PROVIDER_WAITING = "provider_waiting"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    STOPPED = "stopped"


STARTING_STATES = frozenset(
    {
        StartupState.NOT_STARTED,
        StartupState.PROCESS_STARTING,
        StartupState.LISTENER_STARTING,
        StartupState.MODEL_LOADING,
        StartupState.PROVIDER_WAITING,
    }
)


@dataclass(frozen=True)
class DependencySpec:
    name: str
    required_for_rcc_start: bool
    required_for_t0: bool
    optional_after_t0: bool
    locality: str
    starting_state: StartupState
    cold_timeout_seconds: float
    warm_timeout_seconds: float
    max_no_progress_seconds: float
    degraded_behavior: str
    hard_fail_condition: str

    def timeout_for(self, *, cold_start: bool) -> float:
        return self.cold_timeout_seconds if cold_start else self.warm_timeout_seconds


@dataclass(frozen=True)
class DependencyObservation:
    """One monitor observation; milestone must denote completed real work."""

    state: StartupState
    milestone: str | None = None
    milestone_completed: bool = False
    hard_failure: str | None = None


@dataclass
class DependencyStatus:
    state: StartupState = StartupState.NOT_STARTED
    stage_started_at: float | None = None
    last_progress_at: float | None = None
    deadline: float | None = None
    last_milestone: str | None = None
    outcome_reason: str | None = None


@dataclass(frozen=True)
class StartupAssessment:
    state: StartupState
    ready_for_t0: bool
    t0_monotonic: float | None
    failure_reason: str | None
    alert_count: int
    stop_requested: bool
    dependencies: Mapping[str, DependencyStatus]


def _spec(
    name: str,
    *,
    required_for_t0: bool,
    locality: str,
    starting_state: StartupState,
    cold: float,
    warm: float,
    no_progress: float,
    degraded: str,
    hard_fail: str,
) -> DependencySpec:
    return DependencySpec(
        name=name,
        required_for_rcc_start=required_for_t0,
        required_for_t0=required_for_t0,
        optional_after_t0=not required_for_t0,
        locality=locality,
        starting_state=starting_state,
        cold_timeout_seconds=cold,
        warm_timeout_seconds=warm,
        max_no_progress_seconds=no_progress,
        degraded_behavior=degraded,
        hard_fail_condition=hard_fail,
    )


CANONICAL_RCC_DEPENDENCIES: tuple[DependencySpec, ...] = (
    _spec(
        "rcc_process",
        required_for_t0=True,
        locality="local",
        starting_state=StartupState.PROCESS_STARTING,
        cold=30,
        warm=15,
        no_progress=30,
        degraded="none; RCC is mandatory",
        hard_fail="exit, executable/path/PID/generation mismatch",
    ),
    _spec(
        "ollama_root",
        required_for_t0=True,
        locality="local",
        starting_state=StartupState.PROCESS_STARTING,
        cold=90,
        warm=30,
        no_progress=90,
        degraded="none; canonical root is mandatory",
        hard_fail="foreign executable, parent or process generation",
    ),
    _spec(
        "ollama_runner",
        required_for_t0=False,
        locality="local",
        starting_state=StartupState.MODEL_LOADING,
        cold=420,
        warm=120,
        no_progress=180,
        degraded="lazy model runner remains unavailable",
        hard_fail="runner listener is not an exact canonical Ollama descendant",
    ),
    _spec(
        "ollama_listener",
        required_for_t0=True,
        locality="local",
        starting_state=StartupState.LISTENER_STARTING,
        cold=180,
        warm=60,
        no_progress=180,
        degraded="none; loopback public API is mandatory",
        hard_fail="foreign listener or wrong bind/identity",
    ),
    _spec(
        "local_model",
        required_for_t0=False,
        locality="local",
        starting_state=StartupState.MODEL_LOADING,
        cold=480,
        warm=180,
        no_progress=180,
        degraded="paper contours continue without local-model advice",
        hard_fail="model process violates canonical ancestry or listener policy",
    ),
    _spec(
        "first_local_inference",
        required_for_t0=False,
        locality="local",
        starting_state=StartupState.MODEL_LOADING,
        cold=540,
        warm=240,
        no_progress=180,
        degraded="first advisory inference remains pending",
        hard_fail="inference attempts private or execution authority",
    ),
    *(
        _spec(
            name,
            required_for_t0=True,
            locality="local",
            starting_state=StartupState.PROCESS_STARTING,
            cold=90,
            warm=30,
            no_progress=90,
            degraded=f"none; {name} process is mandatory",
            hard_fail="process exit or executable/path/PID/generation mismatch",
        )
        for name in ("public_news", "scanner", "paper_cards", "telegram_bot")
    ),
    _spec(
        "cloud_public_providers",
        required_for_t0=False,
        locality="cloud/public",
        starting_state=StartupState.PROVIDER_WAITING,
        cold=120,
        warm=60,
        no_progress=60,
        degraded="record provider degradation and retry safe readiness probes",
        hard_fail="private endpoint or execution-authority attempt",
    ),
    _spec(
        "farm_owner",
        required_for_t0=True,
        locality="local",
        starting_state=StartupState.PROCESS_STARTING,
        cold=120,
        warm=60,
        no_progress=120,
        degraded="none; one fenced canonical process authority is mandatory",
        hard_fail="foreign/second PID, generation/fence mismatch or writer authority",
    ),
    _spec(
        "canonical_safety_gate",
        required_for_t0=True,
        locality="local",
        starting_state=StartupState.PROCESS_STARTING,
        cold=180,
        warm=60,
        no_progress=180,
        degraded="none; DB, fencing and paper-only boundary are mandatory",
        hard_fail="integrity, fence, duplicate, stop, execution or private-endpoint drift",
    ),
)


class CanonicalStartupReadinessMonitor:
    """Deterministic dependency-aware readiness gate driven by monotonic time.

    The monitor never probes processes, ports, databases or providers itself.  A
    private operational adapter supplies verified observations.  This keeps the
    policy logic testable and prevents a missing-yet-not-due dependency from
    being confused with a proved identity or authority violation.
    """

    def __init__(
        self,
        *,
        started_at: float,
        cold_start: bool,
        dependencies: tuple[DependencySpec, ...] = CANONICAL_RCC_DEPENDENCIES,
        total_budget_seconds: float = 600.0,
    ) -> None:
        if total_budget_seconds <= 0 or total_budget_seconds > 600:
            raise ValueError("total startup budget must be within (0, 600] seconds")
        names = [item.name for item in dependencies]
        if len(names) != len(set(names)):
            raise ValueError("dependency names must be unique")
        self.started_at = float(started_at)
        self.cold_start = bool(cold_start)
        self.total_deadline = self.started_at + float(total_budget_seconds)
        self.specs = {item.name: item for item in dependencies}
        self.statuses = {item.name: DependencyStatus() for item in dependencies}
        self.failure_reason: str | None = None
        self.failed_at: float | None = None
        self.t0_monotonic: float | None = None
        self.alert_count = 0
        self.stop_requested = False
        self.stopped_at: float | None = None
        self._last_now = self.started_at

    def _check_time(self, now: float) -> float:
        value = float(now)
        if value < self._last_now:
            raise ValueError("monotonic time moved backwards")
        self._last_now = value
        return value

    def observe(
        self,
        name: str,
        observation: DependencyObservation,
        *,
        now: float,
    ) -> StartupAssessment:
        current = self._check_time(now)
        if name not in self.specs:
            raise KeyError(name)
        if self.failure_reason or self.stop_requested:
            return self.assess(current)
        if observation.hard_failure:
            return self.fail(f"{name}:{observation.hard_failure}", now=current)

        spec = self.specs[name]
        status = self.statuses[name]
        next_state = observation.state
        if next_state is StartupState.FAILED:
            return self.fail(f"{name}:failed_without_reason", now=current)
        if next_state is StartupState.STOPPED:
            return self.stop(now=current)

        if status.stage_started_at is None or next_state is not status.state:
            status.state = next_state
            status.stage_started_at = current
            status.last_progress_at = current
            status.deadline = min(
                current + spec.timeout_for(cold_start=self.cold_start),
                self.total_deadline,
            )
            status.outcome_reason = None

        if observation.milestone_completed:
            if not observation.milestone:
                raise ValueError("completed progress requires a named milestone")
            if observation.milestone != status.last_milestone:
                status.last_milestone = observation.milestone
                status.last_progress_at = current
        elif observation.milestone and observation.milestone != status.last_milestone:
            raise ValueError("a milestone may advance only when completed")

        if next_state is StartupState.READY:
            status.outcome_reason = None
        elif next_state is StartupState.DEGRADED:
            if spec.required_for_t0:
                return self.fail(f"{name}:mandatory_dependency_degraded", now=current)
            status.outcome_reason = spec.degraded_behavior
        return self.assess(current)

    def _expire(self, name: str, *, now: float, reason: str) -> None:
        spec = self.specs[name]
        status = self.statuses[name]
        if spec.required_for_t0:
            self.fail(f"{name}:startup_timeout:{reason}", now=now)
        else:
            status.state = StartupState.DEGRADED
            status.outcome_reason = f"{spec.degraded_behavior}; {reason}"

    def assess(self, now: float) -> StartupAssessment:
        current = self._check_time(now)
        if not self.failure_reason and not self.stop_requested and self.t0_monotonic is None:
            for name, status in self.statuses.items():
                if status.state not in STARTING_STATES:
                    continue
                if current >= self.total_deadline:
                    self._expire(name, now=current, reason="total_budget_exhausted")
                    if self.failure_reason:
                        break
                    continue
                if status.deadline is not None and current >= status.deadline:
                    self._expire(name, now=current, reason="stage_deadline_exhausted")
                    if self.failure_reason:
                        break
                    continue
                last_progress = status.last_progress_at
                if (
                    last_progress is not None
                    and current - last_progress >= self.specs[name].max_no_progress_seconds
                ):
                    self._expire(name, now=current, reason="no_real_progress")
                    if self.failure_reason:
                        break

        ready = bool(
            not self.failure_reason
            and not self.stop_requested
            and all(
                not spec.required_for_t0
                or self.statuses[name].state is StartupState.READY
                for name, spec in self.specs.items()
            )
        )
        if self.stop_requested:
            overall = StartupState.STOPPED
        elif self.failure_reason:
            overall = StartupState.FAILED
        elif ready:
            overall = StartupState.READY
        elif any(item.state is StartupState.DEGRADED for item in self.statuses.values()):
            overall = StartupState.DEGRADED
        else:
            overall = StartupState.PROCESS_STARTING
        snapshot = {
            name: DependencyStatus(**vars(status))
            for name, status in self.statuses.items()
        }
        return StartupAssessment(
            state=overall,
            ready_for_t0=ready,
            t0_monotonic=self.t0_monotonic,
            failure_reason=self.failure_reason,
            alert_count=self.alert_count,
            stop_requested=self.stop_requested,
            dependencies=MappingProxyType(snapshot),
        )

    def establish_t0(self, *, now: float) -> bool:
        current = self._check_time(now)
        if self.t0_monotonic is not None:
            return True
        if not self.assess(current).ready_for_t0:
            return False
        self.t0_monotonic = current
        return True

    def fail(self, reason: str, *, now: float) -> StartupAssessment:
        current = self._check_time(now)
        if not self.failure_reason:
            self.failure_reason = reason
            self.failed_at = current
            self.alert_count = 1
        return self.assess(current)

    def stop(self, *, now: float) -> StartupAssessment:
        current = self._check_time(now)
        if not self.stop_requested:
            self.stop_requested = True
            self.stopped_at = current
            for status in self.statuses.values():
                if status.state is not StartupState.FAILED:
                    status.state = StartupState.STOPPED
        return self.assess(current)
