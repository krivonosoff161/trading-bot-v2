# -*- coding: utf-8 -*-
"""Core causal indicator series for the lab feature layer (stdlib only).

These are pure-Python re-implementations of the numpy indicators in
``src/strategy/indicators.py``. They are kept faithful on purpose so a parity
test (``tests/test_features_parity.py``) can assert they match the old main
engine within float tolerance. Porting the math here (instead of importing the
old engine) keeps the research feature layer free of the live engine and lets
strategies stay stdlib-only.

Input convention: ``candles`` is a chronological (oldest-first) list of dicts
with float-coercible ``open/high/low/close/vol`` (and ``ts``). Every series is
*causal*: value at index ``i`` uses only candles ``0..i``. Warmup positions are
filled with ``0.0`` exactly like the numpy reference, so callers must gate on a
minimum index before trusting a value.
"""

from __future__ import annotations

from typing import Any

Candle = dict[str, Any]


def highs(candles: list[Candle]) -> list[float]:
    return [float(c["high"]) for c in candles]


def lows(candles: list[Candle]) -> list[float]:
    return [float(c["low"]) for c in candles]


def closes(candles: list[Candle]) -> list[float]:
    return [float(c["close"]) for c in candles]


def opens(candles: list[Candle]) -> list[float]:
    return [float(c["open"]) for c in candles]


def vols(candles: list[Candle]) -> list[float]:
    return [float(c.get("vol") or 0.0) for c in candles]


def ema_series(close_values: list[float], period: int) -> list[float]:
    """EMA seeded with the SMA of the first ``period`` closes (== calc_ema)."""
    n = len(close_values)
    ema = [0.0] * n
    if n < period or period <= 0:
        return ema
    k = 2.0 / (period + 1)
    ema[period - 1] = sum(close_values[:period]) / period
    for i in range(period, n):
        ema[i] = close_values[i] * k + ema[i - 1] * (1 - k)
    return ema


def _true_range(h: list[float], low_arr: list[float], c: list[float]) -> list[float]:
    n = len(c)
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(h[i] - low_arr[i], abs(h[i] - c[i - 1]), abs(low_arr[i] - c[i - 1]))
    return tr


def atr_series(h: list[float], low_arr: list[float], c: list[float], period: int = 14) -> list[float]:
    """Wilder-smoothed ATR array (== calc_atr_series; warmup positions are 0)."""
    n = len(c)
    tr = _true_range(h, low_arr, c)
    atr = [0.0] * n
    if period < n:
        atr[period] = sum(tr[1:period + 1]) / period
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def adx_series(
    h: list[float], low_arr: list[float], c: list[float], period: int = 14
) -> tuple[list[float], list[float], list[float]]:
    """Full ADX / +DI / -DI arrays (generalizes calc_adx to every bar)."""
    n = len(c)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    tr = [0.0] * n
    for i in range(1, n):
        up = h[i] - h[i - 1]
        down = low_arr[i - 1] - low_arr[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
        tr[i] = max(h[i] - low_arr[i], abs(h[i] - c[i - 1]), abs(low_arr[i] - c[i - 1]))

    def _wilder(arr: list[float]) -> list[float]:
        res = [0.0] * n
        if period >= n:
            return res
        res[period] = sum(arr[1:period + 1])
        for i in range(period + 1, n):
            res[i] = res[i - 1] - res[i - 1] / period + arr[i]
        return res

    smooth_tr = _wilder(tr)
    smooth_plus = _wilder(plus_dm)
    smooth_minus = _wilder(minus_dm)
    plus_di = [0.0] * n
    minus_di = [0.0] * n
    dx = [0.0] * n
    for i in range(n):
        st = smooth_tr[i]
        if st > 0:
            plus_di[i] = 100 * smooth_plus[i] / st
            minus_di[i] = 100 * smooth_minus[i] / st
        sum_di = plus_di[i] + minus_di[i]
        if sum_di > 0:
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / sum_di

    adx = [0.0] * n
    start = period * 2
    if start < n:
        adx[start] = sum(dx[period:period * 2]) / period
        for i in range(start + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx, plus_di, minus_di


def rsi_wilder(close_values: list[float], idx: int, period: int = 14) -> float | None:
    """Wilder-smoothed RSI as of bar ``idx`` (== calc_rsi on closes[:idx+1])."""
    if idx + 1 < period + 1 or period <= 0:
        return None
    window = close_values[:idx + 1]
    deltas = [window[i] - window[i - 1] for i in range(1, len(window))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def pop_std(values: list[float]) -> float:
    """Population standard deviation (ddof=0), matching numpy np.std default."""
    n = len(values)
    if n == 0:
        return 0.0
    mean = sum(values) / n
    return (sum((v - mean) ** 2 for v in values) / n) ** 0.5
