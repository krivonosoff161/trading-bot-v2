"""
bt_bb_breakout.py — BB Band Touch + Volume Direction signal.

Logic:
  LONG:  candle low touches lower BB AND candle is green (close > open) AND vol >= threshold
  SHORT: candle high touches upper BB AND candle is red  (close < open) AND vol >= threshold

This is different from BB FADE (which uses LOW volume, enters at close).
Here: HIGH volume at band touch + candle direction = signal that price WILL continue/reverse
with momentum.

TP variants: bb_mid, bb_opposite, ATR*1.5, ATR*2.0
Volume thresholds: 1.0x, 1.5x, 2.0x, 2.5x
Hold: 60m, 90m, 120m

Usage:
    python scripts/bt_bb_breakout.py
"""
import bisect
import pickle
import sys
import os
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

CACHE_FILE = Path(__file__).parent / "backtest_candle_cache_65d.pkl"
SYMBOLS    = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT"]

BB_PERIOD  = 20
BB_STD     = 2.0
ATR_PERIOD = 14
RSI_PERIOD = 14
MAX_HOLD_M = 90   # default hold minutes

Trade = namedtuple("Trade", ["symbol","side","ts","entry","sl","tp","outcome","elapsed_m","exit_r"])


def _parse(raw):
    candles = sorted(raw, key=lambda c: int(c[0]))
    ts  = np.array([int(c[0])   for c in candles])
    o   = np.array([float(c[1]) for c in candles])
    h   = np.array([float(c[2]) for c in candles])
    l   = np.array([float(c[3]) for c in candles])
    c   = np.array([float(c[4]) for c in candles])
    v   = np.array([float(c[5]) for c in candles])
    return ts, o, h, l, c, v


def _atr(h, l, c, period=ATR_PERIOD):
    n = len(c)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    result = np.zeros(n)
    for i in range(period, n):
        result[i] = np.mean(tr[i-period+1:i+1])
    return result


def _rsi(c, period=RSI_PERIOD):
    n = len(c)
    rsi = np.full(n, 50.0)
    gains = np.zeros(n)
    losses = np.zeros(n)
    for i in range(1, n):
        delta = c[i] - c[i-1]
        gains[i]  = max(delta, 0)
        losses[i] = max(-delta, 0)
    avg_gain = np.mean(gains[1:period+1])
    avg_loss = np.mean(losses[1:period+1])
    for i in range(period, n):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100 - 100 / (1 + rs)
    return rsi


def simulate(sym, raw5m, vol_mult, tp_mode, hold_m, rsi_filter=False):
    """
    vol_mult:   minimum volume multiplier vs 20-bar avg
    tp_mode:    'mid' | 'opposite' | 'atr1.5' | 'atr2.0'
    hold_m:     max hold in minutes
    rsi_filter: if True, LONG requires RSI < 35, SHORT requires RSI > 65
    """
    ts, o, h, l, c, v = _parse(raw5m)
    n = len(c)

    bb_mid  = np.zeros(n)
    bb_up   = np.zeros(n)
    bb_lo   = np.zeros(n)
    vol_ma  = np.zeros(n)
    atr     = _atr(h, l, c)
    rsi_arr = _rsi(c)

    for i in range(BB_PERIOD - 1, n):
        sl = c[i - BB_PERIOD + 1:i + 1]
        m  = np.mean(sl)
        std = np.std(sl)
        bb_mid[i] = m
        bb_up[i]  = m + BB_STD * std
        bb_lo[i]  = m - BB_STD * std

    for i in range(BB_PERIOD - 1, n):
        vol_ma[i] = np.mean(v[i - BB_PERIOD + 1:i + 1])

    warmup = max(BB_PERIOD, RSI_PERIOD) + ATR_PERIOD + 2
    hold_ms = hold_m * 60 * 1000

    trades = []
    last_trade_end_ts = 0

    for i in range(warmup, n - 1):
        if ts[i] <= last_trade_end_ts:
            continue
        if bb_lo[i] <= 0 or vol_ma[i] <= 0 or atr[i] <= 0:
            continue

        vol_ratio = v[i] / vol_ma[i]
        if vol_ratio < vol_mult:
            continue

        cur_rsi = rsi_arr[i]

        # ── LONG: low touches lower BB, candle green ───────────────────────
        if l[i] <= bb_lo[i] and c[i] > o[i]:
            if rsi_filter and cur_rsi >= 35:
                continue
            side  = "buy"
            entry = c[i]
            sl    = bb_lo[i] - atr[i]
            if tp_mode == "mid":      tp = bb_mid[i]
            elif tp_mode == "opposite": tp = bb_up[i]
            elif tp_mode == "atr1.5": tp = entry + 1.5 * atr[i]
            elif tp_mode == "atr2.0": tp = entry + 2.0 * atr[i]
            else:                     tp = bb_mid[i]

        # ── SHORT: high touches upper BB, candle red ───────────────────────
        elif h[i] >= bb_up[i] and c[i] < o[i]:
            if rsi_filter and cur_rsi <= 65:
                continue
            side  = "sell"
            entry = c[i]
            sl    = bb_up[i] + atr[i]
            if tp_mode == "mid":      tp = bb_mid[i]
            elif tp_mode == "opposite": tp = bb_lo[i]
            elif tp_mode == "atr1.5": tp = entry - 1.5 * atr[i]
            elif tp_mode == "atr2.0": tp = entry - 2.0 * atr[i]
            else:                     tp = bb_mid[i]
        else:
            continue

        # SL/TP sanity
        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        if sl_dist <= 0 or tp_dist <= 0:
            continue
        if tp_dist < sl_dist * 0.3:   # skip if R:R < 0.3
            continue

        # ── Simulate exit ─────────────────────────────────────────────────────
        outcome   = "OPEN"
        elapsed_m = None
        exit_r    = 0.0

        for j in range(i + 1, n):
            elapsed_ms = int(ts[j]) - int(ts[i])
            elapsed_min = elapsed_ms // 60000
            if elapsed_ms > hold_ms:
                outcome   = "TIME_EXIT"
                elapsed_m = elapsed_min
                exit_price = c[j - 1] if j > 0 else entry
                exit_r = (1 if side=="buy" else -1) * (exit_price - entry) / sl_dist
                break
            if side == "buy":
                if l[j] <= sl:
                    outcome, elapsed_m, exit_r = "STOP", elapsed_min, -1.0; break
                if h[j] >= tp:
                    outcome, elapsed_m, exit_r = "TP",   elapsed_min, round(tp_dist/sl_dist, 2); break
            else:
                if h[j] >= sl:
                    outcome, elapsed_m, exit_r = "STOP", elapsed_min, -1.0; break
                if l[j] <= tp:
                    outcome, elapsed_m, exit_r = "TP",   elapsed_min, round(tp_dist/sl_dist, 2); break

        if outcome == "OPEN":
            continue

        trades.append(Trade(sym, side, int(ts[i]), entry, sl, tp, outcome, elapsed_m or 0, exit_r))

        # block new entries until this trade ends
        last_trade_end_ts = int(ts[i]) + (elapsed_m or hold_m) * 60 * 1000

    return trades


def _stats(trades):
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "avg_r": 0.0, "te_pct": 0.0}
    tp_  = [t for t in trades if t.outcome == "TP"]
    sl_  = [t for t in trades if t.outcome == "STOP"]
    te_  = [t for t in trades if t.outcome == "TIME_EXIT"]
    n_rr = len(tp_) + len(sl_)
    wr   = len(tp_) / n_rr * 100 if n_rr else 0
    g_w  = sum(t.exit_r for t in tp_)
    g_l  = abs(sum(t.exit_r for t in sl_))
    pf   = round(g_w / g_l, 2) if g_l > 0 else 999.0
    avg_r = sum(t.exit_r for t in trades) / len(trades)
    te_pct = len(te_) / len(trades) * 100
    return {"n": len(trades), "wr": wr, "pf": pf, "avg_r": avg_r, "te_pct": te_pct}


def main():
    if not CACHE_FILE.exists():
        print(f"Cache not found: {CACHE_FILE}")
        sys.exit(1)

    with open(CACHE_FILE, "rb") as f:
        cache = pickle.load(f)

    raw5m_by_sym = {}
    for sym in SYMBOLS:
        d = cache.get(sym, {})
        if d.get("5m"):
            raw5m_by_sym[sym] = d["5m"]

    print(f"Loaded {len(raw5m_by_sym)} symbols")
    print()

    vol_thresholds = [1.0, 1.5, 2.0, 2.5]
    tp_modes       = ["mid", "opposite", "atr1.5", "atr2.0"]
    hold_options   = [60, 90, 120]

    def _run_sweep(rsi_on):
        label = "WITH RSI<35 lower / RSI>65 upper" if rsi_on else "NO RSI filter"
        print(f"\n{'='*70}")
        print(f"BB BREAKOUT — {label}")
        print(f"  (lower BB + green candle + vol | upper BB + red candle + vol)")
        print(f"{'='*70}")
        print(f"  {'vol_min':>7}  {'tp':>9}  {'hold':>5}  {'n':>4}  {'WR':>6}  {'PF':>6}  {'avg_R':>7}  {'TIME':>5}")
        print("  " + "-" * 65)
        best = []
        for vol_mult in vol_thresholds:
            for tp_mode in tp_modes:
                for hold_m in hold_options:
                    all_trades = []
                    for sym, raw in raw5m_by_sym.items():
                        all_trades += simulate(sym, raw, vol_mult, tp_mode, hold_m, rsi_filter=rsi_on)
                    s = _stats(all_trades)
                    if s["n"] == 0:
                        continue
                    flag = " <--" if s["pf"] >= 1.5 and s["n"] >= 20 else ""
                    print(f"  {vol_mult:>7.1f}x  {tp_mode:>9}  {hold_m:>5}m  {s['n']:>4}"
                          f"  {s['wr']:>5.1f}%  {s['pf']:>6.2f}  {s['avg_r']:>+7.3f}"
                          f"  {s['te_pct']:>4.0f}%{flag}")
                    if s["pf"] >= 1.5 and s["n"] >= 20:
                        best.append((s["pf"], vol_mult, tp_mode, hold_m, s))
        return best

    best_no_rsi = _run_sweep(rsi_on=False)
    best_rsi    = _run_sweep(rsi_on=True)

    print()
    print("=" * 70)
    print("SUMMARY vs current BB FADE")
    print("=" * 70)
    print(f"  BB FADE now (vol<70%, tp=mid, 60m):  WR=61%  PF=3.71  avg_R=+0.983  n=36")
    print()

    def _show_top(label, best):
        if not best:
            print(f"  {label}: no config PF>=1.5 n>=20")
            return
        best.sort(reverse=True)
        pf, vm, tm, hm, s = best[0]
        print(f"  {label}:  vol>={vm}x  tp={tm}  hold={hm}m  "
              f"n={s['n']}  WR={s['wr']:.1f}%  PF={pf:.2f}  avg_R={s['avg_r']:+.3f}")

    _show_top("No RSI  best", best_no_rsi)
    _show_top("RSI<35/>65 best", best_rsi)

    if best_rsi:
        best_rsi.sort(reverse=True)
        _, vol_mult, tp_mode, hold_m, _ = best_rsi[0]
        print(f"\nRSI config (vol>={vol_mult}x  tp={tp_mode}  hold={hold_m}m) -- by symbol:")
        for sym, raw in raw5m_by_sym.items():
            trades = simulate(sym, raw, vol_mult, tp_mode, hold_m, rsi_filter=True)
            s = _stats(trades)
            longs  = sum(1 for t in trades if t.side == "buy")
            shorts = sum(1 for t in trades if t.side == "sell")
            print(f"  {sym:>12}: n={s['n']:>3}  L={longs:>2} S={shorts:>2}"
                  f"  WR={s['wr']:>5.1f}%  PF={s['pf']:>5.2f}  avg_R={s['avg_r']:>+6.3f}")


if __name__ == "__main__":
    main()
