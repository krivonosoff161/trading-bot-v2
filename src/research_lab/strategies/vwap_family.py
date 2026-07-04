# -*- coding: utf-8 -*-
"""vwap_reclaim_reject — VWAP loss/reclaim with an EMA-bias and day-position filter.

Long when price reclaims the rolling VWAP from below while the fast EMA is above the
slow EMA (up-bias) and the day is not already extended to the highs; short mirror.
Signal contract: list of {idx, side, reason}; decision on bar idx, entry at idx+1.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import _core, vwap
from src.research_lab.strategies._helpers import Candle


def signals_vwap_reclaim_reject(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    period = int(params.get("vwap_period", 20))
    ema_fast = int(params.get("ema_fast", 20))
    ema_slow = int(params.get("ema_slow", 50))
    max_day_pos = float(params.get("max_day_position", 0.7))
    use_day_filter = bool(params.get("use_day_filter", True))
    c = _core.closes(candles)
    ema_f = _core.ema_series(c, ema_fast)
    ema_s = _core.ema_series(c, ema_slow)
    signals: list[dict[str, Any]] = []
    for idx in range(max(period + 1, ema_slow), len(candles) - 1):
        if ema_s[idx] == 0.0:
            continue
        reaction = vwap.vwap_reclaim_reject_at(candles, idx, period)
        if reaction is None:
            continue
        day = vwap.day_position_at(candles, idx) if use_day_filter else None
        up_bias = ema_f[idx] > ema_s[idx]
        if reaction == "reclaim_up" and up_bias:
            if day is not None and day["day_position"] > max_day_pos:
                continue
            signals.append({"idx": idx + 1, "side": "long", "reason": "vwap_reclaim_up"})
        elif reaction == "reject_down" and not up_bias:
            if day is not None and day["day_position"] < (1.0 - max_day_pos):
                continue
            signals.append({"idx": idx + 1, "side": "short", "reason": "vwap_reject_down"})
    return signals
