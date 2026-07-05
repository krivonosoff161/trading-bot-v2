from src.research_lab.outcome_learning import (
    build_outcome_learning_case,
    build_outcome_review_pack,
    recommendations_from_outcome_reviews,
    learning_summary,
)


def _row(**overrides):
    row = {
        "schema": "TrainingRow.v2",
        "training_row_id": "training_s1",
        "paper_signal_id": "s1",
        "symbol": "A_USDT_SWAP",
        "okx_inst_id": "A-USDT-SWAP",
        "timeframe": "15m",
        "family": "early_tp_tactical",
        "side": "long",
        "entry_mid": 100.5,
        "entry_zone_low": 100.0,
        "entry_zone_high": 101.0,
        "stop_loss": 98.0,
        "tp1": 105.0,
        "status": "reviewed",
        "mode": "live",
        "exit_mode": "partial_be",
        "result": "stop",
        "diagnosis": "bad_exit_gave_back",
        "net_pct": -0.8,
        "net_r": -0.4,
        "gross_pct": -0.7,
        "mfe_pct": 1.6,
        "mae_pct": 0.5,
        "capture": 0.0,
        "risk_pct": 2.5,
        "final_card_text": "private human card text",
        "paper_only": True,
        "execution_allowed": False,
    }
    row.update(overrides)
    return row


def test_outcome_learning_case_routes_give_back_loss_to_exit_retest():
    case = build_outcome_learning_case(_row(), peers=[_row()])

    assert case.schema == "OutcomeLearningCase.v1"
    assert case.review_kind == "loss"
    assert case.outcome_bucket == "gave_back"
    assert case.actionability == "retest_exit_or_capture"
    assert "exit_mode_partial_be_vs_fixed" in case.next_test_dimensions
    assert case.paper_only is True
    assert case.execution_allowed is False


def test_outcome_review_pack_is_sanitized_for_llm_review():
    pack = build_outcome_review_pack(_row(), peers=[_row()])
    encoded = str(pack)

    assert pack["schema"] == "OutcomeLearningCase.v1.review_input"
    assert pack["hard_rules"]["llm_may_change_trade_numbers"] is False
    assert "entry_mid" not in encoded
    assert "entry_zone_low" not in encoded
    assert "stop_loss" not in encoded
    assert "tp1" not in encoded
    assert "final_card_text" not in encoded
    assert pack["paper_only"] is True
    assert pack["execution_allowed"] is False


def test_outcome_learning_summary_counts_review_kinds():
    rows = [
        _row(result="take", diagnosis="good_signal", net_pct=1.2, mfe_pct=1.5, capture=0.8),
        _row(paper_signal_id="s2", training_row_id="training_s2", result="expired", diagnosis="expired_no_entry"),
        _row(paper_signal_id="s3", training_row_id="training_s3", result="simple_be", diagnosis="breakeven_save"),
    ]

    summary = learning_summary(rows)

    assert summary["rows"] == 3
    assert summary["by_review_kind"]["win"] == 1
    assert summary["by_review_kind"]["missed"] == 1
    assert summary["by_review_kind"]["counterfactual"] == 1
    assert summary["execution_allowed"] is False


def test_accepted_outcome_review_becomes_bounded_followup_recommendation():
    rows = [
        _row(
            family="momentum_breakout",
            candidate_id="candidate_1",
            result="stop",
            diagnosis="bad_exit_gave_back",
        )
    ]
    reviews = [
        {
            "role_id": "outcome_reviewer",
            "review_id": "llmr_1",
            "source_ref": "training_s1",
            "accepted": True,
            "payload": {
                "summary": "Exit gave back positive MFE.",
                "review_kind": "loss",
                "outcome_bucket": "gave_back",
                "actionability": "retest_exit_or_capture",
                "next_test_dimensions": ["exit_mode_partial_be_vs_fixed"],
                "confidence": 0.7,
            },
        }
    ]

    recs = recommendations_from_outcome_reviews(rows, reviews)

    assert len(recs) == 1
    assert recs[0].action == "NARROW_PARAMS"
    assert recs[0].strategy_id == "momentum_breakout"
    assert recs[0].candidate_ids == ["candidate_1"]
    assert "outcome_review:llmr_1" in recs[0].reason_codes
