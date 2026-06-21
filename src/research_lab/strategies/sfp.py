# -*- coding: utf-8 -*-
"""SFP / liquidity-sweep signal generator (the one ICT idea that survived prior hunts).

Signal contract: list of {"idx": entry_bar, "side": "long"|"short", "reason": str}. No look-ahead — the
decision uses bars up to and including idx (the sweep bar); entry is at the open of idx+1.

Swing Failure Pattern = a stop-run / failed breakout: price PIERCES a recent swing extreme (sweeping the
liquidity resting beyond it) then CLOSES back inside the range (the breakout failed). It is a reversal,
not a continuation:
  * sweep ABOVE a prior swing high then close back below  -> SHORT (longs above were stopped, price fades)
  * sweep BELOW a prior swing low then close back above   -> LONG  (shorts below were stopped, price lifts)

Optional volume confirmation (vol_mult > 0): the sweep bar must trade above the recent average volume.
Pure stdlib, deterministic; a research hypothesis only, never an edge claim.
"""
from __future__ import annotations

from typing import Any

from src.research_lab.strategies._helpers import Candle, sma, vols, window_high, window_low


def signals_sfp_liquidity_sweep(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(params.get("lookback", 20))
    vol_mult = float(params.get("vol_mult", 0.0))            # 0 = no volume confirmation
    reclaim_buf_pct = float(params.get("reclaim_buf_pct", 0.0))  # close must reclaim this far back inside
    vseries = vols(candles) if vol_mult > 0 else []
    signals: list[dict[str, Any]] = []
    for idx in range(lookback, len(candles) - 1):
        swing_high = window_high(candles, idx, lookback)     # prior `lookback` bars, strictly before idx
        swing_low = window_low(candles, idx, lookback)
        if swing_high is None or swing_low is None:
            continue
        hi, lo = float(candles[idx]["high"]), float(candles[idx]["low"])
        close = float(candles[idx]["close"])
        if vol_mult > 0:
            avg_vol = sma(vseries, idx, lookback)
            if avg_vol is None or float(candles[idx].get("vol") or 0.0) <= avg_vol * vol_mult:
                continue
        # swept above the swing high but closed back below it -> failed breakout -> short
        if hi > swing_high and close < swing_high * (1 - reclaim_buf_pct / 100):
            signals.append({"idx": idx + 1, "side": "short", "reason": "sfp_sweep_high"})
        # swept below the swing low but closed back above it -> stop-run -> long
        elif lo < swing_low and close > swing_low * (1 + reclaim_buf_pct / 100):
            signals.append({"idx": idx + 1, "side": "long", "reason": "sfp_sweep_low"})
    return signals
