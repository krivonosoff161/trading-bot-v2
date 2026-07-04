# -*- coding: utf-8 -*-
"""bb_volume_fade — mean-reversion fade of a Bollinger extreme on low volume.

Ported from the old BB-fade research (bt_bb_fade.py / signal_engine BB_FADE 5m):
price stretched to a band, fading volume, and a non-trending regime. Long at the
lower band, short at the upper band. The uniform fixed-SL/TP/hold simulator handles
exits; the family's job is the entry hypothesis. Signal contract: list of
{idx, side, reason}; decision on bar idx, entry at idx+1.

Audit note: live BB-fade's disease was the EXIT (it gave back big moves), so this is
a candidate pending hard validation, not a revived live setup.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import _core
from src.research_lab.strategies._helpers import Candle


def signals_bb_volume_fade(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    period = int(params.get("bb_period", 20))
    std_mult = float(params.get("bb_std", 2.0))
    pct_b_hi = float(params.get("pct_b_extreme_high", 95.0))
    pct_b_lo = float(params.get("pct_b_extreme_low", 5.0))
    max_vol = float(params.get("max_vol_ratio", 0.7))
    max_adx = float(params.get("max_adx", 20.0))
    min_width = float(params.get("min_width_pct", 2.0))
    adx_period = int(params.get("adx_period", 14))
    vol_period = int(params.get("vol_period", 20))

    c = _core.closes(candles)
    h, low_arr = _core.highs(candles), _core.lows(candles)
    v = _core.vols(candles)
    adx_arr, _plus, _minus = _core.adx_series(h, low_arr, c, adx_period)
    start = max(period, adx_period * 2, vol_period)
    signals: list[dict[str, Any]] = []
    for idx in range(start, len(candles) - 1):
        window = c[idx - period + 1:idx + 1]
        mid = sum(window) / period
        std = _core.pop_std(window)
        if mid <= 0 or std <= 0:
            continue
        upper, lower = mid + std_mult * std, mid - std_mult * std
        width_pct = (upper - lower) / mid * 100
        if width_pct < min_width:
            continue
        pct_b = (c[idx] - lower) / (upper - lower) * 100 if upper != lower else 50.0
        if adx_arr[idx] >= max_adx:
            continue
        base = sum(v[idx - vol_period:idx]) / vol_period if idx >= vol_period else 0.0
        vol_ratio = v[idx] / base if base > 0 else 99.0
        if vol_ratio >= max_vol:
            continue
        if pct_b >= pct_b_hi:
            signals.append({"idx": idx + 1, "side": "short", "reason": "bb_fade_upper"})
        elif pct_b <= pct_b_lo:
            signals.append({"idx": idx + 1, "side": "long", "reason": "bb_fade_lower"})
    return signals
