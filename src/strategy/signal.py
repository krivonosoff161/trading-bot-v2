"""
Signal — Strategy E: Trend Pullback Breakout (1H → 15m → 5m).

Trend   : 1H EMA(20/50) + DI + ADX — who controls the market
Setup   : 15m pullback to EMA zone with weak counter-trend volume
Trigger : 5m breakout candle with strong volume + DI confirm
Exit    : OCO (TP + SL) on exchange. SL anchored to setup structure.
"""

import numpy as np
from loguru import logger

from src.strategy.indicators import (
    calc_adx, calc_atr, calc_ema, calc_sma, parse_candles, parse_volumes,
)

MIN_CANDLES_1H  = 80   # EMA(50) + ADX(14) warmup
MIN_CANDLES_15M = 80
MIN_CANDLES_5M  = 30


def get_signal(raw_1h: list, raw_15m: list, raw_5m: list, sym_config: dict) -> dict:
    """
    Strategy E: top-down trend pullback breakout.
    raw_1h  → trend direction (EMA20/50, DI, ADX)
    raw_15m → pullback setup (price near EMA20, weak counter volume)
    raw_5m  → entry trigger (breakout candle + volume + DI)

    sym_config keys: ema_fast, ema_slow, adx_period, adx_threshold_1h,
                     pullback_touch_atr, pullback_volume_bars, pullback_volume_factor,
                     breakout_lookback_5m, trigger_volume_ma_period, trigger_volume_factor,
                     sl_buffer_atr, sl_min_atr

    Returns: {side, reason, atr, adx, plus_di, minus_di,
              ema_fast, ema_slow, setup_sl, vol_ratio}
    """
    empty = {
        "side": None, "reason": "", "atr": 0.0,
        "adx": 0.0, "plus_di": 0.0, "minus_di": 0.0,
        "ema_fast": 0.0, "ema_slow": 0.0, "setup_sl": 0.0, "vol_ratio": 0.0,
    }

    if (
        len(raw_1h)  < MIN_CANDLES_1H
        or len(raw_15m) < MIN_CANDLES_15M
        or len(raw_5m)  < MIN_CANDLES_5M
    ):
        return {**empty, "reason": "not_enough_candles"}

    ema_fast   = int(sym_config["ema_fast"])
    ema_slow   = int(sym_config["ema_slow"])
    adx_period = int(sym_config["adx_period"])
    adx_thresh = float(sym_config["adx_threshold_1h"])
    pb_touch   = float(sym_config["pullback_touch_atr"])
    pb_bars    = int(sym_config["pullback_volume_bars"])
    pb_factor  = float(sym_config["pullback_volume_factor"])
    bk_lookbk  = int(sym_config["breakout_lookback_5m"])
    vol_period = int(sym_config["trigger_volume_ma_period"])
    vol_factor = float(sym_config["trigger_volume_factor"])
    sl_buffer  = float(sym_config["sl_buffer_atr"])

    # --- 1H: Trend direction ---
    highs_1h, lows_1h, closes_1h = parse_candles(raw_1h)
    adx, plus_di, minus_di = calc_adx(highs_1h, lows_1h, closes_1h, period=adx_period)
    ema20_1h = calc_ema(closes_1h, ema_fast)
    ema50_1h = calc_ema(closes_1h, ema_slow)

    bull_trend = ema20_1h[-1] > ema50_1h[-1] and plus_di > minus_di and adx >= adx_thresh
    bear_trend = ema20_1h[-1] < ema50_1h[-1] and minus_di > plus_di and adx >= adx_thresh

    base = {
        "side": None, "atr": 0.0,
        "adx": adx, "plus_di": plus_di, "minus_di": minus_di,
        "ema_fast": float(ema20_1h[-1]), "ema_slow": float(ema50_1h[-1]),
        "setup_sl": 0.0, "vol_ratio": 0.0,
    }

    if not bull_trend and not bear_trend:
        logger.debug("Signal skipped | reason=no_trend_1h adx={:.1f}", adx)
        return {**base, "reason": "no_trend_1h"}

    # --- 15m: Pullback setup ---
    highs_15m, lows_15m, closes_15m = parse_candles(raw_15m)
    vols_15m = parse_volumes(raw_15m)
    atr_15m = calc_atr(highs_15m, lows_15m, closes_15m, period=adx_period)
    ema20_15m = calc_ema(closes_15m, ema_fast)
    ema50_15m = calc_ema(closes_15m, ema_slow)

    if atr_15m <= 0:
        return {**base, "atr": atr_15m, "reason": "atr_zero"}

    base["atr"] = atr_15m
    cur_close = closes_15m[-1]

    # Price must be near EMA20 on 15m (pullback zone) and structure intact
    near_ema = abs(cur_close - ema20_15m[-1]) <= pb_touch * atr_15m

    if bull_trend:
        structure_ok = cur_close > ema50_15m[-1]
        # SL anchor = lowest low of recent pullback candles
        setup_sl_raw = float(np.min(lows_15m[-pb_bars - 1:-1])) - sl_buffer * atr_15m
    else:
        structure_ok = cur_close < ema50_15m[-1]
        # SL anchor = highest high of recent pullback candles
        setup_sl_raw = float(np.max(highs_15m[-pb_bars - 1:-1])) + sl_buffer * atr_15m

    if not near_ema or not structure_ok:
        return {**base, "reason": "no_pullback_15m"}

    # Counter-trend volume must be weaker than prior impulse volume
    recent_vols = vols_15m[-pb_bars - 1:-1]
    prior_vols  = vols_15m[-pb_bars * 2 - 1:-pb_bars - 1]
    avg_recent  = float(np.mean(recent_vols)) if len(recent_vols) > 0 else 1.0
    avg_prior   = float(np.mean(prior_vols))  if len(prior_vols)  > 0 else 1.0

    if avg_prior > 0 and avg_recent > avg_prior * pb_factor:
        return {**base, "reason": "pullback_volume_strong"}

    # --- 5m: Entry trigger ---
    highs_5m, lows_5m, closes_5m = parse_candles(raw_5m)
    vols_5m = parse_volumes(raw_5m)
    _, plus_di_5m, minus_di_5m = calc_adx(highs_5m, lows_5m, closes_5m, period=adx_period)

    # Use last completed candle ([-2]); [-1] may be incomplete
    trigger_close = closes_5m[-2]
    trigger_vol   = vols_5m[-2]
    lookback_highs = highs_5m[-bk_lookbk - 2:-2]
    lookback_lows  = lows_5m[-bk_lookbk - 2:-2]

    vol_sma   = calc_sma(vols_5m[:-1], vol_period)
    vol_ratio = trigger_vol / vol_sma if vol_sma > 0 else 0.0

    if bull_trend:
        breakout   = trigger_close > float(np.max(lookback_highs))
        di_confirm = plus_di_5m > minus_di_5m
        side       = "buy"
    else:
        breakout   = trigger_close < float(np.min(lookback_lows))
        di_confirm = minus_di_5m > plus_di_5m
        side       = "sell"

    if not breakout:
        return {**base, "reason": "no_breakout_5m", "vol_ratio": round(vol_ratio, 2)}

    if trigger_vol < vol_sma * vol_factor:
        return {**base, "reason": "breakout_volume_weak", "vol_ratio": round(vol_ratio, 2)}

    if not di_confirm:
        return {**base, "reason": "di_not_confirmed_5m", "vol_ratio": round(vol_ratio, 2)}

    logger.debug(
        "Signal | side={} adx_1h={:.1f} +DI={:.1f} -DI={:.1f} "
        "vol_ratio={:.2f} setup_sl={:.4f} atr_15m={:.4f}",
        side, adx, plus_di, minus_di, vol_ratio, setup_sl_raw, atr_15m,
    )

    return {
        **base,
        "side": side,
        "reason": "trend_pullback_breakout",
        "setup_sl": round(setup_sl_raw, 6),
        "vol_ratio": round(vol_ratio, 2),
    }
