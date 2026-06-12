# -*- coding: utf-8 -*-
"""Breakout-family signal generators.

Signal contract: list of {"idx": entry_bar, "side": "long"|"short", "reason": str}.
The decision is made on bar idx-1; entry happens at the open of bar idx.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.strategies._helpers import Candle, window_high, window_low


def signals_momentum_breakout(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(params.get("lookback", 20))
    threshold_pct = float(params.get("threshold_pct", 0.0))
    signals = []
    for idx in range(lookback, len(candles) - 1):
        high = window_high(candles, idx, lookback)
        low = window_low(candles, idx, lookback)
        close = float(candles[idx]["close"])
        if high is None or low is None:
            continue
        if close > high * (1 + threshold_pct / 100):
            signals.append({"idx": idx + 1, "side": "long", "reason": "breakout_high"})
        elif close < low * (1 - threshold_pct / 100):
            signals.append({"idx": idx + 1, "side": "short", "reason": "breakout_low"})
    return signals


def signals_donchian_breakout(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Intrabar channel pierce: high/low of the current bar crosses the channel."""
    lookback = int(params.get("lookback", 20))
    signals = []
    for idx in range(lookback, len(candles) - 1):
        channel_high = window_high(candles, idx, lookback)
        channel_low = window_low(candles, idx, lookback)
        if channel_high is None or channel_low is None:
            continue
        if float(candles[idx]["high"]) > channel_high:
            signals.append({"idx": idx + 1, "side": "long", "reason": "donchian_pierce_high"})
        elif float(candles[idx]["low"]) < channel_low:
            signals.append({"idx": idx + 1, "side": "short", "reason": "donchian_pierce_low"})
    return signals


def signals_range_breakout(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Breakout out of a tight consolidation range."""
    lookback = int(params.get("lookback", 30))
    max_range_pct = float(params.get("max_range_pct", 12.0))
    signals = []
    for idx in range(lookback, len(candles) - 1):
        high = window_high(candles, idx, lookback)
        low = window_low(candles, idx, lookback)
        if high is None or low is None or low <= 0:
            continue
        if (high / low - 1) * 100 > max_range_pct:
            continue
        close = float(candles[idx]["close"])
        if close > high:
            signals.append({"idx": idx + 1, "side": "long", "reason": "range_breakout_up"})
        elif close < low:
            signals.append({"idx": idx + 1, "side": "short", "reason": "range_breakout_down"})
    return signals


def signals_volatility_squeeze_breakout(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Range contraction vs the prior window, then a close outside the squeeze."""
    lookback = int(params.get("lookback", 20))
    squeeze_ratio = float(params.get("squeeze_ratio", 0.6))
    signals = []
    for idx in range(2 * lookback, len(candles) - 1):
        cur_high = window_high(candles, idx, lookback)
        cur_low = window_low(candles, idx, lookback)
        prev_high = window_high(candles, idx - lookback, lookback)
        prev_low = window_low(candles, idx - lookback, lookback)
        if None in (cur_high, cur_low, prev_high, prev_low):
            continue
        cur_range = cur_high - cur_low
        prev_range = prev_high - prev_low
        if prev_range <= 0 or cur_range > prev_range * squeeze_ratio:
            continue
        close = float(candles[idx]["close"])
        if close > cur_high:
            signals.append({"idx": idx + 1, "side": "long", "reason": "squeeze_break_up"})
        elif close < cur_low:
            signals.append({"idx": idx + 1, "side": "short", "reason": "squeeze_break_down"})
    return signals


def signals_breakout_retest(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Enter on the first successful retest of a broken level, not on the break."""
    lookback = int(params.get("lookback", 20))
    retest_window = int(params.get("retest_window", 5))
    tol_pct = float(params.get("retest_tol_pct", 1.0))
    signals = []
    idx = lookback
    while idx < len(candles) - 1:
        level_high = window_high(candles, idx, lookback)
        level_low = window_low(candles, idx, lookback)
        close = float(candles[idx]["close"])
        if level_high is not None and close > level_high:
            hit = _find_retest(candles, idx, retest_window, level_high, tol_pct, "long")
            if hit is not None:
                signals.append({"idx": hit + 1, "side": "long", "reason": "retest_hold_support"})
                idx = hit + 1
                continue
        elif level_low is not None and close < level_low:
            hit = _find_retest(candles, idx, retest_window, level_low, tol_pct, "short")
            if hit is not None:
                signals.append({"idx": hit + 1, "side": "short", "reason": "retest_hold_resistance"})
                idx = hit + 1
                continue
        idx += 1
    return signals


def _find_retest(
    candles: list[Candle], break_idx: int, retest_window: int, level: float, tol_pct: float, side: str
) -> int | None:
    tol = level * tol_pct / 100
    for j in range(break_idx + 1, min(break_idx + 1 + retest_window, len(candles) - 1)):
        if side == "long":
            if float(candles[j]["low"]) <= level + tol and float(candles[j]["close"]) > level:
                return j
        else:
            if float(candles[j]["high"]) >= level - tol and float(candles[j]["close"]) < level:
                return j
    return None
