from __future__ import annotations

import numpy as np

from src.strategy.indicators import atr_regime, find_fvg, find_swing_levels


def test_find_swing_levels_excludes_forming_candle_from_right_window() -> None:
    highs = np.array([1.0, 2.0, 5.0, 2.0, 1.0, 9.0])
    lows = np.array([0.5, 1.0, 1.5, 1.0, 0.5, 0.1])

    swings = find_swing_levels(highs, lows, lookback=1, count=4)

    assert swings["recent_highs"] == [5.0]
    assert 9.0 not in swings["recent_highs"]


def test_atr_regime_returns_expected_shape() -> None:
    closes = np.linspace(100.0, 140.0, 80)
    highs = closes + 2.0
    lows = closes - 2.0

    pct, label = atr_regime(highs, lows, closes, period=14, history=30)

    assert isinstance(pct, float)
    assert 0.0 <= pct <= 100.0
    assert isinstance(label, str)
    assert label


def test_find_fvg_returns_nearest_gap_for_direction() -> None:
    candles = [
        [0, 10, 11, 9, 10, 1],
        [1, 10, 10.5, 9.8, 10.2, 1],
        [2, 10.3, 12.5, 12.0, 12.2, 1],
        [3, 12.1, 12.2, 11.8, 12.0, 1],
    ]

    gaps = find_fvg(candles, "bull", lookback=4)

    assert gaps
    assert gaps[0]["low"] == 11.0
    assert gaps[0]["high"] == 12.0
