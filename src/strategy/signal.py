"""
Signal — Strategy C: Range + RSI with Multi-Timeframe (MTF) analysis.

Regime filter : ADX(14) on 15m candles — stable, not affected by 5m spikes.
Entry signal  : RSI(3) + Range(20) on 5m candles — precise entry timing.
Reversal      : price crosses MPR on 5m against open position.
"""

import numpy as np
from typing import Optional

from loguru import logger

from src.strategy.indicators import calc_adx, calc_atr, calc_rsi, parse_candles

ADX_THRESHOLD  = 25    # ADX(15m) < this → ranging market
ATR_PERIOD     = 14
RANGE_PERIOD   = 20    # 5m candles for High/Low range (~100 min)
RSI_PERIOD     = 3
RSI_OVERSOLD   = 35
RSI_OVERBOUGHT = 65
ENTRY_ATR_MULT = 0.5   # entry zone: boundary ± ATR * mult
MIN_CANDLES_15M = 35   # enough for ADX(14) warmup on 15m
MIN_CANDLES_5M  = 60   # enough for RSI + Range on 5m


def get_signal(raw_5m: list, raw_15m: list) -> dict:
    """
    MTF entry signal.
    raw_15m → ADX (regime filter)
    raw_5m  → RSI + Range + ATR (entry)

    Returns: {"side": "buy"/"sell"/None, "reason": str, "atr": float,
              "rsi": float, "adx": float, "range_high": float,
              "range_low": float, "mpr": float}
    """
    empty = {
        "side": None, "reason": "", "atr": 0.0, "rsi": 50.0,
        "adx": 0.0, "range_high": 0.0, "range_low": 0.0, "mpr": 0.0,
    }

    if len(raw_15m) < MIN_CANDLES_15M or len(raw_5m) < MIN_CANDLES_5M:
        return {**empty, "reason": "not_enough_candles"}

    # 15m — regime detection
    highs_15m, lows_15m, closes_15m = parse_candles(raw_15m)
    adx = calc_adx(highs_15m, lows_15m, closes_15m, period=14)

    # 5m — entry signal
    highs_5m, lows_5m, closes_5m = parse_candles(raw_5m)
    atr   = calc_atr(highs_5m, lows_5m, closes_5m, period=ATR_PERIOD)
    rsi   = calc_rsi(closes_5m, period=RSI_PERIOD)

    range_high = float(np.max(highs_5m[-RANGE_PERIOD:]))
    range_low  = float(np.min(lows_5m[-RANGE_PERIOD:]))
    mpr        = (range_high + range_low) / 2.0
    price      = closes_5m[-1]

    base = {
        "side": None, "atr": atr, "rsi": rsi, "adx": adx,
        "range_high": range_high, "range_low": range_low, "mpr": mpr,
    }

    if adx >= ADX_THRESHOLD:
        logger.debug("Signal skipped | reason=trending adx_15m={:.1f}", adx)
        return {**base, "reason": "trending"}

    entry_zone = atr * ENTRY_ATR_MULT

    # BUY near range low
    if price <= range_low + entry_zone and rsi < RSI_OVERSOLD:
        logger.debug(
            "Signal | side=buy rsi={:.1f} price={:.2f} range_low={:.2f} adx_15m={:.1f}",
            rsi, price, range_low, adx,
        )
        return {**base, "side": "buy", "reason": "range_low_rsi"}

    # SELL near range high
    if price >= range_high - entry_zone and rsi > RSI_OVERBOUGHT:
        logger.debug(
            "Signal | side=sell rsi={:.1f} price={:.2f} range_high={:.2f} adx_15m={:.1f}",
            rsi, price, range_high, adx,
        )
        return {**base, "side": "sell", "reason": "range_high_rsi"}

    return {**base, "reason": "no_signal"}


def get_reversal_signal(raw_5m: list, raw_15m: list, position_side: str) -> Optional[str]:
    """
    Range reversal: price crosses MPR (5m) against open position.
    Always verify position is still open (get_positions) before acting on this.
    Returns reason string or None.
    """
    if len(raw_5m) < MIN_CANDLES_5M:
        return None

    highs_5m, lows_5m, closes_5m = parse_candles(raw_5m)

    range_high = float(np.max(highs_5m[-RANGE_PERIOD:]))
    range_low  = float(np.min(lows_5m[-RANGE_PERIOD:]))
    mpr        = (range_high + range_low) / 2.0
    price      = closes_5m[-1]

    if position_side == "buy" and price > mpr:
        return "price_above_mpr"

    if position_side == "sell" and price < mpr:
        return "price_below_mpr"

    return None
