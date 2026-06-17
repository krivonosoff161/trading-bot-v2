# -*- coding: utf-8 -*-
"""fractal_swing_break_retest — break a confirmed fractal pivot, enter on the retest.

A swing/fractal high (pivot, confirmed ``lookback`` bars after it formed) that price
closes above is a structural break; the entry is the first successful retest+hold of
that level (not the break itself), giving a structure-anchored stop. Short mirrors on
swing lows. Signal contract: list of {idx, side, reason}; decision on bar idx, entry
at idx+1; no-lookahead (a pivot is only usable once confirmed).
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import _core
from src.research_lab.strategies._helpers import Candle


def _confirmed_pivots(candles: list[Candle], lookback: int) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """(highs, lows) as (confirm_bar, level); confirm_bar = pivot_bar + lookback."""
    h, low_arr = _core.highs(candles), _core.lows(candles)
    n = len(candles)
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for i in range(lookback, n - lookback):
        win_h = h[i - lookback:i + lookback + 1]
        win_l = low_arr[i - lookback:i + lookback + 1]
        if h[i] >= max(win_h):
            highs.append((i + lookback, h[i]))
        if low_arr[i] <= min(win_l):
            lows.append((i + lookback, low_arr[i]))
    return highs, lows


def _last_level(pivots: list[tuple[int, float]], as_of: int) -> float | None:
    level = None
    for confirm_bar, lvl in pivots:
        if confirm_bar <= as_of:
            level = lvl
        else:
            break
    return level


def _find_retest(candles: list[Candle], break_idx: int, window: int, level: float, tol_pct: float, side: str) -> int | None:
    tol = level * tol_pct / 100
    for j in range(break_idx + 1, min(break_idx + 1 + window, len(candles) - 1)):
        low = float(candles[j]["low"])
        high = float(candles[j]["high"])
        close = float(candles[j]["close"])
        if side == "long" and low <= level + tol and close > level:
            return j
        if side == "short" and high >= level - tol and close < level:
            return j
    return None


def signals_fractal_swing_break_retest(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(params.get("swing_lookback", 3))
    retest_window = int(params.get("retest_window", 5))
    tol_pct = float(params.get("retest_tol_pct", 1.0))
    highs, lows = _confirmed_pivots(candles, lookback)
    c = _core.closes(candles)
    signals: list[dict[str, Any]] = []
    idx = lookback * 2
    while idx < len(candles) - 1:
        res = _last_level(highs, idx)
        sup = _last_level(lows, idx)
        if res is not None and c[idx] > res:
            hit = _find_retest(candles, idx, retest_window, res, tol_pct, "long")
            if hit is not None:
                signals.append({"idx": hit + 1, "side": "long", "reason": "fractal_break_retest_long"})
                idx = hit + 1
                continue
        if sup is not None and c[idx] < sup:
            hit = _find_retest(candles, idx, retest_window, sup, tol_pct, "short")
            if hit is not None:
                signals.append({"idx": hit + 1, "side": "short", "reason": "fractal_break_retest_short"})
                idx = hit + 1
                continue
        idx += 1
    return signals
