# -*- coding: utf-8 -*-
"""main_fast_swing_regime — single-timeframe regime-gated breakout family.

Ports the FAST/SWING/DRIFT/RANGING idea from src/strategy/signal_engine._detect_regime
into a self-contained research family that consumes the lab feature layer. The old
engine used multi-TF (4h+1h); a lab sweep runs ONE timeframe, so this classifies the
regime on the swept timeframe (ADX + DI spread + EMA bias) and only fires a
continuation breakout while the regime is trending in the EMA-bias direction.

Research framing (audit): the old 15m scalp's late entry / RR geometry were NOT
levers and DRIFT/RANGING-fade were NO-GO. This family is a hypothesis generator,
gated by hard validation — never a revived live engine. Signal contract:
list of {idx, side, reason}; decision on bar idx, entry at open of idx+1.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import _core
from src.research_lab.strategies._helpers import Candle, window_high, window_low


def _regime(adx: float, di_spread: float, p: dict[str, Any]) -> str:
    """Crude single-TF regime label (trending / drift / ranging)."""
    adx_trend = float(p.get("adx_trend", 22.0))
    di_trend = float(p.get("di_trend", 10.0))
    drift_lo = float(p.get("drift_adx_lo", 12.0))
    drift_hi = float(p.get("drift_adx_hi", 30.0))
    drift_di = float(p.get("drift_di", 5.0))
    if adx >= adx_trend and abs(di_spread) >= di_trend:
        return "trending"
    if drift_lo <= adx <= drift_hi and abs(di_spread) >= drift_di:
        return "drift"
    return "ranging"


def signals_main_fast_swing_regime(candles: list[Candle], params: dict[str, Any]) -> list[dict[str, Any]]:
    ema_fast = int(params.get("ema_fast", 20))
    ema_slow = int(params.get("ema_slow", 50))
    adx_period = int(params.get("adx_period", 14))
    lookback = int(params.get("breakout_lookback", 20))
    min_vol = float(params.get("min_vol_ratio", 1.2))
    vol_period = int(params.get("vol_period", 20))

    h, low_arr, c = _core.highs(candles), _core.lows(candles), _core.closes(candles)
    ema_f = _core.ema_series(c, ema_fast)
    ema_s = _core.ema_series(c, ema_slow)
    adx_arr, plus_di, minus_di = _core.adx_series(h, low_arr, c, adx_period)
    v = _core.vols(candles)
    start = max(adx_period * 2, ema_slow, lookback, vol_period)

    signals: list[dict[str, Any]] = []
    for idx in range(start, len(candles) - 1):
        if ema_s[idx] == 0.0 or adx_arr[idx] == 0.0:
            continue
        regime = _regime(adx_arr[idx], plus_di[idx] - minus_di[idx], params)
        if regime != "trending":
            continue
        base = sum(v[idx - vol_period:idx]) / vol_period if idx >= vol_period else 0.0
        vol_ratio = v[idx] / base if base > 0 else 0.0
        if vol_ratio < min_vol:
            continue
        hi = window_high(candles, idx, lookback)
        lo = window_low(candles, idx, lookback)
        if hi is None or lo is None:
            continue
        up_bias = ema_f[idx] > ema_s[idx] and plus_di[idx] > minus_di[idx]
        down_bias = ema_f[idx] < ema_s[idx] and minus_di[idx] > plus_di[idx]
        if up_bias and c[idx] > hi:
            signals.append({"idx": idx + 1, "side": "long", "reason": "regime_trend_breakout_up"})
        elif down_bias and c[idx] < lo:
            signals.append({"idx": idx + 1, "side": "short", "reason": "regime_trend_breakout_down"})
    return signals
