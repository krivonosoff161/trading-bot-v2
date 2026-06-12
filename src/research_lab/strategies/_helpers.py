# -*- coding: utf-8 -*-
"""Shared deterministic indicator helpers for lab strategies (stdlib only)."""

from __future__ import annotations

from typing import Any

Candle = dict[str, Any]


def sma(values: list[float], idx: int, lookback: int) -> float | None:
    """Mean of values[idx-lookback:idx] (data strictly before idx)."""
    if idx - lookback < 0 or lookback <= 0:
        return None
    window = values[idx - lookback:idx]
    return sum(window) / len(window) if window else None


def window_high(candles: list[Candle], idx: int, lookback: int) -> float | None:
    if idx - lookback < 0 or lookback <= 0:
        return None
    return max(float(c["high"]) for c in candles[idx - lookback:idx])


def window_low(candles: list[Candle], idx: int, lookback: int) -> float | None:
    if idx - lookback < 0 or lookback <= 0:
        return None
    return min(float(c["low"]) for c in candles[idx - lookback:idx])


def closes(candles: list[Candle]) -> list[float]:
    return [float(c["close"]) for c in candles]


def vols(candles: list[Candle]) -> list[float]:
    return [float(c.get("vol") or 0.0) for c in candles]


def body_pct(candle: Candle) -> float | None:
    open_ = float(candle["open"])
    if open_ <= 0:
        return None
    return (float(candle["close"]) / open_ - 1) * 100


def rsi_at(close_values: list[float], idx: int, period: int) -> float | None:
    """Simple-average RSI over the last `period` deltas ending at idx.

    Needs `period + 1` closes: the first delta references close_values[idx-period].
    """
    if idx - period < 0 or period <= 0:
        return None
    gains = 0.0
    losses = 0.0
    for j in range(idx - period + 1, idx + 1):
        delta = close_values[j] - close_values[j - 1]
        if delta > 0:
            gains += delta
        else:
            losses -= delta
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)
