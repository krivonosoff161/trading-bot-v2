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


def calc_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14, bar_index: int = -1) -> tuple:
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
    if start >= n or abs(bar_index) > n:
        return 0.0, 0.0, 0.0
    adx[start] = np.mean(dx[period:period * 2])
    for i in range(start + 1, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return float(adx[bar_index]), float(plus_di[bar_index]), float(minus_di[bar_index])


def parse_volumes(raw_candles: list) -> np.ndarray:
    """Extract volume from OKX candles (index 5). Reverses to chronological order."""
    candles = list(reversed(raw_candles))
    return np.array([float(c[5]) for c in candles])


def calc_sma(values: np.ndarray, period: int) -> float:
    """Simple moving average of last `period` values."""
    if len(values) < period:
        return float(np.mean(values)) if len(values) > 0 else 0.0
    return float(np.mean(values[-period:]))


def calc_atr_series(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Full ATR array using Wilder's smoothing (same logic as calc_atr but returns array)."""
    n = len(closes)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    atr = np.zeros(n)
    if period < n:
        atr[period] = float(np.mean(tr[1:period + 1]))
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def atr_regime(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
               period: int = 14, history: int = 50) -> tuple:
    """ATR percentile vs recent history bars. Returns (pct: float, label: str)."""
    atr_arr = calc_atr_series(highs, lows, closes, period)
    valid = atr_arr[period + 1:]  # skip warmup zeros
    if len(valid) < 10:
        return 50.0, "нормальная волатильность"
    h = min(history, len(valid))
    recent = valid[-h:]
    current = float(valid[-1])
    pct = float(np.mean(recent <= current) * 100)
    if pct <= 30:
        label = "сжатие (волатильность низкая)"
    elif pct >= 70:
        label = "расширение (волатильность высокая)"
    else:
        label = "нормальная волатильность"
    return round(pct, 1), label


def find_swing_levels(highs: np.ndarray, lows: np.ndarray,
                      lookback: int = 3, count: int = 4) -> dict:
    """Pivot-based swing high/low detection.

    A swing high at bar i: highs[i] is the max in window [i-lookback, i+lookback].
    A swing low  at bar i: lows[i]  is the min in window [i-lookback, i+lookback].
    Excludes last `lookback` bars (incomplete pivots).
    Returns last `count` confirmed swings of each type.
    """
    n = len(highs)
    swing_highs: list = []
    swing_lows:  list = []
    # Stop one bar earlier so the right window never includes the forming (unclosed) candle at n-1
    for i in range(lookback, n - lookback - 1):
        win_h = highs[max(0, i - lookback): i + lookback + 1]
        win_l = lows[max(0, i - lookback): i + lookback + 1]
        if float(highs[i]) >= float(np.max(win_h)):
            swing_highs.append(float(highs[i]))
        if float(lows[i]) <= float(np.min(win_l)):
            swing_lows.append(float(lows[i]))
    return {
        "recent_highs": swing_highs[-count:] if swing_highs else [],
        "recent_lows":  swing_lows[-count:]  if swing_lows  else [],
    }


def calc_bollinger_bands(closes: np.ndarray, period: int = 20, std_mult: float = 2.0) -> dict:
    """Bollinger Bands. Returns upper, middle, lower for last bar, plus %B and width%."""
    n = len(closes)
    if n < period:
        mid = float(closes[-1])
        return {"upper": mid, "middle": mid, "lower": mid, "pct_b": 50.0, "width_pct": 0.0}
    mid   = float(np.mean(closes[-period:]))
    std   = float(np.std(closes[-period:], ddof=0))
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    pct_b = round((float(closes[-1]) - lower) / (upper - lower) * 100, 1) if upper != lower else 50.0
    width = round((upper - lower) / mid * 100, 2) if mid else 0.0
    return {
        "upper":     round(upper, 6),
        "middle":    round(mid,   6),
        "lower":     round(lower, 6),
        "pct_b":     pct_b,   # 0=at lower, 50=at middle, 100=at upper
        "width_pct": width,   # band width as % of price — high = volatile
    }


def calc_supertrend(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                    period: int = 14, multiplier: float = 3.0) -> dict:
    """SuperTrend indicator. Returns current value, direction, and distance from price."""
    n = len(closes)
    if n < period + 2:
        return {"value": float(closes[-1]), "direction": "up", "distance_pct": 0.0}

    atr_arr = calc_atr_series(highs, lows, closes, period)

    upper = np.zeros(n)
    lower = np.zeros(n)
    st    = np.zeros(n)
    direction = np.ones(n, dtype=int)  # 1=up (bullish), -1=down (bearish)

    for i in range(period, n):
        mid = (highs[i] + lows[i]) / 2
        basic_upper = mid + multiplier * atr_arr[i]
        basic_lower = mid - multiplier * atr_arr[i]

        upper[i] = basic_upper if basic_upper < upper[i-1] or closes[i-1] > upper[i-1] else upper[i-1]
        lower[i] = basic_lower if basic_lower > lower[i-1] or closes[i-1] < lower[i-1] else lower[i-1]

        if st[i-1] == upper[i-1]:
            st[i] = lower[i] if closes[i] > upper[i] else upper[i]
        else:
            st[i] = upper[i] if closes[i] < lower[i] else lower[i]

        direction[i] = 1 if closes[i] > st[i] else -1

    cur_st  = round(float(st[-1]),  6)
    cur_dir = "up" if direction[-1] == 1 else "down"
    dist    = round(abs(float(closes[-1]) - cur_st) / float(closes[-1]) * 100, 2)
    return {"value": cur_st, "direction": cur_dir, "distance_pct": dist}


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


def calc_chandelier_exit(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                         lookback: int = 22, multiplier: float = 3.5) -> dict:
    """Chandelier Exit — volatility-based trailing stop.

    Long:  CE = Highest High(lookback) - ATR(lookback) * multiplier
    Short: CE = Lowest Low(lookback)   + ATR(lookback) * multiplier

    Crypto params: lookback=22 (1H candles ≈ 1 day), multiplier=3.5 (wider than std 3.0).
    Returns ce_long and ce_short for last bar.
    """
    n = len(closes)
    if n < lookback + 1:
        return {"ce_long": float(lows[-1]), "ce_short": float(highs[-1]), "atr": 0.0}

    atr_arr = calc_atr_series(highs, lows, closes, period=lookback)
    current_atr = float(atr_arr[-1])

    highest_high = float(np.max(highs[-lookback:]))
    lowest_low   = float(np.min(lows[-lookback:]))

    ce_long  = round(highest_high - multiplier * current_atr, 6)
    ce_short = round(lowest_low   + multiplier * current_atr, 6)

    return {"ce_long": ce_long, "ce_short": ce_short, "atr": round(current_atr, 6)}


def calc_slope(closes: np.ndarray, period: int = 5) -> float:
    """Linear regression slope angle (degrees) for the last `period` closes.

    Drozdov method: both x and y are min-max normalized to [0,1] before OLS.
    This makes the angle scale-independent (works on BTC and DOGE alike).
    Returns 0.0 for flat series or insufficient data.
    """
    if len(closes) < period:
        return 0.0
    y = closes[-period:].astype(float)
    y_range = float(y.max() - y.min())
    if y_range == 0.0:
        return 0.0
    x_sc = np.linspace(0.0, 1.0, period)
    y_sc = (y - y.min()) / y_range
    slope = float(np.polyfit(x_sc, y_sc, 1)[0])
    return float(np.degrees(np.arctan(slope)))


def find_fvg(candles: list, direction: str, lookback: int = 30) -> list[dict]:
    """Find recent Fair Value Gaps sorted by proximity to current price.

    Input candles must be chronological (oldest first) in OKX format:
    [ts, open, high, low, close, ...].
    """
    if len(candles) < 3:
        return []

    current_price = float(candles[-1][4])
    start = max(0, len(candles) - lookback - 2)
    gaps: list[dict] = []

    for i in range(start, len(candles) - 2):
        high_0 = float(candles[i][2])
        low_0 = float(candles[i][3])
        high_2 = float(candles[i + 2][2])
        low_2 = float(candles[i + 2][3])

        if direction == "bull" and high_0 < low_2:
            low_gap, high_gap = high_0, low_2
        elif direction == "bear" and low_0 > high_2:
            low_gap, high_gap = high_2, low_0
        else:
            continue

        gaps.append(
            {
                "low": round(low_gap, 6),
                "high": round(high_gap, 6),
                "mid": round((low_gap + high_gap) / 2.0, 6),
                "age_bars": len(candles) - (i + 3),
            }
        )

    return sorted(gaps, key=lambda gap: abs(gap["mid"] - current_price))
