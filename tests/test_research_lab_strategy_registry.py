# -*- coding: utf-8 -*-

import pytest

from src.research_lab.strategy_registry import (
    REGISTRY,
    get_strategy,
    list_strategies,
    registry_summary,
    strategy_ids,
)

REQUIRED_STRATEGIES = {
    "momentum_breakout",
    "mean_reversion_fade",
    "volume_shock_continuation",
    "trend_pullback",
    "volatility_squeeze_breakout",
    "range_breakout",
    "moving_average_reclaim",
    "rsi_reversal",
    "donchian_breakout",
    "impulse_continuation",
}


def _trending_candles(n: int = 120) -> list[dict]:
    rows = []
    price = 100.0
    for i in range(n):
        if i in {40, 80}:
            price *= 1.15
            vol = 9000
        else:
            price *= 1.012 if i % 4 else 0.99
            vol = 1000 + i
        rows.append(
            {
                "ts": 1_700_000_000_000 + i * 86_400_000,
                "open": round(price * 0.99, 6),
                "high": round(price * 1.04, 6),
                "low": round(price * 0.96, 6),
                "close": round(price, 6),
                "vol": vol,
            }
        )
    return rows


def test_registry_contains_required_strategies():
    assert REQUIRED_STRATEGIES <= set(strategy_ids())
    assert len(REGISTRY) >= 10


def test_registry_metadata_complete():
    for d in list_strategies():
        assert d.strategy_id and d.display_name and d.family and d.description
        assert d.compatible_asset_classes
        assert d.compatible_timeframes
        assert "hold_bars" in d.parameter_defaults
        assert callable(d.generate_signals)


def test_every_strategy_runs_with_defaults_and_returns_valid_signals():
    candles = _trending_candles()
    for d in list_strategies():
        signals = d.generate_signals(candles, dict(d.parameter_defaults))
        assert isinstance(signals, list), d.strategy_id
        for sig in signals:
            assert sig["side"] in {"long", "short"}, d.strategy_id
            assert 0 < sig["idx"] <= len(candles) - 1, d.strategy_id
            assert sig["reason"], d.strategy_id


def test_strategies_are_deterministic():
    candles = _trending_candles()
    for d in list_strategies():
        first = d.generate_signals(candles, dict(d.parameter_defaults))
        second = d.generate_signals(candles, dict(d.parameter_defaults))
        assert first == second, d.strategy_id


def test_get_strategy_unknown_raises():
    with pytest.raises(ValueError):
        get_strategy("no_such_strategy")


def test_registry_summary_is_public_safe():
    rows = registry_summary()
    assert len(rows) == len(REGISTRY)
    for row in rows:
        assert set(row) == {
            "strategy_id", "display_name", "family", "description",
            "compatible_asset_classes", "compatible_timeframes",
            "parameter_defaults", "risk_notes",
        }
