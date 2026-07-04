# -*- coding: utf-8 -*-
"""microstructure_confirmed_breakout — breakout gated by order-book/tape state.

A close out of the prior range, confirmed by book imbalance + signed trade delta +
a tight spread. Microstructure fields (``obi_top5`` / ``trade_delta_100`` /
``spread_bps``) are only present when the farm captured a public snapshot, so on
plain historical OHLCV this family emits NO signals (surfaces as NEEDS_DATA). No
private endpoints. Signal contract: list of {idx, side, reason}; decision on bar
idx, entry at idx+1.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import _core, microstructure
from src.research_lab.strategies._helpers import Candle, window_high, window_low


def signals_microstructure_confirmed_breakout(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(params.get("range_lookback", 20))
    min_obi = float(params.get("min_obi", 0.15))
    min_trade_delta = float(params.get("min_trade_delta", 0.0))
    max_spread_bps = float(params.get("max_spread_bps", 5.0))
    c = _core.closes(candles)
    signals: list[dict[str, Any]] = []
    for idx in range(lookback, len(candles) - 1):
        if not microstructure.has_microstructure(candles, idx):
            continue
        obi = microstructure.obi_top5_at(candles, idx)
        delta = microstructure.trade_delta_at(candles, idx)
        spread = microstructure.spread_bps_at(candles, idx)
        if spread is not None and spread > max_spread_bps:
            continue
        hi = window_high(candles, idx, lookback)
        lo = window_low(candles, idx, lookback)
        if hi is None or lo is None:
            continue
        buy_confirm = (obi is not None and obi >= min_obi) and (delta is None or delta >= min_trade_delta)
        sell_confirm = (obi is not None and obi <= -min_obi) and (delta is None or delta <= -min_trade_delta)
        if c[idx] > hi and buy_confirm:
            signals.append({"idx": idx + 1, "side": "long", "reason": "micro_confirmed_breakout_up"})
        elif c[idx] < lo and sell_confirm:
            signals.append({"idx": idx + 1, "side": "short", "reason": "micro_confirmed_breakout_down"})
    return signals
