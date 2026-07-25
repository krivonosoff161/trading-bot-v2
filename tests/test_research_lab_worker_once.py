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

    monkeypatch.setattr(worker_once, "connect", lambda _: FakeConn())
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
    monkeypatch.setattr(worker_once, "connect", lambda _: FakeConn())
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
        lambda _path: (_ for _ in ()).throw(sqlite3.OperationalError("synthetic")),
    )
    heartbeat = worker_once._JobLeaseHeartbeat(
        db_path=tmp_path / "strategy.sqlite",
        job_id=7,
        owner_id="worker",
        fencing_token=3,
    )

    with pytest.raises(
        WorkerLeaseLifecycleError,
        match="failed to initialize",
    ):
        heartbeat.start()

    heartbeat.stop()
    assert heartbeat.thread.is_alive() is False


def test_process_heartbeat_connection_failure_is_visible_before_work(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        worker_once,
        "OwnershipStore",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("synthetic")
        ),
    )
    heartbeat = worker_once._ProcessLeaseHeartbeat(
        ownership_path=tmp_path / "ownership.sqlite",
        process_lease=object(),
    )

    heartbeat.start()

    with pytest.raises(
        WorkerLeaseLifecycleError,
        match="initialization",
    ):
        worker_once._raise_process_heartbeat_failure(
            heartbeat,
            stage="initialization",
        )
    heartbeat.stop()
    assert heartbeat.thread.is_alive() is False
