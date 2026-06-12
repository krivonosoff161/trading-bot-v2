# -*- coding: utf-8 -*-

import json
import sqlite3
from pathlib import Path

from src.research_lab.state_db import (
    claim_next_job,
    complete_job,
    connect,
    dashboard_snapshot,
    default_db_path,
    enqueue_experiment,
    ensure_experiment_queued,
    fail_job,
    import_completed_runs,
    init_db,
)


def _write_completed_run(root: Path, run_name: str = "20260612_010000_demo") -> Path:
    run = root / "experiments" / "completed" / run_name
    run.mkdir(parents=True)
    payload = {
        "schema": "strategy_lab_results.v0",
        "experiment_id": "demo",
        "created_at": "2026-06-12T00:00:00+00:00",
        "results": [
            {
                "run_id": "abc",
                "symbol": "BTC_USDT_SWAP",
                "family": "momentum_breakout",
                "params": {"lookback": 20},
                "metrics": {"n_trades": 11, "avg_net_pct": 0.4},
                "decision": "PROMOTE_FOR_PRESSURE_TEST",
                "reasons": ["passed_basic_gates"],
            },
            {
                "run_id": "def",
                "symbol": "ETH_USDT_SWAP",
                "family": "mean_reversion_fade",
                "params": {"lookback": 5},
                "metrics": {"n_trades": 3, "avg_net_pct": -0.2},
                "decision": "REJECT",
                "reasons": ["too_few_trades"],
            },
        ],
    }
    (run / "metrics.json").write_text(json.dumps(payload), encoding="utf-8")
    return run


def test_import_completed_runs_indexes_results(tmp_path):
    _write_completed_run(tmp_path)

    stats = import_completed_runs(tmp_path)
    db_path = default_db_path(tmp_path)

    assert stats == {"seen": 1, "imported": 1, "candidates": 2}
    conn = sqlite3.connect(db_path)
    run = conn.execute("SELECT candidate_count, promote_count, reject_count FROM runs").fetchone()
    candidates = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    conn.close()
    assert run == (2, 1, 1)
    assert candidates == 2


def test_queue_claim_complete_and_fail(tmp_path):
    db_path = default_db_path(tmp_path)
    conn = connect(db_path)
    init_db(conn)
    first = enqueue_experiment(conn, tmp_path / "b.json", priority=20)
    second = enqueue_experiment(conn, tmp_path / "a.json", priority=10)

    job = claim_next_job(conn)

    assert job is not None
    assert job["job_id"] == second
    assert job["attempts"] == 1
    complete_job(conn, int(job["job_id"]), "experiments/completed/demo")
    fail_job(conn, first, "broken")

    statuses = {
        int(r["job_id"]): r["status"]
        for r in conn.execute("SELECT job_id, status FROM queue ORDER BY job_id")
    }
    conn.close()
    assert statuses == {first: "failed", second: "completed"}


def test_ensure_experiment_queued_does_not_duplicate_pending_job(tmp_path):
    db_path = default_db_path(tmp_path)
    conn = connect(db_path)
    init_db(conn)
    spec = tmp_path / "spec.json"

    first, first_created = ensure_experiment_queued(conn, spec, priority=50)
    second, second_created = ensure_experiment_queued(conn, spec, priority=50)

    assert first == second
    assert first_created is True
    assert second_created is False
    assert conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 1
    conn.close()


def test_dashboard_snapshot_has_no_absolute_private_paths(tmp_path):
    _write_completed_run(tmp_path)
    import_completed_runs(tmp_path)
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    enqueue_experiment(conn, Path("configs/strategy_lab/l2_smoke.json"))
    conn.close()

    snap = dashboard_snapshot(default_db_path(tmp_path))

    assert snap["exists"] is True
    assert snap["totals"]["run_count"] == 1
    assert snap["queue_counts"]["queued"] == 1
    assert str(tmp_path) not in json.dumps(snap)
