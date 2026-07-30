from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from src.research_lab.ownership import (
    OwnershipConflictError,
    OwnershipStore,
    ProcessIdentity,
)
from src.research_lab.process_lease_heartbeat import (
    ProcessLeaseHeartbeat,
    ProcessLeaseHeartbeatLifecycleError,
    ProcessLeaseRenewalBudgetExceeded,
)


IDENTITY = ProcessIdentity(
    pid=4242,
    started_at=100.0,
    executable="C:/synthetic/python.exe",
    command_digest="sha256:synthetic",
)


def _acquired(path: Path, *, lease_seconds: float = 0.8):
    store = OwnershipStore(path, identity_probe=lambda _pid: IDENTITY)
    lease = store.acquire(
        resource_id="canonical_farm",
        role_id="farm",
        owner_id="synthetic-owner",
        identity=IDENTITY,
        lease_seconds=lease_seconds,
    )
    store.close()
    return lease


def _heartbeat(path: Path, lease, **kwargs) -> ProcessLeaseHeartbeat:
    options = {
        "lease_seconds": 0.8,
        "renew_interval_seconds": 0.08,
        "renewal_busy_timeout_seconds": 0.02,
        "renewal_retry_seconds": 0.01,
        "max_transient_seconds": 0.25,
        "lease_safety_margin_seconds": 0.05,
        "identity_probe": lambda _pid: IDENTITY,
    }
    options.update(kwargs)
    return ProcessLeaseHeartbeat(path, lease, **options)


def _wait_until(predicate, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_open_existing_does_not_create_missing_authority_store(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite"

    heartbeat = _heartbeat(path, _acquired(tmp_path / "source.sqlite"))

    with pytest.raises(ProcessLeaseHeartbeatLifecycleError):
        heartbeat.start()
    heartbeat.stop()
    assert not path.exists()


def test_sqlite_contention_recovers_inside_bounded_budget(tmp_path: Path) -> None:
    path = tmp_path / "ownership.sqlite"
    lease = _acquired(path)
    blocker = sqlite3.connect(path, timeout=0.1)
    blocker.execute("BEGIN IMMEDIATE")
    heartbeat = _heartbeat(path, lease)

    heartbeat.start()
    assert _wait_until(
        lambda: heartbeat.snapshot()["transient_events"] >= 1,
    )
    blocker.rollback()
    blocker.close()

    assert _wait_until(lambda: heartbeat.snapshot()["renewals"] >= 1)
    snapshot = heartbeat.snapshot()
    assert snapshot["failure"] is None
    assert snapshot["transient_active"] is False
    heartbeat.stop()
    assert heartbeat.thread.is_alive() is False


def test_initial_renewal_connection_retries_bounded_sqlite_contention(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "ownership.sqlite"
    lease = _acquired(path)
    original = OwnershipStore.open_existing.__func__
    attempts = 0

    def flaky_open(cls, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise sqlite3.OperationalError("database is locked")
        return original(cls, *args, **kwargs)

    monkeypatch.setattr(
        OwnershipStore,
        "open_existing",
        classmethod(flaky_open),
    )
    heartbeat = _heartbeat(path, lease)

    heartbeat.start()

    assert attempts == 3
    assert heartbeat.failure is None
    assert heartbeat.snapshot()["transient_events"] == 2
    heartbeat.stop()


def test_contention_failure_is_visible_before_lease_expiry(tmp_path: Path) -> None:
    path = tmp_path / "ownership.sqlite"
    lease = _acquired(path, lease_seconds=0.5)
    blocker = sqlite3.connect(path, timeout=0.1)
    blocker.execute("BEGIN IMMEDIATE")
    notifications: list[dict[str, object]] = []
    heartbeat = _heartbeat(
        path,
        lease,
        lease_seconds=0.5,
        renew_interval_seconds=0.05,
        max_transient_seconds=0.12,
        on_failure=lambda _failure, snapshot: notifications.append(snapshot),
    )

    heartbeat.start()
    assert heartbeat.failure_event.wait(1.0)
    observed_at = time.time()
    blocker.rollback()
    blocker.close()

    assert isinstance(heartbeat.failure, ProcessLeaseRenewalBudgetExceeded)
    assert observed_at < lease.lease_expires_at
    assert len(notifications) == 1
    assert notifications[0]["failure_kind"] == "process_lease"
    assert "owner_id" not in notifications[0]
    heartbeat.stop()


def test_transient_identity_probe_recovers_but_fence_change_fails_immediately(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ownership.sqlite"
    lease = _acquired(path)
    attempts = 0

    def transient_probe(_pid: int):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise OwnershipConflictError("synthetic probe unavailable")
        return IDENTITY

    heartbeat = _heartbeat(path, lease, identity_probe=transient_probe)
    heartbeat.start()
    assert _wait_until(lambda: heartbeat.snapshot()["renewals"] >= 1)
    assert heartbeat.failure is None
    heartbeat.stop()

    second = _heartbeat(path, heartbeat.lease)
    second.start()
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE ownership_resources SET next_fence=next_fence+1 "
        "WHERE resource_id='canonical_farm'"
    )
    conn.commit()
    conn.close()

    assert second.failure_event.wait(1.0)
    assert second.snapshot()["transient_events"] == 0
    second.stop()


def test_stop_is_bounded_and_leaves_no_background_thread(tmp_path: Path) -> None:
    path = tmp_path / "ownership.sqlite"
    heartbeat = _heartbeat(path, _acquired(path))

    heartbeat.start()
    heartbeat.stop(timeout=0.5)

    assert heartbeat.thread.is_alive() is False
    assert heartbeat.failure is None
