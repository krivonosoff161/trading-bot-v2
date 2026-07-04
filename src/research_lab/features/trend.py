# -*- coding: utf-8 -*-
"""Trend features: EMA, EMA slope (Drozdov), ADX / DI spread, SuperTrend.

All values are as-of the close of bar ``idx`` using only candles ``0..idx``.
Returns ``None`` when there is not enough warmup history.
"""

from __future__ import annotations

import math
from typing import Any

from src.research_lab.features import _core

Candle = dict[str, Any]


def ema_at(candles: list[Candle], idx: int, period: int) -> float | None:
    series = _core.ema_series(_core.closes(candles[:idx + 1]), period)
    if idx >= len(series) or series[idx] == 0.0:
        return None
    return series[idx]


def slope_deg(candles: list[Candle], idx: int, period: int = 5) -> float:
    """Linear-regression angle (degrees) of the last ``period`` closes (== calc_slope).

    x and y are both min-max normalized to [0,1] so the angle is scale-free.
    """
    if idx + 1 < period:
        return 0.0
    y = _core.closes(candles[idx - period + 1:idx + 1])
    y_min, y_max = min(y), max(y)
    y_range = y_max - y_min
    if y_range == 0.0:
        return 0.0
    xs = [i / (period - 1) for i in range(period)] if period > 1 else [0.0]
    ys = [(v - y_min) / y_range for v in y]
    x_bar = sum(xs) / period
    y_bar = sum(ys) / period
    num = sum((xs[i] - x_bar) * (ys[i] - y_bar) for i in range(period))
    den = sum((xs[i] - x_bar) ** 2 for i in range(period))
    if den == 0.0:
        return 0.0
    slope = num / den
    return math.degrees(math.atan(slope))


def adx_at(candles: list[Candle], idx: int, period: int = 14) -> tuple[float, float, float] | None:
    """(adx, plus_di, minus_di) as of bar ``idx``; None during warmup."""
    sub = candles[:idx + 1]
    h, low_arr, c = _core.highs(sub), _core.lows(sub), _core.closes(sub)
    adx, plus_di, minus_di = _core.adx_series(h, low_arr, c, period)
    if idx >= len(adx) or idx < period * 2:
        return None
    return adx[idx], plus_di[idx], minus_di[idx]


def di_spread_at(candles: list[Candle], idx: int, period: int = 14) -> float | None:
    res = adx_at(candles, idx, period)
    if res is None:
        return None
    _adx, plus_di, minus_di = res
    return plus_di - minus_di


def supertrend_dir_at(
    candles: list[Candle], idx: int, period: int = 14, multiplier: float = 3.0
) -> str | None:
    """SuperTrend direction ('up'/'down') as of bar ``idx`` (== calc_supertrend dir)."""
    sub = candles[:idx + 1]
    n = len(sub)
    if n < period + 2:
        return None
    h, low_arr, c = _core.highs(sub), _core.lows(sub), _core.closes(sub)
    atr = _core.atr_series(h, low_arr, c, period)
    upper = [0.0] * n
    lower = [0.0] * n
    st = [0.0] * n
    direction = [1] * n
    for i in range(period, n):
        mid = (h[i] + low_arr[i]) / 2
        basic_upper = mid + multiplier * atr[i]
        basic_lower = mid - multiplier * atr[i]
        upper[i] = basic_upper if (basic_upper < upper[i - 1] or c[i - 1] > upper[i - 1]) else upper[i - 1]
        lower[i] = basic_lower if (basic_lower > lower[i - 1] or c[i - 1] < lower[i - 1]) else lower[i - 1]
        if st[i - 1] == upper[i - 1]:
            st[i] = lower[i] if c[i] > upper[i] else upper[i]
        else:
            st[i] = upper[i] if c[i] < lower[i] else lower[i]
        direction[i] = 1 if c[i] > st[i] else -1
    return "up" if direction[-1] == 1 else "down"
