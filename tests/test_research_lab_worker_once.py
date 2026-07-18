# -*- coding: utf-8 -*-

import scripts.strategy_lab.worker_once as worker_once
from scripts.strategy_lab.worker_once import run_worker_once
from src.research_lab.experiment import ExperimentSpec


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
