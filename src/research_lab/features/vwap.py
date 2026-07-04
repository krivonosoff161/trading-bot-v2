# -*- coding: utf-8 -*-
"""VWAP and intraday-position features.

Rolling VWAP (typical-price, volume-weighted), VWAP stretch, reclaim/reject, and
UTC-day position. As-of the close of bar ``idx`` using candles ``0..idx``.
Crypto perps have no session gaps, so the "day" anchor is the UTC calendar day
derived from each candle's ``ts`` (ms).
"""

from __future__ import annotations

from typing import Any


Candle = dict[str, Any]
DAY_MS = 86_400_000


def _typical(candle: Candle) -> float:
    return (float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3.0


def rolling_vwap_at(candles: list[Candle], idx: int, period: int = 20) -> float | None:
    if idx - period + 1 < 0 or period <= 0:
        return None
    window = candles[idx - period + 1:idx + 1]
    num = sum(_typical(c) * float(c.get("vol") or 0.0) for c in window)
    den = sum(float(c.get("vol") or 0.0) for c in window)
    if den <= 0:
        return None
    return num / den


def vwap_stretch_at(candles: list[Candle], idx: int, period: int = 20) -> float | None:
    """Distance of close from rolling VWAP, in percent (>0 = above VWAP)."""
    vwap = rolling_vwap_at(candles, idx, period)
    if vwap is None or vwap <= 0:
        return None
    return round((float(candles[idx]["close"]) / vwap - 1) * 100, 4)


def vwap_reclaim_reject_at(candles: list[Candle], idx: int, period: int = 20) -> str | None:
    """Did bar ``idx`` reclaim or reject the rolling VWAP vs bar ``idx-1``?

    'reclaim_up'  : prior close below VWAP, current close back above.
    'reject_down' : prior close above VWAP, current close back below.
    None          : no cross.
    """
    if idx < 1:
        return None
    vwap_now = rolling_vwap_at(candles, idx, period)
    vwap_prev = rolling_vwap_at(candles, idx - 1, period)
    if vwap_now is None or vwap_prev is None:
        return None
    prev_close = float(candles[idx - 1]["close"])
    cur_close = float(candles[idx]["close"])
    if prev_close < vwap_prev and cur_close > vwap_now:
        return "reclaim_up"
    if prev_close > vwap_prev and cur_close < vwap_now:
        return "reject_down"
    return None


def day_position_at(candles: list[Candle], idx: int) -> dict[str, float] | None:
    """Where close sits within the current UTC day's range, and the day's range %."""
    ts = int(candles[idx].get("ts") or 0)
    if ts <= 0:
        return None
    day = ts // DAY_MS
    day_high = float("-inf")
    day_low = float("inf")
    day_open = None
    for j in range(idx, -1, -1):
        cj = candles[j]
        if int(cj.get("ts") or 0) // DAY_MS != day:
            break
        day_high = max(day_high, float(cj["high"]))
        day_low = min(day_low, float(cj["low"]))
        day_open = float(cj["open"])
    if day_open is None or day_high <= day_low:
        return None
    close = float(candles[idx]["close"])
    return {
        "day_position": round((close - day_low) / (day_high - day_low), 4),
        "daily_range_pct": round((day_high - day_low) / day_open * 100, 4) if day_open else 0.0,
    }
