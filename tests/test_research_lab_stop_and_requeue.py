# -*- coding: utf-8 -*-
"""Tests for graceful stop intent and stale requeue maintenance (Phase 4).

Verifies:
- Stop intent causes loop to stop after iteration.
- Requeue stale dry-run writes nothing.
- Requeue stale apply changes stale running to queued.
- Status exposes hints without absolute path leaks.
"""

import datetime as dt
import json
import sqlite3
from pathlib import Path

from src.research_lab.state_db import connect, default_db_path, init_db
from src.research_lab.stop_intent import (
    clear_stop,
    is_stop_requested,
    read_stop_intent,
    request_stop,
)
from scripts.strategy_lab.requeue_stale_jobs import (
    find_stale_jobs,
    requeue_stale_jobs,
)
from scripts.strategy_lab.worker_loop import loop as worker_loop


def _make_db(private_root: Path) -> Path:
    db_path = default_db_path(private_root)
    conn = connect(db_path)
    init_db(conn)
    conn.close()
    return db_path


def _insert_running_job(conn: sqlite3.Connection, job_id_val: int, spec_path: str, started_at: str) -> None:
    conn.execute(
        "INSERT INTO queue (job_id, spec_path, status, created_at, started_at) VALUES (?, ?, 'running', ?, ?)",
        (job_id_val, spec_path, "2026-01-01T00:00:00+00:00", started_at),
    )
    conn.commit()


def test_stop_intent_write_and_read(tmp_path):
    path = request_stop(tmp_path, reason="test_stop")
    assert path.exists()
    data = read_stop_intent(tmp_path)
    assert data["reason"] == "test_stop"
    assert data["schema"] == "strategy_lab_stop_intent.v1"
    assert is_stop_requested(tmp_path)


def test_stop_intent_clear(tmp_path):
    request_stop(tmp_path, reason="test")
    assert is_stop_requested(tmp_path)
    clear_stop(tmp_path)
    assert not is_stop_requested(tmp_path)
    assert read_stop_intent(tmp_path) == {}


def test_stop_intent_idempotent(tmp_path):
    request_stop(tmp_path, reason="first")
    request_stop(tmp_path, reason="second")
    data = read_stop_intent(tmp_path)
    assert data["reason"] == "second"


def test_requeue_stale_dry_run_writes_nothing(tmp_path):
    db_path = _make_db(tmp_path)
    conn = connect(db_path)
    now = dt.datetime.now(dt.timezone.utc)
    stale_time = (now - dt.timedelta(minutes=60)).isoformat()
    _insert_running_job(conn, 1, "specs/test.json", stale_time)
    conn.close()

    stale = find_stale_jobs(db_path, threshold_minutes=30)
    assert len(stale) == 1
    assert stale[0]["job_id"] == 1

    requeued = requeue_stale_jobs(db_path, threshold_minutes=9999)
    assert requeued == 0

    conn = connect(db_path)
    row = conn.execute("SELECT status FROM queue WHERE job_id = 1").fetchone()
    conn.close()
    assert row["status"] == "running"


def test_requeue_stale_apply_changes_to_queued(tmp_path):
    db_path = _make_db(tmp_path)
    conn = connect(db_path)
    now = dt.datetime.now(dt.timezone.utc)
    stale_time = (now - dt.timedelta(minutes=60)).isoformat()
    _insert_running_job(conn, 1, "specs/test.json", stale_time)
    conn.close()

    requeued = requeue_stale_jobs(db_path, threshold_minutes=30)
    assert requeued == 1

    conn = connect(db_path)
    row = conn.execute("SELECT status, started_at FROM queue WHERE job_id = 1").fetchone()
    conn.close()
    assert row["status"] == "queued"
    assert row["started_at"] is None


def test_requeue_fresh_running_not_touched(tmp_path):
    db_path = _make_db(tmp_path)
    conn = connect(db_path)
    now = dt.datetime.now(dt.timezone.utc)
    fresh_time = now.isoformat()
    _insert_running_job(conn, 1, "specs/test.json", fresh_time)
    conn.close()

    stale = find_stale_jobs(db_path, threshold_minutes=30)
    assert len(stale) == 0

    requeued = requeue_stale_jobs(db_path, threshold_minutes=30)
    assert requeued == 0

    conn = connect(db_path)
    row = conn.execute("SELECT status FROM queue WHERE job_id = 1").fetchone()
    conn.close()
    assert row["status"] == "running"


def test_stop_intent_no_absolute_paths(tmp_path):
    request_stop(tmp_path, reason="test")
    data = read_stop_intent(tmp_path)
    blob = json.dumps(data)
    assert str(tmp_path) not in blob
    assert "github_projects" not in blob


def test_worker_loop_respects_stop_intent_before_running(tmp_path):
    request_stop(tmp_path, reason="test")
    rc = worker_loop(tmp_path, sleep_seconds=1, error_sleep_seconds=1, max_iterations=1)
    assert rc == 0
    log = tmp_path / "logs" / "worker_loop.log"
    assert "stopped by stop intent" in log.read_text(encoding="utf-8")
