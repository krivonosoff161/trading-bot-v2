from __future__ import annotations

from src.research_lab.compute_pipeline_health import assess_compute_pipeline


def test_active_worker_failure_is_hard_fail_without_identity_payload() -> None:
    health = assess_compute_pipeline(
        priority_status={
            "stage": "worker_failed",
            "updated_at": 100.0,
            "details": {"owner_id": "must-not-propagate"},
        },
        worker_status={
            "status": "failed",
            "reason_code": "expired_alive_conflict",
            "updated_at": "1970-01-01T00:01:40+00:00",
        },
        farm_running=True,
        now=101.0,
    )

    assert health["state"] == "failed"
    assert health["hard_fail"] is True
    assert health["reason"] == "priority_worker_failed"
    assert "owner_id" not in health
    assert health["execution_allowed"] is False


def test_stale_failure_from_stopped_run_does_not_grant_current_hard_fail() -> None:
    health = assess_compute_pipeline(
        priority_status={"stage": "worker_failed", "updated_at": 100.0},
        worker_status={
            "status": "failed",
            "reason_code": "expired_alive_conflict",
            "updated_at": "1970-01-01T00:01:40+00:00",
        },
        farm_running=False,
        now=10_000.0,
    )

    assert health["state"] == "stopped"
    assert health["hard_fail"] is False
    assert health["reason"] == "farm_not_running"


def test_stale_failure_before_current_farm_start_does_not_false_fail() -> None:
    health = assess_compute_pipeline(
        priority_status={"stage": "worker_failed", "updated_at": 100.0},
        worker_status={
            "status": "failed",
            "reason_code": "expired_alive_conflict",
            "updated_at": 100.0,
        },
        farm_running=True,
        farm_started_at=200.0,
        now=201.0,
    )

    assert health["state"] == "starting"
    assert health["reason"] == "stale_failure_artifact"
    assert health["hard_fail"] is False


def test_priority_worker_reports_working_and_idle_without_false_failure() -> None:
    working = assess_compute_pipeline(
        priority_status={"stage": "running_slot", "updated_at": 100.0},
        worker_status={"status": "running", "updated_at": 100.0},
        farm_running=True,
        now=105.0,
    )
    idle = assess_compute_pipeline(
        priority_status={"stage": "idle", "updated_at": 110.0},
        worker_status={"status": "queue_empty", "updated_at": 110.0},
        farm_running=True,
        now=111.0,
    )

    assert working["state"] == "working"
    assert working["hard_fail"] is False
    assert idle["state"] == "idle"
    assert idle["hard_fail"] is False


def test_missing_priority_status_is_starting_not_healthy() -> None:
    health = assess_compute_pipeline(
        priority_status={},
        worker_status={},
        farm_running=True,
        now=100.0,
    )

    assert health["state"] == "starting"
    assert health["reason"] == "priority_status_pending"
    assert health["hard_fail"] is False


def test_untrusted_status_strings_are_not_forwarded_to_operator_surfaces() -> None:
    health = assess_compute_pipeline(
        priority_status={"stage": "synthetic-sensitive-value", "updated_at": 100.0},
        worker_status={
            "status": "failed",
            "reason_code": "synthetic-sensitive-value",
            "updated_at": 100.0,
        },
        farm_running=True,
        now=101.0,
    )

    assert health["hard_fail"] is True
    assert health["priority_stage"] == "unknown"
    assert health["worker_reason_code"] == "unclassified_failure"
    assert "synthetic-sensitive-value" not in str(health)
