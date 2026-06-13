# -*- coding: utf-8 -*-

from src.research_lab.reducer import (
    CANDIDATE_FOR_FORWARD,
    ENTRY_LATE,
    FORWARD_PAPER,
    NEEDS_MORE_DATA,
    OBSERVE,
    REGIME_SPECIFIC,
    REJECT,
    UNSTABLE_PARAMETERS,
    reduce_results,
)


def _m(n, avg, test, pf=1.5, total=30.0, dd=5.0, regime=None, entry=None):
    m = {
        "n_trades": n, "avg_net_pct": avg, "test_avg_net_pct": test,
        "profit_factor": pf, "total_net_pct": total, "max_drawdown_pct": dd,
    }
    if regime is not None:
        m["regime_breakdown"] = regime
    if entry is not None:
        m["entry_timing"] = entry
    return m


def _rows(family, symbol, metrics_list):
    return [{"family": family, "symbol": symbol, "metrics": m} for m in metrics_list]


def test_single_lucky_best_not_promoted_without_neighbor_support():
    metrics = [_m(30, 1.0, 0.8)] + [_m(30, -0.5, -0.4, pf=0.8) for _ in range(4)]
    report = reduce_results(_rows("momentum_breakout", "BTC_USDT_SWAP", metrics))
    g = report.groups[0]
    assert g.verdict == OBSERVE
    assert g.verdict != FORWARD_PAPER
    assert UNSTABLE_PARAMETERS in g.reason_codes


def test_strong_neighbor_support_promotes_to_forward():
    metrics = [_m(30, 1.0, 0.8) for _ in range(4)] + [_m(30, -0.2, -0.1, pf=0.9)]
    report = reduce_results(_rows("momentum_breakout", "BTC_USDT_SWAP", metrics))
    g = report.groups[0]
    assert g.verdict == FORWARD_PAPER
    assert CANDIDATE_FOR_FORWARD in g.reason_codes
    assert g.support_ratio >= 0.6


def test_too_few_trades_needs_more_data():
    metrics = [_m(2, 1.0, 1.0) for _ in range(3)]
    report = reduce_results(_rows("rsi_reversal", "ETH_USDT_SWAP", metrics))
    assert report.groups[0].verdict == NEEDS_MORE_DATA


def test_all_negative_rejected():
    metrics = [_m(30, -1.0, -1.0, pf=0.7) for _ in range(4)]
    report = reduce_results(_rows("mean_reversion_fade", "SOL_USDT_SWAP", metrics))
    assert report.groups[0].verdict == REJECT


def test_regime_carry_yields_regime_specific():
    regime = {"high|up|normal": {"n_trades": 12, "avg_net_pct": 0.6}}
    metrics = [_m(20, -0.1, -0.1, pf=0.9, total=-2.0, regime=regime) for _ in range(2)]
    report = reduce_results(_rows("donchian_breakout", "BTC_USDT_SWAP", metrics))
    assert report.groups[0].verdict == REGIME_SPECIFIC


def test_entry_late_blocks_forward():
    entry = {"avg_capture_ratio": 0.1, "avg_mfe_pct": 5.0, "avg_mae_pct": 4.0}
    metrics = [_m(30, 1.0, 0.8, entry=entry) for _ in range(4)] + [_m(30, -0.2, -0.1, pf=0.9, entry=entry)]
    report = reduce_results(_rows("momentum_breakout", "BTC_USDT_SWAP", metrics))
    g = report.groups[0]
    assert ENTRY_LATE in g.reason_codes
    assert g.verdict == OBSERVE


def test_verdict_counts_aggregate():
    rows = _rows("f", "A", [_m(30, 1.0, 0.8) for _ in range(4)])
    rows += _rows("f", "B", [_m(30, -1.0, -1.0, pf=0.7) for _ in range(3)])
    report = reduce_results(rows)
    assert report.verdict_counts.get(FORWARD_PAPER) == 1
    assert report.verdict_counts.get(REJECT) == 1
