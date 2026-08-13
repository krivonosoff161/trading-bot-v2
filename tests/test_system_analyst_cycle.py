from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.research_lab import system_analyst_cycle
from src.research_lab.role_environment import gate_role_environment
from tests.test_role_environment import _gate_artifacts
from src.research_lab.system_analyst_cycle import (
    feedback_payloads_from_outcomes,
    feedback_payloads_from_system_results,
    outcome_review_source_binding,
    run_system_analyst_cycle,
)

FRESH_REVIEW_NOW = "2026-07-11T13:00:00+00:00"


@pytest.fixture(autouse=True)
def _trusted_training_projection(monkeypatch):
    def load(private_root, **_kwargs):
        path = private_root / "state" / "derived" / "paper_signal_training.jsonl"
        rows = []
        if path.exists():
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        return {
            "items": rows,
            "source_rows": len(rows),
            "eligible_rows": len(rows),
            "excluded_rows": 0,
            "rejection_counts": {},
            "paper_generation_run_id": "synthetic-current-run",
            "account_generation_id": "synthetic-current-account",
            "generation_status": "completed",
            "current_generation_compatible": True,
            "display_only": False,
            "paper_only": True,
            "execution_allowed": False,
        }

    monkeypatch.setattr(system_analyst_cycle, "load_current_training_evidence", load)


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

    summary = run_system_analyst_cycle(tmp_path, apply=True, now=FRESH_REVIEW_NOW)
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


def test_v2_outcome_review_requires_exact_generation_terminal_and_content_binding():
    training = {
        **_training(),
        "paper_generation_run_id": "paper-run-current",
        "terminal_lifecycle_event_id": "terminal-event-current",
        "diagnosis": "bounded-current-diagnosis",
    }
    exact = {
        **_review(),
        "source_binding": outcome_review_source_binding(training),
    }

    assert feedback_payloads_from_outcomes(
        [training], [exact], require_source_binding=True
    )
    assert feedback_payloads_from_outcomes(
        [training], [_review()], require_source_binding=True
    ) == []
    stale_generation = {
        **exact,
        "source_binding": {
            **exact["source_binding"],
            "paper_generation_run_id": "paper-run-stale",
        },
    }
    assert feedback_payloads_from_outcomes(
        [training], [stale_generation], require_source_binding=True
    ) == []
    changed_source_content = {**training, "diagnosis": "different-content"}
    assert feedback_payloads_from_outcomes(
        [changed_source_content], [exact], require_source_binding=True
    ) == []
    review_enriched_projection = {
        **training,
        "outcome_review_id": "review-downstream-projection",
        "outcome_learning_bucket": "win",
    }
    assert outcome_review_source_binding(review_enriched_projection) == exact[
        "source_binding"
    ]


def test_empty_current_generation_does_not_scan_historical_role_environments(
    tmp_path, monkeypatch
):
    historical_root = tmp_path / "state" / "role_environments"
    for recipient in ("farm", "validator", "trader"):
        directory = historical_root / recipient
        directory.mkdir(parents=True)
        for index in range(250):
            (directory / f"env_historical_{index:04d}.json").write_text(
                "not part of the current generation", encoding="utf-8"
            )

    monkeypatch.setattr(
        system_analyst_cycle,
        "load_outcome_reviews",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("empty generation must not load outcome history")
        ),
    )
    original_read_text = Path.read_text

    def reject_historical_read(path, *args, **kwargs):
        if "role_environments" in Path(path).parts:
            raise AssertionError("historical role directory was scanned")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_historical_read)

    summary = run_system_analyst_cycle(
        tmp_path,
        apply=True,
        expected_generation_run_id="synthetic-current-run",
    )

    assert summary["feedback_candidates"] == 0
    assert summary["role_environment_candidates"] == {
        "farm": 0,
        "validator": 0,
        "trader": 0,
    }
    assert summary["accepted_environment_ids"] == {
        "farm": [],
        "validator": [],
        "trader": [],
    }


def test_liveness_failure_aborts_analyst_before_private_state_write(tmp_path):
    with pytest.raises(RuntimeError, match="synthetic stop"):
        run_system_analyst_cycle(
            tmp_path,
            apply=True,
            check_active=lambda: (_ for _ in ()).throw(
                RuntimeError("synthetic stop")
            ),
        )

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
        run_system_analyst_cycle(tmp_path, apply=True, now=FRESH_REVIEW_NOW)
    monkeypatch.setattr(role_environment, "_write_state", original_write)
    recovered = run_system_analyst_cycle(tmp_path, apply=True, now=FRESH_REVIEW_NOW)
    assert recovered["accepted_role_requests"]["farm"] == 1


def test_current_generation_index_recovers_ack_state_crash_without_history_scan(
    tmp_path, monkeypatch
):
    derived = tmp_path / "state" / "derived"
    advice = tmp_path / "state" / "llm_advice"
    derived.mkdir(parents=True)
    advice.mkdir(parents=True)
    training = {
        **_training(),
        "paper_generation_run_id": "synthetic-current-run",
        "terminal_lifecycle_event_id": "synthetic-terminal-event",
    }
    (derived / "paper_signal_training.jsonl").write_text(
        json.dumps(training) + "\n", encoding="utf-8"
    )
    (advice / "outcome_reviews.jsonl").write_text(
        json.dumps(
            {
                **_review(),
                "source_binding": outcome_review_source_binding(training),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    import src.research_lab.role_environment as role_environment

    original_write = role_environment._write_state
    calls = {"count": 0}

    def fail_first(path, state):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("projection failed")
        return original_write(path, state)

    monkeypatch.setattr(role_environment, "_write_state", fail_first)
    with pytest.raises(OSError, match="projection failed"):
        run_system_analyst_cycle(
            tmp_path,
            apply=True,
            now=FRESH_REVIEW_NOW,
            expected_generation_run_id="synthetic-current-run",
        )

    monkeypatch.setattr(role_environment, "_write_state", original_write)
    monkeypatch.setattr(
        system_analyst_cycle,
        "recoverable_role_requests",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("production recovery must not scan history")
        ),
    )
    recovered = run_system_analyst_cycle(
        tmp_path,
        apply=True,
        now=FRESH_REVIEW_NOW,
        expected_generation_run_id="synthetic-current-run",
    )

    assert recovered["accepted_role_requests"] == {
        "farm": 1,
        "validator": 1,
        "trader": 1,
    }


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
