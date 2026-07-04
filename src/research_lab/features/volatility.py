# -*- coding: utf-8 -*-
"""Volatility features: ATR, ATR percentile, Bollinger width/%B, squeeze ratio.

As-of the close of bar ``idx`` using candles ``0..idx``. None during warmup.
Ported faithfully from src/strategy/indicators.py (parity-tested).
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import _core

Candle = dict[str, Any]


def atr_at(candles: list[Candle], idx: int, period: int = 14) -> float | None:
    sub = candles[:idx + 1]
    atr = _core.atr_series(_core.highs(sub), _core.lows(sub), _core.closes(sub), period)
    if idx >= len(atr) or idx <= period:
        return None
    return atr[idx]


def atr_percentile_at(
    candles: list[Candle], idx: int, period: int = 14, history: int = 50
) -> float | None:
    """ATR rank vs recent history (0..100); == atr_regime percentile."""
    sub = candles[:idx + 1]
    atr = _core.atr_series(_core.highs(sub), _core.lows(sub), _core.closes(sub), period)
    valid = atr[period + 1:]
    if len(valid) < 10:
        return None
    h = min(history, len(valid))
    recent = valid[-h:]
    current = valid[-1]
    return round(sum(1 for v in recent if v <= current) / len(recent) * 100, 1)


def bollinger_at(
    candles: list[Candle], idx: int, period: int = 20, std_mult: float = 2.0
) -> dict[str, float] | None:
    """Bollinger bands as of bar ``idx`` (== calc_bollinger_bands)."""
    sub = _core.closes(candles[:idx + 1])
    if len(sub) < period:
        return None
    window = sub[-period:]
    mid = sum(window) / period
    std = _core.pop_std(window)
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    last = sub[-1]
    pct_b = round((last - lower) / (upper - lower) * 100, 1) if upper != lower else 50.0
    width = round((upper - lower) / mid * 100, 2) if mid else 0.0
    return {
        "upper": round(upper, 6),
        "middle": round(mid, 6),
        "lower": round(lower, 6),
        "pct_b": pct_b,
        "width_pct": width,
    }


def bb_width_at(candles: list[Candle], idx: int, period: int = 20, std_mult: float = 2.0) -> float | None:
    bb = bollinger_at(candles, idx, period, std_mult)
    return None if bb is None else bb["width_pct"]


def squeeze_ratio_at(candles: list[Candle], idx: int, lookback: int = 20) -> float | None:
    """Range compression: current N-bar range / prior N-bar range (<1 = contracting).

    Uses bars strictly before ``idx`` for both windows, matching the no-lookahead
    convention of signals_volatility_squeeze_breakout.
    """
    if idx - 2 * lookback < 0 or lookback <= 0:
        return None
    h, low_arr = _core.highs(candles), _core.lows(candles)
    cur_high = max(h[idx - lookback:idx])
    cur_low = min(low_arr[idx - lookback:idx])
    prev_high = max(h[idx - 2 * lookback:idx - lookback])
    prev_low = min(low_arr[idx - 2 * lookback:idx - lookback])
    prev_range = prev_high - prev_low
    if prev_range <= 0:
        return None
    return round((cur_high - cur_low) / prev_range, 4)
