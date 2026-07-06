from src.research_lab.outcome_learning import (
    build_outcome_learning_case,
    build_outcome_review_pack,
    recommendations_from_outcome_reviews,
    learning_summary,
)
from src.research_lab.data_prepare import write_candles
from src.research_lab.outcome_retest import build_outcome_retest_specs


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
        "boundary_ts": 1_700_000_000_000,
        "observed_entry": 100.0,
        "observed_exit": 98.0,
        "bars_held": 6,
        "reached_tp1": False,
        "partial_done": False,
        "banked_pct": 0.0,
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
    assert pack["original_plan"]["entry_mid"] == 100.5
    assert pack["original_plan"]["stop_loss"] == 98.0
    assert pack["original_plan"]["tp1"] == 105.0
    assert pack["observed_trade"]["observed_entry"] == 100.0
    assert pack["observed_trade"]["observed_exit"] == 98.0
    assert pack["observed_trade"]["observed_return_pct"] == -2.0
    assert pack["market_context"]["status"] == "not_available"
    assert pack["hard_rules"]["llm_may_change_trade_numbers"] is False
    assert pack["hard_rules"]["llm_may_read_trade_numbers"] is True
    assert pack["hard_rules"]["llm_output_must_be_hypotheses_not_orders"] is True
    assert "final_card_text" not in encoded
    assert pack["paper_only"] is True
    assert pack["execution_allowed"] is False


def test_outcome_review_pack_includes_private_candle_path_when_available(tmp_path):
    row = _row()
    tf_ms = 15 * 60_000
    candles = []
    start = int(row["boundary_ts"]) - 3 * tf_ms
    for idx in range(70):
        px = 100 + idx
        candles.append(
            {
                "ts": start + idx * tf_ms,
                "open": px,
                "high": px + 1,
                "low": px - 1,
                "close": px + 0.5,
                "vol": 1000 + idx,
            }
        )
    write_candles(
        candles,
        symbol="A-USDT-SWAP",
        start_ts=start,
        end_ts=start + 69 * tf_ms,
        timeframe="15m",
        data_dir=tmp_path / "market_data" / "15m",
    )

    pack = build_outcome_review_pack(row, peers=[row], private_root=tmp_path)

    assert pack["market_context"]["status"] == "available"
    assert pack["market_context"]["source_label"].startswith("market_data/15m/")
    assert pack["market_context"]["summary"]["status"] == "available"
    assert pack["market_context"]["candles"]
    assert pack["market_context"]["candles"][0]["close_vs_entry_pct"] is not None


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


def test_accepted_outcome_review_becomes_retest_spec():
    rows = [
        _row(
            family="momentum_breakout",
            candidate_id="candidate_1",
            result="stop",
            diagnosis="bad_exit_gave_back",
            max_hold_bars=10,
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
                "counterfactual_tests": [{"dimension": "earlier_profit_lock"}],
                "confidence": 0.7,
            },
        }
    ]

    specs = build_outcome_retest_specs(rows, reviews)

    assert len(specs) == 1
    spec = specs[0]
    assert spec.schema == "OutcomeRetestSpec.v1"
    assert spec.queueable is True
    assert spec.execution_allowed is False
    assert spec.paper_only is True
    assert spec.sweep_spec["setup_family"] == "momentum_breakout"
    assert "take_pct" in spec.sweep_spec["exit_grid"]
    assert spec.baseline["diagnosis"] == "bad_exit_gave_back"
