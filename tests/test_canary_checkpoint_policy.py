from __future__ import annotations

import pytest

from src.research_lab.canary_checkpoint_policy import (
    CANONICAL_CANARY_CHECKPOINTS,
    FINAL_QUIESCENT_CHECKPOINT,
    CanaryFastSampleWatchdog,
    IntegrityEvidenceMode,
    collect_checkpoint_integrity_evidence,
    due_active_checkpoints,
)


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


def test_watchdog_rejects_monotonic_clock_regression() -> None:
    watchdog = CanaryFastSampleWatchdog(started_at=100.0)
    watchdog.record_fast_sample(now=110.0)

    with pytest.raises(ValueError, match="backwards"):
        watchdog.assess(now=109.0)
