# -*- coding: utf-8 -*-

import json
import sqlite3
import time

import pytest

import scripts.strategy_lab.worker_once as worker_once
from scripts.strategy_lab.worker_once import (
    WorkerLeaseLifecycleError,
    run_worker_once,
)
from src.research_lab.experiment import ExperimentSpec
from src.research_lab.ownership import (
    OwnershipStore,
    current_process_identity,
    probe_process_identity,
)
from src.research_lab.state_db import (
    claim_next_job,
    connect,
    enqueue_experiment,
    init_db,
)


def test_worker_once_defers_when_lock_exists(tmp_path):
    lock = tmp_path / "state" / "worker.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text('{"pid": 123, "created_at": 1}', encoding="utf-8")

    out = run_worker_once(tmp_path)

    assert out["status"] == "deferred"
    assert out["reason"] == "worker_already_running"


def test_worker_once_reports_running_before_evaluate(tmp_path, monkeypatch):
    spec_path = tmp_path / "spec.json"
    ExperimentSpec(
        experiment_id="exp-visible",
        data_glob="missing/*.json",
        symbols=["A"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{}]},
        max_runs=1,
    ).write_json(spec_path)
    status_events: list[dict] = []

    class FakeConn:
        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(worker_once, "connect", lambda *_args, **_kwargs: FakeConn())
    monkeypatch.setattr(worker_once, "init_db", lambda _: None)
    monkeypatch.setattr(worker_once, "recover_pending_publications", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(worker_once, "reap_stale_jobs", lambda _: 0)
    monkeypatch.setattr(
        worker_once,
        "claim_next_job",
        lambda *_args, **_kwargs: {
            "job_id": 7,
            "spec_path": str(spec_path),
            "fencing_token": 1,
            "claim_expires_at": time.time() + 90.0,
        },
    )
    monkeypatch.setattr(worker_once, "mark_job_executing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_once, "write_worker_status", lambda _path, **fields: status_events.append(fields))
    monkeypatch.setattr(worker_once, "evaluate_spec", lambda _spec, _runtime_meta, **_kwargs: [])
    monkeypatch.setattr(worker_once, "write_run_outputs", lambda *_args, **_kwargs: tmp_path / "runs" / "r1")
    monkeypatch.setattr(
        worker_once,
        "publish_completed_job",
        lambda *_args, **_kwargs: (tmp_path / "experiments" / "completed" / "r1", 0),
    )
    monkeypatch.setattr(worker_once, "publish_run_indexes", lambda *_args, **_kwargs: None)

    out = run_worker_once(tmp_path)

    assert out["status"] == "completed"
    assert status_events[0]["status"] == "running"
    assert status_events[0]["job_id"] == 7
    assert status_events[0]["experiment_id"] == "exp-visible"
    assert status_events[0]["symbols"] == 1
    assert status_events[0]["families"] == 1
    assert status_events[0]["max_runs"] == 1


def test_slow_secondary_indexes_keep_process_lease_alive_and_release_it(
    tmp_path,
    monkeypatch,
):
    spec_path = tmp_path / "spec.json"
    ExperimentSpec(
        experiment_id="exp-slow-index",
        data_glob="missing/*.json",
        symbols=["A"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{}]},
        max_runs=1,
    ).write_json(spec_path)

    class FakeConn:
        def close(self):
            return None

    monkeypatch.setattr(worker_once, "_LEASE_SECONDS", 0.2)
    monkeypatch.setattr(worker_once, "_RENEW_SECONDS", 0.02)
    monkeypatch.setattr(worker_once, "connect", lambda *_args, **_kwargs: FakeConn())
    monkeypatch.setattr(worker_once, "init_db", lambda _: None)
    monkeypatch.setattr(worker_once, "recover_pending_publications", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(worker_once, "reap_stale_jobs", lambda _: 0)
    monkeypatch.setattr(
        worker_once,
        "claim_next_job",
        lambda *_args, **_kwargs: {
            "job_id": 7,
            "spec_path": str(spec_path),
            "fencing_token": 1,
            "claim_expires_at": time.time() + 90.0,
        },
    )
    monkeypatch.setattr(worker_once, "mark_job_executing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_once, "renew_job_lease", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_once, "write_worker_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_once, "evaluate_spec", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        worker_once,
        "write_run_outputs",
        lambda *_args, **_kwargs: tmp_path / "experiments" / "provisional" / "r1",
    )
    monkeypatch.setattr(
        worker_once,
        "publish_completed_job",
        lambda *_args, **_kwargs: (
            tmp_path / "experiments" / "completed" / "r1",
            0,
        ),
    )
    monkeypatch.setattr(
        worker_once,
        "publish_run_indexes",
        lambda *_args, **_kwargs: time.sleep(0.35),
    )
    monkeypatch.setattr(
        worker_once,
        "mark_publication_indexes_published",
        lambda *_args, **_kwargs: None,
    )

    out = run_worker_once(tmp_path)

    assert out["status"] == "completed"
    ownership = sqlite3.connect(tmp_path / "state" / "ownership.sqlite")
    row = ownership.execute(
        "SELECT owner_id, pid, lease_expires_at FROM ownership_resources "
        "WHERE resource_id='strategy_lab_worker'"
    ).fetchone()
    ownership.close()
    assert row == (None, None, None)


def test_expired_same_process_owner_is_fatal_not_deferred(tmp_path):
    path = tmp_path / "state" / "ownership.sqlite"
    store = OwnershipStore(path, identity_probe=probe_process_identity)
    store.acquire(
        resource_id="strategy_lab_worker",
        role_id="compute_worker",
        owner_id="stuck-generation",
        identity=current_process_identity(),
        lease_seconds=0.01,
    )
    store.close()
    time.sleep(0.02)

    with pytest.raises(
        WorkerLeaseLifecycleError,
        match="expired_alive_conflict",
    ):
        run_worker_once(tmp_path)

    status = json.loads(
        (tmp_path / "state" / "worker_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "failed"
    assert status["reason"] == "worker_ownership_unavailable"
    assert status["reason_code"] == "expired_alive_conflict"


def test_active_worker_owner_remains_bounded_deferred(tmp_path):
    path = tmp_path / "state" / "ownership.sqlite"
    store = OwnershipStore(path, identity_probe=probe_process_identity)
    lease = store.acquire(
        resource_id="strategy_lab_worker",
        role_id="compute_worker",
        owner_id="active-generation",
        identity=current_process_identity(),
        lease_seconds=30.0,
    )

    out = run_worker_once(tmp_path)

    assert out["status"] == "deferred"
    assert out["reason_code"] == "active_worker_owner"
    store.release(lease)
    store.close()


def test_job_heartbeat_connection_failure_is_visible_before_work(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        worker_once,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("synthetic")
        ),
    )
    heartbeat = worker_once._JobLeaseHeartbeat(
        db_path=tmp_path / "strategy.sqlite",
        job_id=7,
        owner_id="worker",
        fencing_token=3,
        claim_expires_at=time.time() + 90.0,
    )

    with pytest.raises(
        WorkerLeaseLifecycleError,
        match="failed to initialize",
    ):
        heartbeat.start()

    heartbeat.stop()
    assert heartbeat.thread.is_alive() is False


def _claimed_job(tmp_path, *, lease_seconds: float):
    db_path = tmp_path / "strategy.sqlite"
    conn = connect(db_path)
    init_db(conn)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text("{}", encoding="utf-8")
    enqueue_experiment(conn, spec_path)
    job = claim_next_job(
        conn,
        owner_id="worker",
        lease_seconds=lease_seconds,
    )
    assert job is not None
    return db_path, conn, job


def test_job_heartbeat_retries_real_sqlite_contention_before_claim_deadline(
    tmp_path,
):
    db_path, conn, job = _claimed_job(tmp_path, lease_seconds=2.0)
    blocker = sqlite3.connect(db_path, timeout=0.1)
    blocker.execute("PRAGMA busy_timeout = 100")
    blocker.execute("BEGIN IMMEDIATE")
    heartbeat = worker_once._JobLeaseHeartbeat(
        db_path=db_path,
        job_id=int(job["job_id"]),
        owner_id="worker",
        fencing_token=(
            int(job["fencing_token"])
        ),
        claim_expires_at=float(job["claim_expires_at"]),
        lease_seconds=2.0,
        renew_interval_seconds=0.05,
        renewal_busy_timeout_seconds=0.05,
        renewal_retry_seconds=0.02,
        max_renewal_contention_seconds=0.6,
        lease_safety_margin_seconds=0.1,
    )

    started = time.monotonic()
    heartbeat.start()
    assert time.monotonic() - started < 0.5
    contention_deadline = time.monotonic() + 0.45
    while (
        heartbeat.snapshot()["renewal_contention_events"] == 0
        and time.monotonic() < contention_deadline
    ):
        time.sleep(0.01)
    snapshot = heartbeat.snapshot()
    assert snapshot["failure"] is None
    assert snapshot["renewal_contention_active"] is True
    assert snapshot["renewal_contention_events"] >= 1

    blocker.rollback()
    deadline = time.monotonic() + 1.0
    while heartbeat.snapshot()["renewals"] == 0 and time.monotonic() < deadline:
        time.sleep(0.01)

    heartbeat.assert_active(stage="test")
    assert heartbeat.snapshot()["renewals"] >= 1
    heartbeat.stop()
    blocker.close()
    conn.close()
    assert heartbeat.thread.is_alive() is False


def test_job_heartbeat_contention_fails_closed_before_claim_expiry(tmp_path):
    db_path, conn, job = _claimed_job(tmp_path, lease_seconds=1.0)
    blocker = sqlite3.connect(db_path, timeout=0.1)
    blocker.execute("PRAGMA busy_timeout = 100")
    blocker.execute("BEGIN IMMEDIATE")
    heartbeat = worker_once._JobLeaseHeartbeat(
        db_path=db_path,
        job_id=int(job["job_id"]),
        owner_id="worker",
        fencing_token=(
            int(job["fencing_token"])
        ),
        claim_expires_at=float(job["claim_expires_at"]),
        lease_seconds=1.0,
        renew_interval_seconds=0.05,
        renewal_busy_timeout_seconds=0.03,
        renewal_retry_seconds=0.01,
        max_renewal_contention_seconds=0.15,
        lease_safety_margin_seconds=0.1,
    )
    heartbeat.start()

    deadline = time.monotonic() + 1.0
    while heartbeat.failure is None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert isinstance(
        heartbeat.failure,
        worker_once.JobLeaseRenewalContentionExceeded,
    )
    with pytest.raises(
        WorkerLeaseLifecycleError,
        match="terminal publication",
    ):
        with heartbeat.terminal_transition():
            pytest.fail("failed heartbeat must block materialization")
    assert time.time() < float(job["claim_expires_at"])
    heartbeat.stop()
    blocker.rollback()
    blocker.close()
    conn.close()
    assert heartbeat.thread.is_alive() is False


def test_worker_pipeline_blocks_publication_after_job_heartbeat_failure(
    tmp_path,
    monkeypatch,
):
    db_path = worker_once.default_db_path(tmp_path)
    conn = connect(db_path)
    init_db(conn)
    spec_path = tmp_path / "spec.json"
    ExperimentSpec(
        experiment_id="exp-heartbeat-fail-closed",
        data_glob="missing/*.json",
        symbols=["A"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{}]},
        max_runs=1,
    ).write_json(spec_path)
    job_id = enqueue_experiment(conn, spec_path)
    conn.close()
    provisional_called = False

    def locked_renewal(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    def slow_evaluation(*_args, **_kwargs):
        time.sleep(0.2)
        return []

    def forbidden_provisional_output(*_args, **_kwargs):
        nonlocal provisional_called
        provisional_called = True
        pytest.fail("heartbeat failure must block provisional publication")

    monkeypatch.setattr(worker_once, "_LEASE_SECONDS", 1.0)
    monkeypatch.setattr(worker_once, "_RENEW_SECONDS", 0.02)
    monkeypatch.setattr(worker_once, "_JOB_RENEWAL_BUSY_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(worker_once, "_JOB_RENEWAL_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(worker_once, "_JOB_MAX_RENEWAL_CONTENTION_SECONDS", 0.05)
    monkeypatch.setattr(worker_once, "_JOB_LEASE_SAFETY_MARGIN_SECONDS", 0.1)
    monkeypatch.setattr(worker_once, "renew_job_lease", locked_renewal)
    monkeypatch.setattr(worker_once, "evaluate_spec", slow_evaluation)
    monkeypatch.setattr(
        worker_once,
        "write_run_outputs",
        forbidden_provisional_output,
    )

    with pytest.raises(
        WorkerLeaseLifecycleError,
        match="provisional output",
    ):
        run_worker_once(tmp_path, ignore_cadence=True)

    verify = connect(db_path)
    row = verify.execute(
        "SELECT status, run_dir_label FROM queue WHERE job_id=?",
        (job_id,),
    ).fetchone()
    verify.close()
    assert tuple(row) == ("failed", None)
    assert provisional_called is False


def test_state_db_disabled_journal_configuration_is_read_only(tmp_path):
    db_path = tmp_path / "delete-mode.sqlite"
    raw = sqlite3.connect(db_path)
    raw.execute("CREATE TABLE marker(value INTEGER)")
    raw.commit()
    assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    raw.close()

    heartbeat_conn = connect(
        db_path,
        busy_timeout_seconds=0.1,
        configure_journal_mode=False,
    )
    assert heartbeat_conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    heartbeat_conn.close()

    with pytest.raises(
        RuntimeError,
        match="required SQLite journal mode",
    ):
        connect(
            db_path,
            busy_timeout_seconds=0.1,
            configure_journal_mode=False,
            required_journal_mode="wal",
        )

    verify = sqlite3.connect(db_path)
    assert verify.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    verify.close()


def test_state_db_default_connection_still_configures_wal(tmp_path):
    conn = connect(tmp_path / "default.sqlite")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_process_heartbeat_connection_failure_is_visible_before_work(
    tmp_path,
):
    heartbeat = worker_once._ProcessLeaseHeartbeat(
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=object(),
    )

    with pytest.raises(
        WorkerLeaseLifecycleError,
        match="failed to initialize",
    ):
        heartbeat.start()
    assert heartbeat.failure is not None
    heartbeat.stop()
    assert heartbeat.thread.is_alive() is False
