# -*- coding: utf-8 -*-
"""Tests for the feedback follow-up bridge (recommendations -> bounded next steps)."""
from __future__ import annotations

from src.research_lab import feedback_reader as fr
from src.research_lab.feedback_followup import (
    plan_followup,
    plan_followups,
)
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.scanner_bridge import MAX_VARIANTS_CAP
from src.research_lab.sweep_spec import validate_sweep_spec
from src.research_lab.timeframes import load_timeframe_profiles


def _rec(action, *, status="", symbol="DOGE-USDT-SWAP", tf="1d", strategy="mean_reversion_fade", cid="c1"):
    return fr.Recommendation(
        action=action, strategy_id=strategy, symbol=symbol, timeframe=tf,
        reason="r", hard_status=status or action, priority="normal", candidate_ids=[cid],
    )


PARAMS = {"lookback": 8, "hold_bars": 4, "move_pct": 8.0}


def test_narrow_params_queues_bounded_valid_sweep():
    plan = plan_followup(_rec(fr.NARROW_PARAMS, status="FAILED_FRAGILITY"), PARAMS, max_variants=8)
    assert plan.queued is True
    assert plan.sweep is not None
    assert plan.sweep.variant_count() <= plan.sweep.max_variants <= MAX_VARIANTS_CAP
    assert plan.sweep.anchor_symbol == "DOGE-USDT-SWAP"
    assert plan.sweep.timeframe == "1d"
    # validates against the real resource policy + timeframe profiles
    result = validate_sweep_spec(
        plan.sweep, timeframe_profiles=load_timeframe_profiles(),
        resource_policy=load_resource_policy(),
    )
    assert result.ok, result.errors


def test_failed_costs_biases_hold_bars_up():
    plan = plan_followup(_rec(fr.NARROW_PARAMS, status="FAILED_COSTS"), PARAMS, max_variants=8)
    hold_bars = plan.sweep.exit_grid["hold_bars"]
    # lower-turnover bias: every option >= candidate hold_bars (never shorter)
    assert min(hold_bars) >= PARAMS["hold_bars"]


def test_regime_sweep_queues_when_strong_bucket_exists():
    context = {
        "params": PARAMS,
        "validation_reasons": ["strong_regime_bucket:high|up|normal"],
    }
    plan = plan_followup(_rec(fr.REGIME_SWEEP, status="REGIME_ONLY"), context)
    assert plan.queued is True
    assert plan.sweep is not None
    assert plan.sweep.filter_grid == {
        "volatility": ["high"],
        "trend": ["up"],
        "volume": ["normal"],
    }
    result = validate_sweep_spec(
        plan.sweep, timeframe_profiles=load_timeframe_profiles(),
        resource_policy=load_resource_policy(),
    )
    assert result.ok, result.errors


def test_regime_sweep_blocks_without_regime_evidence():
    plan = plan_followup(_rec(fr.REGIME_SWEEP, status="REGIME_ONLY"), PARAMS)
    assert plan.queued is False
    assert plan.not_queued_reason == "missing_regime_filter"


def test_require_more_data_is_note():
    plan = plan_followup(_rec(fr.REQUIRE_MORE_DATA, status="NEEDS_MORE_DATA"), PARAMS)
    assert plan.queued is False
    assert plan.not_queued_reason == "needs_more_data"


def test_promote_is_forward_paper_note():
    plan = plan_followup(_rec(fr.PROMOTE, status="PAPER_FORWARD_READY"), PARAMS)
    assert plan.queued is False
    assert plan.not_queued_reason == "promote_is_forward_paper_only"


def test_reject_and_suppress_are_notes():
    for action in (fr.REJECT, fr.SUPPRESS):
        plan = plan_followup(_rec(action), PARAMS)
        assert plan.queued is False
        assert plan.not_queued_reason == "suppressed_or_rejected"


def test_unknown_timeframe_blocks_queue():
    plan = plan_followup(_rec(fr.NARROW_PARAMS, tf="unknown"), PARAMS)
    assert plan.queued is False
    assert plan.not_queued_reason == "unknown_timeframe"


def test_unknown_strategy_blocks_queue():
    plan = plan_followup(_rec(fr.NARROW_PARAMS, strategy="nope"), PARAMS)
    assert plan.queued is False
    assert plan.not_queued_reason == "unknown_strategy"


def test_no_candidate_params_blocks_queue():
    plan = plan_followup(_rec(fr.NARROW_PARAMS), None)
    assert plan.queued is False
    assert plan.not_queued_reason == "no_candidate_params"


def test_widen_params_capped_and_queues():
    plan = plan_followup(_rec(fr.WIDEN_PARAMS, status="FAILED_OOS"), PARAMS, max_variants=8)
    assert plan.queued is True
    # widened lookback is capped at 2x the family default, never runaway
    from src.research_lab.strategy_registry import get_strategy
    lb_def = int(get_strategy("mean_reversion_fade").parameter_defaults["lookback"])
    assert max(plan.sweep.setup_grid["lookback"]) <= lb_def * 2


def test_symbol_cap_and_allowed_actions():
    recs = [
        _rec(fr.NARROW_PARAMS, status="FAILED_FRAGILITY", symbol="A-USDT-SWAP", cid="a"),
        _rec(fr.NARROW_PARAMS, status="FAILED_FRAGILITY", symbol="B-USDT-SWAP", cid="b"),
        _rec(fr.NARROW_PARAMS, status="FAILED_FRAGILITY", symbol="C-USDT-SWAP", cid="c"),
    ]
    params = {"a": PARAMS, "b": PARAMS, "c": PARAMS}
    plans = plan_followups(recs, params, max_symbols=2, allowed_actions={fr.NARROW_PARAMS})
    queued = [p for p in plans if p.queued]
    assert len(queued) == 2  # third blocked by symbol cap
    assert any(p.not_queued_reason == "symbol_cap_reached" for p in plans)

    # allowed_actions gate: empty allowed -> nothing queues
    plans2 = plan_followups(recs, params, max_symbols=5, allowed_actions=set())
    assert all(not p.queued for p in plans2)
    assert all(p.not_queued_reason == "action_not_allowed" for p in plans2)


def test_allowed_actions_can_queue_regime_sweep():
    recs = [_rec(fr.REGIME_SWEEP, status="REGIME_ONLY", cid="r1")]
    params = {"r1": {"params": PARAMS, "regime_summary": {"dominant_bucket": "medium|down|normal"}}}
    plans = plan_followups(recs, params, allowed_actions={fr.REGIME_SWEEP})
    assert plans[0].queued is True
    assert plans[0].sweep.filter_grid["trend"] == ["down"]


def test_narrow_followup_is_idempotent_sweep_id():
    a = plan_followup(_rec(fr.NARROW_PARAMS, status="FAILED_FRAGILITY"), PARAMS)
    b = plan_followup(_rec(fr.NARROW_PARAMS, status="FAILED_FRAGILITY"), PARAMS)
    assert a.sweep.sweep_id == b.sweep.sweep_id  # deterministic -> dedupes on re-run


def test_followup_uses_family_axis_without_inventing_lookback():
    params = {"period": 14, "oversold": 30, "overbought": 70, "hold_bars": 4}
    plan = plan_followup(
        _rec(fr.NARROW_PARAMS, strategy="rsi_reversal"), params, max_variants=8
    )
    assert plan.queued is True
    assert "period" in plan.sweep.setup_grid
    assert "lookback" not in plan.sweep.setup_grid
