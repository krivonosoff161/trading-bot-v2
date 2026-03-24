"""
Full backtest simulation: runs signal logic on historical candles every 30 min.
Does NOT use LLM or chart drawing — pure signal logic only.

Usage:
    python scripts/backtest_simulate.py
"""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from src.exchange.okx_client import OKXClient
from src.strategy.indicators import (
    calc_atr, calc_adx, calc_ema, parse_candles, parse_volumes,
    find_swing_levels, calc_bollinger_bands, calc_supertrend,
)

# ── Config ───────────────────────────────────────────────────────────────────
SYMBOLS     = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "XRP-USDT"]
DAYS_BACK   = 30       # how many days of history to simulate
INTERVAL_M  = 15       # simulate every N minutes
OUTCOME_H   = 24       # hours of forward candles to check outcome
ADX_PERIOD  = 14

# ── Param sets to compare ────────────────────────────────────────────────────
PARAM_SETS = [
    {"label": "OLD  tp1×1.5 adx30",          "tp1_mult": 1.5, "adx_thresh": 30, "regime_top": 0.85, "use_dynamic_tp": False, "night_filter": False},
    {"label": "NEW  tp1×1.0 adx25",          "tp1_mult": 1.0, "adx_thresh": 25, "regime_top": 0.80, "use_dynamic_tp": False, "night_filter": False},
    {"label": "NEW+ dynamic_tp+night_filter", "tp1_mult": 1.0, "adx_thresh": 25, "regime_top": 0.80, "use_dynamic_tp": True,  "night_filter": True},
]

# ── Signal logic (mirrors analyze_chart.py llm_context block) ────────────────

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


def compute_signal(raw_4h, raw_1h, raw_15m, funding, tp1_mult=1.0, adx_thresh=25, regime_top=0.80,
                   use_dynamic_tp=False, night_filter=False):
    """Run signal logic. Returns dict with signal fields or None if not enough data."""
    if not raw_4h or len(raw_4h) < 20:
        return None
    if not raw_1h or len(raw_1h) < 20:
        return None
    if not raw_15m or len(raw_15m) < 30:
        return None

    # Parse candles (chronological)
    h4h, l4h, c4h = parse_candles(raw_4h)
    h1h, l1h, c1h = parse_candles(raw_1h)
    h15, l15, c15 = parse_candles(raw_15m)

    # 4H indicators
    adx_4h, pdi_4h, mdi_4h = calc_adx(h4h, l4h, c4h, period=ADX_PERIOD)
    ema20_4h = calc_ema(c4h, 20)
    ema50_4h = calc_ema(c4h, 50)
    bull_4h  = bool(ema20_4h[-1] > ema50_4h[-1])
    bear_4h  = bool(ema20_4h[-1] < ema50_4h[-1])
    bias_4h  = _bias(bull_4h, bear_4h)

    # 1H indicators
    adx_1h, pdi_1h, mdi_1h = calc_adx(h1h, l1h, c1h, period=ADX_PERIOD)
    ema20_1h = calc_ema(c1h, 20)
    ema50_1h = calc_ema(c1h, 50)
    bull_1h  = bool(ema20_1h[-1] > ema50_1h[-1])
    bear_1h  = bool(ema20_1h[-1] < ema50_1h[-1])
    bias_1h  = _bias(bull_1h, bear_1h)

    # 15m indicators
    atr_15m = float(calc_atr(h15, l15, c15, period=ADX_PERIOD))
    atr_1h  = float(calc_atr(h1h, l1h, c1h, period=ADX_PERIOD))
    close   = float(c15[-1])

    # Volume ratio
    vols_15m = [float(c[5]) for c in list(reversed(raw_15m))]
    recent_vol = np.mean(vols_15m[:5]) if len(vols_15m) >= 5 else 0
    prior_vol  = np.mean(vols_15m[5:20]) if len(vols_15m) >= 20 else recent_vol
    vol_ratio  = recent_vol / prior_vol if prior_vol > 0 else 1.0

    # RSI 15m (simple)
    changes = np.diff(c15[-15:])
    gains   = np.where(changes > 0, changes, 0)
    losses  = np.where(changes < 0, -changes, 0)
    avg_g   = np.mean(gains) if len(gains) > 0 else 0
    avg_l   = np.mean(losses) if len(losses) > 0 else 1
    rsi_15m = 100 - (100 / (1 + avg_g / avg_l)) if avg_l > 0 else 50

    # Supertrend dir (simplified: compare close to midpoint of last 14 candles)
    atr_st   = float(calc_atr(h15, l15, c15, period=10))
    mid_st   = (h15[-1] + l15[-1]) / 2
    st_upper = mid_st + 3 * atr_st
    st_lower = mid_st - 3 * atr_st
    supertrend_dir = "up" if close > st_lower else "down"

    # DI
    plus_di_1h  = float(pdi_1h) if pdi_1h else 0
    minus_di_1h = float(mdi_1h) if mdi_1h else 0

    # VWAP & day range from 15m
    ts_last = int(raw_15m[0][0])  # newest candle ts (raw is newest-first)
    dt_last = datetime.fromtimestamp(ts_last / 1000, tz=timezone.utc)
    day_start_ms = int(datetime(dt_last.year, dt_last.month, dt_last.day,
                                tzinfo=timezone.utc).timestamp() * 1000)
    vwap, day_high, day_low = _calc_vwap_and_day(raw_15m, day_start_ms)

    day_position = None
    if day_high and day_low and day_high != day_low:
        day_position = (close - day_low) / (day_high - day_low)

    # Signal hour for night filter
    signal_hour = dt_last.hour
    is_night    = 1 <= signal_hour < 7

    # Dynamic TP multiplier
    if use_dynamic_tp and day_high and day_low and day_low > 0:
        daily_range_pct = (day_high - day_low) / day_low * 100
        if daily_range_pct >= 4.0:
            tp1_mult = 1.0
        elif daily_range_pct >= 2.0:
            tp1_mult = 0.8
        else:
            tp1_mult = 0.6

    # CE (simplified)
    ce_long  = float(l15[-1]) - 3 * atr_15m
    ce_short = float(h15[-1]) + 3 * atr_15m

    # ── Level 1: Trade style ──────────────────────────────────────────────────
    trade_style = "NO_TRADE"
    if adx_4h >= adx_thresh and bias_4h == bias_1h and bias_1h != "NEUTRAL":
        trade_style = "SWING"
    elif adx_4h >= adx_thresh and bias_4h != "NEUTRAL" and bias_1h == "NEUTRAL":
        pb_long  = (bias_4h == "UP"   and supertrend_dir == "up"
                    and plus_di_1h > minus_di_1h
                    and day_position is not None and day_position < 0.45
                    and rsi_15m < 70)
        pb_short = (bias_4h == "DOWN" and supertrend_dir == "down"
                    and minus_di_1h > plus_di_1h
                    and day_position is not None and day_position > 0.55
                    and rsi_15m > 30)
        trade_style = "PULLBACK" if (pb_long or pb_short) else "NO_TRADE"
    elif adx_1h >= 20 and vol_ratio >= 1.5:
        trade_style = "SCALP"

    # Night session filters (01-07 UTC)
    if night_filter and is_night:
        if trade_style == "SCALP":
            trade_style = "NO_TRADE"
        elif trade_style == "SWING" and not (adx_4h >= 30 and vol_ratio >= 3.0):
            trade_style = "NO_TRADE"

    # ── Level 2: Direction ───────────────────────────────────────────────────
    side = None
    if bias_1h == "UP":   side = "buy"
    elif bias_1h == "DOWN": side = "sell"
    elif bias_4h == "UP": side = "buy"
    elif bias_4h == "DOWN": side = "sell"

    if trade_style == "PULLBACK":
        side = "buy" if bias_4h == "UP" else "sell"

    # ── VWAP filter ──────────────────────────────────────────────────────────
    vwap_ok = True
    if vwap and close:
        if trade_style == "PULLBACK":
            if side == "buy"  and close > vwap * 1.02: vwap_ok = False
            if side == "sell" and close < vwap * 0.98: vwap_ok = False
        else:
            if side == "buy"  and close < vwap: vwap_ok = False
            if side == "sell" and close > vwap: vwap_ok = False

    # ── Funding filter ───────────────────────────────────────────────────────
    funding_abs   = abs(funding) if funding is not None else 0
    thresholds    = {"SWING": 0.003, "PULLBACK": 0.008, "SCALP": 0.005}
    warn_thresh   = {"SWING": 0.001, "PULLBACK": 0.003, "SCALP": 0.001}
    funding_block = funding_abs > thresholds.get(trade_style, 0.01)
    funding_warn  = funding_abs > warn_thresh.get(trade_style, 0.005)

    # ── Regime filter ────────────────────────────────────────────────────────
    regime_ok = True
    if day_position is not None and trade_style in ("SWING", "SCALP"):
        if side == "buy"  and day_position > regime_top:        regime_ok = False
        if side == "sell" and day_position < (1 - regime_top):  regime_ok = False

    # ── Level 4: SL/TP ───────────────────────────────────────────────────────
    sl_p = tp1_p = tp2_p = None
    sl_dist = 0

    if trade_style == "SWING" and side and close:
        sl_dist = max(atr_1h * 2.0, close * 0.008)
        tp1_dist = sl_dist * tp1_mult
        if side == "buy":
            sl_p  = round(close - sl_dist, 6)
            tp1_p = round(close + tp1_dist, 6)
            tp2_p = day_high if (day_high and day_high > tp1_p) else round(close + sl_dist * 2.5, 6)
        else:
            sl_p  = round(close + sl_dist, 6)
            tp1_p = round(close - tp1_dist, 6)
            tp2_p = day_low if (day_low and day_low < tp1_p) else round(close - sl_dist * 2.5, 6)

    elif trade_style == "PULLBACK" and side and close:
        if side == "buy":
            sl_p_raw = min(ce_long, day_low * 0.995 if day_low else ce_long)
            sl_p     = round(min(sl_p_raw, close * 0.994), 6)
            sl_dist  = max(close - sl_p, atr_15m * 0.5)
            tp1_p    = round(close + sl_dist * 2.0, 6)
            tp2_p    = day_high if (day_high and day_high > tp1_p) else round(close + sl_dist * 3.0, 6)
        else:
            sl_p_raw = max(ce_short, day_high * 1.005 if day_high else ce_short)
            sl_p     = round(max(sl_p_raw, close * 1.006), 6)
            sl_dist  = max(sl_p - close, atr_15m * 0.5)
            tp1_p    = round(close - sl_dist * 2.0, 6)
            tp2_p    = day_low if (day_low and day_low < tp1_p) else round(close - sl_dist * 3.0, 6)

    elif trade_style == "SCALP" and side and close:
        sl_dist  = max(atr_15m * 1.5, close * 0.005)
        tp1_dist = sl_dist * tp1_mult
        if side == "buy":
            sl_p  = round(close - sl_dist, 6)
            tp1_p = round(close + tp1_dist, 6)
            tp2_p = day_high if (day_high and day_high > tp1_p) else round(close + sl_dist * 2.5, 6)
        else:
            sl_p  = round(close + sl_dist, 6)
            tp1_p = round(close - tp1_dist, 6)
            tp2_p = day_low if (day_low and day_low < tp1_p) else round(close - sl_dist * 2.5, 6)

    # R/R check
    rr_ok = True
    if sl_p and tp2_p and close:
        if abs(close - tp2_p) < abs(close - sl_p):
            rr_ok = False

    # ── Final signal ─────────────────────────────────────────────────────────
    vol_too_low = vol_ratio < 0.7 and trade_style != "PULLBACK"
    if trade_style == "NO_TRADE" or not vwap_ok or funding_block or not rr_ok or vol_too_low or not regime_ok:
        entry_signal = "NO_TRADE"
    elif funding_warn or (vol_ratio < 1.3 and trade_style == "SCALP"):
        entry_signal = "WAIT"
    else:
        entry_signal = "ENTRY"

    return {
        "entry_signal": entry_signal,
        "trade_style":  trade_style,
        "side":         side,
        "close":        close,
        "sl":           sl_p,
        "tp1":          tp1_p,
        "tp2":          tp2_p,
        "adx_4h":       round(float(adx_4h), 1),
        "adx_1h":       round(float(adx_1h), 1),
        "bias_4h":      bias_4h,
        "bias_1h":      bias_1h,
        "vol_ratio":    round(vol_ratio, 2),
        "funding":      funding,
        "day_position": round(day_position, 3) if day_position else None,
    }


def check_outcome(raw_forward: list, side: str, sl: float, tp1: float, tp2: float,
                  signal_ts_ms: int, max_hold_ms: int):
    """Check if SL/TP hit in forward candles. Returns (result, minutes_to_hit)."""
    if not raw_forward:
        return "NO_DATA", None

    for c in reversed(raw_forward):  # chronological order
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
            if low  <= sl:  return "STOP", mins
            if tp2 and high >= tp2:  return "TP2",  mins
            if high >= tp1: return "TP1",  mins
        else:
            if high >= sl:  return "STOP", mins
            if tp2 and low  <= tp2:  return "TP2",  mins
            if low  <= tp1: return "TP1",  mins

    return "OPEN", None


# ── Main ──────────────────────────────────────────────────────────────────────

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
    hold_ms  = {"SCALP": 120*60*1000, "PULLBACK": 480*60*1000, "SWING": 360*60*1000}
    timestamps = list(range(start_ms, now_ms - OUTCOME_H * 3600 * 1000, step_ms))

    # Pre-fetch all candles — all symbols in parallel (semaphore limits OKX rate)
    _api_sem = asyncio.Semaphore(3)  # max 3 concurrent OKX requests — stay under rate limit

    async def _fetch_symbol(symbol: str) -> tuple:
        async with _api_sem:
            funding = await client.get_funding_rate(symbol)
        await asyncio.sleep(0.3)
        raw_cache = {}
        for ts_ms in timestamps[::4]:  # sample every 4th point
            after_ms = ts_ms + step_ms
            async with _api_sem:
                h4  = await client.get_history_candles(symbol, "4H",  after=after_ms, limit=60)
                await asyncio.sleep(0.2)
            async with _api_sem:
                h1  = await client.get_history_candles(symbol, "1H",  after=after_ms, limit=60)
                await asyncio.sleep(0.2)
            async with _api_sem:
                h15 = await client.get_history_candles(symbol, "15m", after=after_ms, limit=96)
                await asyncio.sleep(0.2)
        print(f"  {symbol} загружен")
        return symbol, funding, raw_cache

    candle_cache: dict = {}
    print(f"Загрузка свечей для {len(SYMBOLS)} пар (параллельно)...")
    results_fetch = await asyncio.gather(*[_fetch_symbol(s) for s in SYMBOLS])
    for symbol, funding, raw_cache in results_fetch:
        candle_cache[symbol] = {"funding": funding, "raw": raw_cache}

    # Run two param sets
    all_set_results = {}
    for pset in PARAM_SETS:
        results = []
        for symbol in SYMBOLS:
            funding = candle_cache[symbol]["funding"]
            for ts_ms in timestamps:
                # Find nearest cached candles
                cached_ts = min(candle_cache[symbol]["raw"].keys(), key=lambda t: abs(t - ts_ms))
                raw = candle_cache[symbol]["raw"][cached_ts]

                sig = compute_signal(
                    raw["4h"], raw["1h"], raw["15m"], funding,
                    tp1_mult=pset["tp1_mult"],
                    adx_thresh=pset["adx_thresh"],
                    regime_top=pset["regime_top"],
                    use_dynamic_tp=pset.get("use_dynamic_tp", False),
                    night_filter=pset.get("night_filter", False),
                )
                if sig is None or sig["entry_signal"] not in ("ENTRY", "WAIT"):
                    continue
                if not sig["sl"] or not sig["tp1"]:
                    continue

                # Fetch forward candles
                fwd_end = ts_ms + OUTCOME_H * 3600 * 1000 + step_ms
                raw_fwd = await client.get_history_candles(symbol, "5m", after=fwd_end, limit=288)
                await asyncio.sleep(0.1)

                max_h = hold_ms.get(sig["trade_style"], hold_ms["SWING"])
                outcome, elapsed = check_outcome(
                    raw_fwd, sig["side"], sig["sl"], sig["tp1"], sig["tp2"], ts_ms, max_h
                )
                dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                results.append({
                    "symbol":    symbol,
                    "ts":        dt.strftime("%m-%d %H:%M"),
                    "hour":      dt.hour,
                    "signal":    sig["entry_signal"],
                    "style":     sig["trade_style"],
                    "side":      sig["side"],
                    "close":     sig["close"],
                    "sl":        sig["sl"],
                    "tp1":       sig["tp1"],
                    "sl_dist":   abs(sig["close"] - sig["sl"]),
                    "outcome":   outcome,
                    "elapsed_m": elapsed,
                    "day_pos":   sig["day_position"],
                })
        all_set_results[pset["label"]] = results

    await client.close()

    # ── Report ────────────────────────────────────────────────────────────────
    def _report(label, results):
        wins   = [r for r in results if r["outcome"] in ("TP1", "TP2")]
        losses = [r for r in results if r["outcome"] == "STOP"]
        time_x = [r for r in results if r["outcome"] == "TIME_EXIT"]
        total_r = len(wins) + len(losses)
        winrate = len(wins) * 100 // total_r if total_r > 0 else 0

        # Profit factor (assuming R:R 1:tp1_mult, but simplified: 1:1 for new, 1:1.5 for old)
        gross_w = sum(r["sl_dist"] * (1.0 if "1.0" in label else 1.5) for r in wins)
        gross_l = sum(r["sl_dist"] for r in losses)
        pf = round(gross_w / gross_l, 2) if gross_l > 0 else 99.0

        # Max drawdown (sequential losses)
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

        # LONG vs SHORT
        for side in ("buy", "sell"):
            sr = [r for r in results if r["side"] == side]
            sw = [r for r in sr if r["outcome"] in ("TP1","TP2")]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw)*100//(len(sw)+len(sl_)) if (len(sw)+len(sl_)) > 0 else 0
                label_s = "LONG" if side == "buy" else "SHORT"
                print(f"  {label_s}:  {len(sr)} сигналов | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        # By style
        print(f"\n  По стилю:")
        for style in ("SWING", "SCALP", "PULLBACK"):
            sr = [r for r in results if r["style"] == style]
            sw = [r for r in sr if r["outcome"] in ("TP1","TP2")]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw)*100//(len(sw)+len(sl_)) if (len(sw)+len(sl_)) > 0 else 0
                print(f"    {style:8}: {len(sr):3} | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        # By symbol
        print(f"\n  По паре:")
        for sym in SYMBOLS:
            sr = [r for r in results if r["symbol"] == sym]
            sw = [r for r in sr if r["outcome"] in ("TP1","TP2")]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw)*100//(len(sw)+len(sl_)) if (len(sw)+len(sl_)) > 0 else 0
                print(f"    {sym:12}: {len(sr):3} | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        # Best/worst hours
        hour_wins = {}
        hour_total = {}
        for r in results:
            h = r["hour"]
            hour_total[h] = hour_total.get(h, 0) + 1
            if r["outcome"] in ("TP1","TP2"):
                hour_wins[h] = hour_wins.get(h, 0) + 1
        if hour_total:
            hour_wr = {h: hour_wins.get(h,0)*100//hour_total[h] for h in hour_total if hour_total[h] >= 2}
            if hour_wr:
                best  = max(hour_wr, key=hour_wr.get)
                worst = min(hour_wr, key=hour_wr.get)
                print(f"\n  Лучший час:  {best:02d}:00 UTC — {hour_wr[best]}% winrate ({hour_total[best]} сигналов)")
                print(f"  Худший час:  {worst:02d}:00 UTC — {hour_wr[worst]}% winrate ({hour_total[worst]} сигналов)")

        # Pass/Fail
        print(f"\n  PASS/FAIL (S1.3):")
        print(f"    Winrate ≥50%:      {'✅ PASS' if winrate >= 50 else '❌ FAIL'}  ({winrate}%)")
        print(f"    Profit Factor ≥1.2:{'✅ PASS' if pf >= 1.2  else '❌ FAIL'}  ({pf})")
        print(f"    Сигналов/день ≥2:  {'✅ PASS' if signals_per_day >= 2 else '❌ FAIL'}  ({signals_per_day})")
        print(f"    Max серия SL ≤5:   {'✅ PASS' if max_dd <= 5 else '❌ FAIL'}  ({max_dd})")

    for label, res in all_set_results.items():
        _report(label, res)


if __name__ == "__main__":
    asyncio.run(run())
