"""
Signal — market regime filter + entry signal + reversal detection.
All based on 5m candles from OKX REST.
"""

from typing import Optional

from loguru import logger

from src.strategy.indicators import calc_adx, calc_atr, calc_ema, parse_candles

ADX_THRESHOLD = 25
EMA_FAST      = 9
EMA_SLOW      = 21
ATR_PERIOD    = 14
MIN_CANDLES   = 50  # minimum candles needed for reliable ADX(14)


def get_signal(raw_candles: list) -> dict:
    """
    Analyse candles and return entry signal.
    Returns: {"side": "buy"/"sell"/None, "reason": str, "adx": float, "atr": float}
    """
    if len(raw_candles) < MIN_CANDLES:
        return {"side": None, "reason": "not_enough_candles", "adx": 0.0, "atr": 0.0}

    highs, lows, closes = parse_candles(raw_candles)

    adx = calc_adx(highs, lows, closes, period=14)
    atr = calc_atr(highs, lows, closes, period=ATR_PERIOD)

    if adx < ADX_THRESHOLD:
        logger.debug("Signal skipped | reason=choppy adx={:.1f}", adx)
        return {"side": None, "reason": "choppy", "adx": adx, "atr": atr}

    ema_fast = calc_ema(closes, EMA_FAST)
    ema_slow = calc_ema(closes, EMA_SLOW)

    prev_fast, curr_fast = ema_fast[-2], ema_fast[-1]
    prev_slow, curr_slow = ema_slow[-2], ema_slow[-1]

    if prev_fast <= prev_slow and curr_fast > curr_slow:
        logger.debug("Signal | side=buy reason=ema_cross_up adx={:.1f}", adx)
        return {"side": "buy", "reason": "ema_cross_up", "adx": adx, "atr": atr}

    if prev_fast >= prev_slow and curr_fast < curr_slow:
        logger.debug("Signal | side=sell reason=ema_cross_down adx={:.1f}", adx)
        return {"side": "sell", "reason": "ema_cross_down", "adx": adx, "atr": atr}

    return {"side": None, "reason": "no_signal", "adx": adx, "atr": atr}


def get_reversal_signal(raw_candles: list, position_side: str) -> Optional[str]:
    """
    Check if candles show a reversal against the open position.
    Always verify position is still open (get_positions) before acting on this.
    Returns reason string or None.
    """
    if len(raw_candles) < MIN_CANDLES:
        return None

    highs, lows, closes = parse_candles(raw_candles)
    ema_fast = calc_ema(closes, EMA_FAST)
    ema_slow = calc_ema(closes, EMA_SLOW)

    prev_fast, curr_fast = ema_fast[-2], ema_fast[-1]
    prev_slow, curr_slow = ema_slow[-2], ema_slow[-1]

    if position_side == "buy" and prev_fast >= prev_slow and curr_fast < curr_slow:
        return "ema_cross_down"

    if position_side == "sell" and prev_fast <= prev_slow and curr_fast > curr_slow:
        return "ema_cross_up"

    return None
