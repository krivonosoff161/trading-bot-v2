# -*- coding: utf-8 -*-

from src.research_lab.proposal_generator import generate_proposals_from_registry
from src.research_lab.universe import load_universe

NOW = "2026-06-13T00:00:00+00:00"


def _entry(status, symbol="BTC_USDT_SWAP", reasons=None, capture=None, params=None):
    metrics = {"n_trades": 25, "avg_net_pct": 0.5}
    if capture is not None:
        metrics["entry_timing"] = {"avg_capture_ratio": capture}
    return {
        "validation_status": status,
        "symbol": symbol,
        "strategy_id": "momentum_breakout",
        "params": params or {"lookback": 20, "hold_bars": 5},
        "validation_reasons": reasons or [],
        "metrics_summary": metrics,
        "experiment_id": "exp1",
    }


def _by_rule(proposals):
    return {p.reason_codes[0]: p for p in proposals}


def test_generator_maps_statuses_to_rules():
    entries = [
        _entry("FORWARD_PAPER"),
        _entry("REGIME_SPECIFIC", symbol="ETH_USDT_SWAP", reasons=["strong_regime_bucket:high|up|normal"]),
        _entry("OBSERVE", symbol="SOL_USDT_SWAP", capture=0.1),
        _entry("OBSERVE", symbol="XRP_USDT_SWAP", capture=0.9),
        _entry("REJECT", symbol="BNB_USDT_SWAP"),
    ]
    proposals = generate_proposals_from_registry(entries, universe=load_universe(), created_at=NOW, limit=10)
    rules = _by_rule(proposals)
    assert "candidate_for_forward" in rules
    assert "regime_dependent" in rules
    assert "entry_late" in rules
    assert "unstable_parameters" in rules
    # REJECT produced nothing
    assert all(p.expected_validation != "REJECT" for p in proposals)


def test_forward_uses_neighborhood_grid():
    proposals = generate_proposals_from_registry([_entry("FORWARD_PAPER")], universe=load_universe(), created_at=NOW)
    grid = proposals[0].parameter_grid["momentum_breakout"]
    assert len(grid) > 1  # neighborhood, not a single point


def test_regime_specific_has_filters_and_single_point():
    entries = [_entry("REGIME_SPECIFIC", reasons=["strong_regime_bucket:high|up|normal"])]
    p = generate_proposals_from_registry(entries, universe=load_universe(), created_at=NOW)[0]
    assert p.filters.get("trend") == ["up"]
    assert len(p.parameter_grid["momentum_breakout"]) == 1


def test_entry_late_requests_shorter_timeframe():
    entries = [_entry("OBSERVE", capture=0.05)]
    p = generate_proposals_from_registry(entries, universe=load_universe(), created_at=NOW)[0]
    assert p.requested_timeframe == "15m"
    assert p.reason_codes == ["entry_late"]


def test_too_few_trades_broadens_symbols():
    entries = [_entry("OBSERVE", symbol="ZEC_USDT_SWAP", reasons=["too_few_trades"])]
    p = generate_proposals_from_registry(entries, universe=load_universe(), created_at=NOW)[0]
    assert p.reason_codes == ["too_few_trades"]
    assert len(p.symbols) > 1  # peers added
