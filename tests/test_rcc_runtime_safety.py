from __future__ import annotations

from collections.abc import Mapping

import pytest

from src.research_lab.canary_checkpoint_policy import CanaryMonitorHardFailure
from src.research_lab.ownership import ProcessIdentity
from src.research_lab.rcc_runtime_safety import (
    CanonicalOwnerSafetyMonitor,
    decide_rcc_finalizer_action,
    parse_rcc_heartbeat_process_identity,
    verify_rcc_heartbeat_process_identity,
)
from src.research_lab.rcc_startup_evidence import RccRunIdentity


IDENTITY = ProcessIdentity(
    pid=401,
    started_at=1_700_000_000.0,
    executable="python.exe",
    command_digest="sha256:farm",
)
RCC_RUN = RccRunIdentity(
    attempt_id="rccstartup_" + "a" * 32,
    revision="a" * 40,
    pid=IDENTITY.pid,
    process_started_at=IDENTITY.started_at,
)


def _rcc_heartbeat(
    *,
    pid: int = IDENTITY.pid,
    started_at: float = IDENTITY.started_at,
    shutdown_state: str = "running",
    rcc_run: RccRunIdentity = RCC_RUN,
    updated_at: float = 1_700_000_001.0,
) -> Mapping[str, object]:
    return {
        "schema": "ResearchControlCenterHeartbeat.v4",
        "pid": pid,
        "started_at": started_at,
        "updated_at": updated_at,
        "rcc_run": rcc_run.to_payload(),
        "paper_only": True,
        "execution_allowed": False,
        "shutdown": {
            "state": shutdown_state,
            "reason_code": (
                "runtime_hard_fail" if shutdown_state != "running" else None
            ),
            "started_at": (
                1_700_000_100.0 if shutdown_state != "running" else None
            ),
        },
    }


def test_rcc_heartbeat_identity_requires_exact_pid_and_process_start() -> None:
    parsed = parse_rcc_heartbeat_process_identity(_rcc_heartbeat())

    assert parsed.pid == IDENTITY.pid
    assert parsed.started_at == IDENTITY.started_at
    assert (
        verify_rcc_heartbeat_process_identity(
            _rcc_heartbeat(),
            identity_probe=lambda _pid: IDENTITY,
        )
        == IDENTITY
    )


@pytest.mark.parametrize(
    ("heartbeat", "reason"),
    [
        (
            {
                "schema": "ResearchControlCenterHeartbeat.v2",
                "pid": IDENTITY.pid,
                "paper_only": True,
                "execution_allowed": False,
            },
            "schema_mismatch",
        ),
        (
            {
                "schema": "ResearchControlCenterHeartbeat.v4",
                "pid": IDENTITY.pid,
                "paper_only": True,
                "execution_allowed": False,
                "rcc_run": RCC_RUN.to_payload(),
                "updated_at": 1_700_000_001.0,
            },
            "start_missing",
        ),
        (
            {
                "schema": "ResearchControlCenterHeartbeat.v4",
                "pid": IDENTITY.pid,
                "started_at": IDENTITY.started_at,
                "paper_only": True,
                "execution_allowed": True,
                "rcc_run": RCC_RUN.to_payload(),
                "updated_at": 1_700_000_001.0,
            },
            "execution_boundary_missing",
        ),
    ],
)
def test_rcc_heartbeat_identity_missing_or_unsafe_fields_fail_closed(
    heartbeat: Mapping[str, object],
    reason: str,
) -> None:
    with pytest.raises(CanaryMonitorHardFailure, match=reason):
        parse_rcc_heartbeat_process_identity(heartbeat)


def test_rcc_heartbeat_identity_does_not_fall_back_to_reused_pid() -> None:
    replacement = ProcessIdentity(
        pid=IDENTITY.pid,
        started_at=IDENTITY.started_at + 0.001,
        executable=IDENTITY.executable,
        command_digest=IDENTITY.command_digest,
    )

    with pytest.raises(CanaryMonitorHardFailure, match="generation_mismatch"):
        verify_rcc_heartbeat_process_identity(
            _rcc_heartbeat(),
            identity_probe=lambda _pid: replacement,
        )


def test_rcc_heartbeat_identity_rejects_process_disappearance() -> None:
    with pytest.raises(CanaryMonitorHardFailure, match="process_not_live"):
        verify_rcc_heartbeat_process_identity(
            _rcc_heartbeat(),
            identity_probe=lambda _pid: None,
        )


def test_rcc_heartbeat_identity_rejects_another_startup_attempt() -> None:
    other = RccRunIdentity(
        attempt_id="rccstartup_" + "b" * 32,
        revision=RCC_RUN.revision,
        pid=IDENTITY.pid,
        process_started_at=IDENTITY.started_at,
    )

    with pytest.raises(CanaryMonitorHardFailure, match="attempt_mismatch"):
        verify_rcc_heartbeat_process_identity(
            _rcc_heartbeat(rcc_run=other),
            identity_probe=lambda _pid: IDENTITY,
            expected_run=RCC_RUN,
        )


def test_rcc_heartbeat_identity_rejects_stale_current_attempt() -> None:
    with pytest.raises(CanaryMonitorHardFailure, match="rcc_heartbeat_identity:stale"):
        verify_rcc_heartbeat_process_identity(
            _rcc_heartbeat(updated_at=1_700_000_001.0),
            identity_probe=lambda _pid: IDENTITY,
            expected_run=RCC_RUN,
            now=1_700_000_100.0,
            max_age_seconds=5.0,
        )


def test_finalizer_requests_stop_only_for_exact_running_rcc_generation() -> None:
    result = decide_rcc_finalizer_action(
        _rcc_heartbeat(),
        identity_probe=lambda _pid: IDENTITY,
    )

    assert result.action == "request_graceful_stop"
    assert result.shutdown_state == "running"
    assert result.process_identity == IDENTITY
    assert result.reason_code is None


def test_finalizer_waits_when_internal_hard_fail_shutdown_is_in_progress() -> None:
    result = decide_rcc_finalizer_action(
        _rcc_heartbeat(shutdown_state="stopping"),
        identity_probe=lambda _pid: IDENTITY,
    )

    assert result.action == "wait_for_quiescence"
    assert result.shutdown_state == "stopping"
    assert result.reason_code == "runtime_hard_fail"


def test_finalizer_fails_closed_on_reported_stop_failure() -> None:
    result = decide_rcc_finalizer_action(
        _rcc_heartbeat(shutdown_state="stop_failed"),
        identity_probe=lambda _pid: IDENTITY,
    )

    assert result.action == "fail_closed"
    assert result.shutdown_state == "stop_failed"


@pytest.mark.parametrize(
    ("shutdown", "reason"),
    [
        (None, "shutdown_state_missing"),
        ({"state": "unknown"}, "shutdown_state_invalid"),
        (
            {"state": "running", "started_at": 1.0},
            "running_shutdown_timestamp_present",
        ),
        ({"state": "stopping", "started_at": None}, "shutdown_timestamp_missing"),
    ],
)
def test_finalizer_rejects_ambiguous_shutdown_contract(
    shutdown: object,
    reason: str,
) -> None:
    heartbeat = dict(_rcc_heartbeat())
    heartbeat["shutdown"] = shutdown

    with pytest.raises(CanaryMonitorHardFailure, match=reason):
        decide_rcc_finalizer_action(
            heartbeat,
            identity_probe=lambda _pid: IDENTITY,
        )


def test_finalizer_does_not_accept_shutdown_state_from_reused_pid() -> None:
    replacement = ProcessIdentity(
        pid=IDENTITY.pid,
        started_at=IDENTITY.started_at + 1.0,
        executable=IDENTITY.executable,
        command_digest=IDENTITY.command_digest,
    )

    with pytest.raises(CanaryMonitorHardFailure, match="generation_mismatch"):
        decide_rcc_finalizer_action(
            _rcc_heartbeat(shutdown_state="stopping"),
            identity_probe=lambda _pid: replacement,
        )


def _row(
    *,
    resource_id: str = "canonical_farm",
    role_id: str = "farm",
    owner_id: str = "farm-owner-1",
    identity: ProcessIdentity = IDENTITY,
    fence: int = 38,
    expires_at: float = 1_100.0,
) -> Mapping[str, object]:
    return {
        "resource_id": resource_id,
        "role_id": role_id,
        "owner_id": owner_id,
        "pid": identity.pid,
        "started_at": identity.started_at,
        "executable": identity.executable,
        "command_digest": identity.command_digest,
        "lease_expires_at": expires_at,
        "next_fence": fence,
    }


def test_owner_absence_is_starting_until_bounded_deadline() -> None:
    monotonic = [10.0]
    monitor = CanonicalOwnerSafetyMonitor(
        rows_reader=lambda: (),
        identity_probe=lambda _pid: None,
        startup_budget_seconds=600.0,
        monotonic=lambda: monotonic[0],
        wall_clock=lambda: 1_000.0,
    )

    assert monitor.sample().state == "process_starting"
    monotonic[0] = 610.0

    with pytest.raises(CanaryMonitorHardFailure, match="owner_startup_timeout"):
        monitor.sample()


def test_nested_resource_same_identity_is_one_ready_authority() -> None:
    rows = [
        _row(),
        _row(
            resource_id="strategy_lab_worker",
            role_id="compute_worker",
            fence=12,
        ),
    ]
    monitor = CanonicalOwnerSafetyMonitor(
        rows_reader=lambda: rows,
        identity_probe=lambda _pid: IDENTITY,
        wall_clock=lambda: 1_000.0,
    )

    sample = monitor.sample()

    assert sample.ready is True
    assert sample.owner_id == "farm-owner-1"
    assert sample.canonical_fence == 38
    assert sample.resources == ("canonical_farm", "strategy_lab_worker")


def test_authority_loss_after_readiness_is_immediate_hard_failure() -> None:
    rows = [_row()]
    monitor = CanonicalOwnerSafetyMonitor(
        rows_reader=lambda: rows,
        identity_probe=lambda _pid: IDENTITY,
        wall_clock=lambda: 1_000.0,
    )
    assert monitor.sample().ready is True
    rows.clear()

    with pytest.raises(
        CanaryMonitorHardFailure,
        match="owner_authority:canonical_owner_missing",
    ):
        monitor.sample()


def test_different_process_authority_fails_closed() -> None:
    second = ProcessIdentity(
        pid=402,
        started_at=1_700_000_100.0,
        executable="python.exe",
        command_digest="sha256:worker",
    )
    rows = [
        _row(),
        _row(
            resource_id="strategy_lab_worker",
            role_id="compute_worker",
            identity=second,
            fence=12,
        ),
    ]
    identities = {IDENTITY.pid: IDENTITY, second.pid: second}
    monitor = CanonicalOwnerSafetyMonitor(
        rows_reader=lambda: rows,
        identity_probe=lambda pid: identities[pid],
        wall_clock=lambda: 1_000.0,
    )

    with pytest.raises(CanaryMonitorHardFailure, match="distinct_process_authority"):
        monitor.sample()


def test_fence_change_after_readiness_fails_closed() -> None:
    rows = [_row()]
    monitor = CanonicalOwnerSafetyMonitor(
        rows_reader=lambda: rows,
        identity_probe=lambda _pid: IDENTITY,
        wall_clock=lambda: 1_000.0,
    )
    assert monitor.sample().ready is True
    rows[0] = _row(fence=39)

    with pytest.raises(CanaryMonitorHardFailure, match="generation_changed"):
        monitor.sample()


def test_nested_worker_can_appear_release_and_reacquire_on_same_identity() -> None:
    rows = [_row()]
    monitor = CanonicalOwnerSafetyMonitor(
        rows_reader=lambda: rows,
        identity_probe=lambda _pid: IDENTITY,
        wall_clock=lambda: 1_000.0,
    )
    assert monitor.sample().ready is True
    rows.append(
        _row(
            resource_id="strategy_lab_worker",
            role_id="compute_worker",
            fence=12,
        )
    )
    assert monitor.sample().ready is True
    rows.pop()
    assert monitor.sample().ready is True
    rows.append(
        _row(
            resource_id="strategy_lab_worker",
            role_id="compute_worker",
            fence=13,
        )
    )

    assert monitor.sample().ready is True


def test_process_generation_change_with_reused_owner_and_fence_fails_closed() -> None:
    rows = [_row()]
    current_identity = [IDENTITY]
    monitor = CanonicalOwnerSafetyMonitor(
        rows_reader=lambda: rows,
        identity_probe=lambda _pid: current_identity[0],
        wall_clock=lambda: 1_000.0,
    )
    assert monitor.sample().ready is True
    replacement = ProcessIdentity(
        pid=IDENTITY.pid,
        started_at=IDENTITY.started_at + 10.0,
        executable=IDENTITY.executable,
        command_digest=IDENTITY.command_digest,
    )
    current_identity[0] = replacement
    rows[0] = _row(identity=replacement)

    with pytest.raises(CanaryMonitorHardFailure, match="process_generation_changed"):
        monitor.sample()


def test_expired_historical_row_does_not_become_startup_authority() -> None:
    monitor = CanonicalOwnerSafetyMonitor(
        rows_reader=lambda: [_row(expires_at=999.0)],
        identity_probe=lambda _pid: IDENTITY,
        wall_clock=lambda: 1_000.0,
    )

    sample = monitor.sample()

    assert sample.ready is False
    assert sample.state == "process_starting"
