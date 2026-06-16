# -*- coding: utf-8 -*-
"""Tests for the feedback reader (hard-validation verdicts -> farm recommendations)."""
from __future__ import annotations

from src.research_lab.feedback_reader import (
    NARROW_PARAMS,
    PROMOTE,
    REGIME_SWEEP,
    REQUIRE_MORE_DATA,
    SUPPRESS,
    WIDEN_PARAMS,
    build_recommendations,
    recommendations_from_feedback,
    summarize,
)


def _fb(status, strategy="mean_reversion_fade", symbol="DOGE-USDT-SWAP", tf="1d", cid="c1"):
    return {
        "candidate_id": cid,
        "symbol": symbol,
        "timeframe": tf,
        "strategy_id": strategy,
        "hard_status": status,
        "failed_checks": [],
        "reason_codes": [],
    }


def test_failed_costs_narrows_not_widens():
    recs = recommendations_from_feedback([_fb("FAILED_COSTS")])
    assert len(recs) == 1
    assert recs[0].action == NARROW_PARAMS
    assert recs[0].action != WIDEN_PARAMS
    assert "do not widen" in recs[0].reason


def test_failed_overfit_suppresses():
    recs = recommendations_from_feedback([_fb("FAILED_OVERFIT")])
    assert recs[0].action == SUPPRESS
    assert recs[0].priority == "high"


def test_regime_only_creates_regime_sweep():
    recs = recommendations_from_feedback([_fb("REGIME_ONLY")])
    assert recs[0].action == REGIME_SWEEP


def test_needs_more_data_requires_data():
    recs = recommendations_from_feedback([_fb("NEEDS_MORE_DATA")])
    assert recs[0].action == REQUIRE_MORE_DATA


def test_passed_card_promotes_but_not_main_engine_ready():
    cards = [{
        "candidate_id": "p1", "symbol": "BTC-USDT-SWAP", "timeframe": "15m",
        "strategy_id": "trend", "hard_status": "PAPER_FORWARD_READY",
        "main_engine_ready": False,
    }]
    recs = build_recommendations([], cards)
    promotes = [r for r in recs if r.action == PROMOTE]
    assert len(promotes) == 1
    # promotion is forward-paper only; reader never asserts main-engine readiness
    assert "not main-engine ready" in promotes[0].reason


def test_paper_forward_ready_not_treated_as_failure():
    # A PAPER_FORWARD_READY row should not produce a failure recommendation.
    recs = recommendations_from_feedback([_fb("PAPER_FORWARD_READY")])
    assert recs == []


def test_same_scope_status_merges_candidate_ids():
    rows = [_fb("NEEDS_MORE_DATA", cid="a"), _fb("NEEDS_MORE_DATA", cid="b")]
    recs = recommendations_from_feedback(rows)
    assert len(recs) == 1
    assert recs[0].candidate_ids == ["a", "b"]


def test_summarize_counts():
    recs = recommendations_from_feedback([
        _fb("FAILED_OVERFIT", cid="a"),
        _fb("NEEDS_MORE_DATA", symbol="ETH-USDT-SWAP", cid="b"),
    ])
    s = summarize(recs)
    assert s["total"] == 2
    assert s["by_action"][SUPPRESS] == 1
    assert s["by_action"][REQUIRE_MORE_DATA] == 1
