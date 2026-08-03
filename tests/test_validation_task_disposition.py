from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.research_lab.farm_tasks_db import (
    FarmTasksDB,
    StaleTaskClaimError,
    tasks_db_path,
)
from src.research_lab.hard_validation_export import validation_id_for_unique_candidate
from src.research_lab.validation_orchestrator import run_due_validations
from src.research_lab.validation_task_disposition import apply_plan, build_plan


def _candidate(store: FarmTasksDB, uc_key: str, status: str = "FORWARD_PAPER") -> None:
    store.upsert_unique_candidate(
        {
            "uc_key": uc_key,
            "symbol": "X",
            "timeframe": "1h",
            "family": "momentum_breakout",
            "params_hash": "ph",
            "data_fingerprint": "fp",
            "decision": "OBSERVE",
            "validation_status": status,
            "candidate_id": "source",
            "params": {},
        },
        now=1.0,
    )


def _task(store: FarmTasksDB, uc_key: str, suffix: str) -> None:
    store.enqueue_task(
        task_type="export_validation",
        task_key=f"export::{suffix}",
        payload={"candidate_id": "source", "uc_key": uc_key},
        now=1.0,
    )


def test_hash_bound_disposition_is_atomic_and_idempotent(tmp_path) -> None:
    path = tasks_db_path(tmp_path)
    store = FarmTasksDB(path, clock=lambda: 1.0)
    _task(store, "missing", "missing")
    _candidate(store, "obsolete", status="REJECT")
    _task(store, "obsolete", "obsolete")
    _candidate(store, "valid")
    _task(store, "valid", "valid")
    store.close()

    plan = build_plan(path, now=1_000.0, missing_grace_seconds=10.0)
    assert plan["counts"]["dispositioned"] == 2
    assert plan["counts"]["retained"] == 1
    result = apply_plan(
        path,
        plan,
        expected_plan_digest=plan["plan_digest"],
        now=1_001.0,
    )
    assert result["changed"] == 2
    second = apply_plan(
        path,
        plan,
        expected_plan_digest=plan["plan_digest"],
        now=1_002.0,
    )
    assert second["changed"] == 0
    assert second["idempotent_reapply"] is True

    check = FarmTasksDB(path, read_only=True)
    assert len(check.tasks_in_state("skipped", task_type="export_validation")) == 2
    assert len(check.tasks_in_state("queued", task_type="export_validation")) == 1
    conn = check.raw_connection
    assert conn.execute("SELECT COUNT(*) FROM materialization_outbox").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM task_transitions").fetchone()[0] == 2
    check.close()


def test_disposition_fails_closed_on_plan_drift(tmp_path) -> None:
    path = tasks_db_path(tmp_path)
    store = FarmTasksDB(path, clock=lambda: 1.0)
    _task(store, "missing", "missing")
    store.close()
    plan = build_plan(path, now=1_000.0, missing_grace_seconds=10.0)

    mutate = FarmTasksDB(path, clock=lambda: 2_000.0)
    row = mutate.tasks_in_state("queued", task_type="export_validation")[0]
    claimed = mutate.claim_next_task(task_types=("export_validation",), now=2_000.0)
    assert claimed and claimed["task_id"] == row["task_id"]
    mutate.defer_task(
        claimed["task_id"], until=2_500.0, reason="concurrent_change", now=2_000.0
    )
    mutate.close()

    with pytest.raises(StaleTaskClaimError, match="plan is stale"):
        apply_plan(
            path,
            plan,
            expected_plan_digest=plan["plan_digest"],
            now=2_001.0,
        )


def test_runtime_scans_past_orphan_to_valid_task(monkeypatch, tmp_path) -> None:
    path = tasks_db_path(tmp_path)
    store = FarmTasksDB(path, clock=lambda: 1.0)
    _task(store, "missing", "000-missing")
    _candidate(store, "valid")
    _task(store, "valid", "001-valid")
    validation_id = validation_id_for_unique_candidate({"uc_key": "valid"})
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.prepare_requests",
        lambda *_args, **_kwargs: (
            {"skipped_no_artifact": 0},
            [SimpleNamespace(candidate_id=validation_id)],
        ),
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.write_prepared_requests",
        lambda *_args, **_kwargs: [validation_id],
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.run_validation_batch",
        lambda *_args, **_kwargs: {
            "total": 1,
            "validated": 0,
            "errors": 0,
            "results": [],
        },
    )

    out = run_due_validations(store, tmp_path, apply=True, limit=1, now=1_000.0)
    assert out["tasks_examined"] == 2
    assert out["orphan_tasks_skipped"] == 1
    assert out["exported"] == 1
    assert len(store.tasks_in_state("skipped", task_type="export_validation")) == 1
    assert len(store.tasks_in_state("deferred", task_type="export_validation")) == 1
    store.close()


def test_no_verdict_retry_budget_is_terminal(monkeypatch, tmp_path) -> None:
    path = tasks_db_path(tmp_path)
    store = FarmTasksDB(path, clock=lambda: 1.0)
    _candidate(store, "valid")
    _task(store, "valid", "valid")
    validation_id = validation_id_for_unique_candidate({"uc_key": "valid"})
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.prepare_requests",
        lambda *_args, **_kwargs: (
            {"skipped_no_artifact": 0},
            [SimpleNamespace(candidate_id=validation_id)],
        ),
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.write_prepared_requests",
        lambda *_args, **_kwargs: [validation_id],
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.run_validation_batch",
        lambda *_args, **_kwargs: {
            "total": 1,
            "validated": 0,
            "errors": 0,
            "results": [],
        },
    )

    for now in (1_000.0, 1_400.0, 1_800.0):
        run_due_validations(store, tmp_path, apply=True, limit=1, now=now)
    skipped = store.tasks_in_state("skipped", task_type="export_validation")
    assert len(skipped) == 1
    assert skipped[0]["machine_reason"] == "validation_no_verdict_retry_exhausted"
    assert skipped[0]["attempts"] == 3
    store.close()


def test_runtime_orphan_scan_is_bounded_and_preserves_generation(tmp_path) -> None:
    path = tasks_db_path(tmp_path)
    store = FarmTasksDB(path, clock=lambda: 1.0)
    for index in range(40):
        _task(store, f"missing-{index}", f"missing-{index:03d}")

    generation = tmp_path / "hard_validation" / "current_generation.json"
    generation.parent.mkdir(parents=True)
    generation.write_bytes(b"previous-generation")
    before = generation.read_bytes()

    out = run_due_validations(store, tmp_path, apply=True, limit=1, now=1_000.0)

    assert out["tasks_examined"] == 32
    assert out["orphan_tasks_skipped"] == 32
    assert out["generation_unchanged"] == 1
    assert len(store.tasks_in_state("queued", task_type="export_validation")) == 8
    assert generation.read_bytes() == before
    store.close()
