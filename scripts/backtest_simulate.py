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

# ── Config ─────────────────────────────────────────────────────────────────────
SYMBOLS       = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT"]
EXTRA_SYMBOLS = ["ADA-USDT"]   # checked hourly (every 6th step)
ALL_SYMBOLS   = SYMBOLS + EXTRA_SYMBOLS

DAYS_BACK  = 30
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
        "fast_vol":  1.6, "fast_adx":  18, "fast_sl_k":  1.2,
        "swing_vol": 1.3, "swing_adx": 18, "swing_sl_k": 1.6,
        "late_range": 4.0,
    },
    "ETH-USDT":  {
        "fast_vol":  1.8, "fast_adx":  18, "fast_sl_k":  1.3,
        "swing_vol": 1.5, "swing_adx": 18, "swing_sl_k": 1.6,
        "late_range": 7.0,
    },
    "SOL-USDT":  {
        "fast_vol":  2.2, "fast_adx":  20, "fast_sl_k":  1.6,
        "swing_vol": 1.8, "swing_adx": 20, "swing_sl_k": 1.9,
        "late_range": 10.0,
    },
    "DOGE-USDT": None,   # OFF — too noisy
    "XRP-USDT":  {
        "fast_vol":  1.8, "fast_adx":  18, "fast_sl_k":  1.4,
        "swing_vol": 1.4, "swing_adx": 18, "swing_sl_k": 1.8,
        "late_range": 7.0,
    },
    "ADA-USDT":  {
        "fast_vol":  1.8, "fast_adx":  20, "fast_sl_k":  1.4,
        "swing_vol": 1.4, "swing_adx": 18, "swing_sl_k": 1.8,
        "late_range": 7.0,
    },
}

# ── Param sets ──────────────────────────────────────────────────────────────────
PARAM_SETS = [
    {
        "label":        "FAST_INTRADAY | per-pair | adx_rising",
        "mode":         "FAST",
        "night_filter": True,
    },
    {
        "label":        "INTRADAY_SWING | per-pair | adx_rising",
        "mode":         "SWING",
        "night_filter": True,
    },
    {
        "label":        "COMBINED | FAST priority → SWING | per-pair",
        "mode":         "COMBINED",
        "night_filter": True,
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
                   mode="SWING", night_filter=False):
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

    # ATR on 15m
    atr_15m = float(calc_atr(h15, l15, c15, period=ADX_PERIOD))
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

    # ── Mode detection ────────────────────────────────────────────────────────
    trade_style = "NO_TRADE"

    if mode in ("FAST", "COMBINED"):
        if (float(adx_1h) >= pp["fast_adx"]
                and adx_1h_rising
                and vol_ratio >= pp["fast_vol"]
                and bb_expanding):
            trade_style = "FAST"

    if mode in ("SWING", "COMBINED") and trade_style == "NO_TRADE":
        if (float(adx_1h) >= pp["swing_adx"]
                and adx_1h_rising
                and vol_ratio >= pp["swing_vol"]
                and bb_expanding
                and bias_1h != "NEUTRAL"):
            trade_style = "SWING"

    # Night filter — block all signals 01-07 UTC
    if night_filter and is_night:
        trade_style = "NO_TRADE"

    # Late-move veto
    if late_move:
        trade_style = "NO_TRADE"

    # ── Direction ─────────────────────────────────────────────────────────────
    side = None
    if bias_1h == "UP":    side = "buy"
    elif bias_1h == "DOWN": side = "sell"
    else:
        trade_style = "NO_TRADE"

    # ── 4H late veto: strong opposing trend ───────────────────────────────────
    fourch_veto = (float(adx_4h) > 30
                   and bias_4h != "NEUTRAL"
                   and bias_4h != bias_1h)

    # ── VWAP filter ───────────────────────────────────────────────────────────
    vwap_ok = True
    if vwap and close and side:
        if side == "buy"  and close < vwap: vwap_ok = False
        if side == "sell" and close > vwap: vwap_ok = False

    # ── Side-aware funding filter ─────────────────────────────────────────────
    funding_val   = funding if funding is not None else 0
    FUND_THRESH   = 0.0005   # 0.05%
    funding_block = False
    if side == "buy"  and funding_val >  FUND_THRESH: funding_block = True
    if side == "sell" and funding_val < -FUND_THRESH: funding_block = True

    # ── SL / TP ───────────────────────────────────────────────────────────────
    sl_p = tp1_p = tp2_p = None
    sl_dist = 0.0

    if trade_style == "FAST" and side and close:
        sl_dist = max(pp["fast_sl_k"] * atr_15m, close * 0.004)
        if side == "buy":
            sl_p  = round(close - sl_dist, 6)
            tp1_p = round(close + sl_dist * 0.8, 6)   # TP1 = 0.8R
            tp2_p = round(close + sl_dist * 1.5, 6)   # TP2 = 1.5R
        else:
            sl_p  = round(close + sl_dist, 6)
            tp1_p = round(close - sl_dist * 0.8, 6)
            tp2_p = round(close - sl_dist * 1.5, 6)

    elif trade_style == "SWING" and side and close:
        # Structural SL: swing level + ATR buffer, at least k*ATR
        swings = find_swing_levels(h15, l15, lookback=3, count=4)
        if side == "buy":
            atr_sl = close - pp["swing_sl_k"] * atr_15m
            struct_sl = (swings["recent_lows"][-1] - 0.3 * atr_15m
                         if swings["recent_lows"] else None)
            sl_p    = round(min(struct_sl, atr_sl) if struct_sl else atr_sl, 6)
            sl_dist = close - sl_p
            tp1_p   = round(close + sl_dist * 1.0, 6)   # TP1 = 1R
            tp2_p   = round(close + sl_dist * 2.5, 6)   # TP2 = 2.5R
        else:
            atr_sl = close + pp["swing_sl_k"] * atr_15m
            struct_sl = (swings["recent_highs"][-1] + 0.3 * atr_15m
                         if swings["recent_highs"] else None)
            sl_p    = round(max(struct_sl, atr_sl) if struct_sl else atr_sl, 6)
            sl_dist = sl_p - close
            tp1_p   = round(close - sl_dist * 1.0, 6)
            tp2_p   = round(close - sl_dist * 2.5, 6)

    # ── Final signal ──────────────────────────────────────────────────────────
    blocked = (trade_style == "NO_TRADE" or not vwap_ok
               or funding_block or fourch_veto
               or not sl_p or not tp1_p)

    entry_signal = "NO_TRADE" if blocked else "ENTRY"

    return {
        "entry_signal":    entry_signal,
        "trade_style":     trade_style,
        "side":            side,
        "close":           close,
        "sl":              sl_p,
        "tp1":             tp1_p,
        "tp2":             tp2_p,
        "adx_1h":          round(float(adx_1h), 1),
        "adx_1h_rising":   adx_1h_rising,
        "adx_4h":          round(float(adx_4h), 1),
        "bias_1h":         bias_1h,
        "vol_ratio":       round(vol_ratio, 2),
        "bb_width":        round(bb_width, 2),
        "funding":         funding,
        "day_position":    round(day_position, 3) if day_position else None,
        "fourch_veto":     fourch_veto,
        "late_move":       late_move,
    }


def check_outcome(raw_forward, side, sl, tp1, tp2, signal_ts_ms, max_hold_ms):
    if not raw_forward:
        return "NO_DATA", None
    for c in reversed(raw_forward):
        ts_c = int(c[0])
        if ts_c <= signal_ts_ms:
            continue
        elapsed_ms = ts_c - signal_ts_ms
        if elapsed_ms > max_hold_ms:
            return "TIME_EXIT", elapsed_ms // 60000
        high = float(c[2])
        low  = float(c[3])
        mins = elapsed_ms // 60000
        if side == "buy":
            if low  <= sl:              return "STOP", mins
            if tp2 and high >= tp2:     return "TP2",  mins
            if high >= tp1:             return "TP1",  mins
        else:
            if high >= sl:              return "STOP", mins
            if tp2 and low  <= tp2:     return "TP2",  mins
            if low  <= tp1:             return "TP1",  mins
    return "OPEN", None


# ── Main ────────────────────────────────────────────────────────────────────────

async def run():
    client = OKXClient(
        api_key=os.getenv("OKX_API_KEY", ""),
        secret_key=os.getenv("OKX_SECRET_KEY", ""),
        passphrase=os.getenv("OKX_PASSPHRASE", ""),
        is_demo=False,
    )

    now_ms   = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    start_ms = now_ms - DAYS_BACK * 24 * 3600 * 1000
    step_ms  = INTERVAL_M * 60 * 1000
    hold_ms  = {"FAST": 120 * 60 * 1000, "SWING": 240 * 60 * 1000}
    timestamps = list(range(start_ms, now_ms - OUTCOME_H * 3600 * 1000, step_ms))

    _api_sem = asyncio.Semaphore(1)

    async def _fetch_symbol(symbol: str) -> tuple:
        async with _api_sem:
            funding = await client.get_funding_rate(symbol)
        await asyncio.sleep(1.0)
        raw_cache = {}
        for ts_ms in timestamps[::4]:
            after_ms = ts_ms + step_ms
            async with _api_sem:
                h4  = await client.get_history_candles(symbol, "4H",  after=after_ms, limit=60)
            await asyncio.sleep(0.5)
            async with _api_sem:
                h1  = await client.get_history_candles(symbol, "1H",  after=after_ms, limit=60)
            await asyncio.sleep(0.5)
            async with _api_sem:
                h15 = await client.get_history_candles(symbol, "15m", after=after_ms, limit=96)
            await asyncio.sleep(0.5)
            raw_cache[ts_ms] = {"4h": h4, "1h": h1, "15m": h15}
        print(f"  {symbol} загружен")
        return symbol, funding, raw_cache

    # ── Load or fetch candles ──────────────────────────────────────────────────
    candle_cache: dict = {}
    try:
        cache_valid = (
            CACHE_FILE.exists()
            and (time.time() - CACHE_FILE.stat().st_mtime) < CACHE_MAX_AGE
            and all(s in pickle.load(open(CACHE_FILE, "rb")) for s in ALL_SYMBOLS)
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
        for symbol, funding, raw_cache in results_fetch:
            candle_cache[symbol] = {"funding": funding, "raw": raw_cache}
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(candle_cache, f)
        print(f"  Кэш сохранён → {CACHE_FILE.name}")

    for sym in ALL_SYMBOLS:
        raw   = candle_cache[sym]["raw"]
        valid = sum(1 for v in raw.values() if v["1h"] and len(v["1h"]) >= 20)
        status = "OFF (per config)" if PAIR_PARAMS.get(sym) is None else "active"
        print(f"  {sym}: {len(raw)} точек, {valid} с 1H≥20  [{status}]")
    print()

    # ── Run param sets ─────────────────────────────────────────────────────────
    all_set_results = {}
    for pset in PARAM_SETS:
        results = []
        mode         = pset["mode"]
        night_filter = pset.get("night_filter", False)

        def _process_symbol(symbol, ts_list):
            sym_results = []
            if PAIR_PARAMS.get(symbol) is None:
                return sym_results
            funding = candle_cache[symbol]["funding"]
            for ts_ms in ts_list:
                cached_ts = min(candle_cache[symbol]["raw"].keys(),
                                key=lambda t: abs(t - ts_ms))
                raw = candle_cache[symbol]["raw"][cached_ts]

                sig = compute_signal(
                    raw["4h"], raw["1h"], raw["15m"], funding,
                    symbol=symbol, mode=mode, night_filter=night_filter,
                )
                if sig is None or sig["entry_signal"] != "ENTRY":
                    continue
                if not sig["sl"] or not sig["tp1"]:
                    continue
                sym_results.append((ts_ms, sig))
            return sym_results

        # Collect signals
        pending = []
        for symbol in SYMBOLS:
            for ts_ms, sig in _process_symbol(symbol, timestamps):
                pending.append((symbol, ts_ms, sig))
        for symbol in EXTRA_SYMBOLS:
            for ts_ms, sig in _process_symbol(symbol, timestamps[::6]):
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
            outcome, elapsed = check_outcome(
                raw_fwd, sig["side"], sig["sl"], sig["tp1"], sig["tp2"],
                ts_ms, max_h,
            )
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            results.append({
                "symbol":    symbol,
                "ts":        dt.strftime("%m-%d %H:%M"),
                "hour":      dt.hour,
                "style":     sig["trade_style"],
                "side":      sig["side"],
                "close":     sig["close"],
                "sl":        sig["sl"],
                "tp1":       sig["tp1"],
                "sl_dist":   abs(sig["close"] - sig["sl"]),
                "outcome":   outcome,
                "elapsed_m": elapsed,
                "day_pos":   sig["day_position"],
                "bb_width":  sig["bb_width"],
            })

        all_set_results[pset["label"]] = results

    await client.close()

    # ── Report ─────────────────────────────────────────────────────────────────
    def _report(label, results):
        wins   = [r for r in results if r["outcome"] in ("TP1", "TP2")]
        losses = [r for r in results if r["outcome"] == "STOP"]
        time_x = [r for r in results if r["outcome"] == "TIME_EXIT"]
        total_r = len(wins) + len(losses)
        winrate = len(wins) * 100 // total_r if total_r > 0 else 0

        gross_w = sum(r["sl_dist"] for r in wins)
        gross_l = sum(r["sl_dist"] for r in losses)
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
            sw  = [r for r in sr if r["outcome"] in ("TP1", "TP2")]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw) * 100 // (len(sw) + len(sl_)) if (len(sw) + len(sl_)) > 0 else 0
                print(f"  {'LONG' if side == 'buy' else 'SHORT'}:  "
                      f"{len(sr)} сигналов | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        print(f"\n  По стилю:")
        for style in ("FAST", "SWING"):
            sr  = [r for r in results if r["style"] == style]
            sw  = [r for r in sr if r["outcome"] in ("TP1", "TP2")]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw) * 100 // (len(sw) + len(sl_)) if (len(sw) + len(sl_)) > 0 else 0
                print(f"    {style:8}: {len(sr):3} | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        print(f"\n  По паре:")
        for sym in ALL_SYMBOLS:
            sr  = [r for r in results if r["symbol"] == sym]
            sw  = [r for r in sr if r["outcome"] in ("TP1", "TP2")]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw) * 100 // (len(sw) + len(sl_)) if (len(sw) + len(sl_)) > 0 else 0
                print(f"    {sym:12}: {len(sr):3} | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        hour_wins  = {}
        hour_total = {}
        for r in results:
            h = r["hour"]
            hour_total[h] = hour_total.get(h, 0) + 1
            if r["outcome"] in ("TP1", "TP2"):
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

    for label, res in all_set_results.items():
        _report(label, res)


if __name__ == "__main__":
    asyncio.run(run())
