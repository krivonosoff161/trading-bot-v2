from __future__ import annotations

import json

from src.research_lab.role_environment import gate_role_environment
from tests.test_role_environment import _gate_artifacts
from src.research_lab.system_analyst_cycle import (
    feedback_payloads_from_outcomes,
    feedback_payloads_from_system_results,
    run_system_analyst_cycle,
)


def _training():
    return {
        "training_row_id": "training-1", "candidate_id": "candidate-1",
        "boundary_ts": 1_700_000_000_000, "paper_only": True,
        "execution_allowed": False,
    }


def _review():
    return {
        "role_id": "outcome_reviewer", "review_id": "review-1",
        "source_ref": "training-1", "accepted": True,
        "created_at": "2026-07-11T10:00:00+00:00",
        "payload": {"summary": "Bounded review only."},
    }


def test_outcome_to_three_role_environments_and_gate_ack(tmp_path, monkeypatch):
    derived = tmp_path / "state" / "derived"
    advice = tmp_path / "state" / "llm_advice"
    derived.mkdir(parents=True)
    advice.mkdir(parents=True)
    (derived / "paper_signal_training.jsonl").write_text(json.dumps(_training()) + "\n")
    (advice / "outcome_reviews.jsonl").write_text(json.dumps(_review()) + "\n")

    summary = run_system_analyst_cycle(tmp_path, apply=True)
    assert summary["routed"] == 1
    assert summary["role_environment_candidates"] == {"farm": 1, "validator": 1, "trader": 1}
    assert summary["accepted_role_requests"] == {"farm": 1, "validator": 1, "trader": 1}

    for recipient in ("farm", "validator", "trader"):
        path = next((tmp_path / "state" / "role_environments" / recipient).glob("*.json"))
        row = json.loads(path.read_text())
        assert row["status"] == "candidate"
        gate_ref, evaluation_ref = _gate_artifacts(tmp_path, row["environment_id"])
        gated = gate_role_environment(
            tmp_path, recipient=recipient, environment_id=row["environment_id"], accepted=True,
            gate_result_ref=gate_ref, untouched_evaluation_ref=evaluation_ref,
        )
        assert gated["status"] == "accepted"


def test_system_analyst_preview_does_not_write(tmp_path):
    assert feedback_payloads_from_outcomes([_training()], [_review()])
    summary = run_system_analyst_cycle(tmp_path, apply=False)
    assert summary["apply"] is False
    assert not (tmp_path / "state").exists()


def test_expired_feedback_is_counted_and_skipped_without_writing_ledger(tmp_path):
    derived = tmp_path / "state" / "derived"
    advice = tmp_path / "state" / "llm_advice"
    derived.mkdir(parents=True)
    advice.mkdir(parents=True)
    (derived / "paper_signal_training.jsonl").write_text(
        json.dumps(_training()) + "\n", encoding="utf-8"
    )
    (advice / "outcome_reviews.jsonl").write_text(
        json.dumps(_review()) + "\n", encoding="utf-8"
    )

    summary = run_system_analyst_cycle(
        tmp_path, apply=True, now="2026-08-01T00:00:00+00:00"
    )

    assert summary["routed"] == 0
    assert summary["rejected"] == 1
    assert summary["rejection_reasons"] == {"provenance_expired": 1}
    assert not (
        tmp_path / "state" / "system_analyst_feedback" / "ledger.jsonl"
    ).exists()


def test_cycle_bounds_feedback_and_prefers_newest_review(tmp_path):
    derived = tmp_path / "state" / "derived"
    advice = tmp_path / "state" / "llm_advice"
    derived.mkdir(parents=True)
    advice.mkdir(parents=True)
    training_rows = []
    review_rows = []
    for index in range(3):
        training = {**_training(), "training_row_id": f"training-{index}"}
        review = {
            **_review(),
            "review_id": f"review-{index}",
            "source_ref": f"training-{index}",
            "created_at": f"2026-07-11T1{index}:00:00+00:00",
        }
        training_rows.append(training)
        review_rows.append(review)
    (derived / "paper_signal_training.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in training_rows), encoding="utf-8"
    )
    (advice / "outcome_reviews.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in review_rows), encoding="utf-8"
    )

    summary = run_system_analyst_cycle(
        tmp_path,
        apply=True,
        now="2026-07-11T13:00:00+00:00",
        max_feedback=1,
    )

    assert summary["feedback_candidates_total"] == 3
    assert summary["feedback_candidates"] == 1
    assert summary["max_feedback"] == 1
    assert summary["routed"] == 1
    ledger = (
        tmp_path / "state" / "system_analyst_feedback" / "ledger.jsonl"
    ).read_text(encoding="utf-8")
    assert "review-2" in ledger
    assert "review-0" not in ledger


def test_cycle_recovers_request_projection_after_ack_succeeded(tmp_path, monkeypatch):
    derived = tmp_path / "state" / "derived"
    advice = tmp_path / "state" / "llm_advice"
    derived.mkdir(parents=True)
    advice.mkdir(parents=True)
    (derived / "paper_signal_training.jsonl").write_text(json.dumps(_training()) + "\n")
    (advice / "outcome_reviews.jsonl").write_text(json.dumps(_review()) + "\n")

    import src.research_lab.role_environment as role_environment
    original_write = role_environment._write_state
    calls = {"count": 0}

    def fail_first(path, state):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("projection failed")
        return original_write(path, state)

    monkeypatch.setattr(role_environment, "_write_state", fail_first)
    import pytest
    with pytest.raises(OSError, match="projection failed"):
        run_system_analyst_cycle(tmp_path, apply=True)
    monkeypatch.setattr(role_environment, "_write_state", original_write)
    recovered = run_system_analyst_cycle(tmp_path, apply=True)
    assert recovered["accepted_role_requests"]["farm"] == 1


def test_completed_role_result_creates_one_bounded_next_generation():
    result = {
        "result_id": "role_result::env_1::farm",
        "environment_id": "env_1",
        "feedback_id": "feedback_1",
        "recipient": "farm",
        "result": {"status": "completed", "task_type": "run_sweep"},
        "task_spec": {
            "generation": 0,
            "source_ref": "training-1",
            "subject": {"symbol": "BTC_USDT_SWAP", "timeframe": "15m"},
        },
    }
    draft = {
        "role_id": "system_analyst",
        "review_id": "review-system-1",
        "source_ref": result["result_id"],
        "accepted": True,
        "created_at": "2026-07-13T08:00:00+00:00",
        "payload": {
            "summary": "One bounded follow-up is justified.",
            "next_test_dimensions": ["exit_policy"],
            "counterfactual_tests": ["later_exit"],
        },
    }

    payloads = feedback_payloads_from_system_results([result], [draft])

    assert len(payloads) == 1
    specs = [item["task_spec"] for item in payloads[0]["recommendations"]]
    assert {spec["generation"] for spec in specs} == {1}
    assert {spec["kind"] for spec in specs} == {
        "bounded_sweep", "untouched_validation", "paper_replay"
    }
    result["task_spec"]["generation"] = 2
    assert feedback_payloads_from_system_results([result], [draft]) == []
