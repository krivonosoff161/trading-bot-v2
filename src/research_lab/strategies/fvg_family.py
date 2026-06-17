# -*- coding: utf-8 -*-
"""fvg_reclaim_reject — Fair Value Gap reaction entry.

Long when bar idx dips into the nearest bullish (demand) FVG and reclaims it, while
the gap is close enough to price to be actionable; short when bar idx pokes into the
nearest bearish (supply) FVG and is rejected. Structure-based ICT idea, framed as a
research candidate. Signal contract: list of {idx, side, reason}; decision on bar
idx, entry at idx+1.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import fvg
from src.research_lab.strategies._helpers import Candle


def signals_fvg_reclaim_reject(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(params.get("fvg_lookback", 30))
    max_distance = float(params.get("max_distance_pct", 3.0))
    signals: list[dict[str, Any]] = []
    for idx in range(3, len(candles) - 1):
        bull_dist = fvg.nearest_fvg_distance_at(candles, idx, "bull", lookback)
        if bull_dist is not None and abs(bull_dist) <= max_distance:
            if fvg.fvg_reaction_at(candles, idx, "bull", lookback) == "reclaim":
                signals.append({"idx": idx + 1, "side": "long", "reason": "fvg_bull_reclaim"})
                continue
        bear_dist = fvg.nearest_fvg_distance_at(candles, idx, "bear", lookback)
        if bear_dist is not None and abs(bear_dist) <= max_distance:
            if fvg.fvg_reaction_at(candles, idx, "bear", lookback) == "reject":
                signals.append({"idx": idx + 1, "side": "short", "reason": "fvg_bear_reject"})
    return signals
