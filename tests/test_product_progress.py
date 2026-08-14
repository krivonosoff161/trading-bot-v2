import json
import os
from pathlib import Path

import pytest

from src.research_lab.product_progress import (
    ProductProgressMonitor,
    ProductProgressSlo,
    ProductProgressTransitionError,
    assess_post_t0_product_progress,
    farm_metrics,
    publish_checkpoint,
    scanner_metrics,
)
from src.research_lab import product_progress


def _steady_report(*, waiting: bool) -> dict[str, object]:
    return {
        "schema": "ProductProgressReport.v1",
        "state": "starting" if waiting else "ready",
        "ready": not waiting,
        "hard_fail_reasons": [],
        "components": {
            "scanner": {"current_run": True},
            "farm": {
                "current_run": True,
                "metrics": {
                    "paper_generation_waiting": waiting,
                    "validation_generation_started_at": 100.0 if waiting else 0.0,
                },
            },
        },
        "paper_only": True,
        "execution_allowed": False,
    }


def test_checkpoint_retries_transient_windows_replace_contention(
    tmp_path: Path, monkeypatch
) -> None:
    real_replace = os.replace
    attempts = 0

    def contended_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            exc = PermissionError("synthetic sharing denial")
            exc.winerror = 5
            raise exc
        real_replace(source, target)

    monkeypatch.setattr(product_progress.os, "replace", contended_replace)
    monkeypatch.setattr(product_progress.time, "sleep", lambda _delay: None)

    publish_checkpoint(
        tmp_path,
        component="farm_progress",
        sequence=1,
        status="progress",
        metrics={"stage": "paper_generation_v2", "milestone": "chunk_completed"},
        completed_at=1.0,
    )

    assert attempts == 4
    assert json.loads(
        (tmp_path / "state" / "product_progress" / "farm_progress.json").read_text(
            encoding="utf-8"
        )
    )["sequence"] == 1
    assert not list((tmp_path / "state" / "product_progress").glob(".*.tmp"))


def test_checkpoint_persistent_windows_contention_fails_closed_and_cleans_temp(
    tmp_path: Path, monkeypatch
) -> None:
    attempts = 0

    def always_contended(_source, _target):
        nonlocal attempts
        attempts += 1
        exc = PermissionError("synthetic sharing denial")
        exc.winerror = 32
        raise exc

    monkeypatch.setattr(product_progress.os, "replace", always_contended)
    monkeypatch.setattr(product_progress.time, "sleep", lambda _delay: None)

    with pytest.raises(PermissionError, match="synthetic sharing denial"):
        publish_checkpoint(
            tmp_path,
            component="farm_progress",
            sequence=1,
            status="progress",
            metrics={"stage": "paper_generation_v2", "milestone": "chunk_completed"},
            completed_at=1.0,
        )

    assert attempts == 6
    progress_dir = tmp_path / "state" / "product_progress"
    assert not (progress_dir / "farm_progress.json").exists()
    assert not list(progress_dir.glob(".*.tmp"))


def test_checkpoint_uses_unique_temporary_name_per_write(
    tmp_path: Path, monkeypatch
) -> None:
    sources = []
    real_replace = os.replace

    def observe_replace(source, target):
        sources.append(Path(source).name)
        real_replace(source, target)

    ticks = iter((101, 102))
    monkeypatch.setattr(product_progress.time, "time_ns", lambda: next(ticks))
    monkeypatch.setattr(product_progress.os, "replace", observe_replace)

    for sequence in (1, 2):
        publish_checkpoint(
            tmp_path,
            component="farm_progress",
            sequence=sequence,
            status="progress",
            metrics={"stage": "paper_generation_v2", "milestone": "chunk_completed"},
            completed_at=float(sequence),
        )

    assert len(set(sources)) == 2
    assert sources[0].endswith(".101.tmp")
    assert sources[1].endswith(".102.tmp")


def test_post_t0_pending_generation_is_bounded_transition() -> None:
    assessment = assess_post_t0_product_progress(_steady_report(waiting=True))

    assert assessment.state == "transitioning"
    assert assessment.transitioning is True
    assert assessment.hard_failure is None


def test_post_t0_ready_generation_is_green() -> None:
    assessment = assess_post_t0_product_progress(_steady_report(waiting=False))

    assert assessment.state == "ready"
    assert assessment.transitioning is False
    assert assessment.hard_failure is None


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            {"hard_fail_reasons": ["farm_product_progress_stale"]},
            "product_progress:farm_product_progress_stale",
        ),
        (
            {"state": "starting", "ready": False, "components": {}},
            "product_progress_not_ready_after_t0",
        ),
        ({"execution_allowed": True}, "product_progress_report_invalid"),
    ],
)
def test_post_t0_policy_fails_closed(mutation: dict[str, object], reason: str) -> None:
    report = _steady_report(waiting=True)
    report.update(mutation)

    assessment = assess_post_t0_product_progress(report)

    assert assessment.state == "failed"
    assert assessment.hard_failure == reason


def _publish_green(root: Path, *, completed_at: float = 101.0) -> None:
    publish_checkpoint(
        root,
        component="scanner",
        sequence=1,
        status="idle",
        metrics=scanner_metrics(
            inputs=0,
            fresh=0,
            cards=0,
            dropped=0,
            llm_failures=0,
            provider_failures=0,
        ),
        completed_at=completed_at,
    )
    publish_checkpoint(
        root,
        component="farm",
        sequence=1,
        status="completed",
        metrics={
            "validation_oldest_age_seconds": 0.0,
            "validation_backlog_slo_seconds": 3600.0,
            "generation_consistent": True,
            "operational_rows_retained": 0,
        },
        completed_at=completed_at,
    )


def test_zero_signal_completed_cycles_are_real_progress(tmp_path: Path) -> None:
    _publish_green(tmp_path)

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()

    assert report["ready"] is True
    assert report["state"] == "ready"
    assert report["components"]["scanner"]["status"] == "idle"
    assert report["run_started_at"] == 100.0


def test_green_t0_transition_preserves_launch_boundary(tmp_path: Path) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    startup = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()

    steady = ProductProgressMonitor.from_green_t0_report(
        tmp_path,
        t0_report=startup,
        t0_observed_at=102.0,
        wall_clock=lambda: 103.0,
    )

    assert steady.run_started_at == 100.0
    assert steady.sample()["ready"] is True
    assert ProductProgressMonitor(
        tmp_path,
        run_started_at=102.0,
        wall_clock=lambda: 103.0,
    ).sample()["ready"] is False


def test_green_t0_transition_accepts_new_bounded_generation_in_progress(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    startup = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()
    publish_checkpoint(
        tmp_path,
        component="farm",
        sequence=2,
        status="waiting",
        metrics={
            "validation_oldest_age_seconds": 0.0,
            "validation_backlog_slo_seconds": 3600.0,
            "generation_consistent": False,
            "paper_generation_waiting": True,
            "validation_generation_started_at": 102.5,
            "product_cycle_complete": True,
            "operational_rows_retained": 0,
        },
        completed_at=103.0,
    )

    steady = ProductProgressMonitor.from_green_t0_report(
        tmp_path,
        t0_report=startup,
        t0_observed_at=102.0,
        wall_clock=lambda: 104.0,
    )

    report = steady.sample()
    assert steady.run_started_at == 100.0
    assert report["state"] == "starting"
    assert report["ready"] is False
    assert assess_post_t0_product_progress(report).transitioning is True


def test_green_t0_transition_rejects_new_unbounded_generation_state(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    startup = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()
    publish_checkpoint(
        tmp_path,
        component="farm",
        sequence=2,
        status="waiting",
        metrics={
            "validation_oldest_age_seconds": 0.0,
            "validation_backlog_slo_seconds": 3600.0,
            "generation_consistent": False,
            "paper_generation_waiting": False,
            "product_cycle_complete": True,
            "operational_rows_retained": 0,
        },
        completed_at=103.0,
    )

    with pytest.raises(ProductProgressTransitionError, match="changed"):
        ProductProgressMonitor.from_green_t0_report(
            tmp_path,
            t0_report=startup,
            t0_observed_at=102.0,
        )


def test_t0_transition_rejects_pre_run_or_rebased_report(tmp_path: Path) -> None:
    _publish_green(tmp_path, completed_at=99.0)
    pre_run = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 101.0,
    ).sample()
    with pytest.raises(ProductProgressTransitionError, match="not green"):
        ProductProgressMonitor.from_green_t0_report(
            tmp_path,
            t0_report=pre_run,
            t0_observed_at=101.0,
        )

    _publish_green(tmp_path, completed_at=101.0)
    green = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()
    green["run_started_at"] = 102.0
    with pytest.raises(ProductProgressTransitionError, match="changed"):
        ProductProgressMonitor.from_green_t0_report(
            tmp_path,
            t0_report=green,
            t0_observed_at=102.0,
        )


@pytest.mark.parametrize("invalid_boundary", [None, "100.0", True])
def test_t0_transition_rejects_untyped_boundary(
    tmp_path: Path, invalid_boundary: object
) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    green = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()
    green["run_started_at"] = invalid_boundary

    with pytest.raises(ProductProgressTransitionError, match="no valid"):
        ProductProgressMonitor.from_green_t0_report(
            tmp_path,
            t0_report=green,
            t0_observed_at=102.0,
        )


def test_t0_transition_rejects_regressed_sequence(tmp_path: Path) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    green = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()
    green["components"]["farm"]["sequence"] = 2

    with pytest.raises(ProductProgressTransitionError, match="regressed"):
        ProductProgressMonitor.from_green_t0_report(
            tmp_path,
            t0_report=green,
            t0_observed_at=102.0,
        )


@pytest.mark.parametrize("run_started_at", [0.0, float("nan"), float("inf")])
def test_monitor_rejects_invalid_run_boundary(
    tmp_path: Path, run_started_at: float
) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        ProductProgressMonitor(tmp_path, run_started_at=run_started_at)


def test_steady_monitor_still_fails_on_real_post_t0_staleness(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    slo = ProductProgressSlo(scanner_seconds=10.0, farm_seconds=10.0)
    startup = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        slo=slo,
        wall_clock=lambda: 102.0,
    ).sample()
    steady = ProductProgressMonitor.from_green_t0_report(
        tmp_path,
        t0_report=startup,
        t0_observed_at=102.0,
        slo=slo,
        wall_clock=lambda: 112.0,
    )

    assert set(steady.sample()["hard_fail_reasons"]) == {
        "scanner_product_progress_stale",
        "farm_product_progress_stale",
    }


def test_provider_degradation_does_not_hide_completed_pass(tmp_path: Path) -> None:
    _publish_green(tmp_path)
    publish_checkpoint(
        tmp_path,
        component="scanner",
        sequence=2,
        status="degraded",
        metrics=scanner_metrics(
            inputs=0,
            fresh=0,
            cards=0,
            dropped=0,
            llm_failures=0,
            provider_failures=2,
        ),
        completed_at=102.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 103.0,
    ).sample()

    assert report["ready"] is True
    assert report["state"] == "degraded"
    assert report["hard_fail_reasons"] == []


@pytest.mark.parametrize(
    ("component", "reason"),
    [
        ("scanner", "scanner_product_progress_stale"),
        ("farm", "farm_product_progress_stale"),
    ],
)
def test_stale_completed_stage_fails_closed(
    tmp_path: Path, component: str, reason: str
) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    slo = ProductProgressSlo(scanner_seconds=10.0, farm_seconds=10.0)

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        slo=slo,
        wall_clock=lambda: 112.0,
    ).sample()

    assert reason in report["hard_fail_reasons"]


def test_stale_pre_run_checkpoint_cannot_establish_t0(tmp_path: Path) -> None:
    _publish_green(tmp_path, completed_at=99.0)

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 101.0,
    ).sample()

    assert report["ready"] is False
    assert report["state"] == "starting"


def test_real_farm_milestone_keeps_long_cycle_live(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    publish_checkpoint(
        tmp_path,
        component="farm_progress",
        sequence=2,
        status="progress",
        metrics={"stage": "paper_runtime", "milestone": "chunk_completed"},
        completed_at=111.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        slo=ProductProgressSlo(scanner_seconds=20.0, farm_seconds=10.0),
        wall_clock=lambda: 112.0,
    ).sample()

    assert "farm_product_progress_stale" not in report["hard_fail_reasons"]
    assert report["components"]["farm_progress"]["current_run"] is True


def test_real_farm_milestone_extends_initial_cycle_inside_bounded_budget(
    tmp_path: Path,
) -> None:
    publish_checkpoint(
        tmp_path,
        component="scanner",
        sequence=1,
        status="completed",
        metrics=scanner_metrics(
            inputs=1,
            fresh=1,
            cards=1,
            dropped=0,
            llm_failures=0,
            provider_failures=0,
        ),
        completed_at=101.0,
    )
    publish_checkpoint(
        tmp_path,
        component="farm_progress",
        sequence=2,
        status="progress",
        metrics={
            "stage": "setup_outcome_memory_refresh",
            "milestone": "incremental_sources_classified",
            "completed": 21_500,
            "total": 26_845,
        },
        completed_at=404.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        slo=ProductProgressSlo(
            scanner_seconds=900.0,
            farm_seconds=300.0,
            farm_startup_max_seconds=600.0,
            farm_startup_progress_stale_seconds=60.0,
        ),
        wall_clock=lambda: 405.0,
    ).sample()

    assert report["state"] == "starting"
    assert report["hard_fail_reasons"] == []
    assert report["components"]["farm_progress"]["startup_liveness_eligible"] is True


def test_stale_farm_milestone_cannot_extend_initial_cycle(tmp_path: Path) -> None:
    publish_checkpoint(
        tmp_path,
        component="farm_progress",
        sequence=2,
        status="progress",
        metrics={"stage": "paper_runtime", "milestone": "chunk_completed"},
        completed_at=340.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        slo=ProductProgressSlo(
            scanner_seconds=900.0,
            farm_seconds=300.0,
            farm_startup_max_seconds=600.0,
            farm_startup_progress_stale_seconds=60.0,
        ),
        wall_clock=lambda: 405.0,
    ).sample()

    assert "farm_product_progress_startup_timeout" in report["hard_fail_reasons"]
    assert report["components"]["farm_progress"]["startup_liveness_eligible"] is False


def test_fresh_farm_milestone_cannot_extend_past_startup_max(tmp_path: Path) -> None:
    publish_checkpoint(
        tmp_path,
        component="farm_progress",
        sequence=2,
        status="progress",
        metrics={"stage": "paper_runtime", "milestone": "chunk_completed"},
        completed_at=704.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        slo=ProductProgressSlo(
            scanner_seconds=900.0,
            farm_seconds=300.0,
            farm_startup_max_seconds=600.0,
            farm_startup_progress_stale_seconds=60.0,
        ),
        wall_clock=lambda: 705.0,
    ).sample()

    assert "farm_product_progress_startup_timeout" in report["hard_fail_reasons"]
    assert report["components"]["farm_progress"]["startup_liveness_eligible"] is True


def test_validation_generation_and_training_invariants_fail_closed(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path)
    publish_checkpoint(
        tmp_path,
        component="farm",
        sequence=2,
        status="completed",
        metrics={
            "validation_oldest_age_seconds": 3601.0,
            "validation_backlog_slo_seconds": 3600.0,
            "generation_consistent": False,
            "operational_rows_retained": 1,
        },
        completed_at=102.0,
    )

    reasons = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 103.0,
    ).sample()["hard_fail_reasons"]

    assert set(reasons) == {
        "paper_generation_stage_mismatch",
        "technical_outcome_entered_training",
    }


def test_unrelated_progress_cannot_extend_pending_generation_forever(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    publish_checkpoint(
        tmp_path,
        component="farm",
        sequence=2,
        status="waiting",
        metrics={
            "validation_oldest_age_seconds": 0.0,
            "validation_backlog_slo_seconds": 3600.0,
            "generation_consistent": False,
            "paper_generation_waiting": True,
            "validation_generation_started_at": 102.0,
            "product_cycle_complete": True,
            "operational_rows_retained": 0,
        },
        completed_at=103.0,
    )
    publish_checkpoint(
        tmp_path,
        component="farm_progress",
        sequence=99,
        status="progress",
        metrics={
            "stage": "setup_outcome_memory_refresh",
            "milestone": "real_chunk_completed",
        },
        completed_at=801.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        slo=ProductProgressSlo(
            scanner_seconds=900.0,
            farm_seconds=300.0,
            validation_generation_transition_seconds=600.0,
        ),
        wall_clock=lambda: 802.1,
    ).sample()

    assert "validation_generation_transition_timeout" in report["hard_fail_reasons"]
    assert assess_post_t0_product_progress(report).state == "failed"


def test_stable_current_generation_does_not_hide_stalled_successor_build(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    publish_checkpoint(
        tmp_path,
        component="farm",
        sequence=2,
        status="completed",
        metrics={
            "validation_oldest_age_seconds": 0.0,
            "validation_backlog_slo_seconds": 3600.0,
            "generation_consistent": True,
            "validation_generation_build_active": True,
            "validation_generation_build_started_at": 102.0,
            "operational_rows_retained": 0,
        },
        completed_at=700.0,
    )

    healthy_build = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 701.0,
    ).sample()
    stalled_build = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 703.0,
    ).sample()

    assert healthy_build["state"] == "degraded"
    assert healthy_build["hard_fail_reasons"] == []
    assert "validation_generation_build_timeout" in stalled_build[
        "hard_fail_reasons"
    ]


def test_monitor_exposes_scanner_intake_and_validation_backlog_latency(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    publish_checkpoint(
        tmp_path,
        component="farm",
        sequence=2,
        status="completed",
        metrics={
            "scanner_uningested_remaining": 4,
            "scanner_oldest_uningested_age_seconds": 901.0,
            "validation_oldest_age_seconds": 3601.0,
            "validation_backlog_slo_seconds": 3600.0,
            "generation_consistent": True,
            "operational_rows_retained": 0,
        },
        completed_at=102.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 103.0,
    ).sample()

    assert report["hard_fail_reasons"] == [
        "scanner_intake_latency_slo_exceeded"
    ]
    assert "validation_historical_backlog_slo_exceeded" in report[
        "degraded_reasons"
    ]


def test_historical_validation_debt_with_positive_service_is_degraded_not_failed(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path, completed_at=4101.0)
    publish_checkpoint(
        tmp_path,
        component="farm",
        sequence=2,
        status="completed",
        metrics={
            "validation_active": 100,
            "validation_eligible": 100,
            "validation_oldest_age_seconds": 10_000.0,
            "validation_backlog_slo_seconds": 3600.0,
            "validation_arrival_rate_per_hour": 2.0,
            "validation_service_rate_per_hour": 10.0,
            "validation_net_drain_rate_per_hour": 8.0,
            "generation_consistent": True,
            "operational_rows_retained": 0,
        },
        completed_at=4101.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 4102.0,
    ).sample()

    assert report["ready"] is True
    assert report["hard_fail_reasons"] == []
    assert "validation_historical_backlog_slo_exceeded" in report[
        "degraded_reasons"
    ]


@pytest.mark.parametrize(
    ("service_rate", "net_drain_rate", "expected"),
    [
        (0.0, 0.0, "validation_backlog_service_stalled"),
        (3.0, 0.0, "validation_backlog_not_draining"),
        (3.0, -1.0, "validation_backlog_not_draining"),
    ],
)
def test_historical_validation_debt_fails_after_bounded_non_drain_observation(
    tmp_path: Path,
    service_rate: float,
    net_drain_rate: float,
    expected: str,
) -> None:
    _publish_green(tmp_path, completed_at=4101.0)
    publish_checkpoint(
        tmp_path,
        component="farm",
        sequence=2,
        status="completed",
        metrics={
            "validation_active": 100,
            "validation_eligible": 100,
            "validation_oldest_age_seconds": 10_000.0,
            "validation_backlog_slo_seconds": 3600.0,
            "validation_arrival_rate_per_hour": 3.0 - net_drain_rate,
            "validation_service_rate_per_hour": service_rate,
            "validation_net_drain_rate_per_hour": net_drain_rate,
            "generation_consistent": True,
            "operational_rows_retained": 0,
        },
        completed_at=4101.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 4102.0,
    ).sample()

    assert expected in report["hard_fail_reasons"]


def test_fresh_validation_task_latency_still_fails_closed(tmp_path: Path) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    publish_checkpoint(
        tmp_path,
        component="farm",
        sequence=2,
        status="completed",
        metrics={
            "validation_active": 20,
            "validation_eligible": 20,
            "validation_oldest_age_seconds": 20_000.0,
            "validation_backlog_slo_seconds": 3600.0,
            "validation_fresh_eligible": 1,
            "validation_fresh_oldest_age_seconds": 901.0,
            "generation_consistent": True,
            "operational_rows_retained": 0,
        },
        completed_at=102.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 103.0,
    ).sample()

    assert report["hard_fail_reasons"] == [
        "validation_fresh_task_latency_slo_exceeded"
    ]


def test_current_zero_backlog_does_not_fall_back_to_stale_cycle_counters() -> None:
    from src.research_lab.product_progress import farm_metrics

    metrics = farm_metrics({
        "counters": {
            "validation": {
                "active": 9,
                "eligible": 8,
                "oldest_age_seconds": 7200.0,
            }
        },
        "validation_backlog": {
            "active": 0,
            "eligible": 0,
            "oldest_age_seconds": 0.0,
            "backlog_slo_seconds": 3600.0,
        },
    })

    assert metrics["validation_active"] == 0
    assert metrics["validation_eligible"] == 0
    assert metrics["validation_oldest_age_seconds"] == 0.0


def test_real_progress_does_not_hide_unbounded_product_cycle(tmp_path: Path) -> None:
    _publish_green(tmp_path, completed_at=101.0)
    publish_checkpoint(
        tmp_path,
        component="farm_progress",
        sequence=5,
        status="progress",
        metrics={"stage": "memory", "milestone": "real_chunk_completed"},
        completed_at=2001.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        slo=ProductProgressSlo(
            scanner_seconds=3000.0,
            farm_seconds=300.0,
            farm_cycle_max_seconds=1800.0,
        ),
        wall_clock=lambda: 2002.0,
    ).sample()

    assert "farm_product_progress_stale" not in report["hard_fail_reasons"]
    assert "farm_product_cycle_timeout" in report["hard_fail_reasons"]


def test_checkpoint_rejects_sensitive_or_nested_metrics(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sensitive"):
        publish_checkpoint(
            tmp_path,
            component="scanner",
            sequence=1,
            status="completed",
            metrics={"recipient_id": "synthetic"},
        )
    with pytest.raises(TypeError, match="scalar"):
        publish_checkpoint(
            tmp_path,
            component="scanner",
            sequence=1,
            status="completed",
            metrics={"raw_rows": [{"synthetic": True}]},  # type: ignore[dict-item]
        )


def test_farm_metrics_detects_stage_race_and_censor_invariant() -> None:
    metrics = farm_metrics(
        {
            "counters": {"validation": {"oldest_age_seconds": 5.0}},
            "paper_generation_v2": {"run_id": "run-new"},
            "main_paper_runtime_observation": {
                "paper_generation_run_id": "run-new",
            },
            "paper_telegram_preview": {"paper_generation_run_id": "run-old"},
            "paper_signal_training_export": {
                "paper_generation_run_id": "run-new",
                "operational_rows_retained": 0,
            },
        }
    )

    assert metrics["generation_consistent"] is False
    assert metrics["operational_rows_retained"] == 0


def test_farm_metrics_requires_complete_same_generation_chain() -> None:
    run = "run-current"
    metrics = farm_metrics(
        {
            "paper_generation_v2": {
                "run_id": run,
                "producer_membership": {
                    "active_executable_signals": 4,
                    "validation_bound_members": 1,
                    "research_only_excluded": 3,
                },
            },
            "main_paper_bridge": {
                "paper_generation_run_id": run,
                "instructions": 1,
            },
            "main_paper_runtime_queue": {
                "paper_generation_run_id": run,
                "queued": 1,
            },
            "main_paper_runtime_observation": {
                "paper_generation_run_id": run,
                "observed": 1,
            },
            "paper_telegram_preview": {
                "paper_generation_run_id": run,
                "rendered": 1,
            },
            "paper_telegram_delivery": {
                "paper_generation_run_id": run,
                "sent": 1,
            },
            "paper_signal_training_export": {
                "paper_generation_run_id": run,
                "rows": 1,
            },
            "outcome_retest_results": {
                "training_evidence": {"paper_generation_run_id": run},
                "items": [],
            },
        }
    )

    assert metrics["generation_consistent"] is True
    assert metrics["queue_items"] == 1
    assert metrics["producer_active_executable_signals"] == 4
    assert metrics["producer_validation_bound_members"] == 1
    assert metrics["producer_research_only_excluded"] == 3


def test_farm_metrics_rejects_delivery_from_another_generation() -> None:
    run = "run-current"
    metrics = farm_metrics(
        {
            "paper_generation_v2": {"run_id": run},
            "main_paper_bridge": {"paper_generation_run_id": run},
            "main_paper_runtime_queue": {"paper_generation_run_id": run},
            "main_paper_runtime_observation": {"paper_generation_run_id": run},
            "paper_telegram_preview": {"paper_generation_run_id": run},
            "paper_telegram_delivery": {"paper_generation_run_id": "run-stale"},
            "paper_signal_training_export": {"paper_generation_run_id": run},
            "outcome_retest_results": {
                "training_evidence": {"paper_generation_run_id": run}
            },
        }
    )

    assert metrics["generation_consistent"] is False


def test_farm_metrics_exposes_paper_pipeline_cycle_failure_without_payload() -> None:
    metrics = farm_metrics(
        {
            "errors": [
                {"where": "true_forward", "error": "synthetic ordinary error"},
                {"where": "paper_signals", "error": "synthetic invariant error"},
            ]
        }
    )

    assert metrics["errors"] == 2
    assert metrics["paper_pipeline_errors"] == 1
    assert "synthetic" not in json.dumps(metrics)


def test_farm_metrics_exposes_delivery_analysis_and_memory_aggregates() -> None:
    metrics = farm_metrics(
        {
            "paper_telegram_delivery": {
                "external_ack_ambiguous_messages": 3,
                "external_ack_ambiguous_current_attempts": 1,
                "external_ack_ambiguous_carried": 2,
            },
            "paper_telegram_preview": {
                "analysis_llm_linked": 4,
                "analysis_template": 5,
                "analysis_fallback": 6,
            },
            "calculator_advisor": {"processed": 7, "accepted": 4, "blocked": 3},
            "agent_role_reviews": {"reviews": 8, "accepted": 7, "rejected": 1},
            "system_analyst_feedback": {"feedback_candidates": 9, "routed": 8},
            "setup_outcome_memory_refresh": {
                "product_rows": 10,
                "product_terminal_rows": 2,
                "reject_characterization": {
                    "cache_hits": 26,
                    "snapshot_bootstrap_hits": 27,
                    "recomputed": 3,
                    "run_artifacts_reread": 1,
                    "run_artifacts_unavailable": 2,
                },
            },
            "runtime_storage_maintenance": {"state": "ready"},
            "mandatory_product_cycle_complete": True,
            "product_cycle_complete": True,
        }
    )

    assert metrics["delivery_ack_ambiguous"] == 3
    assert metrics["delivery_ack_ambiguous_current"] == 1
    assert metrics["delivery_ack_ambiguous_carried"] == 2
    assert metrics["analysis_llm_linked"] == 4
    assert metrics["analysis_template"] == 5
    assert metrics["analysis_fallback"] == 6
    assert metrics["calculator_processed"] == 7
    assert metrics["calculator_accepted"] == 4
    assert metrics["calculator_blocked"] == 3
    assert metrics["role_reviews_requested"] == 8
    assert metrics["role_reviews_accepted"] == 7
    assert metrics["role_reviews_rejected"] == 1
    assert metrics["analyst_feedback_candidates"] == 9
    assert metrics["analyst_routed"] == 8
    assert metrics["memory_rows"] == 10
    assert metrics["memory_terminal_rows"] == 2
    assert metrics["memory_reject_cache_hits"] == 26
    assert metrics["memory_reject_snapshot_bootstrap_hits"] == 27
    assert metrics["memory_reject_recomputed"] == 3
    assert metrics["memory_run_artifacts_reread"] == 1
    assert metrics["memory_run_artifacts_unavailable"] == 2
    assert metrics["storage_maintenance_state"] == "ready"
    assert metrics["mandatory_product_cycle_complete"] is True
    assert metrics["product_cycle_complete"] is True


def test_product_monitor_waits_for_mandatory_product_cycle_checkpoint(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path)
    farm_path = tmp_path / "state" / "product_progress" / "farm.json"
    payload = json.loads(farm_path.read_text(encoding="utf-8"))
    payload["metrics"]["mandatory_product_cycle_complete"] = False
    payload["metrics"]["product_cycle_complete"] = False
    farm_path.write_text(json.dumps(payload), encoding="utf-8")

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()

    assert report["state"] == "starting"
    assert report["ready"] is False
    assert report["hard_fail_reasons"] == []


def test_optional_advisory_work_does_not_block_mandatory_t0(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path)
    farm_path = tmp_path / "state" / "product_progress" / "farm.json"
    payload = json.loads(farm_path.read_text(encoding="utf-8"))
    payload["metrics"]["mandatory_product_cycle_complete"] = True
    payload["metrics"]["product_cycle_complete"] = False
    payload["metrics"]["calculator_processed"] = 0
    payload["metrics"]["role_reviews_requested"] = 0
    farm_path.write_text(json.dumps(payload), encoding="utf-8")

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()

    assert report["ready"] is True
    assert report["hard_fail_reasons"] == []


def test_product_monitor_fails_on_current_delivery_ambiguity_not_carried_debt(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path)
    farm_path = tmp_path / "state" / "product_progress" / "farm.json"
    payload = json.loads(farm_path.read_text(encoding="utf-8"))
    payload["metrics"].update(
        {
            "delivery_ack_ambiguous_current": 1,
            "delivery_ack_ambiguous_carried": 2,
        }
    )
    farm_path.write_text(json.dumps(payload), encoding="utf-8")

    failed = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()
    assert failed["state"] == "failed"
    assert failed["hard_fail_reasons"] == ["telegram_delivery_ack_ambiguous"]

    payload["metrics"]["delivery_ack_ambiguous_current"] = 0
    farm_path.write_text(json.dumps(payload), encoding="utf-8")
    carried_only = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()
    assert carried_only["state"] == "ready"
    assert carried_only["hard_fail_reasons"] == []


def test_product_monitor_reports_advisory_degradation_without_hard_fail(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path)
    farm_path = tmp_path / "state" / "product_progress" / "farm.json"
    payload = json.loads(farm_path.read_text(encoding="utf-8"))
    payload["metrics"].update(
        {"calculator_blocked": 1, "role_reviews_rejected": 2}
    )
    farm_path.write_text(json.dumps(payload), encoding="utf-8")

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()

    assert report["state"] == "degraded"
    assert report["ready"] is True
    assert report["hard_fail_reasons"] == []
    assert report["degraded_reasons"] == [
        "calculator_advisory_degraded",
        "agent_role_review_degraded",
    ]


def test_research_cards_without_validated_setups_are_reported_honestly(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path)
    farm_path = tmp_path / "state" / "product_progress" / "farm.json"
    payload = json.loads(farm_path.read_text(encoding="utf-8"))
    payload["metrics"].update(
        {
            "preview_rendered": 10,
            "research_observation_cards": 10,
            "validated_setup_cards": 0,
            "analysis_llm_linked": 0,
            "analysis_template": 10,
            "analyst_input_rows": 0,
        }
    )
    farm_path.write_text(json.dumps(payload), encoding="utf-8")

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()

    assert report["ready"] is True
    assert report["state"] == "degraded"
    assert report["hard_fail_reasons"] == []
    assert report["degraded_reasons"] == ["no_current_validated_paper_setup"]


def test_bounded_scanner_backlog_is_visible_degraded_progress(tmp_path: Path) -> None:
    _publish_green(tmp_path)
    publish_checkpoint(
        tmp_path,
        component="scanner",
        sequence=2,
        status="degraded",
        metrics=scanner_metrics(
            inputs=20,
            fresh=0,
            cards=0,
            dropped=0,
            llm_failures=0,
            provider_failures=0,
            budget_exhausted=True,
            resolver_deferred=7,
            completed_chunks=4,
            pass_elapsed_seconds=180.0,
        ),
        completed_at=102.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 103.0,
    ).sample()

    assert report["ready"] is True
    assert report["state"] == "degraded"
    assert report["hard_fail_reasons"] == []
    assert report["degraded_reasons"] == ["scanner_bounded_work_deferred"]


def test_scanner_chunk_progress_does_not_replace_completed_pass(tmp_path: Path) -> None:
    publish_checkpoint(
        tmp_path,
        component="scanner_progress",
        sequence=5,
        status="progress",
        metrics={"stage": "resolver_document_completed", "completed_chunks": 5},
        completed_at=109.0,
    )
    publish_checkpoint(
        tmp_path,
        component="farm",
        sequence=1,
        status="completed",
        metrics={"generation_consistent": True},
        completed_at=101.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        slo=ProductProgressSlo(scanner_seconds=10.0, farm_seconds=300.0),
        wall_clock=lambda: 111.0,
    ).sample()

    assert report["ready"] is False
    assert report["state"] == "failed"
    assert report["hard_fail_reasons"] == [
        "scanner_product_progress_startup_timeout"
    ]


def test_product_monitor_hard_fails_completed_paper_pipeline_cycle_error(
    tmp_path: Path,
) -> None:
    _publish_green(tmp_path)
    publish_checkpoint(
        tmp_path,
        component="farm",
        sequence=2,
        status="degraded",
        metrics={
            "paper_generation_waiting": False,
            "generation_consistent": True,
            "paper_pipeline_errors": 1,
            "operational_rows_retained": 0,
            "validation_oldest_age_seconds": 0.0,
            "validation_backlog_slo_seconds": 3600.0,
        },
        completed_at=102.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 103.0,
    ).sample()

    assert report["ready"] is False
    assert report["state"] == "failed"
    assert report["hard_fail_reasons"] == ["paper_pipeline_cycle_failed"]


def test_code_stale_validation_generation_fails_closed(
    tmp_path: Path,
) -> None:
    publish_checkpoint(
        tmp_path,
        component="scanner",
        sequence=1,
        status="idle",
        metrics=scanner_metrics(
            inputs=0,
            fresh=0,
            cards=0,
            dropped=0,
            llm_failures=0,
            provider_failures=0,
        ),
        completed_at=101.0,
    )
    metrics = farm_metrics(
        {
            "paper_generation_v2": {
                "state": "waiting_validation_generation",
                "validation_generation_status": "code_stale",
                "run_id": "",
            }
        }
    )
    publish_checkpoint(
        tmp_path,
        component="farm",
        sequence=1,
        status="waiting",
        metrics=metrics,
        completed_at=101.0,
    )

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 102.0,
    ).sample()

    assert report["ready"] is False
    assert report["state"] == "failed"
    assert report["hard_fail_reasons"] == ["validation_generation_code_stale"]
    assert metrics["paper_generation_waiting"] is True
    assert metrics["validation_generation_status"] == "code_stale"


def _publish_code_stale_successor_build(
    root: Path,
    *,
    build_code_status: str = "code_current",
    build_started_at: float = 101.0,
    progress_stage: str = "validation_maintenance",
    progress_at: float = 102.5,
) -> None:
    publish_checkpoint(
        root,
        component="scanner",
        sequence=1,
        status="idle",
        metrics=scanner_metrics(
            inputs=0,
            fresh=0,
            cards=0,
            dropped=0,
            llm_failures=0,
            provider_failures=0,
        ),
        completed_at=101.0,
    )
    metrics = farm_metrics(
        {
            "validation_backlog": {
                "active": 6243,
                "eligible": 6241,
                "oldest_age_seconds": 1_332_669.0,
                "backlog_slo_seconds": 3600.0,
            },
            "paper_generation_v2": {
                "state": "waiting_validation_generation",
                "validation_generation_status": "code_stale",
                "validation_generation_started_at": build_started_at,
                "run_id": "",
            },
            "validation_generation_build": {
                "active": True,
                "started_at": build_started_at,
                "code_status": build_code_status,
            },
            "mandatory_product_cycle_complete": True,
        }
    )
    publish_checkpoint(
        root,
        component="farm",
        sequence=1,
        status="waiting",
        metrics=metrics,
        completed_at=102.0,
    )
    publish_checkpoint(
        root,
        component="validation_progress",
        sequence=1,
        status="progress",
        metrics={
            "stage": progress_stage,
            "milestone": "requests_exported",
            "completed": 2,
            "total": 2,
        },
        completed_at=progress_at,
    )


def test_code_stale_generation_allows_exact_current_bounded_successor_build(
    tmp_path: Path,
) -> None:
    _publish_code_stale_successor_build(tmp_path, progress_at=333.0)

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 334.0,
    ).sample()

    assert report["ready"] is False
    assert report["hard_fail_reasons"] == []
    assert "validation_generation_rebuild_in_progress" in report[
        "degraded_reasons"
    ]
    assert report["components"]["validation_progress"][
        "build_liveness_eligible"
    ] is True


def test_code_stale_generation_rejects_successor_from_another_revision(
    tmp_path: Path,
) -> None:
    _publish_code_stale_successor_build(tmp_path, build_code_status="code_stale")

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 103.0,
    ).sample()

    assert "validation_generation_successor_not_current" in report[
        "hard_fail_reasons"
    ]


def test_code_stale_generation_rejects_wrong_or_stale_build_progress(
    tmp_path: Path,
) -> None:
    _publish_code_stale_successor_build(
        tmp_path,
        progress_stage="setup_outcome_memory_refresh",
    )
    wrong_stage = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 103.0,
    ).sample()
    assert "validation_generation_build_progress_stalled" in wrong_stage[
        "hard_fail_reasons"
    ]

    _publish_code_stale_successor_build(tmp_path, progress_at=102.0)
    stale = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 163.0,
    ).sample()
    assert "validation_generation_build_progress_stalled" in stale[
        "hard_fail_reasons"
    ]


def test_code_stale_generation_rejects_pre_run_successor_marker(
    tmp_path: Path,
) -> None:
    _publish_code_stale_successor_build(tmp_path, build_started_at=99.0)

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        wall_clock=lambda: 103.0,
    ).sample()

    assert "validation_generation_successor_not_current_run" in report[
        "hard_fail_reasons"
    ]


def test_checkpoint_payload_contains_only_safe_aggregates(tmp_path: Path) -> None:
    _publish_green(tmp_path)
    payload = json.loads(
        (tmp_path / "state" / "product_progress" / "scanner.json").read_text(
            encoding="utf-8"
        )
    )

    serialized = json.dumps(payload, sort_keys=True)
    assert "recipient" not in serialized.lower()
    assert "token" not in serialized.lower()
    assert "path" not in serialized.lower()
    assert payload["execution_allowed"] is False
