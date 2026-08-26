"""Synthetic run-bound lifecycle proof for the canonical paper product path.

No provider, Telegram client, Ollama process, runtime database, or real
recipient is created here.  The test composes the same public-safe handoff
contracts used by the canonical launcher and its child processes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.research_lab.canary_checkpoint_policy import CanaryMonitorHardFailure
from src.research_lab.farm_tasks_db import FarmTasksDB, StaleTaskClaimError
from src.research_lab.ownership import ProcessIdentity
from src.research_lab.product_progress import (
    ProductProgressMonitor,
    ProductProgressSlo,
    publish_checkpoint,
    scanner_metrics,
)
from src.research_lab.rcc_runtime_safety import (
    CanonicalOwnerSafetyMonitor,
    decide_rcc_finalizer_action,
    verify_rcc_heartbeat_process_identity,
)
from src.research_lab.rcc_startup_evidence import RccRunIdentity


def _run(letter: str, *, pid: int = 4100) -> RccRunIdentity:
    return RccRunIdentity(
        attempt_id="rccstartup_" + letter * 32,
        revision=letter * 40,
        pid=pid,
        process_started_at=100.0,
    )


def _identity(run: RccRunIdentity) -> ProcessIdentity:
    return ProcessIdentity(
        pid=run.pid,
        started_at=run.process_started_at,
        executable="python.exe",
        command_digest="sha256:synthetic-rcc",
    )


def _heartbeat(
    run: RccRunIdentity,
    *,
    updated_at: float = 101.0,
    shutdown_state: str = "running",
) -> dict[str, object]:
    return {
        "schema": "ResearchControlCenterHeartbeat.v4",
        "updated_at": updated_at,
        "pid": run.pid,
        "started_at": run.process_started_at,
        "rcc_run": run.to_payload(),
        "paper_only": True,
        "execution_allowed": False,
        "shutdown": {
            "state": shutdown_state,
            "reason_code": "runtime_hard_fail"
            if shutdown_state != "running"
            else None,
            "started_at": 102.0 if shutdown_state != "running" else None,
        },
    }


def _publish_completed_cycle(
    root: Path,
    run: RccRunIdentity,
    *,
    analysis_fallback: int = 0,
    delivery_ack_ambiguous_current: int = 0,
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
        rcc_run=run,
    )
    publish_checkpoint(
        root,
        component="farm",
        sequence=1,
        status="completed",
        metrics={
            "generation_consistent": True,
            "mandatory_product_cycle_complete": True,
            "operational_rows_retained": 0,
            "analysis_fallback": analysis_fallback,
            "delivery_ack_ambiguous_current": delivery_ack_ambiguous_current,
        },
        completed_at=101.0,
        rcc_run=run,
    )


def test_synthetic_e2e_no_candidate_cycle_reaches_t0_with_exact_run_and_llm_fallback(
    tmp_path: Path,
) -> None:
    run = _run("a")
    _publish_completed_cycle(tmp_path, run, analysis_fallback=1)

    assert (
        verify_rcc_heartbeat_process_identity(
            _heartbeat(run),
            identity_probe=lambda _pid: _identity(run),
            expected_run=run,
            now=102.0,
            max_age_seconds=5.0,
        )
        == _identity(run)
    )
    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        expected_rcc_run=run,
        wall_clock=lambda: 102.0,
    ).sample()

    assert report["ready"] is True
    assert report["components"]["scanner"]["run_binding"] == "match"
    assert report["components"]["farm"]["run_binding"] == "match"
    assert "validated_card_llm_advisory_unavailable" in report["degraded_reasons"]


def test_synthetic_e2e_cold_or_stale_state_never_establishes_current_t0(
    tmp_path: Path,
) -> None:
    current_run = _run("b")
    prior_run = _run("c", pid=4200)
    _publish_completed_cycle(tmp_path, prior_run)

    initial = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        expected_rcc_run=current_run,
        wall_clock=lambda: 101.0,
    ).sample()
    expired = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        expected_rcc_run=current_run,
        wall_clock=lambda: 1001.0,
    ).sample()

    assert initial["ready"] is False
    assert initial["state"] == "starting"
    assert expired["ready"] is False
    assert "scanner_product_progress_run_binding_mismatch" in expired[
        "hard_fail_reasons"
    ]
    assert "farm_product_progress_run_binding_mismatch" in expired[
        "hard_fail_reasons"
    ]


def test_synthetic_e2e_rejects_stale_heartbeat_and_waits_for_internal_stop() -> None:
    run = _run("d")
    prior_run = _run("e", pid=4200)

    with pytest.raises(CanaryMonitorHardFailure, match="attempt_mismatch"):
        verify_rcc_heartbeat_process_identity(
            _heartbeat(prior_run),
            identity_probe=lambda _pid: _identity(prior_run),
            expected_run=run,
        )
    with pytest.raises(CanaryMonitorHardFailure, match="rcc_heartbeat_identity:stale"):
        verify_rcc_heartbeat_process_identity(
            _heartbeat(run, updated_at=101.0),
            identity_probe=lambda _pid: _identity(run),
            expected_run=run,
            now=120.0,
            max_age_seconds=5.0,
        )

    decision = decide_rcc_finalizer_action(
        _heartbeat(run, shutdown_state="stopping"),
        identity_probe=lambda _pid: _identity(run),
    )
    assert decision.action == "wait_for_quiescence"


def test_synthetic_e2e_delivery_ambiguity_and_owner_fence_loss_remain_hard_failures(
    tmp_path: Path,
) -> None:
    run = _run("f")
    _publish_completed_cycle(tmp_path, run, delivery_ack_ambiguous_current=1)
    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        expected_rcc_run=run,
        wall_clock=lambda: 102.0,
    ).sample()
    assert "telegram_delivery_ack_ambiguous" in report["hard_fail_reasons"]

    rows = [
        {
            "resource_id": "canonical_farm",
            "role_id": "farm",
            "owner_id": "synthetic-owner",
            "pid": run.pid,
            "started_at": run.process_started_at,
            "executable": "python.exe",
            "command_digest": "sha256:synthetic-rcc",
            "lease_expires_at": 1_100.0,
            "next_fence": 7,
        }
    ]
    wall_time = [1_000.0]
    monitor = CanonicalOwnerSafetyMonitor(
        rows_reader=lambda: tuple(rows),
        identity_probe=lambda _pid: _identity(run),
        wall_clock=lambda: wall_time[0],
        monotonic=lambda: wall_time[0],
    )
    assert monitor.sample().ready is True
    rows[0] = {**rows[0], "next_fence": 8}
    with pytest.raises(CanaryMonitorHardFailure, match="canonical_generation_changed"):
        monitor.sample()


def test_synthetic_e2e_stale_claim_cannot_complete_a_new_run_cycle(
    tmp_path: Path,
) -> None:
    """A pre-restart task claim cannot create a current run side effect."""

    now = [100.0]
    tasks = FarmTasksDB(
        tmp_path / "synthetic_farm.sqlite",
        owner_id="synthetic-owner",
        lease_seconds=5.0,
        clock=lambda: now[0],
    )
    try:
        task_id, _ = tasks.enqueue_task(
            task_type="run_sweep", task_key="synthetic-current", now=now[0]
        )
        assert tasks.claim_next_task(now=now[0]) is not None
        now[0] = 106.0
        with pytest.raises(StaleTaskClaimError):
            tasks.complete_task(task_id, now=now[0])
    finally:
        tasks.close()

    report = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        expected_rcc_run=_run("b"),
        wall_clock=lambda: 106.0,
    ).sample()
    assert report["ready"] is False
    assert report["components"]["farm"]["sequence"] == 0


def test_synthetic_e2e_missing_current_checkpoint_stays_bounded_before_t0_timeout(
    tmp_path: Path,
) -> None:
    run = _run("a", pid=4300)
    slo = ProductProgressSlo(scanner_seconds=20.0, farm_seconds=20.0)

    before_deadline = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        expected_rcc_run=run,
        slo=slo,
        wall_clock=lambda: 110.0,
    ).sample()
    scanner_deadline = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        expected_rcc_run=run,
        slo=slo,
        wall_clock=lambda: 121.0,
    ).sample()
    farm_deadline = ProductProgressMonitor(
        tmp_path,
        run_started_at=100.0,
        expected_rcc_run=run,
        slo=slo,
        wall_clock=lambda: 701.0,
    ).sample()

    assert before_deadline["state"] == "starting"
    assert before_deadline["hard_fail_reasons"] == []
    assert "scanner_product_progress_startup_timeout" in scanner_deadline[
        "hard_fail_reasons"
    ]
    assert "farm_product_progress_startup_timeout" in farm_deadline[
        "hard_fail_reasons"
    ]
