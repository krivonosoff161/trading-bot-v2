from __future__ import annotations

import threading
import time

import pytest

from src.research_lab.canary_checkpoint_policy import (
    CANONICAL_CANARY_CHECKPOINTS,
    CANONICAL_MONITORING_LANES,
    FINAL_QUIESCENT_CHECKPOINT,
    CanaryLaneSample,
    CanonicalCanaryRuntimeWatchdog,
    CanaryFastSampleWatchdog,
    CanaryMonitorHardFailure,
    CanaryMonitoringCoordinator,
    CanaryMonitoringService,
    IntegrityEvidenceMode,
    collect_checkpoint_integrity_evidence,
    build_monitoring_lane_watchdogs,
    due_active_checkpoints,
    require_healthy_watchdog,
)
from src.research_lab.windows_listener_probe import WindowsListenerProbeError


def test_active_checkpoint_never_invokes_full_integrity_probe() -> None:
    active = next(item for item in CANONICAL_CANARY_CHECKPOINTS if item.name == "5m")
    calls: list[str] = []

    def bounded() -> dict[str, object]:
        calls.append("bounded")
        return {"read_only_access": "ok", "lock_errors": 0}

    def forbidden_full() -> dict[str, object]:
        raise AssertionError("active checkpoint attempted full integrity")

    result = collect_checkpoint_integrity_evidence(
        active,
        bounded_health_probe=bounded,
        full_integrity_probe=forbidden_full,
    )

    assert result == {"read_only_access": "ok", "lock_errors": 0}
    assert calls == ["bounded"]


def test_full_integrity_is_limited_to_pre_t0_and_final_quiescence() -> None:
    immediate = CANONICAL_CANARY_CHECKPOINTS[0]
    calls: list[str] = []

    def full() -> dict[str, object]:
        calls.append("full")
        return {"integrity_check": "ok"}

    for spec in (immediate, FINAL_QUIESCENT_CHECKPOINT):
        result = collect_checkpoint_integrity_evidence(
            spec,
            bounded_health_probe=lambda: {"bounded": "ok"},
            full_integrity_probe=full,
        )
        assert result == {"integrity_check": "ok"}

    assert immediate.integrity_mode is IntegrityEvidenceMode.FULL_PRE_T0
    assert FINAL_QUIESCENT_CHECKPOINT.integrity_mode is IntegrityEvidenceMode.FULL_QUIESCENT
    assert calls == ["full", "full"]


def test_every_active_checkpoint_uses_only_bounded_health_signals() -> None:
    active = [item for item in CANONICAL_CANARY_CHECKPOINTS if item.name != "immediate"]

    assert active
    assert all(item.integrity_mode is IntegrityEvidenceMode.BOUNDED_HEALTH for item in active)
    assert active[-1].name == "300m"
    assert active[-1].due_seconds == 18_000.0


def test_checkpoint_schedule_returns_all_overdue_uncompleted_items() -> None:
    result = due_active_checkpoints(
        elapsed_seconds=901,
        completed=frozenset({"2m"}),
    )

    assert [item.name for item in result] == ["5m", "15m"]


def test_checkpoint_schedule_rejects_non_monotonic_elapsed_value() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        due_active_checkpoints(elapsed_seconds=-0.1, completed=frozenset())


def test_fast_samples_remain_green_inside_bounded_gap() -> None:
    watchdog = CanaryFastSampleWatchdog(started_at=100.0, max_fast_sample_gap_seconds=45.0)
    watchdog.record_fast_sample(now=110.0)
    watchdog.record_fast_sample(now=140.0)

    result = watchdog.assess(now=184.9)

    assert result.failure_reason is None
    assert result.fast_sample_age_seconds == pytest.approx(44.9)


def test_missing_fast_sample_fails_before_checkpoint_can_mask_it() -> None:
    watchdog = CanaryFastSampleWatchdog(started_at=100.0, max_fast_sample_gap_seconds=45.0)

    result = watchdog.assess(now=145.1)

    assert result.failure_reason == "monitor_fast_sample_initial_deadline_exhausted"
    assert result.alert_count == 1


def test_stale_fast_sample_latches_one_idempotent_alert() -> None:
    watchdog = CanaryFastSampleWatchdog(started_at=100.0, max_fast_sample_gap_seconds=45.0)
    watchdog.record_fast_sample(now=105.0)
    first = watchdog.assess(now=150.1)
    second = watchdog.assess(now=160.0)

    assert first.failure_reason == "monitor_fast_sample_freshness_lost"
    assert second.failure_reason == first.failure_reason
    assert second.alert_count == 1


def test_late_sample_cannot_clear_latched_watchdog_failure() -> None:
    watchdog = CanaryFastSampleWatchdog(started_at=100.0, max_fast_sample_gap_seconds=45.0)
    watchdog.assess(now=145.1)

    result = watchdog.record_fast_sample(now=146.0)

    assert result.failure_reason == "monitor_fast_sample_initial_deadline_exhausted"
    assert result.last_fast_sample_at is None


def test_late_completed_sample_cannot_hide_gap_without_prior_assess() -> None:
    watchdog = CanaryFastSampleWatchdog(started_at=100.0, max_fast_sample_gap_seconds=45.0)
    watchdog.record_fast_sample(now=105.0)

    result = watchdog.record_fast_sample(now=150.1)

    assert result.failure_reason == "monitor_fast_sample_freshness_lost"
    assert result.last_fast_sample_at == 105.0
    assert result.fast_sample_age_seconds == pytest.approx(45.1)


def test_adapter_must_escalate_latched_watchdog_before_side_effects() -> None:
    watchdog = CanaryFastSampleWatchdog(started_at=100.0, max_fast_sample_gap_seconds=45.0)
    watchdog.record_fast_sample(now=105.0)
    late = watchdog.record_fast_sample(now=150.1)

    with pytest.raises(CanaryMonitorHardFailure, match="monitor_fast_sample_freshness_lost"):
        require_healthy_watchdog(late)


def test_healthy_watchdog_assessment_does_not_raise() -> None:
    watchdog = CanaryFastSampleWatchdog(started_at=100.0, max_fast_sample_gap_seconds=45.0)

    require_healthy_watchdog(watchdog.record_fast_sample(now=110.0))


def test_fast_safety_lane_excludes_database_snapshot() -> None:
    lanes = {item.name: item for item in CANONICAL_MONITORING_LANES}

    assert not lanes["fast_safety"].permits_database_snapshot
    assert lanes["fast_safety"].max_sample_gap_seconds == 15.0
    assert not lanes["listener_inventory"].permits_database_snapshot
    assert lanes["listener_inventory"].max_sample_gap_seconds == 90.0
    assert lanes["deep_database"].permits_database_snapshot
    assert lanes["deep_database"].max_sample_gap_seconds == 300.0
    assert not lanes["product_progress"].permits_database_snapshot
    assert lanes["product_progress"].max_sample_gap_seconds == 90.0


def test_deep_database_progress_cannot_refresh_fast_safety_lane() -> None:
    lanes = build_monitoring_lane_watchdogs(started_at=100.0)
    lanes["fast_safety"].record_fast_sample(now=105.0)
    lanes["deep_database"].record_fast_sample(now=105.0)
    lanes["deep_database"].record_fast_sample(now=149.0)

    fast = lanes["fast_safety"].assess(now=150.1)
    deep = lanes["deep_database"].assess(now=150.1)

    assert fast.failure_reason == "monitor_fast_sample_freshness_lost"
    assert deep.failure_reason is None


def test_fast_and_deep_lanes_have_independent_latched_alerts() -> None:
    lanes = build_monitoring_lane_watchdogs(started_at=100.0)
    fast = lanes["fast_safety"].assess(now=145.1)

    assert fast.alert_count == 1
    assert lanes["deep_database"].assess(now=145.1).alert_count == 0


def test_watchdog_rejects_monotonic_clock_regression() -> None:
    watchdog = CanaryFastSampleWatchdog(started_at=100.0)
    watchdog.record_fast_sample(now=110.0)

    with pytest.raises(ValueError, match="backwards"):
        watchdog.assess(now=109.0)


def test_deep_probe_failure_does_not_poison_fast_safety_lane() -> None:
    monitor = CanaryMonitoringCoordinator(started_at=100.0)

    fast = monitor.sample(
        "fast_safety",
        lambda: {"owner_authority": "valid"},
        now=105.0,
    )
    deep = monitor.sample(
        "deep_database",
        lambda: (_ for _ in ()).throw(
            RuntimeError("synthetic private detail must not escape")
        ),
        now=105.0,
    )
    next_fast = monitor.sample(
        "fast_safety",
        lambda: {"owner_authority": "valid"},
        now=110.0,
    )

    assert fast.state == "healthy"
    assert deep.state == "degraded"
    assert deep.error_type == "RuntimeError"
    assert dict(deep.payload) == {}
    assert "private detail" not in repr(deep)
    assert next_fast.state == "healthy"
    monitor.require_lane("fast_safety", now=110.0)


def test_repeated_fast_probe_failure_latches_only_after_freshness_budget() -> None:
    monitor = CanaryMonitoringCoordinator(started_at=100.0)
    monitor.sample("fast_safety", lambda: {"ok": True}, now=105.0)

    transient = monitor.sample(
        "fast_safety",
        lambda: (_ for _ in ()).throw(OSError("synthetic")),
        now=110.0,
    )
    exhausted = monitor.sample(
        "fast_safety",
        lambda: (_ for _ in ()).throw(OSError("synthetic")),
        now=120.1,
    )

    assert transient.state == "degraded"
    assert transient.watchdog.failure_reason is None
    assert exhausted.state == "failed"
    assert exhausted.watchdog.failure_reason == "monitor_fast_sample_freshness_lost"
    with pytest.raises(CanaryMonitorHardFailure):
        monitor.require_lane("fast_safety", now=120.1)


def test_blocked_deep_probe_cannot_block_fast_lane_or_hide_freshness_failure() -> None:
    deep_entered = threading.Event()
    release_deep = threading.Event()
    failures: list[str] = []
    samples: list[str] = []

    def deep_probe() -> dict[str, object]:
        deep_entered.set()
        release_deep.wait(1.0)
        return {"bounded": True}

    service = CanaryMonitoringService(
        fast_probe=lambda: {"authority": "valid"},
        deep_probe=deep_probe,
        product_probe=lambda: {"ready": True},
        on_sample=lambda sample: samples.append(sample.lane),
        on_failure=lambda lane, _assessment: failures.append(lane),
        fast_interval_seconds=0.01,
        deep_interval_seconds=0.01,
        product_interval_seconds=0.01,
        supervisor_interval_seconds=0.005,
    )
    service.coordinator.watchdogs[
        "fast_safety"
    ].max_fast_sample_gap_seconds = 0.2
    service.coordinator.watchdogs[
        "deep_database"
    ].max_fast_sample_gap_seconds = 0.05

    service.start()
    assert deep_entered.wait(0.2)
    deadline = time.monotonic() + 0.5
    while not failures and time.monotonic() < deadline:
        time.sleep(0.005)

    assert failures == ["deep_database"]
    assert samples.count("fast_safety") >= 2
    assert samples.count("product_progress") >= 2
    release_deep.set()
    assert service.stop(timeout=0.5) == ()


def test_blocked_listener_inventory_cannot_starve_fast_authority_samples() -> None:
    listener_entered = threading.Event()
    release_listener = threading.Event()
    failures: list[str] = []
    samples: list[str] = []

    def listener_probe() -> dict[str, object]:
        listener_entered.set()
        release_listener.wait(0.3)
        return {"ready": True, "listener_pid": 42}

    service = CanaryMonitoringService(
        fast_probe=lambda: {"authority": "valid"},
        listener_probe=listener_probe,
        deep_probe=lambda: {"bounded": True},
        product_probe=lambda: {"ready": True},
        on_sample=lambda sample: samples.append(sample.lane),
        on_failure=lambda lane, _assessment: failures.append(lane),
        fast_interval_seconds=0.01,
        listener_interval_seconds=0.01,
        deep_interval_seconds=1.0,
        product_interval_seconds=1.0,
        supervisor_interval_seconds=0.005,
    )
    service.coordinator.watchdogs[
        "fast_safety"
    ].max_fast_sample_gap_seconds = 0.05
    service.coordinator.watchdogs[
        "listener_inventory"
    ].max_fast_sample_gap_seconds = 0.25

    service.start()
    assert listener_entered.wait(0.1)
    time.sleep(0.08)
    assert failures == []
    assert samples.count("fast_safety") >= 3
    release_listener.set()
    deadline = time.monotonic() + 0.3
    while samples.count("listener_inventory") == 0 and time.monotonic() < deadline:
        time.sleep(0.005)

    assert samples.count("listener_inventory") >= 1
    assert failures == []
    assert service.stop(timeout=0.5) == ()


def test_listener_inventory_freshness_fails_its_lane_not_fast_lane() -> None:
    listener_entered = threading.Event()
    release_listener = threading.Event()
    failures: list[str] = []
    fast_samples: list[CanaryLaneSample] = []

    def listener_probe() -> dict[str, object]:
        listener_entered.set()
        release_listener.wait(0.3)
        return {"ready": True}

    service = CanaryMonitoringService(
        fast_probe=lambda: {"authority": "valid"},
        listener_probe=listener_probe,
        deep_probe=lambda: {"bounded": True},
        product_probe=lambda: {"ready": True},
        on_sample=lambda sample: (
            fast_samples.append(sample) if sample.lane == "fast_safety" else None
        ),
        on_failure=lambda lane, _assessment: failures.append(lane),
        fast_interval_seconds=0.01,
        listener_interval_seconds=0.01,
        deep_interval_seconds=1.0,
        product_interval_seconds=1.0,
        supervisor_interval_seconds=0.005,
    )
    service.coordinator.watchdogs[
        "fast_safety"
    ].max_fast_sample_gap_seconds = 0.1
    service.coordinator.watchdogs[
        "listener_inventory"
    ].max_fast_sample_gap_seconds = 0.05

    service.start()
    assert listener_entered.wait(0.1)
    deadline = time.monotonic() + 0.3
    while not failures and time.monotonic() < deadline:
        time.sleep(0.005)

    assert failures == ["listener_inventory"]
    assert len(fast_samples) >= 3
    assert all(sample.state == "healthy" for sample in fast_samples)
    release_listener.set()
    assert service.stop(timeout=0.5) == ()


def test_explicit_safety_violation_is_escalated_without_waiting_for_gap() -> None:
    failed = threading.Event()
    failures: list[tuple[str, str | None]] = []

    def hard_failure() -> dict[str, object]:
        raise CanaryMonitorHardFailure("owner_authority:lease_expired")

    service = CanaryMonitoringService(
        fast_probe=hard_failure,
        deep_probe=lambda: {"bounded": True},
        product_probe=lambda: {"ready": True},
        on_sample=lambda _sample: None,
        on_failure=lambda lane, assessment: (
            failures.append((lane, assessment.failure_reason)),
            failed.set(),
        ),
        fast_interval_seconds=10.0,
        deep_interval_seconds=10.0,
        supervisor_interval_seconds=0.01,
    )
    service.start()

    assert failed.wait(0.5)
    assert service.stop(timeout=0.5) == ()
    assert failures == [("fast_safety", "owner_authority:lease_expired")]


def test_product_stage_race_is_immediate_and_leaves_no_monitor_thread() -> None:
    failed = threading.Event()
    failures: list[tuple[str, str | None]] = []

    def generation_race() -> dict[str, object]:
        raise CanaryMonitorHardFailure("paper_generation_stage_mismatch")

    service = CanaryMonitoringService(
        fast_probe=lambda: {"authority": "valid"},
        deep_probe=lambda: {"bounded": True},
        product_probe=generation_race,
        on_sample=lambda _sample: None,
        on_failure=lambda lane, assessment: (
            failures.append((lane, assessment.failure_reason)),
            failed.set(),
        ),
        fast_interval_seconds=10.0,
        deep_interval_seconds=10.0,
        product_interval_seconds=10.0,
        supervisor_interval_seconds=0.01,
    )
    service.start()

    assert failed.wait(0.5)
    assert service.stop(timeout=0.5) == ()
    assert failures == [("product_progress", "paper_generation_stage_mismatch")]


def test_monitor_failure_callback_is_idempotent_across_lanes() -> None:
    failures: list[str] = []
    service = CanaryMonitoringService(
        fast_probe=lambda: (_ for _ in ()).throw(OSError("synthetic")),
        deep_probe=lambda: (_ for _ in ()).throw(OSError("synthetic")),
        product_probe=lambda: (_ for _ in ()).throw(OSError("synthetic")),
        on_sample=lambda _sample: None,
        on_failure=lambda lane, _assessment: failures.append(lane),
        fast_interval_seconds=0.005,
        deep_interval_seconds=0.005,
        supervisor_interval_seconds=0.005,
    )
    for watchdog in service.coordinator.watchdogs.values():
        watchdog.max_fast_sample_gap_seconds = 0.02

    service.start()
    deadline = time.monotonic() + 0.5
    while not failures and time.monotonic() < deadline:
        time.sleep(0.005)
    service.stop(timeout=0.5)

    assert len(failures) == 1


def _complete_runtime_sample() -> dict[str, object]:
    return {
        "ready": True,
        "hard_fail_reasons": [],
        "owner": {
            "green": True,
            "distinct_process_authorities": 1,
            "process_bound_to_rcc": True,
            "owner_hash": "owner-a",
            "fence": 40,
        },
        "process_lease_supervisor": {
            "ready": True,
            "state": "running",
            "fresh_generation": True,
            "paper_only": True,
            "execution_allowed": False,
            "identity_matches": True,
            "fence_matches": True,
        },
    }


def test_external_listener_probe_error_does_not_fabricate_supervisor_failure() -> None:
    monitor = CanonicalCanaryRuntimeWatchdog(
        started_at=100.0,
        baseline_owner_hash="owner-a",
        baseline_fence=40,
        max_probe_gap_seconds=90.0,
    )
    first = monitor.sample(_complete_runtime_sample, now=100.0)

    def transient_probe() -> dict[str, object]:
        raise WindowsListenerProbeError("synthetic_listener_probe_failure")

    transient = monitor.sample(transient_probe, now=130.0)
    recovered = monitor.sample(_complete_runtime_sample, now=160.0)

    assert first.state == "healthy"
    assert transient.state == "degraded"
    assert transient.error_type == "WindowsListenerProbeError"
    assert transient.payload == {}
    assert transient.watchdog.failure_reason is None
    assert recovered.state == "healthy"
    assert recovered.watchdog.failure_reason is None


def test_external_probe_loss_fails_only_after_monotonic_freshness_deadline() -> None:
    monitor = CanonicalCanaryRuntimeWatchdog(
        started_at=100.0,
        baseline_owner_hash="owner-a",
        baseline_fence=40,
        max_probe_gap_seconds=90.0,
    )
    monitor.sample(_complete_runtime_sample, now=100.0)

    def unavailable() -> dict[str, object]:
        raise OSError("synthetic")

    before_deadline = monitor.sample(unavailable, now=190.0)
    exhausted = monitor.sample(unavailable, now=190.001)

    assert before_deadline.state == "degraded"
    assert before_deadline.watchdog.failure_reason is None
    assert exhausted.state == "failed"
    assert (
        exhausted.watchdog.failure_reason
        == "monitor_fast_sample_freshness_lost"
    )


def test_external_explicit_safety_violation_fails_immediately() -> None:
    monitor = CanonicalCanaryRuntimeWatchdog(
        started_at=100.0,
        baseline_owner_hash="owner-a",
        baseline_fence=40,
    )

    def hard_failure() -> dict[str, object]:
        raise CanaryMonitorHardFailure("foreign_ollama_listener")

    result = monitor.sample(hard_failure, now=100.1)

    assert result.state == "failed"
    assert result.watchdog.failure_reason == "foreign_ollama_listener"


def test_external_complete_sample_missing_supervisor_fails_immediately() -> None:
    monitor = CanonicalCanaryRuntimeWatchdog(
        started_at=100.0,
        baseline_owner_hash="owner-a",
        baseline_fence=40,
    )
    payload = _complete_runtime_sample()
    payload.pop("process_lease_supervisor")

    result = monitor.sample(lambda: payload, now=100.1)

    assert result.state == "failed"
    assert (
        result.watchdog.failure_reason
        == "process_lease_supervisor_not_ready"
    )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("owner_hash", "owner-b", "canonical_owner_identity_changed"),
        ("fence", 41, "canonical_owner_fence_changed"),
        (
            "distinct_process_authorities",
            2,
            "canonical_owner_cardinality_changed",
        ),
        ("process_bound_to_rcc", False, "canonical_owner_left_rcc_tree"),
    ],
)
def test_external_complete_sample_preserves_owner_and_fence_fail_closed(
    field: str,
    value: object,
    reason: str,
) -> None:
    monitor = CanonicalCanaryRuntimeWatchdog(
        started_at=100.0,
        baseline_owner_hash="owner-a",
        baseline_fence=40,
    )
    payload = _complete_runtime_sample()
    owner = payload["owner"]
    assert isinstance(owner, dict)
    owner[field] = value

    result = monitor.sample(lambda: payload, now=100.1)

    assert result.state == "failed"
    assert result.watchdog.failure_reason == reason


def test_external_corrupt_complete_sample_fails_closed_without_probe_crash() -> None:
    monitor = CanonicalCanaryRuntimeWatchdog(
        started_at=100.0,
        baseline_owner_hash="owner-a",
        baseline_fence=40,
    )
    payload = _complete_runtime_sample()
    owner = payload["owner"]
    assert isinstance(owner, dict)
    owner["fence"] = "not-an-integer"

    result = monitor.sample(lambda: payload, now=100.1)

    assert result.state == "failed"
    assert result.error_type == "RuntimeSampleValidationError"
    assert result.watchdog.failure_reason == "runtime_sample_invalid"
