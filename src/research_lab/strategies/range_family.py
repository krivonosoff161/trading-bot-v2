# -*- coding: utf-8 -*-
"""Range / squeeze breakout families.

- range_volume_breakout: a tight sideways range with accumulated volume, then a
  close out of the range confirmed by a volume expansion.
- volatility_squeeze_breakout_v2: BB/ATR contraction (squeeze) + close outside the
  squeeze + volume confirmation + an ATR-percentile compression gate (v2 over the
  pure-range v1 in breakout.py).

Signal contract: list of {idx, side, reason}; decision on bar idx, entry at idx+1.
No-lookahead: range/squeeze windows are strictly before idx.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import _core, volatility, volume
from src.research_lab.strategies._helpers import Candle, window_high, window_low


def signals_range_volume_breakout(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(params.get("range_lookback", 20))
    max_range_pct = float(params.get("max_range_pct", 12.0))
    min_accum = float(params.get("min_accumulation", 1.0))
    min_vol = float(params.get("min_vol_ratio", 1.5))
    vol_period = int(params.get("vol_period", 20))
    c = _core.closes(candles)
    signals: list[dict[str, Any]] = []
    for idx in range(max(2 * lookback, vol_period), len(candles) - 1):
        sideways = volume.sideways_range_volume_at(candles, idx, lookback, max_range_pct)
        if sideways is None or not sideways["is_sideways"]:
            continue
        accum = sideways["accumulation_ratio"]
        if accum is None or accum < min_accum:
            continue
        vol_ratio = volume.vol_ratio_at(candles, idx, vol_period)
        if vol_ratio is None or vol_ratio < min_vol:
            continue
        hi = window_high(candles, idx, lookback)
        lo = window_low(candles, idx, lookback)
        if hi is None or lo is None:
            continue
        if c[idx] > hi:
            signals.append({"idx": idx + 1, "side": "long", "reason": "range_accum_breakout_up"})
        elif c[idx] < lo:
            signals.append({"idx": idx + 1, "side": "short", "reason": "range_accum_breakout_down"})
    return signals


def signals_volatility_squeeze_breakout_v2(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(params.get("squeeze_lookback", 20))
    squeeze_ratio = float(params.get("squeeze_ratio", 0.6))
    atr_pct_max = float(params.get("atr_pct_max", 40.0))
    min_vol = float(params.get("min_vol_ratio", 1.3))
    atr_period = int(params.get("atr_period", 14))
    vol_period = int(params.get("vol_period", 20))
    c = _core.closes(candles)
    signals: list[dict[str, Any]] = []
    for idx in range(max(2 * lookback, atr_period + 12, vol_period), len(candles) - 1):
        sr = volatility.squeeze_ratio_at(candles, idx, lookback)
        if sr is None or sr > squeeze_ratio:
            continue
        atr_pct = volatility.atr_percentile_at(candles, idx, atr_period)
        if atr_pct is None or atr_pct > atr_pct_max:
            continue
        vol_ratio = volume.vol_ratio_at(candles, idx, vol_period)
        if vol_ratio is None or vol_ratio < min_vol:
            continue
        hi = window_high(candles, idx, lookback)
        lo = window_low(candles, idx, lookback)
        if hi is None or lo is None:
            continue
        if c[idx] > hi:
            signals.append({"idx": idx + 1, "side": "long", "reason": "squeeze_v2_break_up"})
        elif c[idx] < lo:
            signals.append({"idx": idx + 1, "side": "short", "reason": "squeeze_v2_break_down"})
    return signals
