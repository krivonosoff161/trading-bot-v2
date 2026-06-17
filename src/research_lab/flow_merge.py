# -*- coding: utf-8 -*-
"""Merge flow series (funding / open interest) into candle dicts — pure, no IO.

Funding prints every ~8h and OI is sampled irregularly, so each is forward-filled
onto the candle timeline: a candle gets the most recent flow value at-or-before its
timestamp (no lookahead). Candles before the first flow point are left untouched
(the flow field stays absent, so flow features report NEEDS_DATA there).
"""

from __future__ import annotations

import bisect
from typing import Any

Candle = dict[str, Any]


def forward_fill(candles: list[Candle], points: list[tuple[int, float]], field: str) -> list[Candle]:
    """Return new candles with ``field`` forward-filled from sorted (ts, value) points."""
    if not points:
        return [dict(c) for c in candles]
    point_ts = [int(p[0]) for p in points]
    out: list[Candle] = []
    for c in candles:
        new = dict(c)
        i = bisect.bisect_right(point_ts, int(c["ts"])) - 1
        if i >= 0:
            new[field] = points[i][1]
        out.append(new)
    return out


def merge_funding(candles: list[Candle], funding_points: list[tuple[int, float]]) -> list[Candle]:
    """Forward-fill funding rate onto candles (field ``funding``)."""
    return forward_fill(candles, funding_points, "funding")


def merge_oi(candles: list[Candle], oi_points: list[tuple[int, float]]) -> list[Candle]:
    """Forward-fill open interest onto candles (field ``oi``). Provider slot: OI
    history is not shipped keyless, so callers pass points only when available."""
    return forward_fill(candles, oi_points, "oi")


def coverage(candles: list[Candle], field: str) -> dict[str, Any]:
    """How many candles carry the flow field (operator-facing, no claim)."""
    present = sum(1 for c in candles if c.get(field) is not None)
    return {"field": field, "candles": len(candles), "with_value": present,
            "coverage_pct": round(present / len(candles) * 100, 2) if candles else 0.0}
