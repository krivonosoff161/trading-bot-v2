"""
Indicators — EMA, ADX, ATR calculated from OHLCV candles.
Input: raw OKX candle list (newest first).
"""

import numpy as np


def parse_candles(raw_candles: list) -> tuple:
    """
    Parse OKX candle format and reverse to chronological order.
    OKX format: [ts, open, high, low, close, vol, ...]
    """
    candles = list(reversed(raw_candles))
    highs  = np.array([float(c[2]) for c in candles])
    lows   = np.array([float(c[3]) for c in candles])
    closes = np.array([float(c[4]) for c in candles])
    return highs, lows, closes


def calc_ema(closes: np.ndarray, period: int) -> np.ndarray:
    ema = np.zeros(len(closes))
    k = 2.0 / (period + 1)
    ema[period - 1] = np.mean(closes[:period])
    for i in range(period, len(closes)):
        ema[i] = closes[i] * k + ema[i - 1] * (1 - k)
    return ema


def calc_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    n = len(closes)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    # Wilder's smoothing
    atr = np.zeros(n)
    atr[period] = np.mean(tr[1:period + 1])
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return float(atr[-1])


def calc_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> tuple:
    n = len(closes)
    plus_dm  = np.zeros(n)
    minus_dm = np.zeros(n)
    tr       = np.zeros(n)

    for i in range(1, n):
        up   = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i]  = up   if up > down and up > 0   else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    def _wilder(arr: np.ndarray) -> np.ndarray:
        result = np.zeros(n)
        result[period] = np.sum(arr[1:period + 1])
        for i in range(period + 1, n):
            result[i] = result[i - 1] - result[i - 1] / period + arr[i]
        return result

    smooth_tr    = _wilder(tr)
    smooth_plus  = _wilder(plus_dm)
    smooth_minus = _wilder(minus_dm)

    safe_tr  = np.where(smooth_tr > 0, smooth_tr, 1.0)
    plus_di  = np.where(smooth_tr > 0, 100 * smooth_plus  / safe_tr, 0.0)
    minus_di = np.where(smooth_tr > 0, 100 * smooth_minus / safe_tr, 0.0)
    sum_di   = plus_di + minus_di
    safe_sum = np.where(sum_di > 0, sum_di, 1.0)
    dx = np.where(sum_di > 0, 100 * np.abs(plus_di - minus_di) / safe_sum, 0.0)

    # ADX = Wilder's smoothed DX
    adx = np.zeros(n)
    start = period * 2
    if start >= n:
        return 0.0, 0.0, 0.0
    adx[start] = np.mean(dx[period:period * 2])
    for i in range(start + 1, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return float(adx[-1]), float(plus_di[-1]), float(minus_di[-1])


def parse_volumes(raw_candles: list) -> np.ndarray:
    """Extract volume from OKX candles (index 5). Reverses to chronological order."""
    candles = list(reversed(raw_candles))
    return np.array([float(c[5]) for c in candles])


def calc_sma(values: np.ndarray, period: int) -> float:
    """Simple moving average of last `period` values."""
    if len(values) < period:
        return float(np.mean(values)) if len(values) > 0 else 0.0
    return float(np.mean(values[-period:]))


def calc_rsi(closes: np.ndarray, period: int = 3) -> float:
    """RSI with Wilder's smoothing."""
    n = len(closes)
    if n < period + 1:
        return 50.0

    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))
