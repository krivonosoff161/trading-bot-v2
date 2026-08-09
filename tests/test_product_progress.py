import json
from pathlib import Path

import pytest

from src.research_lab.product_progress import (
    ProductProgressMonitor,
    ProductProgressSlo,
    ProductProgressTransitionError,
    farm_metrics,
    publish_checkpoint,
    scanner_metrics,
)


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
        "validation_backlog_slo_exceeded",
        "paper_generation_stage_mismatch",
        "technical_outcome_entered_training",
    }


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


def test_validation_generation_waiting_is_starting_not_stage_mismatch(
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
    assert report["state"] == "starting"
    assert report["hard_fail_reasons"] == []
    assert metrics["paper_generation_waiting"] is True
    assert metrics["validation_generation_status"] == "code_stale"


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
