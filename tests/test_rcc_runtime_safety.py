from __future__ import annotations

from collections.abc import Mapping

import pytest

from src.research_lab.canary_checkpoint_policy import CanaryMonitorHardFailure
from src.research_lab.ownership import ProcessIdentity
from src.research_lab.rcc_runtime_safety import CanonicalOwnerSafetyMonitor


IDENTITY = ProcessIdentity(
    pid=401,
    started_at=1_700_000_000.0,
    executable="python.exe",
    command_digest="sha256:farm",
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
