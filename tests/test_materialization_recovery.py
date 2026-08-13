from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.strategy_lab import materialization_recovery as recovery_cli
from src.research_lab.farm_tasks_db import FarmTasksDB, StaleTaskClaimError
from src.research_lab.materialization_recovery import apply_plan, build_plan
from src.research_lab.state_db import connect, ensure_experiment_queued, init_db


def _interrupted_materialization(tmp_path):
    farm_path = tmp_path / "farm_tasks.sqlite"
    compute_path = tmp_path / "strategy_lab.sqlite"
    farm = FarmTasksDB(
        farm_path, owner_id="expired-owner", lease_seconds=5, clock=lambda: 100.0
    )
    task_id, _ = farm.enqueue_task(
        task_type="run_sweep", task_key="sweep::interrupted", now=100.0
    )
    claim = farm.claim_next_task(task_types=("run_sweep",), now=100.0)
    assert claim and int(claim["task_id"]) == task_id
    payload = json.dumps({"experiment_id": "synthetic"}, sort_keys=True) + "\n"
    digest = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    spec = tmp_path / "spec.json"
    spec.write_text(payload, encoding="utf-8")
    materialization_id = f"task:{task_id}:fence:{claim['fencing_token']}"
    farm.prepare_materialization(
        task_id,
        materialization_id=materialization_id,
        spec_path=str(spec),
        spec_digest=digest,
        spec_json=payload,
        priority=10,
        now=100.0,
    )
    compute = connect(compute_path, clock=lambda: 100.0)
    init_db(compute)
    job_id, created = ensure_experiment_queued(
        compute,
        spec,
        priority=10,
        materialization_id=materialization_id,
        materialization_digest=digest,
    )
    assert created is True
    compute.close()
    farm.close()
    return farm_path, compute_path, task_id, job_id, materialization_id


def test_expired_materialization_adopts_existing_compute_job_once(tmp_path) -> None:
    farm_path, compute_path, task_id, job_id, materialization_id = (
        _interrupted_materialization(tmp_path)
    )
    plan = build_plan(farm_path, compute_path, task_id=task_id, now=106.0)
    first = apply_plan(
        farm_path,
        compute_path,
        plan,
        expected_plan_digest=plan["plan_digest"],
        now=106.0,
    )
    second = apply_plan(
        farm_path,
        compute_path,
        plan,
        expected_plan_digest=plan["plan_digest"],
        now=107.0,
    )
    rebuilt = build_plan(farm_path, compute_path, task_id=task_id, now=108.0)
    rebuilt_repeat = apply_plan(
        farm_path,
        compute_path,
        rebuilt,
        expected_plan_digest=rebuilt["plan_digest"],
        now=108.0,
    )

    assert first["changed"] == 1
    assert second["changed"] == 0
    assert rebuilt["entry"]["already_adopted"] is True
    assert rebuilt_repeat["changed"] == 0
    farm = FarmTasksDB(farm_path, owner_id="verify", clock=lambda: 107.0)
    task = farm.get_task(task_id)
    assert task is not None
    assert task["state"] == "deferred"
    assert task["machine_reason"] == "materialized_awaiting_worker"
    assert task["fencing_token"] == 1
    assert task["mutation_seq"] == 2
    assert task["claim_owner"] is None
    assert task["claim_expires_at"] is None
    assert task["materialized_queue_job_id"] == job_id
    outbox = farm.raw_connection.execute(
        """SELECT state,queue_job_id,spec_json FROM materialization_outbox
           WHERE materialization_id=?""",
        (materialization_id,),
    ).fetchone()
    assert tuple(outbox) == ("acknowledged", job_id, "")
    farm.close()
    compute = connect(compute_path, clock=lambda: 107.0)
    assert compute.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 1
    assert (
        compute.execute("SELECT COUNT(*) FROM queue_materializations").fetchone()[0]
        == 1
    )
    compute.close()


def test_recovery_rejects_live_claim_and_creates_no_effect(tmp_path) -> None:
    farm_path, compute_path, task_id, _, _ = _interrupted_materialization(tmp_path)
    with pytest.raises(ValueError, match="not eligible"):
        build_plan(farm_path, compute_path, task_id=task_id, now=104.0)
    compute = connect(compute_path, clock=lambda: 104.0)
    assert compute.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 1
    compute.close()


def test_recovery_fails_closed_after_reclaim_race(tmp_path) -> None:
    farm_path, compute_path, task_id, _, _ = _interrupted_materialization(tmp_path)
    plan = build_plan(farm_path, compute_path, task_id=task_id, now=106.0)
    racer = FarmTasksDB(farm_path, owner_id="racer", clock=lambda: 106.0)
    assert racer.reconcile_orphan_running(now=106.0) == 1
    racer.close()

    with pytest.raises(StaleTaskClaimError, match="plan is stale"):
        apply_plan(
            farm_path,
            compute_path,
            plan,
            expected_plan_digest=plan["plan_digest"],
            now=106.0,
        )
    compute = connect(compute_path, clock=lambda: 106.0)
    assert compute.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 1
    assert (
        compute.execute("SELECT COUNT(*) FROM queue_materializations").fetchone()[0]
        == 1
    )
    compute.close()


def test_recovery_rejects_queue_binding_drift(tmp_path) -> None:
    farm_path, compute_path, task_id, job_id, _ = _interrupted_materialization(tmp_path)
    plan = build_plan(farm_path, compute_path, task_id=task_id, now=106.0)
    compute = connect(compute_path, clock=lambda: 106.0)
    compute.execute(
        "UPDATE queue_materializations SET spec_digest=? WHERE job_id=?",
        ("sha256:" + "0" * 64, job_id),
    )
    compute.commit()
    compute.close()

    with pytest.raises(StaleTaskClaimError, match="compute binding changed"):
        apply_plan(
            farm_path,
            compute_path,
            plan,
            expected_plan_digest=plan["plan_digest"],
            now=106.0,
        )
    farm = FarmTasksDB(farm_path, owner_id="verify", clock=lambda: 106.0)
    assert farm.get_task(task_id)["state"] == "running"
    farm.close()


def test_recovery_rejects_plan_digest_and_task_scope_drift(tmp_path) -> None:
    farm_path, compute_path, task_id, _, _ = _interrupted_materialization(tmp_path)
    plan = build_plan(farm_path, compute_path, task_id=task_id, now=106.0)
    with pytest.raises(ValueError, match="expected digest"):
        apply_plan(
            farm_path,
            compute_path,
            plan,
            expected_plan_digest="0" * 64,
            now=106.0,
        )
    plan["entry"]["task_id"] = task_id + 1
    with pytest.raises(ValueError, match="self-digest"):
        apply_plan(
            farm_path,
            compute_path,
            plan,
            expected_plan_digest=plan["plan_digest"],
            now=106.0,
        )
def test_cli_quiescence_allows_exact_before_and_zero_running_after(monkeypatch) -> None:
    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0]

        def __iter__(self):
            return iter(self.rows)

    class Connection:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, statement, _params=()):
            if "ownership_resources" in statement:
                return Cursor([(0,)])
            return Cursor([(task_id,) for task_id in self.rows])

        def close(self):
            return None

    for rows in ([37640], []):
        connections = iter((Connection([]), Connection(rows)))
        monkeypatch.setattr(recovery_cli, "_read_only", lambda _path: next(connections))
        recovery_cli._require_quiescent(tmp_path := Path("synthetic"), 37640)
        assert tmp_path.name == "synthetic"


def test_cli_quiescence_rejects_any_unrelated_running_task(monkeypatch) -> None:
    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0]

        def __iter__(self):
            return iter(self.rows)

    class Connection:
        def __init__(self, *, owner=False):
            self.owner = owner

        def execute(self, statement, _params=()):
            return Cursor([(0,)]) if self.owner else Cursor([(37640,), (37641,)])

        def close(self):
            return None

    connections = iter((Connection(owner=True), Connection()))
    monkeypatch.setattr(recovery_cli, "_read_only", lambda _path: next(connections))
    with pytest.raises(RuntimeError, match="no unrelated running tasks"):
        recovery_cli._require_quiescent(Path("synthetic"), 37640)
