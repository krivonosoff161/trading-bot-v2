# -*- coding: utf-8 -*-
"""Trend-following family signal generators."""

from __future__ import annotations

from typing import Any

from src.research_lab.strategies._helpers import Candle, closes, sma


def signals_trend_pullback(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Join an established trend after a pullback to the fast MA holds."""
    trend_ma = int(params.get("trend_ma", 30))
    pullback_ma = int(params.get("pullback_ma", 10))
    close_values = closes(candles)
    signals = []
    for idx in range(trend_ma, len(candles) - 1):
        slow = sma(close_values, idx, trend_ma)
        fast = sma(close_values, idx, pullback_ma)
        if slow is None or fast is None:
            continue
        close = float(candles[idx]["close"])
        low = float(candles[idx]["low"])
        high = float(candles[idx]["high"])
        if close > slow and low <= fast and close > fast:
            signals.append({"idx": idx + 1, "side": "long", "reason": "pullback_hold_uptrend"})
        elif close < slow and high >= fast and close < fast:
            signals.append({"idx": idx + 1, "side": "short", "reason": "pullback_hold_downtrend"})
    return signals


def signals_moving_average_reclaim(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Enter when price reclaims its MA after a sustained stay on the other side."""
    ma = int(params.get("ma", 20))
    below_bars = int(params.get("below_bars", 3))
    close_values = closes(candles)
    signals = []
    for idx in range(ma + below_bars, len(candles) - 1):
        cur_ma = sma(close_values, idx, ma)
        if cur_ma is None:
            continue
        close = float(candles[idx]["close"])
        if close > cur_ma and _stayed_side(close_values, idx, below_bars, ma, "below"):
            signals.append({"idx": idx + 1, "side": "long", "reason": "ma_reclaim_up"})
        elif close < cur_ma and _stayed_side(close_values, idx, below_bars, ma, "above"):
            signals.append({"idx": idx + 1, "side": "short", "reason": "ma_reclaim_down"})
    return signals


def _stayed_side(close_values: list[float], idx: int, bars: int, ma: int, side: str) -> bool:
    for j in range(idx - bars, idx):
        ma_j = sma(close_values, j, ma)
        if ma_j is None:
            return False
        if side == "below" and close_values[j] >= ma_j:
            return False
        if side == "above" and close_values[j] <= ma_j:
            return False
    return True
