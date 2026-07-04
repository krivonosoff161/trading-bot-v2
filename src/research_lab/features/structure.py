# -*- coding: utf-8 -*-
"""Structure features: swing/fractal pivots, range bounds, breakout quality.

As-of the close of bar ``idx`` using candles ``0..idx``. Pivots are only counted
once confirmed (a pivot at bar ``p`` needs ``lookback`` bars on each side, so it
is visible only from bar ``p + lookback`` onward — no lookahead).
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import _core

Candle = dict[str, Any]


def swing_levels(
    candles: list[Candle], idx: int, lookback: int = 3, count: int = 4
) -> dict[str, list[float]]:
    """Confirmed pivot highs/lows visible as of bar ``idx`` (== find_swing_levels).

    A pivot high at bar i: highs[i] is the max of [i-lookback, i+lookback]. Only
    pivots whose right window has fully closed by ``idx`` (i + lookback <= idx) are
    returned. Last ``count`` of each are kept (nearest in time last).
    """
    h, low_arr = _core.highs(candles), _core.lows(candles)
    highs_out: list[float] = []
    lows_out: list[float] = []
    last_confirmable = idx - lookback
    for i in range(lookback, last_confirmable + 1):
        win_h = h[max(0, i - lookback): i + lookback + 1]
        win_l = low_arr[max(0, i - lookback): i + lookback + 1]
        if h[i] >= max(win_h):
            highs_out.append(h[i])
        if low_arr[i] <= min(win_l):
            lows_out.append(low_arr[i])
    return {
        "recent_highs": highs_out[-count:] if highs_out else [],
        "recent_lows": lows_out[-count:] if lows_out else [],
    }


def range_bounds(candles: list[Candle], idx: int, lookback: int = 20) -> tuple[float, float] | None:
    """(high, low) of the ``lookback`` bars strictly before ``idx``."""
    if idx - lookback < 0 or lookback <= 0:
        return None
    h, low_arr = _core.highs(candles), _core.lows(candles)
    return max(h[idx - lookback:idx]), min(low_arr[idx - lookback:idx])


def body_pct(candle: Candle) -> float:
    """Signed body as a fraction of the bar range (0..1 magnitude)."""
    high = float(candle["high"])
    low = float(candle["low"])
    rng = high - low
    if rng <= 0:
        return 0.0
    return (float(candle["close"]) - float(candle["open"])) / rng


def close_position(candle: Candle) -> float:
    """Where the close sits in the bar range: 0=at low, 1=at high."""
    high = float(candle["high"])
    low = float(candle["low"])
    rng = high - low
    if rng <= 0:
        return 0.5
    return (float(candle["close"]) - low) / rng


def breakout_quality_at(candles: list[Candle], idx: int, lookback: int = 20) -> dict[str, Any] | None:
    """Causal breakout quality of bar ``idx`` vs the prior ``lookback``-bar range.

    Returns side ('long'/'short'/None for no-break), body strength and close
    position. Follow-through is NOT included here (it is forward-looking and is
    left to the simulator's hold/exit, never peeked at decision time).
    """
    bounds = range_bounds(candles, idx, lookback)
    if bounds is None:
        return None
    hi, lo = bounds
    candle = candles[idx]
    close = float(candle["close"])
    side = None
    if close > hi:
        side = "long"
    elif close < lo:
        side = "short"
    return {
        "side": side,
        "body_pct": round(body_pct(candle), 4),
        "close_position": round(close_position(candle), 4),
        "range_high": hi,
        "range_low": lo,
    }
