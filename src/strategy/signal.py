"""
Signal — Strategy D: EMA(8/21) + RSI(3) scalping with MTF regime.

Regime filter : ADX(14) + DI on 5m — trending market detection.
Trend gate    : EMA(8/21) on 15m — directional confirmation.
Entry signal  : EMA(8/21) uptrend + RSI(3) pullback on 1m.
Exit          : OCO (TP+SL) placed on exchange. No reversal signal.
"""

import numpy as np
from loguru import logger

from src.strategy.indicators import calc_adx, calc_atr, calc_ema, calc_rsi, parse_candles

DI_MIN_DIFF     = 2.0   # min +DI/-DI separation to confirm direction
ATR_PERIOD      = 14
EMA_FAST        = 8
EMA_SLOW        = 21
RSI_PERIOD      = 3
MIN_CANDLES_1M  = 50    # enough for EMA(21) + RSI warmup
MIN_CANDLES_5M  = 35    # enough for ADX(14) warmup


def get_signal(raw_1m: list, raw_5m: list, raw_15m: list, sym_config: dict) -> dict:
    """
    MTF scalping signal.
    raw_15m → EMA(8/21) directional gate
    raw_5m → ADX + DI (regime filter, direction)
    raw_1m → EMA(8/21) + RSI(3) (entry)

    sym_config keys: adx_threshold, rsi_oversold, rsi_overbought

    Returns: {"side": "buy"/"sell"/None, "reason": str, "atr": float,
              "rsi": float, "adx": float, "plus_di": float, "minus_di": float,
              "ema_fast": float, "ema_slow": float}
    """
    empty = {
        "side": None, "reason": "", "atr": 0.0, "rsi": 50.0,
        "adx": 0.0, "plus_di": 0.0, "minus_di": 0.0,
        "ema_fast": 0.0, "ema_slow": 0.0,
    }

    if (
        len(raw_1m) < MIN_CANDLES_1M
        or len(raw_5m) < MIN_CANDLES_5M
        or len(raw_15m) < EMA_SLOW
    ):
        return {**empty, "reason": "not_enough_candles"}

    # 15m — higher timeframe directional gate
    _, _, closes_15m = parse_candles(raw_15m)
    ema8_15m = calc_ema(closes_15m, EMA_FAST)
    ema21_15m = calc_ema(closes_15m, EMA_SLOW)
    trend_up_15m = ema8_15m[-1] > ema21_15m[-1]
    trend_down_15m = ema8_15m[-1] < ema21_15m[-1]

    # 5m — regime detection + trend direction
    highs_5m, lows_5m, closes_5m = parse_candles(raw_5m)
    adx, plus_di, minus_di = calc_adx(highs_5m, lows_5m, closes_5m, period=14)
    ema8_5m = calc_ema(closes_5m, EMA_FAST)
    ema21_5m = calc_ema(closes_5m, EMA_SLOW)

    # 1m — entry indicators
    highs_1m, lows_1m, closes_1m = parse_candles(raw_1m)
    atr   = calc_atr(highs_1m, lows_1m, closes_1m, period=ATR_PERIOD)
    rsi   = calc_rsi(closes_1m, period=RSI_PERIOD)
    ema8  = calc_ema(closes_1m, EMA_FAST)
    ema21 = calc_ema(closes_1m, EMA_SLOW)

    base = {
        "side": None, "atr": atr, "rsi": rsi, "adx": adx,
        "plus_di": plus_di, "minus_di": minus_di,
        "ema_fast": float(ema8[-1]), "ema_slow": float(ema21[-1]),
    }

    adx_threshold = float(sym_config["adx_threshold"])
    if adx < adx_threshold:
        logger.debug(
            "Signal skipped | reason=choppy adx_5m={:.1f} threshold={:.1f}",
            adx, adx_threshold,
        )
        return {**base, "reason": "choppy"}

    rsi_oversold   = sym_config["rsi_oversold"]
    rsi_overbought = sym_config["rsi_overbought"]

    buy_setup = (
        ema8_5m[-1] > ema21_5m[-1]
        and plus_di > minus_di + DI_MIN_DIFF
        and ema8[-1] > ema21[-1]
        and rsi < rsi_oversold
    )
    sell_setup = (
        ema8_5m[-1] < ema21_5m[-1]
        and minus_di > plus_di + DI_MIN_DIFF
        and ema8[-1] < ema21[-1]
        and rsi > rsi_overbought
    )

    if buy_setup and not trend_up_15m:
        logger.debug("Signal skipped | reason=blocked_15m_downtrend")
        return {**base, "reason": "blocked_15m_downtrend"}

    if sell_setup and not trend_down_15m:
        logger.debug("Signal skipped | reason=blocked_15m_uptrend")
        return {**base, "reason": "blocked_15m_uptrend"}

    # BUY: 15m uptrend + 5m uptrend (EMA + DI) + EMA uptrend on 1m + RSI pullback
    if buy_setup:
        logger.debug(
            "Signal | side=buy rsi={:.1f} ema_gap={:.2f} adx={:.1f} +DI={:.1f} -DI={:.1f}",
            rsi, ema8[-1] - ema21[-1], adx, plus_di, minus_di,
        )
        return {**base, "side": "buy", "reason": "ema_uptrend_rsi_pullback"}

    # SELL: 15m downtrend + 5m downtrend (EMA + DI) + EMA downtrend on 1m + RSI spike
    if sell_setup:
        logger.debug(
            "Signal | side=sell rsi={:.1f} ema_gap={:.2f} adx={:.1f} +DI={:.1f} -DI={:.1f}",
            rsi, ema8[-1] - ema21[-1], adx, plus_di, minus_di,
        )
        return {**base, "side": "sell", "reason": "ema_downtrend_rsi_spike"}

    return {**base, "reason": "no_signal"}
