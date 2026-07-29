from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.research_lab.farm_tasks_db import FarmTasksDB, StaleTaskClaimError
from src.research_lab.ownership import OwnershipStore, ProcessIdentity
from src.research_lab.state_db import connect, ensure_experiment_queued, init_db
from src.research_lab.task_claim_heartbeat import (
    TaskClaimHeartbeat,
    TaskClaimProgressStalled,
    TaskClaimRenewalContentionExceeded,
)


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _identity() -> ProcessIdentity:
    return ProcessIdentity(
        pid=4242,
        started_at=10.0,
        executable="C:/Python/python.exe",
        command_digest="sha256:canonical-farm",
    )


def _process_lease(
    tmp_path, *, owner_id="canonical-owner", clock=time.time, lease_seconds=10.0,
):
    identity = _identity()

    def probe(pid):
        return identity if pid == identity.pid else None

    store = OwnershipStore(tmp_path / "ownership.sqlite", clock=clock, identity_probe=probe)
    lease = store.acquire(
        resource_id="canonical_farm",
        role_id="farm",
        owner_id=owner_id,
        identity=identity,
        lease_seconds=lease_seconds,
    )
    return store, lease, probe


def _claimed_task(tmp_path, *, owner_id="canonical-owner", lease_seconds=0.25, clock=time.time):
    db = FarmTasksDB(
        tmp_path / "farm.sqlite",
        owner_id=owner_id,
        lease_seconds=lease_seconds,
        clock=clock,
    )
    task_id, _ = db.enqueue_task(task_type="run_sweep", task_key="one")
    task = db.claim_next_task()
    assert task and task["task_id"] == task_id
    return db, task


def _wait_until(predicate, timeout=1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_progress_heartbeat_keeps_claim_live_beyond_original_lease(tmp_path) -> None:
    clock = Clock()
    owner_store, process_lease, probe = _process_lease(
        tmp_path, clock=clock, lease_seconds=2_000.0
    )
    tasks, task = _claimed_task(tmp_path, lease_seconds=900.0, clock=clock)
    heartbeat = TaskClaimHeartbeat(
        tasks,
        task,
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=process_lease,
        lease_seconds=900.0,
        renew_interval_seconds=30.0,
        max_no_progress_seconds=300.0,
        clock=clock,
        monotonic=clock,
        identity_probe=probe,
    )
    renewal_db = FarmTasksDB(
        tasks.path, owner_id=process_lease.owner_id, lease_seconds=900.0, clock=clock
    )
    try:
        for elapsed in range(100, 1_101, 100):
            clock.value = 100.0 + elapsed
            heartbeat.progress(f"snapshot_{elapsed // 100}:bound")
            assert heartbeat._renew_if_progressed(owner_store, renewal_db) is True
        heartbeat.assert_active()
        snapshot = heartbeat.snapshot()
        assert clock.value == 1_200.0
        assert snapshot["renewals"] == 11
        assert tasks.get_task(task["task_id"])["claim_expires_at"] == 2_100.0
    finally:
        renewal_db.close()
        owner_store.release(process_lease)
        owner_store.close()
        tasks.close()


def test_renewal_stops_when_canonical_owner_is_lost(tmp_path) -> None:
    owner_store, process_lease, probe = _process_lease(tmp_path)
    tasks, task = _claimed_task(tmp_path)
    heartbeat = TaskClaimHeartbeat(
        tasks,
        task,
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=process_lease,
        lease_seconds=0.25,
        renew_interval_seconds=0.02,
        max_no_progress_seconds=0.12,
        identity_probe=probe,
    )
    heartbeat.start()
    heartbeat.progress("anchor_snapshot_selected")
    owner_store.release(process_lease)
    assert _wait_until(lambda: heartbeat.failure is not None)
    expires = tasks.get_task(task["task_id"])["claim_expires_at"]
    time.sleep(0.06)
    assert tasks.get_task(task["task_id"])["claim_expires_at"] == expires
    with pytest.raises(StaleTaskClaimError):
        heartbeat.assert_active()
    heartbeat.stop()
    owner_store.close()
    tasks.close()


def test_old_worker_cannot_renew_or_materialize_after_task_fence_advance(tmp_path) -> None:
    clock = Clock()
    path = tmp_path / "farm.sqlite"
    old = FarmTasksDB(path, owner_id="old", lease_seconds=5, clock=clock)
    task_id, _ = old.enqueue_task(task_type="run_sweep", task_key="one", now=clock())
    first = old.claim_next_task(now=clock())
    assert first
    clock.value = 106.0
    new = FarmTasksDB(path, owner_id="new", lease_seconds=5, clock=clock)
    assert new.reconcile_orphan_running(now=clock()) == 1
    second = new.claim_next_task(now=clock())
    assert second and second["fencing_token"] == first["fencing_token"] + 1
    with pytest.raises(StaleTaskClaimError):
        old.renew_task_claim_token(
            task_id, fencing_token=first["fencing_token"], now=clock()
        )
    with pytest.raises(StaleTaskClaimError):
        old.prepare_materialization(
            task_id,
            materialization_id=f"task:{task_id}:fence:{first['fencing_token']}",
            spec_path="old.json",
            spec_digest="sha256:old",
            spec_json="{}\n",
            priority=10,
            now=clock(),
        )
    assert old.raw_connection.execute(
        "SELECT COUNT(*) FROM materialization_outbox"
    ).fetchone()[0] == 0


def test_renewal_and_reconciliation_race_has_one_atomic_winner(tmp_path) -> None:
    clock = Clock()
    path = tmp_path / "farm.sqlite"
    owner = FarmTasksDB(path, owner_id="owner", lease_seconds=5, clock=clock)
    owner.enqueue_task(task_type="run_sweep", task_key="one", now=clock())
    task = owner.claim_next_task(now=clock())
    assert task
    contender = FarmTasksDB(path, owner_id="contender", lease_seconds=5, clock=clock)

    def renew_at(now: float):
        db = FarmTasksDB(path, owner_id="owner", lease_seconds=5, clock=clock)
        try:
            return db.renew_task_claim_token(
                task["task_id"], fencing_token=task["fencing_token"], now=now
            )
        finally:
            db.close()

    def reconcile_at(now: float):
        db = FarmTasksDB(path, owner_id="contender", lease_seconds=5, clock=clock)
        try:
            return db.reconcile_orphan_running(now=now)
        finally:
            db.close()

    clock.value = 104.0
    with ThreadPoolExecutor(max_workers=2) as pool:
        renew = pool.submit(renew_at, clock())
        reconcile = pool.submit(reconcile_at, clock())
        assert renew.result() == 109.0
        assert reconcile.result() == 0

    clock.value = 110.0
    with ThreadPoolExecutor(max_workers=2) as pool:
        renew = pool.submit(renew_at, clock())
        reconcile = pool.submit(reconcile_at, clock())
        outcomes = []
        for future in (renew, reconcile):
            try:
                outcomes.append(future.result())
            except StaleTaskClaimError:
                outcomes.append("stale")
    assert sorted(outcomes, key=str) == [1, "stale"]
    assert contender.get_task(task["task_id"])["state"] == "queued"


def test_graceful_stop_leaves_no_renewal_thread(tmp_path) -> None:
    owner_store, process_lease, probe = _process_lease(tmp_path)
    tasks, task = _claimed_task(tmp_path)
    heartbeat = TaskClaimHeartbeat(
        tasks,
        task,
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=process_lease,
        renew_interval_seconds=0.02,
        max_no_progress_seconds=0.1,
        identity_probe=probe,
    )
    heartbeat.start()
    heartbeat.stop()
    assert heartbeat.thread.is_alive() is False
    assert heartbeat.snapshot()["thread_alive"] is False
    owner_store.release(process_lease)


def test_external_stop_intent_stops_renewal_and_blocks_foreground(tmp_path) -> None:
    owner_store, process_lease, probe = _process_lease(tmp_path)
    tasks, task = _claimed_task(tmp_path, lease_seconds=0.25)
    stop_requested = False
    heartbeat = TaskClaimHeartbeat(
        tasks,
        task,
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=process_lease,
        lease_seconds=0.25,
        renew_interval_seconds=0.02,
        max_no_progress_seconds=0.12,
        identity_probe=probe,
        stop_requested=lambda: stop_requested,
    )
    heartbeat.start()
    heartbeat.progress("snapshot_bound")
    stop_requested = True
    assert _wait_until(lambda: not heartbeat.thread.is_alive())
    expires = tasks.get_task(task["task_id"])["claim_expires_at"]
    time.sleep(0.06)
    assert tasks.get_task(task["task_id"])["claim_expires_at"] == expires
    with pytest.raises(StaleTaskClaimError, match="heartbeat stopped"):
        heartbeat.progress("must_not_continue")
    assert heartbeat.failure is None
    heartbeat.stop()
    owner_store.release(process_lease)
    owner_store.close()
    tasks.close()


def test_crash_without_heartbeat_expires_for_controlled_reconciliation(tmp_path) -> None:
    clock = Clock()
    path = tmp_path / "farm.sqlite"
    crashed = FarmTasksDB(path, owner_id="crashed", lease_seconds=5, clock=clock)
    crashed.enqueue_task(task_type="run_sweep", task_key="one", now=clock())
    task = crashed.claim_next_task(now=clock())
    assert task
    crashed.close()
    clock.value = 106.0
    recovery = FarmTasksDB(path, owner_id="recovery", lease_seconds=5, clock=clock)
    assert recovery.reconcile_orphan_running(now=clock()) == 1
    assert recovery.get_task(task["task_id"])["state"] == "queued"


def test_repeated_materialization_is_idempotent_across_outbox_and_compute(tmp_path) -> None:
    tasks = FarmTasksDB(tmp_path / "farm.sqlite", owner_id="owner", clock=lambda: 100.0)
    task_id, _ = tasks.enqueue_task(
        task_type="run_sweep", task_key="one", now=100.0
    )
    task = tasks.claim_next_task(now=100.0)
    assert task
    materialization_id = f"task:{task_id}:fence:{task['fencing_token']}"
    payload = json.dumps({"experiment_id": "one"}, sort_keys=True) + "\n"
    digest = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    spec_path = tmp_path / "spec.json"
    for _ in range(2):
        tasks.prepare_materialization(
            task_id,
            materialization_id=materialization_id,
            spec_path=str(spec_path),
            spec_digest=digest,
            spec_json=payload,
            priority=10,
            now=100.0,
        )
    spec_path.write_text(payload, encoding="utf-8")
    compute = connect(tmp_path / "compute.sqlite", clock=lambda: 100.0)
    init_db(compute)
    first = ensure_experiment_queued(
        compute,
        spec_path,
        materialization_id=materialization_id,
        materialization_digest=digest,
    )
    second = ensure_experiment_queued(
        compute,
        spec_path,
        materialization_id=materialization_id,
        materialization_digest=digest,
    )
    assert first[0] == second[0]
    assert (first[1], second[1]) == (True, False)
    assert tasks.raw_connection.execute(
        "SELECT COUNT(*) FROM materialization_outbox"
    ).fetchone()[0] == 1
    assert compute.execute("SELECT COUNT(*) FROM queue_materializations").fetchone()[0] == 1
    assert compute.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 1


def test_no_progress_cannot_renew_claim_indefinitely(tmp_path) -> None:
    owner_store, process_lease, probe = _process_lease(tmp_path)
    tasks, task = _claimed_task(tmp_path, lease_seconds=0.15)
    heartbeat = TaskClaimHeartbeat(
        tasks,
        task,
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=process_lease,
        lease_seconds=0.15,
        renew_interval_seconds=0.02,
        max_no_progress_seconds=0.06,
        identity_probe=probe,
    )
    heartbeat.start()
    assert _wait_until(lambda: isinstance(heartbeat.failure, TaskClaimProgressStalled))
    expires = tasks.get_task(task["task_id"])["claim_expires_at"]
    time.sleep(0.06)
    assert tasks.get_task(task["task_id"])["claim_expires_at"] == expires
    heartbeat.stop()
    owner_store.release(process_lease)


def test_transient_sqlite_writer_contention_retries_then_renews(tmp_path) -> None:
    owner_store, process_lease, probe = _process_lease(tmp_path)
    tasks, task = _claimed_task(tmp_path, lease_seconds=0.5)
    blocker = sqlite3.connect(tasks.path, timeout=0, isolation_level=None)
    heartbeat = TaskClaimHeartbeat(
        tasks,
        task,
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=process_lease,
        lease_seconds=0.5,
        renew_interval_seconds=0.02,
        max_no_progress_seconds=0.4,
        renewal_busy_timeout_seconds=0.01,
        renewal_retry_seconds=0.01,
        max_renewal_contention_seconds=0.15,
        identity_probe=probe,
    )
    try:
        heartbeat.start()
        assert _wait_until(
            lambda: heartbeat.snapshot()["renewal_connection_ready"]
        )
        blocker.execute("BEGIN IMMEDIATE")
        heartbeat.progress("canonical_candles_loaded")
        assert _wait_until(
            lambda: heartbeat.snapshot()["renewal_contention_events"] > 0
        )
        assert heartbeat.failure is None
        blocker.rollback()
        assert _wait_until(lambda: heartbeat.snapshot()["renewals"] == 1)
        snapshot = heartbeat.snapshot()
        assert snapshot["renewal_contention_active"] is False
        assert snapshot["last_renewal_contention"].startswith("SQLITE_")
        assert heartbeat.failure is None
    finally:
        try:
            blocker.rollback()
        except sqlite3.Error:
            pass
        heartbeat.stop()
        blocker.close()
        owner_store.release(process_lease)
        owner_store.close()
        tasks.close()


def test_persistent_sqlite_writer_contention_fails_before_claim_expiry(tmp_path) -> None:
    owner_store, process_lease, probe = _process_lease(tmp_path)
    tasks, task = _claimed_task(tmp_path, lease_seconds=0.5)
    original_expiry = float(task["claim_expires_at"])
    blocker = sqlite3.connect(tasks.path, timeout=0, isolation_level=None)
    observed = []
    heartbeat = TaskClaimHeartbeat(
        tasks,
        task,
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=process_lease,
        lease_seconds=0.5,
        renew_interval_seconds=0.02,
        max_no_progress_seconds=0.4,
        renewal_busy_timeout_seconds=0.01,
        renewal_retry_seconds=0.01,
        max_renewal_contention_seconds=0.08,
        identity_probe=probe,
        on_failure=lambda failure, snapshot: observed.append((failure, snapshot)),
    )
    try:
        heartbeat.start()
        assert _wait_until(
            lambda: heartbeat.snapshot()["renewal_connection_ready"]
        )
        assert _wait_until(
            lambda: float(
                tasks.get_task(task["task_id"])["claim_expires_at"]
            )
            > original_expiry
        )
        blocker.execute("BEGIN IMMEDIATE")
        # Bind the no-renew assertion to the exact claim generation observed
        # after a proven renewal and under the persistent writer lock.
        contention_expiry = float(
            tasks.get_task(task["task_id"])["claim_expires_at"]
        )
        heartbeat.progress("canonical_candles_loaded")
        assert _wait_until(
            lambda: isinstance(
                heartbeat.failure, TaskClaimRenewalContentionExceeded
            )
        )
        assert contention_expiry > original_expiry
        assert time.time() < contention_expiry
        assert len(observed) == 1
        assert observed[0][1]["renewal_contention_active"] is True
        assert (
            tasks.get_task(task["task_id"])["claim_expires_at"]
            == contention_expiry
        )
        assert tasks.raw_connection.execute(
            "SELECT COUNT(*) FROM materialization_outbox"
        ).fetchone()[0] == 0
    finally:
        blocker.rollback()
        heartbeat.stop()
        blocker.close()
        owner_store.release(process_lease)
        owner_store.close()
        tasks.close()


def test_graceful_stop_during_sqlite_contention_leaves_no_renewal_thread(
    tmp_path,
) -> None:
    owner_store, process_lease, probe = _process_lease(tmp_path)
    tasks, task = _claimed_task(tmp_path, lease_seconds=0.5)
    blocker = sqlite3.connect(tasks.path, timeout=0, isolation_level=None)
    heartbeat = TaskClaimHeartbeat(
        tasks,
        task,
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=process_lease,
        lease_seconds=0.5,
        renew_interval_seconds=0.02,
        max_no_progress_seconds=0.4,
        renewal_busy_timeout_seconds=0.01,
        renewal_retry_seconds=0.02,
        max_renewal_contention_seconds=0.2,
        identity_probe=probe,
    )
    try:
        heartbeat.start()
        assert _wait_until(
            lambda: heartbeat.snapshot()["renewal_connection_ready"]
        )
        blocker.execute("BEGIN IMMEDIATE")
        heartbeat.progress("canonical_candles_loaded")
        assert _wait_until(
            lambda: heartbeat.snapshot()["renewal_contention_events"] > 0
        )
        heartbeat.stop()
        assert heartbeat.thread.is_alive() is False
        assert heartbeat.failure is None
    finally:
        blocker.rollback()
        heartbeat.stop()
        blocker.close()
        owner_store.release(process_lease)
        owner_store.close()
        tasks.close()


def test_owner_loss_during_sqlite_contention_cannot_retry_renewal(tmp_path) -> None:
    owner_store, process_lease, probe = _process_lease(tmp_path)
    tasks, task = _claimed_task(tmp_path, lease_seconds=0.5)
    original_expiry = float(task["claim_expires_at"])
    blocker = sqlite3.connect(tasks.path, timeout=0, isolation_level=None)
    heartbeat = TaskClaimHeartbeat(
        tasks,
        task,
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=process_lease,
        lease_seconds=0.5,
        renew_interval_seconds=0.02,
        max_no_progress_seconds=0.4,
        renewal_busy_timeout_seconds=0.01,
        renewal_retry_seconds=0.02,
        max_renewal_contention_seconds=0.2,
        identity_probe=probe,
    )
    try:
        heartbeat.start()
        assert _wait_until(
            lambda: heartbeat.snapshot()["renewal_connection_ready"]
        )
        blocker.execute("BEGIN IMMEDIATE")
        heartbeat.progress("canonical_candles_loaded")
        assert _wait_until(
            lambda: heartbeat.snapshot()["renewal_contention_events"] > 0
        )
        owner_store.release(process_lease)
        blocker.rollback()
        assert _wait_until(lambda: heartbeat.failure is not None)
        assert isinstance(heartbeat.failure, StaleTaskClaimError)
        assert tasks.get_task(task["task_id"])["claim_expires_at"] == original_expiry
        assert heartbeat.snapshot()["renewals"] == 0
    finally:
        try:
            blocker.rollback()
        except sqlite3.Error:
            pass
        heartbeat.stop()
        blocker.close()
        owner_store.close()
        tasks.close()


def test_background_failure_callback_is_immediate_once_and_public_safe(tmp_path) -> None:
    owner_store, process_lease, probe = _process_lease(tmp_path)
    tasks, task = _claimed_task(tmp_path, lease_seconds=0.25)
    observed: list[tuple[BaseException, dict]] = []
    heartbeat = TaskClaimHeartbeat(
        tasks,
        task,
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=process_lease,
        lease_seconds=0.25,
        renew_interval_seconds=0.02,
        max_no_progress_seconds=0.06,
        identity_probe=probe,
        on_failure=lambda failure, snapshot: observed.append((failure, snapshot)),
    )
    heartbeat.start()
    assert _wait_until(lambda: len(observed) == 1)
    failure, snapshot = observed[0]
    assert isinstance(failure, TaskClaimProgressStalled)
    assert snapshot["failure"] == "TaskClaimProgressStalled"
    assert snapshot["task_id"] == task["task_id"]
    assert "owner_id" not in snapshot
    assert tasks.get_task(task["task_id"])["claim_expires_at"] > time.time()
    heartbeat._record_failure(RuntimeError("secondary"))
    assert len(observed) == 1
    heartbeat.stop()
    owner_store.release(process_lease)
    owner_store.close()
    tasks.close()


def test_owner_loss_before_materialization_creates_no_outbox_or_compute_job(tmp_path) -> None:
    owner_store, process_lease, probe = _process_lease(tmp_path)
    tasks, task = _claimed_task(tmp_path)
    heartbeat = TaskClaimHeartbeat(
        tasks,
        task,
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=process_lease,
        renew_interval_seconds=0.02,
        max_no_progress_seconds=0.1,
        identity_probe=probe,
    )
    heartbeat.start()
    owner_store.release(process_lease)
    with pytest.raises(StaleTaskClaimError):
        heartbeat.assert_active()
    assert tasks.raw_connection.execute(
        "SELECT COUNT(*) FROM materialization_outbox"
    ).fetchone()[0] == 0
    compute = connect(tmp_path / "compute.sqlite")
    init_db(compute)
    assert compute.execute("SELECT COUNT(*) FROM queue_materializations").fetchone()[0] == 0
    assert compute.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 0
    heartbeat.stop()
