"""
Signal — Strategy C: Range + RSI filter.

Market regime: ADX < 25 → ranging (good for this strategy).
Entry: near range boundary confirmed by RSI extreme.
Reversal: price crosses MPR (midpoint of range) against position.
"""

import numpy as np
from typing import Optional

from loguru import logger

from src.strategy.indicators import calc_adx, calc_atr, calc_rsi, parse_candles

ADX_THRESHOLD  = 25    # ADX < this → ranging market (we want this)
ATR_PERIOD     = 14
RANGE_PERIOD   = 20    # candles to measure High/Low range
RSI_PERIOD     = 3
RSI_OVERSOLD   = 35
RSI_OVERBOUGHT = 65
ENTRY_ATR_MULT = 0.5   # entry zone: boundary ± ATR * mult
MIN_CANDLES    = 60    # buffer for ADX(14) warmup + RANGE_PERIOD


def get_signal(raw_candles: list) -> dict:
    """
    Analyse candles and return entry signal.
    Returns: {"side": "buy"/"sell"/None, "reason": str, "atr": float,
              "rsi": float, "adx": float, "range_high": float,
              "range_low": float, "mpr": float}
    """
    empty = {
        "side": None, "reason": "", "atr": 0.0, "rsi": 50.0,
        "adx": 0.0, "range_high": 0.0, "range_low": 0.0, "mpr": 0.0,
    }

    if len(raw_candles) < MIN_CANDLES:
        return {**empty, "reason": "not_enough_candles"}

    highs, lows, closes = parse_candles(raw_candles)

    adx = calc_adx(highs, lows, closes, period=14)
    atr = calc_atr(highs, lows, closes, period=ATR_PERIOD)
    rsi = calc_rsi(closes, period=RSI_PERIOD)

    range_high = float(np.max(highs[-RANGE_PERIOD:]))
    range_low  = float(np.min(lows[-RANGE_PERIOD:]))
    mpr        = (range_high + range_low) / 2.0
    price      = closes[-1]

    base = {
        "side": None, "atr": atr, "rsi": rsi, "adx": adx,
        "range_high": range_high, "range_low": range_low, "mpr": mpr,
    }

    if adx >= ADX_THRESHOLD:
        logger.debug("Signal skipped | reason=trending adx={:.1f}", adx)
        return {**base, "reason": "trending"}

    entry_zone = atr * ENTRY_ATR_MULT

    # BUY near range low
    if price <= range_low + entry_zone and rsi < RSI_OVERSOLD:
        logger.debug(
            "Signal | side=buy rsi={:.1f} price={:.2f} range_low={:.2f}",
            rsi, price, range_low,
        )
        return {**base, "side": "buy", "reason": "range_low_rsi"}

    # SELL near range high
    if price >= range_high - entry_zone and rsi > RSI_OVERBOUGHT:
        logger.debug(
            "Signal | side=sell rsi={:.1f} price={:.2f} range_high={:.2f}",
            rsi, price, range_high,
        )
        return {**base, "side": "sell", "reason": "range_high_rsi"}

    return {**base, "reason": "no_signal"}


def get_reversal_signal(raw_candles: list, position_side: str) -> Optional[str]:
    """
    Range reversal: price crosses MPR against the open position.
    Always verify position is still open (get_positions) before acting on this.
    Returns reason string or None.
    """
    if len(raw_candles) < MIN_CANDLES:
        return None

    highs, lows, closes = parse_candles(raw_candles)

    range_high = float(np.max(highs[-RANGE_PERIOD:]))
    range_low  = float(np.min(lows[-RANGE_PERIOD:]))
    mpr        = (range_high + range_low) / 2.0
    price      = closes[-1]

    if position_side == "buy" and price > mpr:
        return "price_above_mpr"

    if position_side == "sell" and price < mpr:
        return "price_below_mpr"

    return None
