# -*- coding: utf-8 -*-
"""Fair Value Gap (FVG) features.

A 3-candle imbalance, confirmed at the third bar (no lookahead). Ported from
src/strategy/indicators.find_fvg, adapted to chronological lab candle dicts and
evaluated as-of bar ``idx`` (only gaps whose third bar closed by ``idx``).
"""

from __future__ import annotations

from typing import Any

Candle = dict[str, Any]


def find_fvg(
    candles: list[Candle], idx: int, direction: str, lookback: int = 30
) -> list[dict[str, Any]]:
    """Recent FVGs visible as of bar ``idx``, sorted by proximity to close[idx].

    direction 'bull': gap where high[i] < low[i+2] (unfilled demand below).
    direction 'bear': gap where low[i] > high[i+2] (unfilled supply above).
    """
    if idx < 2:
        return []
    current_price = float(candles[idx]["close"])
    start = max(0, idx - lookback - 1)
    gaps: list[dict[str, Any]] = []
    for i in range(start, idx - 1):  # third bar i+2 must be <= idx
        high_0 = float(candles[i]["high"])
        low_0 = float(candles[i]["low"])
        high_2 = float(candles[i + 2]["high"])
        low_2 = float(candles[i + 2]["low"])
        if direction == "bull" and high_0 < low_2:
            low_gap, high_gap = high_0, low_2
        elif direction == "bear" and low_0 > high_2:
            low_gap, high_gap = high_2, low_0
        else:
            continue
        gaps.append({
            "low": round(low_gap, 6),
            "high": round(high_gap, 6),
            "mid": round((low_gap + high_gap) / 2.0, 6),
            "age_bars": idx - (i + 2),
        })
    return sorted(gaps, key=lambda g: abs(g["mid"] - current_price))


def nearest_fvg_distance_at(
    candles: list[Candle], idx: int, direction: str, lookback: int = 30
) -> float | None:
    """Percent distance from close[idx] to the nearest gap mid (None if no gap)."""
    gaps = find_fvg(candles, idx, direction, lookback)
    if not gaps:
        return None
    price = float(candles[idx]["close"])
    if price <= 0:
        return None
    return round((gaps[0]["mid"] - price) / price * 100, 4)


def fvg_reaction_at(
    candles: list[Candle], idx: int, direction: str, lookback: int = 30
) -> str | None:
    """Reaction of bar ``idx`` to the nearest gap: reclaim / reject / inside / None.

    bull gap: 'reclaim' when the bar dipped into the gap (low<=high_gap) and closed
    back above it; 'inside' when it closed within the gap.
    bear gap: 'reject' when the bar poked into the gap (high>=low_gap) and closed
    back below it; 'inside' when it closed within the gap.
    """
    gaps = find_fvg(candles, idx, direction, lookback)
    if not gaps:
        return None
    gap = gaps[0]
    candle = candles[idx]
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])
    if gap["low"] <= close <= gap["high"]:
        return "inside"
    if direction == "bull" and low <= gap["high"] and close > gap["high"]:
        return "reclaim"
    if direction == "bear" and high >= gap["low"] and close < gap["low"]:
        return "reject"
    return None
