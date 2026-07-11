from __future__ import annotations

import json

from src.research_lab.role_environment import gate_role_environment
from tests.test_role_environment import _gate_artifacts
from src.research_lab.system_analyst_cycle import (
    feedback_payloads_from_outcomes,
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
