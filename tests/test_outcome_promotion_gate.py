import json

from src.research_lab.outcome_promotion_gate import (
    COLLECT_TRUE_FORWARD,
    ELIGIBLE_FOR_OPERATOR_REVIEW,
    NEEDS_RETEST,
    NEEDS_SHADOW,
    NEEDS_TRUE_FORWARD,
    OPERATOR_REVIEW_ONLY,
    REVIEW_ONLY,
    build_gate_verdicts,
    build_outcome_promotion_gate,
    summarize_gate,
)


def _training(**overrides):
    row = {
        "schema": "TrainingRow.v2",
        "training_row_id": "training_s1",
        "candidate_id": "candidate_1",
        "symbol": "BTC",
        "timeframe": "1h",
        "family": "momentum_breakout",
        "paper_only": True,
        "execution_allowed": False,
    }
    row.update(overrides)
    return row


def _review(actionability="retest_exit_or_capture", **overrides):
    row = {
        "schema": "OutcomeReview.v1",
        "review_id": "llmr_1",
        "role_id": "outcome_reviewer",
        "source_ref": "training_s1",
        "accepted": True,
        "payload": {
            "review_kind": "loss",
            "outcome_bucket": "gave_back",
            "actionability": actionability,
        },
        "paper_only": True,
        "execution_allowed": False,
    }
    row.update(overrides)
    return row


def test_accepted_outcome_review_alone_needs_retest_not_promotion():
    verdicts = build_gate_verdicts([_training()], [_review()])

    assert len(verdicts) == 1
    assert verdicts[0].gate_stage == NEEDS_RETEST
    assert verdicts[0].paper_only is True
    assert verdicts[0].execution_allowed is False
    assert "accepted_review_requires_retest:retest_exit_or_capture" in verdicts[0].reasons


def test_loss_cluster_review_stays_review_only():
    verdicts = build_gate_verdicts([_training()], [_review("cluster_before_retest")])

    assert verdicts[0].gate_stage == REVIEW_ONLY
    assert "review_only_actionability:cluster_before_retest" in verdicts[0].reasons


def test_positive_preserve_pattern_requires_shadow_watch():
    verdicts = build_gate_verdicts([_training()], [_review("preserve_pattern")])

    assert verdicts[0].gate_stage == NEEDS_SHADOW
    assert "positive_pattern_needs_forward_watch" in verdicts[0].reasons


def test_shadow_candidate_must_move_to_true_forward_before_operator_review():
    verdicts = build_gate_verdicts(
        [_training(candidate_id="uc_1")],
        [_review()],
        shadow_index={"uc_1": {"status": "shadow_forward_candidate", "paper_forward_ready": False}},
    )

    assert verdicts[0].gate_stage == NEEDS_TRUE_FORWARD
    assert verdicts[0].evidence_refs["shadow_uc_key"] == "uc_1"
    assert verdicts[0].execution_allowed is False


def test_true_forward_collecting_is_not_operator_ready():
    verdicts = build_gate_verdicts(
        [_training(candidate_id="uc_1")],
        [_review()],
        true_forward_index={"uc_1": {"status": "collecting", "paper_forward_ready": False}},
    )

    assert verdicts[0].gate_stage == COLLECT_TRUE_FORWARD
    assert "true_forward_status:collecting" in verdicts[0].reasons


def test_true_forward_matured_without_ready_catalog_is_operator_review_only():
    verdicts = build_gate_verdicts(
        [_training(candidate_id="uc_1")],
        [_review()],
        true_forward_index={"uc_1": {"status": "matured", "paper_forward_ready": False}},
    )

    assert verdicts[0].gate_stage == OPERATOR_REVIEW_ONLY
    assert "true_forward_matured_is_evidence_not_edge" in verdicts[0].reasons


def test_true_forward_matured_and_ready_catalog_is_only_operator_eligible():
    verdicts = build_gate_verdicts(
        [_training(candidate_id="candidate_1")],
        [_review()],
        true_forward_index={"candidate_1": {"status": "matured", "paper_forward_ready": False}},
        ready_index={
            "candidate_1": {
                "status": "ready_for_paper_runtime",
                "ready_strategy_id": "ready_1",
                "execution_allowed": False,
            }
        },
    )

    assert verdicts[0].gate_stage == ELIGIBLE_FOR_OPERATOR_REVIEW
    assert verdicts[0].evidence_refs["ready_strategy_id"] == "ready_1"
    assert verdicts[0].execution_allowed is False


def test_summary_counts_gate_stages():
    verdicts = build_gate_verdicts([_training()], [_review()])
    summary = summarize_gate(verdicts)

    assert summary["schema"] == "OutcomePromotionGate.v1"
    assert summary["by_stage"] == {NEEDS_RETEST: 1}
    assert summary["execution_allowed"] is False


def test_build_outcome_promotion_gate_reads_existing_private_artifacts(tmp_path):
    derived = tmp_path / "state" / "derived"
    advice = tmp_path / "state" / "llm_advice"
    derived.mkdir(parents=True)
    advice.mkdir(parents=True)
    (derived / "paper_signal_training.jsonl").write_text(
        json.dumps(_training(candidate_id="uc_1"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (advice / "outcome_reviews.jsonl").write_text(
        json.dumps(_review("preserve_pattern"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (derived / "shadow_forward.json").write_text(
        json.dumps({
            "schema": "shadow_forward.v1",
            "by_uc_key": {"uc_1": {"status": "shadow_forward_candidate", "paper_forward_ready": False}},
        }),
        encoding="utf-8",
    )

    gate = build_outcome_promotion_gate(tmp_path)

    assert gate["verdicts"] == 1
    assert gate["by_stage"] == {NEEDS_TRUE_FORWARD: 1}
    assert gate["items"][0]["execution_allowed"] is False
