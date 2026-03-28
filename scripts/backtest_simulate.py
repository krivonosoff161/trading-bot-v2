"""
Full backtest simulation: two intraday engines — FAST (1-2h) and SWING (2-4h).
Runs on historical candles every 10 minutes.

New architecture (Qwen + Codex audit):
- ADX_1H rising (not static threshold)
- BB Width expansion on 15m as regime gate
- Structural SL from swing levels
- Side-aware funding filter
- Late-move veto (day_range + day_position)
- DOGE excluded (too noisy)
- No day_high/low as TP — fixed R multiples only
- ADX_4H as late veto only (not main gate)

Usage:
    python scripts/backtest_simulate.py
"""
import asyncio
import bisect
import os
import pickle
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from src.exchange.okx_client import OKXClient
from src.strategy.indicators import (
    calc_atr, calc_adx, calc_ema, parse_candles, parse_volumes,
    find_swing_levels, calc_bollinger_bands,
)

# ── Historical data helpers ─────────────────────────────────────────────────────

def _get_hist_funding(funding_history: list, ts_ms: int) -> float:
    """Return the funding rate active at ts_ms.
    Funding settles every 8h; rate is set at settlement and holds until next.
    Searches for the most recent settlement <= ts_ms.
    """
    best_rate = 0.0
    best_ts   = 0
    for entry in funding_history:
        ft = int(entry.get("fundingTime", 0))
        if ft <= ts_ms and ft > best_ts:
            best_ts   = ft
            best_rate = float(entry.get("fundingRate", 0))
    return best_rate


def _get_oi_delta(oi_history: list, ts_ms: int) -> float:
    """Return OI change fraction vs previous bar at ts_ms: (oi_now - oi_prev) / oi_prev.
    OKX rubik returns entries as lists [ts, oi, oiCcy] or dicts.
    Returns 0.0 if data unavailable.
    """
    if not oi_history:
        return 0.0

    def _parse(entry):
        if isinstance(entry, dict):
            return int(entry.get("ts", 0)), float(entry.get("oi", 0) or entry.get("oiCcy", 0))
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            return int(entry[0]), float(entry[1])
        return 0, 0.0

    sorted_oi = sorted((_parse(e) for e in oi_history), key=lambda x: x[0])
    prev_oi = cur_oi = None
    for t, oi in sorted_oi:
        if t <= ts_ms:
            prev_oi = cur_oi
            cur_oi  = oi
    if cur_oi and prev_oi and prev_oi > 0:
        return (cur_oi - prev_oi) / prev_oi
    return 0.0

# ── Config ─────────────────────────────────────────────────────────────────────
SYMBOLS       = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT"]
EXTRA_SYMBOLS = []
ALL_SYMBOLS   = SYMBOLS + EXTRA_SYMBOLS

DAYS_BACK  = 14
INTERVAL_M = 10
OUTCOME_H  = 24
ADX_PERIOD = 14

# ── Candle cache ────────────────────────────────────────────────────────────────
CACHE_FILE    = Path(__file__).parent / "backtest_candle_cache.pkl"
CACHE_MAX_AGE = 23 * 3600

# ── Per-pair thresholds ─────────────────────────────────────────────────────────
# None = pair is OFF (DOGE excluded)
PAIR_PARAMS = {
    "BTC-USDT":  {
        "fast_vol": 1.6, "fast_adx": 18,
        "swing_vol": 1.3, "swing_adx": 18,
        "sl_k": 1.4, "fast_tp_k": 0.8, "swing_tp_k": 1.5,
        "late_range": 4.0,
        "allowed_modes": ["FAST", "SWING"],
    },
    "ETH-USDT":  {
        "fast_vol": 1.8, "fast_adx": 18,
        "swing_vol": 1.5, "swing_adx": 18,
        "sl_k": 1.4, "fast_tp_k": 0.8, "swing_tp_k": 1.5,
        "late_range": 7.0,
        "allowed_modes": ["FAST", "SWING"],
    },
    "SOL-USDT":  {
        "fast_vol": 2.8, "fast_adx": 24,
        "swing_vol": 2.2, "swing_adx": 24,
        "sl_k": 1.7, "fast_tp_k": 0.8, "swing_tp_k": 1.5,
        "late_range": 10.0,
        "allowed_modes": ["FAST", "SWING"],
    },
    "DOGE-USDT": None,                       # OFF — too noisy
    "XRP-USDT":  {
        "fast_vol": 1.8, "fast_adx": 18,
        "swing_vol": 1.4, "swing_adx": 18,
        "sl_k": 1.6, "fast_tp_k": 0.8, "swing_tp_k": 1.5,
        "late_range": 7.0,
        "allowed_modes": ["FAST", "SWING"],
    },
}

# ── Param sets ──────────────────────────────────────────────────────────────────
PARAM_SETS = [
    {
        "label":         "COMBINED | period1 (last 14d) | 10UTC block",
        "mode":          "COMBINED",
        "night_filter":  True,
        "time_block_h":  [10],
        "offset_days":   0,               # current 14 days
    },
    {
        "label":         "COMBINED | period2 (14d-28d ago) | 10UTC block",
        "mode":          "COMBINED",
        "night_filter":  True,
        "time_block_h":  [10],
        "offset_days":   14,
    },
    {
        "label":         "COMBINED | period3 (28d-42d ago) | 10UTC block",
        "mode":          "COMBINED",
        "night_filter":  True,
        "time_block_h":  [10],
        "offset_days":   28,
    },
    {
        "label":         "COMBINED | period4 (42d-56d ago) | 10UTC block",
        "mode":          "COMBINED",
        "night_filter":  True,
        "time_block_h":  [10],
        "offset_days":   42,
    },
]

# ── Helpers ─────────────────────────────────────────────────────────────────────

def _bias(bull: bool, bear: bool) -> str:
    if bull: return "UP"
    if bear: return "DOWN"
    return "NEUTRAL"


def _calc_vwap_and_day(raw_15m: list, day_start_ms: int):
    day_candles = [c for c in raw_15m if int(c[0]) >= day_start_ms]
    if not day_candles:
        return None, None, None
    closes = [float(c[4]) for c in day_candles]
    vols   = [float(c[5]) for c in day_candles]
    highs  = [float(c[2]) for c in day_candles]
    lows   = [float(c[3]) for c in day_candles]
    vol_sum = sum(vols)
    vwap = sum(c * v for c, v in zip(closes, vols)) / vol_sum if vol_sum > 0 else None
    return vwap, max(highs), min(lows)


# ── Signal engine ───────────────────────────────────────────────────────────────

def compute_signal(raw_4h, raw_1h, raw_15m, funding, symbol="",
                   mode="SWING", night_filter=False, oi_delta=0.0,
                   time_block_h=None, raw_5m=None):
    """
    mode="FAST"     — fast intraday only (1-2h hold)
    mode="SWING"    — intraday swing only (2-4h hold)
    mode="COMBINED" — FAST checked first, SWING as fallback
    """
    pp = PAIR_PARAMS.get(symbol)
    if pp is None:
        return None   # pair is OFF

    if not raw_4h or len(raw_4h) < 20: return None
    if not raw_1h or len(raw_1h) < 20: return None
    if not raw_15m or len(raw_15m) < 30: return None

    h4h, l4h, c4h = parse_candles(raw_4h)
    h1h, l1h, c1h = parse_candles(raw_1h)
    h15, l15, c15 = parse_candles(raw_15m)

    # ADX on 1H — current and previous bar for rising check
    adx_1h,      _, _ = calc_adx(h1h, l1h, c1h, period=ADX_PERIOD, bar_index=-1)
    adx_1h_prev, _, _ = calc_adx(h1h, l1h, c1h, period=ADX_PERIOD, bar_index=-2)
    adx_1h_rising = float(adx_1h) > float(adx_1h_prev)

    # ADX on 4H — late veto only
    adx_4h, _, _ = calc_adx(h4h, l4h, c4h, period=ADX_PERIOD)

    # EMA bias on 1H (direction source)
    ema20_1h = calc_ema(c1h, 20)
    ema50_1h = calc_ema(c1h, 50)
    bias_1h  = _bias(ema20_1h[-1] > ema50_1h[-1], ema20_1h[-1] < ema50_1h[-1])

    # EMA bias on 4H (veto reference)
    ema20_4h = calc_ema(c4h, 20)
    ema50_4h = calc_ema(c4h, 50)
    bias_4h  = _bias(ema20_4h[-1] > ema50_4h[-1], ema20_4h[-1] < ema50_4h[-1])

    # 4H alignment context (SWING)
    four_h_conflict = bias_4h != "NEUTRAL" and bias_4h != bias_1h
    adx_4h_ok       = float(adx_4h) >= 20

    # 5m trigger (FAST): direction from 5m momentum, independent of 1H bias
    five_m_long  = True  # default: don't block if no 5m data
    five_m_short = True
    if raw_5m and len(raw_5m) >= 21:
        h5, l5, c5 = parse_candles(raw_5m)
        ema20_5m      = calc_ema(c5, 20)
        trigger_close = float(c5[-1])
        five_m_long  = trigger_close > float(ema20_5m[-1])
        five_m_short = trigger_close < float(ema20_5m[-1])

    # ATR on 15m and 1H
    atr_15m = float(calc_atr(h15, l15, c15, period=ADX_PERIOD))
    atr_1h  = float(calc_atr(h1h, l1h, c1h, period=ADX_PERIOD))
    close   = float(c15[-1])

    # Vol ratio on 15m: last 3 bars vs prev 15 bars
    vols_15m   = [float(c[5]) for c in list(reversed(raw_15m))]
    recent_vol = np.mean(vols_15m[:3])   if len(vols_15m) >= 3  else 0
    prior_vol  = np.mean(vols_15m[5:20]) if len(vols_15m) >= 20 else recent_vol
    vol_ratio  = recent_vol / prior_vol if prior_vol > 0 else 1.0

    # BB Width on 15m — expansion regime filter
    bb          = calc_bollinger_bands(c15, period=20, std_mult=2.0)
    bb_width    = bb["width_pct"]
    bb_expanding = bb_width > 1.5   # < 1.5% of price = sideways, skip

    # VWAP and day levels
    ts_last      = int(raw_15m[0][0])
    dt_last      = datetime.fromtimestamp(ts_last / 1000, tz=timezone.utc)
    day_start_ms = int(datetime(dt_last.year, dt_last.month, dt_last.day,
                                tzinfo=timezone.utc).timestamp() * 1000)
    vwap, day_high, day_low = _calc_vwap_and_day(raw_15m, day_start_ms)

    day_position = None
    if day_high and day_low and day_high != day_low:
        day_position = (close - day_low) / (day_high - day_low)

    daily_range_pct = 0.0
    if day_high and day_low and day_low > 0:
        daily_range_pct = (day_high - day_low) / day_low * 100

    signal_hour = dt_last.hour
    is_night    = 1 <= signal_hour < 7

    # Late-move veto: day already moved a lot and price is at the top
    late_move = (daily_range_pct > pp["late_range"]
                 and day_position is not None and day_position > 0.90)

    # ── Mode + Direction detection (both sides independently) ─────────────────
    trade_style = "NO_TRADE"
    side        = None

    fast_base = (float(adx_1h) >= pp["fast_adx"]
                 and adx_1h_rising
                 and vol_ratio >= pp["fast_vol"]
                 and bb_expanding)

    # FAST: direction from 5m momentum
    # Strong 1H trend (ADX > 30): respect the trend — only trade with it
    # Weak 1H trend (ADX <= 30): both directions allowed — 5m decides
    FAST_STRONG_ADX = 30
    strong_trend = float(adx_1h) > FAST_STRONG_ADX
    fast_long_ok  = five_m_long  and (not strong_trend or bias_1h == "UP")
    fast_short_ok = five_m_short and (not strong_trend or bias_1h == "DOWN")

    if mode in ("FAST", "COMBINED"):
        if fast_base and fast_long_ok:
            trade_style, side = "FAST", "buy"
        elif fast_base and fast_short_ok:
            trade_style, side = "FAST", "sell"

    swing_base = (float(adx_1h) >= pp["swing_adx"]
                  and adx_1h_rising
                  and vol_ratio >= pp["swing_vol"]
                  and bb_expanding
                  and not four_h_conflict
                  and adx_4h_ok)

    # SWING: direction from 1H + 4H alignment (trend-following by design)
    if mode in ("SWING", "COMBINED") and trade_style == "NO_TRADE":
        if swing_base and bias_1h == "UP":
            trade_style, side = "SWING", "buy"
        elif swing_base and bias_1h == "DOWN":
            trade_style, side = "SWING", "sell"

    # Per-pair mode restriction
    allowed = pp.get("allowed_modes", ["FAST", "SWING"])
    if trade_style not in allowed:
        trade_style, side = "NO_TRADE", None

    # Night filter — block all signals 01-07 UTC
    if night_filter and is_night:
        trade_style, side = "NO_TRADE", None

    # Time block (e.g. 10:00 UTC = 0% WR)
    if time_block_h and signal_hour in time_block_h:
        trade_style, side = "NO_TRADE", None

    # Late-move veto
    if late_move:
        trade_style, side = "NO_TRADE", None

    # 4H veto — informational only; SWING handles 4H in condition, FAST is 4H-agnostic
    fourch_veto = four_h_conflict

    # ── VWAP filter ───────────────────────────────────────────────────────────
    vwap_ok = True
    if vwap and close and side:
        if side == "buy"  and close < vwap: vwap_ok = False
        if side == "sell" and close > vwap: vwap_ok = False

    # ── Side-aware funding filter ─────────────────────────────────────────────
    funding_val   = funding if funding is not None else 0.0
    FUND_THRESH   = 0.0005   # 0.05%
    funding_block = False
    if side == "buy"  and funding_val >  FUND_THRESH: funding_block = True
    if side == "sell" and funding_val < -FUND_THRESH: funding_block = True

    # ── OI delta filter ───────────────────────────────────────────────────────
    # OI dropping >3% = positions being closed, not new money = weaker signal
    oi_weak = oi_delta < -0.03

    # ── SL / TP ───────────────────────────────────────────────────────────────
    sl_p = tp_p = None
    sl_dist = 0.0

    if trade_style == "FAST" and side and close:
        sl_dist  = max(pp["sl_k"] * atr_15m, close * 0.004)
        tp_dist  = min(sl_dist * pp["fast_tp_k"], atr_1h * FAST_ATR_CAP)
        if side == "buy":
            sl_p = round(close - sl_dist, 6)
            tp_p = round(close + tp_dist,  6)
        else:
            sl_p = round(close + sl_dist, 6)
            tp_p = round(close - tp_dist,  6)

    elif trade_style == "SWING" and side and close:
        # Structural SL: swing level + ATR buffer, at least sl_k*ATR
        swings = find_swing_levels(h15, l15, lookback=3, count=4)
        if side == "buy":
            atr_sl    = close - pp["sl_k"] * atr_15m
            struct_sl = (swings["recent_lows"][-1] - 0.3 * atr_15m
                         if swings["recent_lows"] else None)
            sl_p    = round(min(struct_sl, atr_sl) if struct_sl else atr_sl, 6)
            sl_dist = close - sl_p
            tp_dist = min(sl_dist * pp["swing_tp_k"], atr_1h * SWING_ATR_CAP)
            tp_p    = round(close + tp_dist, 6)
        else:
            atr_sl    = close + pp["sl_k"] * atr_15m
            struct_sl = (swings["recent_highs"][-1] + 0.3 * atr_15m
                         if swings["recent_highs"] else None)
            sl_p    = round(max(struct_sl, atr_sl) if struct_sl else atr_sl, 6)
            sl_dist = sl_p - close
            tp_dist = min(sl_dist * pp["swing_tp_k"], atr_1h * SWING_ATR_CAP)
            tp_p    = round(close - tp_dist, 6)

    # ── Final signal ──────────────────────────────────────────────────────────
    blocked = (trade_style == "NO_TRADE" or not vwap_ok
               or funding_block or oi_weak
               or not sl_p or not tp_p)

    entry_signal = "NO_TRADE" if blocked else "ENTRY"

    return {
        "entry_signal":    entry_signal,
        "trade_style":     trade_style,
        "side":            side,
        "close":           close,
        "sl":              sl_p,
        "tp":              tp_p,
        "adx_1h":          round(float(adx_1h), 1),
        "adx_1h_rising":   adx_1h_rising,
        "adx_4h":          round(float(adx_4h), 1),
        "bias_1h":         bias_1h,
        "vol_ratio":       round(vol_ratio, 2),
        "bb_width":        round(bb_width, 2),
        "funding":         funding_val,
        "oi_delta":        round(oi_delta, 4),
        "day_position":    round(day_position, 3) if day_position else None,
        "fourch_veto":     fourch_veto,
        "late_move":       late_move,
        "oi_weak":         oi_weak,
    }


RISK_PCT      = 0.03   # risk 3% of current balance per trade (6x leverage, 0.5% margin)
FAST_ATR_CAP  = 0.7    # FAST TP capped at 0.7 × ATR_1H  (≈ realistic 2h move)
SWING_ATR_CAP = 1.5    # SWING TP capped at 1.5 × ATR_1H (≈ realistic 4h move)


def calc_pnl(outcome, side, entry, sl, tp, exit_price, balance):
    """Calculate P&L in dollars for one trade outcome.
    TP:        full close at TP → +R * risk_amount
    STOP:      full close at SL → -risk_amount
    TIME_EXIT: close at last candle in hold window → market P&L
    """
    sl_dist = abs(entry - sl)
    if sl_dist == 0:
        return 0.0
    risk_amount = balance * RISK_PCT
    direction   = 1 if side == "buy" else -1

    if outcome == "STOP":
        return -risk_amount
    if outcome == "TP":
        r = abs(tp - entry) / sl_dist
        return risk_amount * r
    if outcome == "TIME_EXIT":
        if exit_price is None:
            return 0.0
        price_move = direction * (exit_price - entry)
        tp_dist    = abs(tp - entry)
        price_move = max(-sl_dist, min(tp_dist, price_move))
        return risk_amount * (price_move / sl_dist)
    return 0.0


def check_outcome(raw_forward, side, sl, tp, signal_ts_ms, max_hold_ms):
    """Returns (outcome, elapsed_mins, exit_price)."""
    if not raw_forward:
        return "NO_DATA", None, None
    last_close = None
    for c in reversed(raw_forward):
        ts_c = int(c[0])
        if ts_c <= signal_ts_ms:
            continue
        elapsed_ms = ts_c - signal_ts_ms
        if elapsed_ms > max_hold_ms:
            return "TIME_EXIT", elapsed_ms // 60000, last_close
        last_close = float(c[4])
        high = float(c[2])
        low  = float(c[3])
        mins = elapsed_ms // 60000
        if side == "buy":
            if low  <= sl:  return "STOP", mins, sl
            if high >= tp:  return "TP",   mins, tp
        else:
            if high >= sl:  return "STOP", mins, sl
            if low  <= tp:  return "TP",   mins, tp
    return "OPEN", None, None


# ── Main ────────────────────────────────────────────────────────────────────────

async def run():
    client = OKXClient(
        api_key=os.getenv("OKX_API_KEY", ""),
        secret_key=os.getenv("OKX_SECRET_KEY", ""),
        passphrase=os.getenv("OKX_PASSPHRASE", ""),
        is_demo=False,
    )

    now_ms  = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    step_ms = INTERVAL_M * 60 * 1000
    hold_ms = {"FAST": 120 * 60 * 1000, "SWING": 240 * 60 * 1000}

    # Cache must cover all periods: DAYS_BACK + max offset + warmup for EMA-50 on 4H
    INDICATOR_WARMUP_DAYS = 10   # EMA-50 on 4H needs 50 bars = 8.3 days
    max_offset_days  = max(p.get("offset_days", 0) for p in PARAM_SETS)
    total_days       = DAYS_BACK + max_offset_days + INDICATOR_WARMUP_DAYS
    cache_start_ms   = now_ms - total_days * 24 * 3600 * 1000
    all_timestamps   = list(range(cache_start_ms,
                                  now_ms - OUTCOME_H * 3600 * 1000, step_ms))

    _api_sem = asyncio.Semaphore(1)

    async def _fetch_full_history(symbol: str, bar: str, since_ms: int) -> list:
        """Fetch all candles since since_ms via pagination. Returns sorted oldest-first."""
        all_candles: list = []
        after_ms = None
        while True:
            async with _api_sem:
                batch = await client.get_history_candles(
                    symbol, bar, after=after_ms, limit=100)
            await asyncio.sleep(0.2)
            if not batch:
                break
            all_candles.extend(batch)
            oldest_ts = int(batch[-1][0])   # batch is newest-first → last = oldest
            if oldest_ts <= since_ms:
                break
            after_ms = oldest_ts            # next page: older than current oldest
        # keep only candles in range, sort oldest→newest
        all_candles = [c for c in all_candles if int(c[0]) >= since_ms]
        all_candles.sort(key=lambda c: int(c[0]))
        return all_candles

    async def _fetch_symbol(symbol: str) -> tuple:
        async with _api_sem:
            funding = await client.get_funding_rate(symbol)
        await asyncio.sleep(0.2)
        async with _api_sem:
            funding_hist = await client.get_funding_rate_history(symbol, limit=100)
        await asyncio.sleep(0.2)
        async with _api_sem:
            oi_hist = await client.get_oi_history(symbol, period="1H", limit=720)
        await asyncio.sleep(0.2)
        # One full-history fetch per timeframe instead of per-timestamp
        h4  = await _fetch_full_history(symbol, "4H",  cache_start_ms)
        h1  = await _fetch_full_history(symbol, "1H",  cache_start_ms)
        h15 = await _fetch_full_history(symbol, "15m", cache_start_ms)
        h5  = await _fetch_full_history(symbol, "5m",  cache_start_ms)
        print(f"  {symbol}: 4H={len(h4)}, 1H={len(h1)}, 15m={len(h15)}, 5m={len(h5)}, "
              f"funding={len(funding_hist)}, OI={len(oi_hist)})")
        return symbol, funding, funding_hist, oi_hist, h4, h1, h15, h5

    # ── Load or fetch candles ──────────────────────────────────────────────────
    candle_cache: dict = {}
    try:
        _tmp = pickle.load(open(CACHE_FILE, "rb")) if CACHE_FILE.exists() else {}
        cache_valid = (
            CACHE_FILE.exists()
            and (time.time() - CACHE_FILE.stat().st_mtime) < CACHE_MAX_AGE
            and all(s in _tmp for s in ALL_SYMBOLS)
            and "4h" in _tmp.get("BTC-USDT", {})    # new cache structure check
            and "5m" in _tmp.get("BTC-USDT", {})    # 5m trigger requires 5m data
            and _tmp.get("_cache_start_ms", now_ms) <= cache_start_ms  # covers full range
        )
    except Exception:
        cache_valid = False

    if cache_valid:
        print(f"Загрузка свечей из кэша ({CACHE_FILE.name})...")
        with open(CACHE_FILE, "rb") as f:
            candle_cache = pickle.load(f)
        print(f"  Кэш загружен: {len(candle_cache)} пар")
    else:
        print(f"Загрузка свечей для {len(ALL_SYMBOLS)} пар (параллельно)...")
        results_fetch = await asyncio.gather(*[_fetch_symbol(s) for s in ALL_SYMBOLS])
        for symbol, funding, funding_hist, oi_hist, h4, h1, h15, h5 in results_fetch:
            candle_cache[symbol] = {
                "funding":         funding,
                "funding_history": funding_hist,
                "oi_history":      oi_hist,
                "4h":              h4,
                "1h":              h1,
                "15m":             h15,
                "5m":              h5,
            }
        candle_cache["_cache_start_ms"] = cache_start_ms
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(candle_cache, f)
        print(f"  Кэш сохранён → {CACHE_FILE.name}")

    for sym in ALL_SYMBOLS:
        h1  = candle_cache[sym]["1h"]
        h15 = candle_cache[sym]["15m"]
        status = "OFF (per config)" if PAIR_PARAMS.get(sym) is None else "active"
        print(f"  {sym}: 1H={len(h1)} баров, 15m={len(h15)} баров  [{status}]")
    print()

    # ── Run param sets ─────────────────────────────────────────────────────────
    all_set_results = {}
    for pset in PARAM_SETS:
        results = []
        mode         = pset["mode"]
        night_filter = pset.get("night_filter", False)
        time_block_h = pset.get("time_block_h", [])

        # Per-pset timestamp window based on offset_days
        offset_ms       = pset.get("offset_days", 0) * 24 * 3600 * 1000
        pset_end_ms     = now_ms - offset_ms - OUTCOME_H * 3600 * 1000
        pset_start_ms   = pset_end_ms - DAYS_BACK * 24 * 3600 * 1000
        pset_timestamps = list(range(pset_start_ms, pset_end_ms, step_ms))

        def _process_symbol(symbol, ts_list):
            sym_results = []
            if PAIR_PARAMS.get(symbol) is None:
                return sym_results
            funding_hist = candle_cache[symbol].get("funding_history", [])
            oi_hist      = candle_cache[symbol].get("oi_history", [])
            h4_all  = candle_cache[symbol]["4h"]   # sorted oldest→newest
            h1_all  = candle_cache[symbol]["1h"]
            h15_all = candle_cache[symbol]["15m"]
            h5_all  = candle_cache[symbol].get("5m", [])
            # Pre-build timestamp index for fast bisect lookup
            h4_ts  = [int(c[0]) for c in h4_all]
            h1_ts  = [int(c[0]) for c in h1_all]
            h15_ts = [int(c[0]) for c in h15_all]
            h5_ts  = [int(c[0]) for c in h5_all]

            for ts_ms in ts_list:
                # Slice: candles visible at ts_ms (ts < ts_ms), newest-first
                i4  = bisect.bisect_left(h4_ts,  ts_ms)
                i1  = bisect.bisect_left(h1_ts,  ts_ms)
                i15 = bisect.bisect_left(h15_ts, ts_ms)
                i5  = bisect.bisect_left(h5_ts,  ts_ms)
                raw_4h  = list(reversed(h4_all [max(0, i4  - 60):i4 ]))
                raw_1h  = list(reversed(h1_all [max(0, i1  - 60):i1 ]))
                raw_15m = list(reversed(h15_all[max(0, i15 - 96):i15]))
                raw_5m  = list(reversed(h5_all [max(0, i5  - 30):i5 ])) if h5_all else None

                hist_funding = _get_hist_funding(funding_hist, ts_ms)
                oi_delta     = _get_oi_delta(oi_hist, ts_ms)

                sig = compute_signal(
                    raw_4h, raw_1h, raw_15m,
                    funding=hist_funding,
                    symbol=symbol, mode=mode, night_filter=night_filter,
                    oi_delta=oi_delta,
                    time_block_h=time_block_h,
                    raw_5m=raw_5m,
                )
                if sig is None or sig["entry_signal"] != "ENTRY":
                    continue
                if not sig["sl"] or not sig["tp"]:
                    continue
                sym_results.append((ts_ms, sig))
            return sym_results

        # Collect signals
        pending = []
        for symbol in SYMBOLS:
            for ts_ms, sig in _process_symbol(symbol, pset_timestamps):
                pending.append((symbol, ts_ms, sig))
        for symbol in EXTRA_SYMBOLS:
            for ts_ms, sig in _process_symbol(symbol, pset_timestamps[::6]):
                pending.append((symbol, ts_ms, sig))

        # Fetch outcomes
        print(f"  Найдено сигналов: {len(pending)} — загружаю outcomes...")
        for i, (symbol, ts_ms, sig) in enumerate(pending, 1):
            fwd_end = ts_ms + OUTCOME_H * 3600 * 1000 + step_ms
            if i % 20 == 0 or i == len(pending):
                print(f"    {i}/{len(pending)} ({symbol})")
            async with _api_sem:
                raw_fwd = await client.get_history_candles(
                    symbol, "5m", after=fwd_end, limit=288)
            await asyncio.sleep(0.1)

            max_h   = hold_ms.get(sig["trade_style"], hold_ms["SWING"])
            outcome, elapsed, exit_price = check_outcome(
                raw_fwd, sig["side"], sig["sl"], sig["tp"],
                ts_ms, max_h,
            )
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            results.append({
                "symbol":     symbol,
                "ts":         dt.strftime("%m-%d %H:%M"),
                "ts_ms":      ts_ms,
                "hour":       dt.hour,
                "style":      sig["trade_style"],
                "side":       sig["side"],
                "close":      sig["close"],
                "sl":         sig["sl"],
                "tp":         sig["tp"],
                "exit_price": exit_price,
                "sl_dist":    abs(sig["close"] - sig["sl"]) / sig["close"],
                "outcome":    outcome,
                "elapsed_m":  elapsed,
                "day_pos":    sig["day_position"],
                "bb_width":   sig["bb_width"],
            })

        all_set_results[pset["label"]] = results

    await client.close()

    # ── Report ─────────────────────────────────────────────────────────────────
    def _report(label, results):
        wins   = [r for r in results if r["outcome"] == "TP"]
        losses = [r for r in results if r["outcome"] == "STOP"]
        time_x = [r for r in results if r["outcome"] == "TIME_EXIT"]
        total_r = len(wins) + len(losses)
        winrate = len(wins) * 100 // total_r if total_r > 0 else 0

        def _r(r):
            sl_d = abs(r["close"] - r["sl"])
            tp_d = abs(r["tp"] - r["close"]) if r.get("tp") else sl_d
            return tp_d / sl_d if sl_d > 0 else 1.0

        gross_w = sum(_r(r) for r in wins)
        gross_l = len(losses)   # each stop = 1R
        pf = round(gross_w / gross_l, 2) if gross_l > 0 else 99.0

        max_dd = consec = 0
        for r in results:
            if r["outcome"] == "STOP": consec += 1
            else: consec = 0
            max_dd = max(max_dd, consec)

        signals_per_day = round(len(results) / DAYS_BACK, 1)

        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")
        print(f"  Сигналов всего:   {len(results)}  ({signals_per_day}/день)")
        print(f"  ✅ TP:  {len(wins)}  ❌ SL: {len(losses)}  ⏱ TIME: {len(time_x)}")
        print(f"  Winrate:          {winrate}%  ({len(wins)}/{total_r})")
        print(f"  Profit Factor:    {pf}")
        print(f"  Max серия стопов: {max_dd}")

        for side in ("buy", "sell"):
            sr  = [r for r in results if r["side"] == side]
            sw  = [r for r in sr if r["outcome"] == "TP"]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw) * 100 // (len(sw) + len(sl_)) if (len(sw) + len(sl_)) > 0 else 0
                print(f"  {'LONG' if side == 'buy' else 'SHORT'}:  "
                      f"{len(sr)} сигналов | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        print(f"\n  По стилю:")
        for style in ("FAST", "SWING"):
            sr  = [r for r in results if r["style"] == style]
            sw  = [r for r in sr if r["outcome"] == "TP"]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw) * 100 // (len(sw) + len(sl_)) if (len(sw) + len(sl_)) > 0 else 0
                print(f"    {style:8}: {len(sr):3} | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        print(f"\n  По паре:")
        for sym in ALL_SYMBOLS:
            sr  = [r for r in results if r["symbol"] == sym]
            sw  = [r for r in sr if r["outcome"] == "TP"]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw) * 100 // (len(sw) + len(sl_)) if (len(sw) + len(sl_)) > 0 else 0
                print(f"    {sym:12}: {len(sr):3} | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        hour_wins  = {}
        hour_total = {}
        for r in results:
            h = r["hour"]
            hour_total[h] = hour_total.get(h, 0) + 1
            if r["outcome"] == "TP":
                hour_wins[h] = hour_wins.get(h, 0) + 1
        if hour_total:
            hour_wr = {h: hour_wins.get(h, 0) * 100 // hour_total[h]
                       for h in hour_total if hour_total[h] >= 2}
            if hour_wr:
                best  = max(hour_wr, key=hour_wr.get)
                worst = min(hour_wr, key=hour_wr.get)
                print(f"\n  Лучший час:  {best:02d}:00 UTC — {hour_wr[best]}% ({hour_total[best]} сигналов)")
                print(f"  Худший час:  {worst:02d}:00 UTC — {hour_wr[worst]}% ({hour_total[worst]} сигналов)")

        print(f"\n  PASS/FAIL:")
        print(f"    Winrate ≥55%:      {'✅' if winrate >= 55 else '❌'}  {winrate}%")
        print(f"    Profit Factor ≥1.3:{'✅' if pf >= 1.3  else '❌'}  {pf}")
        print(f"    Сигналов/день ≥2:  {'✅' if signals_per_day >= 2 else '❌'}  {signals_per_day}")
        print(f"    Max серия SL ≤5:   {'✅' if max_dd <= 5 else '❌'}  {max_dd}")

        # ── Balance simulation: one position per symbol, 1% risk ──────────────
        # Simulates real bot behavior: new signal skipped if position already open
        START = 1000.0
        balance      = START
        peak         = START
        max_drawdown = 0.0
        executed     = 0
        skipped      = 0
        pos_close_ms = {}   # (symbol, side) → timestamp when position closes

        sorted_r = sorted(results, key=lambda r: r.get("ts_ms", 0))
        for r in sorted_r:
            sym     = r["symbol"]
            side_r  = r["side"]
            ts      = r["ts_ms"]
            elapsed = r.get("elapsed_m")
            max_hold_ms_sym = hold_ms.get(r["style"], hold_ms["SWING"])
            pos_key = (sym, side_r)

            # Block only same pair + same direction
            if pos_close_ms.get(pos_key, 0) > ts:
                r["executed"] = False
                skipped += 1
                continue

            # Register position close time
            if elapsed is not None:
                close_at = ts + elapsed * 60 * 1000
            else:
                close_at = ts + max_hold_ms_sym
            pos_close_ms[pos_key] = close_at

            pnl = calc_pnl(
                r["outcome"], r["side"], r["close"],
                r["sl"], r.get("tp"), r.get("exit_price"),
                balance,
            )
            r["pnl"]      = pnl
            r["executed"] = True
            balance += pnl
            if balance > peak:
                peak = balance
            dd = (peak - balance) / peak * 100
            if dd > max_drawdown:
                max_drawdown = dd
            executed += 1

        total_pct = (balance - START) / START * 100
        sign = "+" if total_pct >= 0 else ""
        print(f"\n  ── Симуляция баланса (1 позиция/пара, риск {int(RISK_PCT*100)}%/сделку) ───")
        print(f"  Старт:            $1000")
        print(f"  Финиш:            ${balance:.0f}  ({sign}{total_pct:.1f}%)")
        print(f"  Макс. просадка:   {max_drawdown:.1f}%")
        print(f"  Исполнено:        {executed}  |  Пропущено: {skipped} (позиция занята)")

        # Per-symbol skipped breakdown
        skip_by_sym = {}
        for r in sorted_r:
            if not r.get("executed", True):
                skip_by_sym[r["symbol"]] = skip_by_sym.get(r["symbol"], 0) + 1
        if skip_by_sym:
            parts = "  ".join(f"{s.split('-')[0]}:{n}" for s, n in skip_by_sym.items())
            print(f"  Пропущено по паре: {parts}")

    for label, res in all_set_results.items():
        _report(label, res)


if __name__ == "__main__":
    asyncio.run(run())
