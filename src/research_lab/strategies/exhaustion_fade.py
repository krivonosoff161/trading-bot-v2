# -*- coding: utf-8 -*-
"""Exhaustion-fade signal generator — fade a parabolic move's exhaustion (a different class).

Not "ride the trend" but "trade the exhaustion": a coin that ran +run_pct over the last K bars with a
climax bar (volume/range spike) tends to snap back. Mirror on capitulation (a -run_pct flush). It is a
conditional mean-reversion — gated on a real parabolic run + climax, NOT a generic deviation-from-MA.

Signal contract: list of {"idx": entry_bar, "side": "long"|"short", "reason": str}. No look-ahead — the
run/climax are measured on bars up to idx; entry is at the open of idx+1.

  * up-exhaustion (cum run >= +run_pct, climax) -> SHORT the snap-back
  * down-capitulation (cum run <= -run_pct, climax) -> LONG the bounce

Aimed at the live-mover universe where 20-60% runs actually happen. A research hypothesis only.
"""
from __future__ import annotations

from typing import Any

from src.research_lab.strategies._helpers import Candle, sma, vols


def signals_exhaustion_fade(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    run_lookback = int(params.get("run_lookback", 6))
    run_pct = float(params.get("run_pct", 15.0))
    vol_climax_mult = float(params.get("vol_climax_mult", 1.5))   # 0 disables the climax filter
    vseries = vols(candles) if vol_climax_mult > 0 else []
    signals: list[dict[str, Any]] = []
    for idx in range(run_lookback, len(candles) - 1):
        base = float(candles[idx - run_lookback]["close"])
        if base <= 0:
            continue
        cum = (float(candles[idx]["close"]) / base - 1) * 100
        if abs(cum) < run_pct:
            continue
        if vol_climax_mult > 0:
            avg_vol = sma(vseries, idx, run_lookback)
            if avg_vol is None or float(candles[idx].get("vol") or 0.0) <= avg_vol * vol_climax_mult:
                continue
        # fade the exhaustion: short an up-run, long a down-capitulation
        side = "short" if cum > 0 else "long"
        signals.append({"idx": idx + 1, "side": side, "reason": "exhaustion_fade_up" if cum > 0 else "exhaustion_fade_down"})
    return signals
