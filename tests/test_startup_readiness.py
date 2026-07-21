from __future__ import annotations

import pytest

from src.research_lab.startup_readiness import (
    CANONICAL_RCC_DEPENDENCIES,
    CanonicalStartupReadinessMonitor,
    DependencyObservation,
    DependencySpec,
    StartupState,
)


def spec(
    name: str,
    *,
    required: bool = True,
    state: StartupState = StartupState.PROCESS_STARTING,
    cold: float = 10.0,
    warm: float = 4.0,
    no_progress: float = 5.0,
) -> DependencySpec:
    return DependencySpec(
        name=name,
        required_for_rcc_start=required,
        required_for_t0=required,
        optional_after_t0=not required,
        locality="local" if state is not StartupState.PROVIDER_WAITING else "cloud/public",
        starting_state=state,
        cold_timeout_seconds=cold,
        warm_timeout_seconds=warm,
        max_no_progress_seconds=no_progress,
        degraded_behavior=f"{name} degraded",
        hard_fail_condition=f"{name} identity violation",
    )


def observation(
    state: StartupState,
    milestone: str | None = None,
    *,
    completed: bool = False,
    hard_failure: str | None = None,
) -> DependencyObservation:
    return DependencyObservation(
        state=state,
        milestone=milestone,
        milestone_completed=completed,
        hard_failure=hard_failure,
    )


def monitor(*items: DependencySpec, cold: bool = True, budget: float = 20.0):
    return CanonicalStartupReadinessMonitor(
        started_at=100.0,
        cold_start=cold,
        dependencies=tuple(items),
        total_budget_seconds=budget,
    )


def test_early_3_76_second_missing_listener_is_starting_without_alert() -> None:
    gate = monitor(
        spec("rcc_process", cold=10, no_progress=10),
        spec("ollama_root", cold=10, no_progress=10),
        spec("ollama_listener", state=StartupState.LISTENER_STARTING, cold=10, no_progress=10),
    )
    gate.observe("rcc_process", observation(StartupState.READY), now=101.0)
    gate.observe("ollama_root", observation(StartupState.PROCESS_STARTING), now=103.76)
    result = gate.observe(
        "ollama_listener",
        observation(StartupState.LISTENER_STARTING),
        now=103.76,
    )

    assert result.state is StartupState.PROCESS_STARTING
    assert result.failure_reason is None
    assert result.alert_count == 0


def test_listener_and_heartbeat_before_deadline_establish_t0() -> None:
    gate = monitor(spec("listener", state=StartupState.LISTENER_STARTING))
    gate.observe("listener", observation(StartupState.LISTENER_STARTING), now=103.76)
    result = gate.observe("listener", observation(StartupState.READY), now=108.0)

    assert result.ready_for_t0
    assert gate.establish_t0(now=108.1)
    assert gate.assess(108.1).t0_monotonic == 108.1


def test_slow_hdd_real_completed_chunks_prevent_no_progress_failure() -> None:
    gate = monitor(spec("model", state=StartupState.MODEL_LOADING, cold=18, no_progress=5))
    gate.observe("model", observation(StartupState.MODEL_LOADING, "weights-1", completed=True), now=101)
    gate.observe("model", observation(StartupState.MODEL_LOADING, "weights-2", completed=True), now=105)
    gate.observe("model", observation(StartupState.MODEL_LOADING, "weights-3", completed=True), now=109)

    assert gate.assess(113).failure_reason is None


def test_listener_ready_while_model_is_loading() -> None:
    gate = monitor(
        spec("listener", state=StartupState.LISTENER_STARTING),
        spec("model", required=False, state=StartupState.MODEL_LOADING),
    )
    gate.observe("listener", observation(StartupState.READY), now=101)
    result = gate.observe("model", observation(StartupState.MODEL_LOADING), now=101)

    assert result.dependencies["model"].state is StartupState.MODEL_LOADING
    assert result.ready_for_t0


def test_warm_and_cold_start_use_different_bounded_deadlines() -> None:
    dependency = spec("listener", cold=10, warm=4, no_progress=20)
    cold = monitor(dependency, cold=True)
    warm = monitor(dependency, cold=False)
    cold.observe("listener", observation(StartupState.LISTENER_STARTING), now=100)
    warm.observe("listener", observation(StartupState.LISTENER_STARTING), now=100)

    assert cold.assess(105).failure_reason is None
    assert warm.assess(105).failure_reason == "listener:startup_timeout:stage_deadline_exhausted"


def test_cloud_provider_waiting_is_not_immediate_failure() -> None:
    gate = monitor(spec("provider", required=False, state=StartupState.PROVIDER_WAITING))
    result = gate.observe("provider", observation(StartupState.PROVIDER_WAITING), now=103)

    assert result.failure_reason is None
    assert result.dependencies["provider"].state is StartupState.PROVIDER_WAITING


def test_optional_provider_degrades_without_blocking_t0() -> None:
    gate = monitor(
        spec("rcc"),
        spec("provider", required=False, state=StartupState.PROVIDER_WAITING, cold=4, no_progress=4),
    )
    gate.observe("rcc", observation(StartupState.READY), now=101)
    gate.observe("provider", observation(StartupState.PROVIDER_WAITING), now=101)
    result = gate.assess(106)

    assert result.dependencies["provider"].state is StartupState.DEGRADED
    assert result.ready_for_t0
    assert result.failure_reason is None


def test_mandatory_provider_times_out_at_its_own_deadline() -> None:
    gate = monitor(spec("provider", state=StartupState.PROVIDER_WAITING, cold=4, no_progress=10))
    gate.observe("provider", observation(StartupState.PROVIDER_WAITING), now=100)

    assert gate.assess(104).failure_reason == "provider:startup_timeout:stage_deadline_exhausted"


def test_rcc_exit_before_readiness_is_immediate_hard_fail() -> None:
    gate = monitor(spec("rcc"))
    result = gate.observe(
        "rcc",
        observation(StartupState.PROCESS_STARTING, hard_failure="process_exited"),
        now=101,
    )

    assert result.state is StartupState.FAILED
    assert result.failure_reason == "rcc:process_exited"


@pytest.mark.parametrize(
    "reason",
    [
        "foreign_listener",
        "wrong_executable_path",
        "second_owner_pid",
        "generation_or_fence_mismatch",
        "execution_authority_drift",
        "private_endpoint_attempt",
    ],
)
def test_proved_identity_or_authority_violations_fail_immediately(reason: str) -> None:
    gate = monitor(spec("canonical"))
    result = gate.observe(
        "canonical",
        observation(StartupState.PROCESS_STARTING, hard_failure=reason),
        now=100.1,
    )

    assert result.failure_reason == f"canonical:{reason}"


def test_canonical_ollama_child_listener_can_be_ready() -> None:
    gate = monitor(spec("ollama_runner", required=False, state=StartupState.MODEL_LOADING))
    result = gate.observe("ollama_runner", observation(StartupState.READY), now=101)

    assert result.failure_reason is None
    assert result.dependencies["ollama_runner"].state is StartupState.READY


def test_same_pid_nested_owner_resources_form_one_ready_dependency() -> None:
    gate = monitor(spec("farm_owner"))
    result = gate.observe(
        "farm_owner",
        observation(StartupState.READY, "pid-4100-generation-7-fence-4", completed=True),
        now=101,
    )

    assert result.ready_for_t0
    assert result.dependencies["farm_owner"].last_milestone == "pid-4100-generation-7-fence-4"


def test_heartbeat_only_does_not_extend_real_progress_window() -> None:
    gate = monitor(spec("model", state=StartupState.MODEL_LOADING, cold=15, no_progress=5))
    gate.observe("model", observation(StartupState.MODEL_LOADING), now=100)
    gate.observe("model", observation(StartupState.MODEL_LOADING), now=103)

    assert gate.assess(105).failure_reason == "model:startup_timeout:no_real_progress"


def test_uncompleted_milestone_cannot_fake_progress() -> None:
    gate = monitor(spec("model", state=StartupState.MODEL_LOADING))

    with pytest.raises(ValueError, match="only when completed"):
        gate.observe("model", observation(StartupState.MODEL_LOADING, "timer-tick"), now=101)


def test_late_heartbeat_after_true_timeout_cannot_establish_t0() -> None:
    gate = monitor(spec("heartbeat", cold=4, no_progress=4))
    gate.observe("heartbeat", observation(StartupState.PROCESS_STARTING), now=100)
    assert gate.assess(104).state is StartupState.FAILED

    late = gate.observe("heartbeat", observation(StartupState.READY), now=105)
    assert late.state is StartupState.FAILED
    assert not gate.establish_t0(now=105)


def test_alert_is_idempotent() -> None:
    gate = monitor(spec("rcc"))
    gate.fail("rcc:process_exited", now=101)
    result = gate.fail("other:later_error", now=102)

    assert result.alert_count == 1
    assert result.failure_reason == "rcc:process_exited"


def test_graceful_stop_is_idempotent_and_stops_all_dependency_renewal() -> None:
    gate = monitor(spec("rcc"), spec("listener"))
    gate.stop(now=101)
    result = gate.stop(now=102)

    assert result.state is StartupState.STOPPED
    assert all(item.state is StartupState.STOPPED for item in result.dependencies.values())
    assert gate.stopped_at == 101


def test_hard_failure_latches_and_does_not_automatically_restart() -> None:
    gate = monitor(spec("rcc"))
    gate.fail("rcc:process_exited", now=101)

    result = gate.observe("rcc", observation(StartupState.READY), now=102)
    assert result.state is StartupState.FAILED
    assert not result.ready_for_t0


def test_pre_t0_side_effect_gate_is_mandatory() -> None:
    gate = monitor(spec("process"), spec("canonical_safety_gate"))
    gate.observe("process", observation(StartupState.READY), now=101)
    result = gate.observe(
        "canonical_safety_gate",
        observation(StartupState.PROCESS_STARTING),
        now=101,
    )

    assert not result.ready_for_t0


def test_side_effect_or_execution_drift_is_immediate_hard_fail() -> None:
    gate = monitor(spec("canonical_safety_gate"))
    result = gate.observe(
        "canonical_safety_gate",
        observation(StartupState.PROCESS_STARTING, hard_failure="duplicate_materialization"),
        now=100.5,
    )

    assert result.failure_reason == "canonical_safety_gate:duplicate_materialization"


def test_total_budget_is_bounded_to_ten_minutes() -> None:
    with pytest.raises(ValueError, match="600"):
        CanonicalStartupReadinessMonitor(
            started_at=0,
            cold_start=True,
            dependencies=(spec("rcc"),),
            total_budget_seconds=601,
        )


def test_monotonic_clock_regression_is_rejected() -> None:
    gate = monitor(spec("rcc"))
    gate.assess(101)

    with pytest.raises(ValueError, match="backwards"):
        gate.assess(100.5)


def test_dependency_matrix_separates_mandatory_and_advisory_contours() -> None:
    matrix = {item.name: item for item in CANONICAL_RCC_DEPENDENCIES}

    assert matrix["ollama_listener"].required_for_t0
    assert matrix["paper_cards"].required_for_t0
    assert matrix["farm_owner"].required_for_t0
    assert not matrix["ollama_runner"].required_for_t0
    assert not matrix["first_local_inference"].required_for_t0
    assert not matrix["cloud_public_providers"].required_for_t0
    assert len({item.cold_timeout_seconds for item in matrix.values()}) > 3


def test_default_monitor_never_expands_trading_authority() -> None:
    source = " ".join(
        item.hard_fail_condition + item.degraded_behavior
        for item in CANONICAL_RCC_DEPENDENCIES
    ).lower()

    assert "execution authority" in source or "execution-authority" in source
    assert "private" in source
    assert "auto_trade" + "=true" not in source
