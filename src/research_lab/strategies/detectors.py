# -*- coding: utf-8 -*-
"""Single-bar signal detectors — shared by batch generators and the PFR bridge.

Each detector checks ONE decision bar (decision_idx) against the bars that preceded it.
No look-ahead: only candles[0:decision_idx+1] are used; any bars after decision_idx are ignored.

The detectors mirror the batch-generator logic exactly so that:
  signals_momentum_breakout(candles, params)[-1]
  == detect_momentum_breakout(candles, len(candles)-2, **extracted_params)
for the same candle sequence (decision at idx, entry at idx+1).
"""
from __future__ import annotations

from typing import Any

from src.research_lab.strategies._helpers import Candle, window_high, window_low


def detect_momentum_breakout(
    candles: list[Candle],
    decision_idx: int,
    *,
    lookback: int,
    threshold_pct: float = 0.0,
) -> dict[str, Any] | None:
    """Check whether bar at decision_idx is a momentum breakout.

    Returns a detection dict on breakout, None otherwise.
    dict keys: side ("long"|"short"), reason (str), ref_level (float — the broken level).

    Uses close[decision_idx] vs max/min of candles[decision_idx-lookback : decision_idx].
    Matches signals_momentum_breakout loop body exactly (same window, same comparison).
    """
    if decision_idx < lookback or decision_idx >= len(candles):
        return None
    ref_high = window_high(candles, decision_idx, lookback)
    ref_low = window_low(candles, decision_idx, lookback)
    close = float(candles[decision_idx]["close"])
    if ref_high is None or ref_low is None:
        return None
    if close > ref_high * (1 + threshold_pct / 100):
        return {"side": "long", "reason": "breakout_high", "ref_level": ref_high}
    if close < ref_low * (1 - threshold_pct / 100):
        return {"side": "short", "reason": "breakout_low", "ref_level": ref_low}
    return None


def detect_mean_reversion_fade(
    candles: list[Candle],
    decision_idx: int,
    *,
    lookback: int,
    move_pct: float,
) -> dict[str, Any] | None:
    """Check whether bar at decision_idx is a mean-reversion fade trigger.

    Returns a detection dict on signal, None otherwise.
    dict keys: side ("long"|"short"), reason (str), move (float), base (float).

    move = (close[decision_idx] / close[decision_idx - lookback] - 1) * 100.
    Matches signals_mean_reversion_fade loop body exactly.
    """
    if decision_idx - lookback < 0 or decision_idx >= len(candles):
        return None
    base = float(candles[decision_idx - lookback]["close"])
    close = float(candles[decision_idx]["close"])
    if base <= 0:
        return None
    move = (close / base - 1) * 100
    if move >= move_pct:
        return {"side": "short", "reason": "fade_up_move", "move": move, "base": base}
    if move <= -move_pct:
        return {"side": "long", "reason": "fade_down_move", "move": move, "base": base}
    return None
