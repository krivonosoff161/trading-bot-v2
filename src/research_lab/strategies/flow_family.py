# -*- coding: utf-8 -*-
"""Flow families: OI/funding squeeze and OI×price quadrant.

Both require the optional ``oi`` / ``funding`` fields the farm merges from OKX
public data. On assets/series without flow data they emit NO signals (the family
then surfaces as NEEDS_DATA downstream) — flow is context, never invented.

Audit verdict guardrail: the OI-quadrant edge is NULL/inconclusive by construction
(single-regime, survivor-biased). These are candidate features pending forward
validation, never proven money edges. Signal contract: list of {idx, side, reason};
decision on bar idx, entry at idx+1.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import flow
from src.research_lab.strategies._helpers import Candle


def signals_oi_funding_squeeze(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Crowded funding + building OI -> fade the crowded side (squeeze hypothesis)."""
    oi_lookback = int(params.get("oi_lookback", 5))
    oi_surge_min = float(params.get("oi_surge_min", 1.0))
    warn = float(params.get("funding_warn", 0.0005))
    block = float(params.get("funding_block", 0.001))
    signals: list[dict[str, Any]] = []
    for idx in range(oi_lookback, len(candles) - 1):
        extreme = flow.funding_extreme_at(candles, idx, warn, block)
        if extreme is None:
            continue
        d_oi = flow.oi_delta_at(candles, idx, oi_lookback)
        if d_oi is None or d_oi < oi_surge_min:
            continue
        if extreme.startswith("long_squeeze_risk"):
            signals.append({"idx": idx + 1, "side": "short", "reason": "fade_crowded_longs"})
        elif extreme.startswith("short_squeeze_risk"):
            signals.append({"idx": idx + 1, "side": "long", "reason": "fade_crowded_shorts"})
    return signals


def signals_oi_price_quadrant(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    """dOI×dPrice continuation: new_longs -> long, new_shorts -> short (trap quadrants skipped)."""
    oi_lookback = int(params.get("oi_lookback", 5))
    fade_traps = bool(params.get("fade_traps", False))
    signals: list[dict[str, Any]] = []
    for idx in range(oi_lookback, len(candles) - 1):
        quadrant = flow.oi_price_quadrant_at(candles, idx, oi_lookback)
        if quadrant is None:
            continue
        if quadrant == "new_longs":
            signals.append({"idx": idx + 1, "side": "long", "reason": "oi_new_longs_continuation"})
        elif quadrant == "new_shorts":
            signals.append({"idx": idx + 1, "side": "short", "reason": "oi_new_shorts_continuation"})
        elif fade_traps and quadrant == "short_covering":
            signals.append({"idx": idx + 1, "side": "short", "reason": "oi_short_covering_fade"})
        elif fade_traps and quadrant == "long_liquidation":
            signals.append({"idx": idx + 1, "side": "long", "reason": "oi_long_flush_fade"})
    return signals
