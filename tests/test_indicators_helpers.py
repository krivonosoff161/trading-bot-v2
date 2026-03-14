from __future__ import annotations

import numpy as np

from src.strategy.indicators import atr_regime, find_swing_levels


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
