# -*- coding: utf-8 -*-
"""pump_dump_scalp — volume-shock impulse continuation (strict cost stress).

Ports the impulse DETECTOR from the closed "rivok" engine (the detector caught the
move; the EXIT was the documented disease, and BSB-style overfit killed it live). A
bar with an abnormally large body AND a volume shock continues in the body direction.
Kept deliberately short-hold; the farm's cost-stress + hard validation must clear it
before it is anything but a hypothesis. Signal contract: list of {idx, side, reason};
decision on bar idx, entry at idx+1.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import _core
from src.research_lab.strategies._helpers import Candle


def signals_pump_dump_scalp(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    min_body_pct = float(params.get("min_body_pct", 3.0))
    vol_mult = float(params.get("vol_mult", 3.0))
    vol_period = int(params.get("vol_period", 20))
    min_close_pos = float(params.get("min_close_pos", 0.6))
    c = _core.closes(candles)
    o = _core.opens(candles)
    h, low_arr = _core.highs(candles), _core.lows(candles)
    v = _core.vols(candles)
    signals: list[dict[str, Any]] = []
    for idx in range(vol_period, len(candles) - 1):
        if o[idx] <= 0:
            continue
        body_pct = (c[idx] / o[idx] - 1) * 100
        base = sum(v[idx - vol_period:idx]) / vol_period
        if base <= 0:
            continue
        vol_ratio = v[idx] / base
        if vol_ratio < vol_mult:
            continue
        rng = h[idx] - low_arr[idx]
        close_pos = (c[idx] - low_arr[idx]) / rng if rng > 0 else 0.5
        if body_pct >= min_body_pct and close_pos >= min_close_pos:
            signals.append({"idx": idx + 1, "side": "long", "reason": "pump_volume_shock_up"})
        elif body_pct <= -min_body_pct and close_pos <= (1.0 - min_close_pos):
            signals.append({"idx": idx + 1, "side": "short", "reason": "dump_volume_shock_down"})
    return signals
