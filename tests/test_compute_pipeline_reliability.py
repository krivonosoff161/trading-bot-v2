from __future__ import annotations

import json
import sqlite3

import pytest

from src.research_lab import farm_coordinator
from src.research_lab.farm_coordinator import PriorityWorkerFatalError
from src.research_lab.farm_tasks_db import FarmTasksDB, StaleTaskClaimError


def _materialize(db: FarmTasksDB, *, now: float = 100.0) -> tuple[int, str]:
    task_id, created = db.enqueue_task(
        task_type="run_sweep",
        task_key="sweep::one",
        now=now,
    )
    assert created
    task = db.claim_next_task(task_types=("run_sweep",), now=now)
    assert task is not None
    materialization_id = (
        f"task:{task_id}:fence:{int(task['fencing_token'])}"
    )
    db.prepare_materialization(
        task_id,
        materialization_id=materialization_id,
        spec_path="synthetic-spec.json",
        spec_digest="sha256:synthetic",
        spec_json=json.dumps({"synthetic": True}),
        priority=10,
        now=now,
    )
    db.mark_materialization_dispatched(
        materialization_id,
        77,
        now=now,
    )
    db.commit_materialization(
        task_id,
        materialization_id=materialization_id,
        queue_job_id=77,
        now=now,
    )
    return task_id, materialization_id


def test_materialized_task_parks_without_renewable_claim() -> None:
    db = FarmTasksDB(":memory:", lease_seconds=5, clock=lambda: 100.0)

    task_id, _ = _materialize(db)
    task = db.get_task(task_id)

    assert task is not None
    assert task["state"] == "deferred"
    assert task["machine_reason"] == "materialized_awaiting_worker"
    assert task["claim_owner"] is None
    assert task["claim_expires_at"] is None
    assert task["deferred_until"] is None
    assert db.claim_next_task(now=10_000.0) is None
    assert db.reconcile_orphan_running(now=10_000.0) == 0
    db.close()


def test_parked_materialization_keeps_active_dedup_key() -> None:
    db = FarmTasksDB(":memory:", clock=lambda: 100.0)
    task_id, _ = _materialize(db)

    duplicate_id, created = db.enqueue_task(
        task_type="run_sweep",
        task_key="sweep::one",
        now=200.0,
    )

    assert not created
    assert duplicate_id == task_id
    db.close()


def test_exact_acknowledged_generation_finishes_parked_task_once() -> None:
    db = FarmTasksDB(":memory:", clock=lambda: 100.0)
    task_id, materialization_id = _materialize(db)

    db.finish_materialized_task(
        task_id,
        materialization_id=materialization_id,
        queue_job_id=77,
        queue_status="completed",
        last_result_ref="experiments/completed/synthetic",
        run_dir_label="experiments/completed/synthetic",
        now=200.0,
    )

    task = db.get_task(task_id)
    assert task is not None
    assert task["state"] == "completed"
    assert task["machine_reason"] == "compute_completed"
    with pytest.raises(StaleTaskClaimError):
        db.finish_materialized_task(
            task_id,
            materialization_id=materialization_id,
            queue_job_id=77,
            queue_status="completed",
            now=201.0,
        )
    db.close()


@pytest.mark.parametrize(
    ("materialization_id", "queue_job_id"),
    [
        ("task:1:fence:999", 77),
        ("task:1:fence:1", 78),
    ],
)
def test_wrong_materialization_generation_cannot_finish(
    materialization_id: str,
    queue_job_id: int,
) -> None:
    db = FarmTasksDB(":memory:", clock=lambda: 100.0)
    task_id, actual_materialization_id = _materialize(db)
    if materialization_id.endswith(":1"):
        materialization_id = actual_materialization_id

    with pytest.raises(StaleTaskClaimError):
        db.finish_materialized_task(
            task_id,
            materialization_id=materialization_id,
            queue_job_id=queue_job_id,
            queue_status="completed",
            now=200.0,
        )
    assert db.get_task(task_id)["state"] == "deferred"
    db.close()


def test_parked_task_missing_acknowledged_binding_fails_closed() -> None:
    db = FarmTasksDB(":memory:", clock=lambda: 100.0)
    task_id, materialization_id = _materialize(db)
    db.raw_connection.execute(
        "DELETE FROM materialization_outbox WHERE materialization_id=?",
        (materialization_id,),
    )
    db.raw_connection.commit()

    with pytest.raises(
        PriorityWorkerFatalError,
        match="acknowledged fence binding",
    ):
        farm_coordinator._sync_completions(
            db,
            conn=sqlite3.connect(":memory:"),
            counters={},
            now=200.0,
        )

    assert db.get_task(task_id)["state"] == "deferred"
    db.close()


def test_parked_task_missing_compute_row_fails_closed() -> None:
    db = FarmTasksDB(":memory:", clock=lambda: 100.0)
    task_id, _ = _materialize(db)
    compute = sqlite3.connect(":memory:")
    compute.row_factory = sqlite3.Row
    compute.executescript(
        """
        CREATE TABLE queue(
            job_id INTEGER PRIMARY KEY,
            status TEXT,
            run_dir_label TEXT,
            spec_path TEXT,
            materialization_digest TEXT
        );
        CREATE TABLE queue_materializations(
            materialization_id TEXT,
            job_id INTEGER,
            spec_digest TEXT
        );
        """
    )

    with pytest.raises(
        PriorityWorkerFatalError,
        match="exact compute row",
    ):
        farm_coordinator._sync_completions(
            db,
            conn=compute,
            counters={},
            now=200.0,
        )

    assert db.get_task(task_id)["state"] == "deferred"
    compute.close()
    db.close()
