# -*- coding: utf-8 -*-
"""Volume / impulse family signal generators."""

from __future__ import annotations

from typing import Any

from src.research_lab.strategies._helpers import Candle, body_pct, sma, vols


def signals_volume_shock_continuation(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(params.get("lookback", 20))
    vol_mult = float(params.get("vol_mult", 2.0))
    min_body_pct = float(params.get("min_body_pct", 3.0))
    vol_values = vols(candles)
    signals = []
    for idx in range(lookback, len(candles) - 1):
        avg_vol = sma(vol_values, idx, lookback)
        if not avg_vol:
            continue
        body = body_pct(candles[idx])
        if body is None:
            continue
        if vol_values[idx] >= avg_vol * vol_mult and abs(body) >= min_body_pct:
            signals.append({
                "idx": idx + 1,
                "side": "long" if body > 0 else "short",
                "reason": "volume_shock",
            })
    return signals


def signals_impulse_continuation(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    """One large directional bar closing near its extreme; continuation entry.

    Crypto perps trade 24/7, so there are no session gaps; this is the
    impulse_continuation variant of gap-continuation.
    """
    min_body_pct = float(params.get("min_body_pct", 6.0))
    min_close_pos = float(params.get("min_close_pos", 0.7))
    signals = []
    for idx in range(1, len(candles) - 1):
        cur = candles[idx]
        body = body_pct(cur)
        if body is None or abs(body) < min_body_pct:
            continue
        high = float(cur["high"])
        low = float(cur["low"])
        bar_range = high - low
        if bar_range <= 0:
            continue
        close_pos = (float(cur["close"]) - low) / bar_range
        if body > 0 and close_pos >= min_close_pos:
            signals.append({"idx": idx + 1, "side": "long", "reason": "impulse_up_strong_close"})
        elif body < 0 and close_pos <= 1 - min_close_pos:
            signals.append({"idx": idx + 1, "side": "short", "reason": "impulse_down_weak_close"})
    return signals
