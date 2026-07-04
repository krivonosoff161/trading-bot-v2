# -*- coding: utf-8 -*-
"""Mean-reversion / fade family signal generators."""

from __future__ import annotations

from typing import Any

from src.research_lab.strategies._helpers import Candle, closes, rsi_at, sma, vols
from src.research_lab.strategies.detectors import detect_mean_reversion_fade


def signals_mean_reversion_fade(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(params.get("lookback", 5))
    move_pct = float(params.get("move_pct", 8.0))
    signals = []
    for idx in range(lookback, len(candles) - 1):
        det = detect_mean_reversion_fade(candles, idx, lookback=lookback, move_pct=move_pct)
        if det:
            signals.append({"idx": idx + 1, "side": det["side"], "reason": det["reason"]})
    return signals


def signals_rsi_reversal(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Enter when RSI crosses back out of an extreme zone."""
    period = int(params.get("period", 14))
    oversold = float(params.get("oversold", 30.0))
    overbought = float(params.get("overbought", 70.0))
    close_values = closes(candles)
    signals = []
    for idx in range(period + 1, len(candles) - 1):
        prev_rsi = rsi_at(close_values, idx - 1, period)
        cur_rsi = rsi_at(close_values, idx, period)
        if prev_rsi is None or cur_rsi is None:
            continue
        if prev_rsi < oversold <= cur_rsi:
            signals.append({"idx": idx + 1, "side": "long", "reason": "rsi_exit_oversold"})
        elif prev_rsi > overbought >= cur_rsi:
            signals.append({"idx": idx + 1, "side": "short", "reason": "rsi_exit_overbought"})
    return signals


def signals_volume_exhaustion_fade(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Fade an extended move that ends on a volume climax."""
    lookback = int(params.get("lookback", 20))
    move_bars = int(params.get("move_bars", 3))
    min_move_pct = float(params.get("min_move_pct", 10.0))
    vol_mult = float(params.get("vol_mult", 3.0))
    vol_values = vols(candles)
    signals = []
    for idx in range(max(lookback, move_bars), len(candles) - 1):
        avg_vol = sma(vol_values, idx, lookback)
        if not avg_vol or vol_values[idx] < avg_vol * vol_mult:
            continue
        base = float(candles[idx - move_bars]["close"])
        close = float(candles[idx]["close"])
        if base <= 0:
            continue
        move = (close / base - 1) * 100
        if move >= min_move_pct:
            signals.append({"idx": idx + 1, "side": "short", "reason": "exhaustion_fade_up"})
        elif move <= -min_move_pct:
            signals.append({"idx": idx + 1, "side": "long", "reason": "exhaustion_fade_down"})
    return signals
