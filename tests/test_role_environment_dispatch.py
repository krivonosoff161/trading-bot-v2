from __future__ import annotations

import json

from src.research_lab import system_analyst_cycle
from src.research_lab.farm_tasks_db import FarmTasksDB
from src.research_lab.role_environment_dispatch import (
    dispatch_role_environments,
    reconcile_role_work_results,
)
from src.research_lab.system_analyst_cycle import run_system_analyst_cycle


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_dispatches_three_accepted_role_requests_to_real_owners(
    tmp_path, monkeypatch
):
    training = {
        "training_row_id": "training-1",
        "candidate_id": "candidate-1",
        "symbol": "BTC_USDT_SWAP",
        "timeframe": "15m",
        "family": "continuation",
        "boundary_ts": 1_788_000_000_000,
        "paper_only": True,
        "execution_allowed": False,
    }
    review = {
        "role_id": "outcome_reviewer",
        "review_id": "review-1",
        "source_ref": "training-1",
        "accepted": True,
        "created_at": "2026-07-13T04:00:00+00:00",
        "payload": {
            "summary": "Bounded review only.",
            "next_test_dimensions": ["exit_policy"],
            "counterfactual_tests": ["later_exit"],
        },
    }
    derived = tmp_path / "state" / "derived"
    advice = tmp_path / "state" / "llm_advice"
    derived.mkdir(parents=True)
    advice.mkdir(parents=True)
    (derived / "paper_signal_training.jsonl").write_text(json.dumps(training) + "\n")
    (advice / "outcome_reviews.jsonl").write_text(json.dumps(review) + "\n")
    _write_json(derived / "outcome_retest_specs.json", {
        "items": [{
            "source_ref": "training-1", "retest_id": "retest-1", "queueable": True,
            "symbol": "BTC_USDT_SWAP", "timeframe": "15m", "family": "continuation",
            "sweep_spec": {},
        }]
    })
    _write_json(derived / "paper_product_trades.json", [{
        "symbol": "BTC_USDT_SWAP", "source_signal_id": "signal-1",
        "created_at": "2026-07-13T04:05:00+00:00", "timeframe": "15m",
        "data_quality": "ok",
    }])
    monkeypatch.setattr(
        system_analyst_cycle,
        "load_current_training_evidence",
        lambda _private_root: {
            "items": [training],
            "source_rows": 1,
            "eligible_rows": 1,
            "excluded_rows": 0,
            "rejection_counts": {},
            "paper_generation_run_id": "synthetic-current-run",
            "account_generation_id": "synthetic-current-account",
            "generation_status": "completed",
            "current_generation_compatible": True,
            "display_only": False,
            "paper_only": True,
            "execution_allowed": False,
        },
    )

    system = run_system_analyst_cycle(
        tmp_path, apply=True, now="2026-07-13T04:30:00+00:00"
    )
    assert system["accepted_role_requests"] == {"farm": 1, "validator": 1, "trader": 1}

    tasks = FarmTasksDB(tmp_path / "state" / "farm_tasks.sqlite")
    tasks.upsert_unique_candidate({
        "uc_key": "uc-1", "candidate_id": "candidate-1", "symbol": "BTC_USDT_SWAP",
        "timeframe": "15m", "family": "continuation", "params_hash": "p1",
        "data_fingerprint": "fp1", "decision": "FORWARD_PAPER",
    })
    summary = dispatch_role_environments(tmp_path, tasks, apply=True)

    assert summary["by_role"]["farm"]["queued"] == 1
    assert summary["by_role"]["validator"]["queued"] == 1
    assert summary["by_role"]["trader"]["completed"] == 1
    assert len(tasks.tasks_in_state("queued", task_type="schedule_retest")) == 1
    assert len(tasks.tasks_in_state("queued", task_type="export_validation")) == 1
    replay = next((tmp_path / "state" / "role_work_results" / "trader").glob("*.json"))
    assert json.loads(replay.read_text())["execution_allowed"] is False
    tasks.close()


def test_dispatch_preview_is_read_only(tmp_path):
    tasks = FarmTasksDB(":memory:")
    summary = dispatch_role_environments(tmp_path, tasks, apply=False)
    assert summary["apply"] is False
    assert not (tmp_path / "state" / "role_work_queue").exists()
    tasks.close()


def test_reconcile_projects_completed_role_work_into_analyst_inbox(tmp_path):
    tasks = FarmTasksDB(tmp_path / "state" / "farm_tasks.sqlite")
    environment_id = "env_completed_farm"
    dispatch = {
        "schema": "RoleEnvironmentDispatch.v1",
        "environment_id": environment_id,
        "feedback_id": "feedback-1",
        "recipient": "farm",
        "task_spec": {"generation": 0, "paper_only": True},
        "paper_only": True,
        "execution_allowed": False,
    }
    _write_json(
        tmp_path / "state" / "role_work_queue" / "farm" / f"{environment_id}.json",
        dispatch,
    )
    task_id, _ = tasks.enqueue_task(
        task_type="run_sweep", task_key="closed-loop-test", priority=50,
        payload={
            "role_environment_id": environment_id,
            "paper_only": True,
            "execution_allowed": False,
        },
    )
    tasks.complete_task(task_id, last_result_ref="runs/closed-loop-test")

    summary = reconcile_role_work_results(tmp_path, tasks, apply=True)

    assert summary["results"] == 1
    assert summary["by_recipient"]["farm"] == 1
    inbox = (
        tmp_path / "state" / "derived" / "system_analyst_result_inbox.jsonl"
    ).read_text(encoding="utf-8")
    row = json.loads(inbox)
    assert row["result_id"] == f"role_result::{environment_id}::farm"
    assert row["result"]["status"] == "completed"
    tasks.close()
