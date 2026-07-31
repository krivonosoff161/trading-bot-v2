from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from src.research_lab.ownership import (
    OwnershipStore,
    current_process_identity,
    probe_process_identity,
)
from src.research_lab.process_lease_supervisor import (
    ProcessLeaseSupervisor,
    _atomic_json,
)


def _acquired(path: Path, *, lease_seconds: float = 0.8):
    identity = current_process_identity()
    store = OwnershipStore(path, identity_probe=probe_process_identity)
    lease = store.acquire(
        resource_id="canonical_farm",
        role_id="farm",
        owner_id="synthetic-owner",
        identity=identity,
        lease_seconds=lease_seconds,
    )
    store.close()
    return lease


def _supervisor(
    tmp_path: Path,
    lease,
    **kwargs,
) -> ProcessLeaseSupervisor:
    options = {
        "status_path": tmp_path / "farm_process_lease_status.json",
        "alert_path": tmp_path / "farm_process_lease_alerts.jsonl",
        "stop_path": tmp_path / "STOP_FARM_FULL_CYCLE.txt",
        "lease_seconds": 0.8,
        "renew_interval_seconds": 0.08,
        "renewal_busy_timeout_seconds": 0.02,
        "renewal_retry_seconds": 0.01,
        "max_transient_seconds": 0.25,
        "lease_safety_margin_seconds": 0.05,
        "max_no_progress_seconds": 2.0,
        "startup_timeout_seconds": 10.0,
    }
    options.update(kwargs)
    return ProcessLeaseSupervisor(
        tmp_path / "ownership.sqlite",
        lease,
        **options,
    )


def _wait_until(predicate, *, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def _hold_foreground_gil(milliseconds: int) -> None:
    if os.name == "nt":
        sleep = ctypes.PyDLL("kernel32").Sleep
        sleep.argtypes = [ctypes.c_uint]
        sleep.restype = None
        sleep(milliseconds)
        return
    sleep = ctypes.PyDLL(None).usleep
    sleep.argtypes = [ctypes.c_uint]
    sleep.restype = ctypes.c_int
    assert sleep(milliseconds * 1000) == 0


def _lease_expiry(path: Path) -> float:
    connection = sqlite3.connect(path)
    try:
        return float(
            connection.execute(
                "SELECT lease_expires_at FROM ownership_resources "
                "WHERE resource_id='canonical_farm'"
            ).fetchone()[0]
        )
    finally:
        connection.close()


def test_atomic_status_publication_retries_transient_windows_reader(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "status.json"
    original_replace = Path.replace
    attempts = 0

    def transient_replace(path: Path, destination: Path):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(5, "synthetic sharing violation")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", transient_replace)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    _atomic_json(target, {"state": "running"})

    assert attempts == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"state": "running"}


def test_supervisor_renews_while_foreground_gil_is_blocked(tmp_path: Path) -> None:
    path = tmp_path / "ownership.sqlite"
    supervisor = _supervisor(tmp_path, _acquired(path))
    supervisor.start()
    supervisor.record_progress("before_gil_bound_artifact_cleanup")

    _hold_foreground_gil(1200)
    supervisor.record_progress("after_gil_bound_artifact_cleanup")

    assert _wait_until(lambda: int(supervisor.snapshot().get("renewals") or 0) >= 2)
    assert _lease_expiry(path) > time.time()
    assert supervisor.failure is None
    supervisor.stop()
    assert supervisor.process.is_alive() is False
    assert supervisor.bridge_thread.is_alive() is False


def test_no_progress_fails_before_expiry_and_requests_canonical_stop(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ownership.sqlite"
    lease = _acquired(path)
    foreground_failures: list[dict[str, object]] = []
    supervisor = _supervisor(
        tmp_path,
        lease,
        max_no_progress_seconds=0.25,
        on_failure=lambda _failure, snapshot: foreground_failures.append(snapshot),
    )
    supervisor.start()

    assert supervisor.failure_event.wait(2.0)
    assert _wait_until(lambda: len(foreground_failures) == 1)
    observed_at = time.time()
    snapshot = supervisor.snapshot()

    assert observed_at < _lease_expiry(path)
    assert snapshot["state"] == "failed"
    assert snapshot["failure_type"] == "ProcessLeaseProgressStalled"
    assert (tmp_path / "STOP_FARM_FULL_CYCLE.txt").is_file()
    alert_text = (tmp_path / "farm_process_lease_alerts.jsonl").read_text(
        encoding="utf-8"
    )
    assert "synthetic-owner" not in alert_text
    assert "execution_allowed" in alert_text
    assert len(alert_text.splitlines()) == 1
    assert foreground_failures[0]["failure_kind"] == "process_lease"
    supervisor.stop()
    assert supervisor.process.is_alive() is False


def test_fence_advance_stops_supervised_renewal_without_reclaim(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ownership.sqlite"
    supervisor = _supervisor(tmp_path, _acquired(path))
    supervisor.start()
    supervisor.record_progress("before_fence_advance")
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE ownership_resources SET next_fence=next_fence+1 "
        "WHERE resource_id='canonical_farm'"
    )
    connection.commit()
    connection.close()

    assert supervisor.failure_event.wait(2.0)
    snapshot = supervisor.snapshot()
    assert snapshot["state"] == "failed"
    assert snapshot["failure_type"] == "StaleProcessLeaseError"
    renewals = int(snapshot["renewals"])
    time.sleep(0.2)
    assert int(supervisor.snapshot()["renewals"]) == renewals
    supervisor.stop()


def test_owner_process_loss_stops_renewal_and_allows_natural_expiry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ownership.sqlite"
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        identity = probe_process_identity(child.pid)
        store = OwnershipStore(path, identity_probe=probe_process_identity)
        lease = store.acquire(
            resource_id="canonical_farm",
            role_id="farm",
            owner_id="synthetic-child-owner",
            identity=identity,
            lease_seconds=0.8,
        )
        store.close()
        supervisor = _supervisor(tmp_path, lease)
        supervisor.start()
        supervisor.record_progress("child_owner_alive")

        child.terminate()
        child.wait(timeout=5.0)

        assert supervisor.failure_event.wait(2.0)
        assert supervisor.snapshot()["failure_type"] == "StaleProcessLeaseError"
        assert _wait_until(lambda: _lease_expiry(path) <= time.time(), timeout=2.0)
        supervisor.stop()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5.0)


def test_graceful_stop_leaves_no_supervisor_process_or_alert(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ownership.sqlite"
    supervisor = _supervisor(tmp_path, _acquired(path))
    supervisor.start()
    supervisor.record_progress("synthetic_completed_chunk")

    supervisor.stop()
    supervisor.stop()

    assert supervisor.process.is_alive() is False
    assert supervisor.bridge_thread.is_alive() is False
    assert supervisor.snapshot()["state"] == "stopped"
    assert not (tmp_path / "farm_process_lease_alerts.jsonl").exists()
    assert not (tmp_path / "STOP_FARM_FULL_CYCLE.txt").exists()


def test_supervisor_status_contains_only_safe_authority_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ownership.sqlite"
    supervisor = _supervisor(tmp_path, _acquired(path))
    supervisor.start()
    supervisor.record_progress("safe_progress_stage")
    assert _wait_until(lambda: int(supervisor.snapshot().get("renewals") or 0) >= 1)
    supervisor.stop()

    status = json.loads(
        (tmp_path / "farm_process_lease_status.json").read_text(encoding="utf-8")
    )
    assert status["paper_only"] is True
    assert status["execution_allowed"] is False
    assert "owner_id" not in status
    assert "command_digest" not in status
