"""
bt_early_entry.py — Research: EMA pullback entry vs direct entry for DRIFT FAST signals.

Hypothesis: scanner catches slope AFTER impulse already happened (entry late).
If we wait for price to pull back to EMA20_5m within 30 min, we get a better entry
with the same SL structural level → less chance of TIME_EXIT, better R.

Methods compared:
  A (current):  entry at 15m signal candle close (as recorded in signal_log.jsonl)
  B (pullback): wait up to PULLBACK_BARS × 5m candles for price to touch EMA20_5m
                  if pullback happens → enter at EMA20 level, keep original SL
                  if no pullback in 30 min → trade skipped

Uses:
  - scripts/signal_log.jsonl     (scanner DRIFT FAST signals)
  - scripts/signal_labels.jsonl  (outcomes)
  - scripts/backtest_candle_cache_65d.pkl (5m candles)

Usage:
    python scripts/bt_early_entry.py
"""
import bisect
import json
import os
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.strategy.indicators import calc_ema

# ── Config ─────────────────────────────────────────────────────────────────────
PULLBACK_BARS = 6    # max 5m bars to wait for EMA touch (= 30 min)
EMA_PERIOD    = 20   # 5m EMA period
TP_R          = 0.4  # DRIFT TP1 multiple of SL-dist (same as prod)

# ── Paths ──────────────────────────────────────────────────────────────────────
_DIR    = Path(__file__).parent
CACHE   = _DIR / "backtest_candle_cache_65d.pkl"
SIG_LOG = _DIR / "signal_log.jsonl"
SIG_LAB = _DIR / "signal_labels.jsonl"


def _load_cache() -> dict:
    if not CACHE.exists():
        print(f"[ERROR] Cache not found: {CACHE}")
        sys.exit(1)
    with open(CACHE, "rb") as f:
        return pickle.load(f)


def _simulate_exit(h5_all, h5_ts, entry_ts_ms, entry_price, sl, tp, side, max_hold_ms):
    """Scan forward 5m candles from entry_ts_ms for TP/SL/TIME_EXIT.
    Returns (outcome, elapsed_min, exit_price, exit_r).
    """
    sl_dist = abs(entry_price - sl)
    i_start = bisect.bisect_right(h5_ts, entry_ts_ms)
    i_end   = bisect.bisect_right(h5_ts, entry_ts_ms + max_hold_ms)
    fwd = h5_all[i_start:i_end]
    if not fwd:
        return "NO_DATA", None, None, 0.0

    mfe = 0.0
    for c in fwd:
        ts_c  = int(c[0])
        high  = float(c[2])
        low   = float(c[3])
        close = float(c[4])
        favorable = (high - entry_price) if side == "buy" else (entry_price - low)
        mfe = max(mfe, favorable)
        elapsed = (ts_c - entry_ts_ms) // 60000
        if side == "buy":
            if low  <= sl: return "STOP",      elapsed, sl, -1.0
            if high >= tp: return "TP",         elapsed, tp, round(mfe / sl_dist, 2) if sl_dist > 0 else 0.0
        else:
            if high >= sl: return "STOP",      elapsed, sl, -1.0
            if low  <= tp: return "TP",         elapsed, tp, round(mfe / sl_dist, 2) if sl_dist > 0 else 0.0

    last = fwd[-1]
    elapsed = (int(last[0]) - entry_ts_ms) // 60000
    exit_price_te = float(last[4])
    dir_mult = 1 if side == "buy" else -1
    exit_r_te = round(dir_mult * (exit_price_te - entry_price) / sl_dist, 2) if sl_dist > 0 else 0.0
    return "TIME_EXIT", elapsed, exit_price_te, exit_r_te


def main():
    cache = _load_cache()

    # Load DRIFT FAST scanner signals
    signals = {}
    with open(SIG_LOG) as f:
        for line in f:
            if not line.strip():
                continue
            s = json.loads(line)
            if (s.get("source") == "scanner"
                    and s.get("regime") == "DRIFT"
                    and s.get("style") == "FAST"):
                signals[s["signal_id"]] = s

    # Load labels for those signals
    labels = {}
    with open(SIG_LAB) as f:
        for line in f:
            if not line.strip():
                continue
            l = json.loads(line)
            if l["signal_id"] in signals:
                labels[l["signal_id"]] = l

    print(f"DRIFT FAST scanner signals: {len(signals)}, labeled: {len(labels)}")
    print()

    rows = []

    for sig_id, sig in sorted(signals.items(), key=lambda x: x[1]["ts_ms"]):
        if sig_id not in labels:
            continue

        symbol = sig["symbol"]
        if symbol not in cache or not cache[symbol].get("5m"):
            continue

        h5_all = cache[symbol]["5m"]   # oldest-first
        h5_ts  = [int(c[0]) for c in h5_all]

        ts_ms       = sig["ts_ms"]
        side        = sig["side"]
        entry_a     = sig["close"]
        sl_orig     = sig["sl"]
        tp_orig     = sig["tp"]
        max_hold_ms = sig.get("max_hold_min", 90) * 60 * 1000

        lab = labels[sig_id]
        outcome_a = lab["outcome"]
        exit_r_a  = lab.get("exit_r", 0.0)

        # ── Method B: EMA20_5m pullback ────────────────────────────────────────
        # EMA at signal time: use last EMA_PERIOD closes before ts_ms
        i5 = bisect.bisect_left(h5_ts, ts_ms)
        if i5 < EMA_PERIOD:
            continue

        closes_for_ema = np.array([float(c[4]) for c in h5_all[i5 - EMA_PERIOD:i5]])
        ema_arr  = calc_ema(closes_for_ema, EMA_PERIOD)
        ema_level = float(ema_arr[-1])

        # Check if entry_a is already AT or BELOW ema_level (no improvement possible)
        if side == "buy" and entry_a <= ema_level * 1.001:
            outcome_b = "NO_IMPROVE"
            entry_b   = entry_a
            exit_r_b  = exit_r_a
        elif side == "sell" and entry_a >= ema_level * 0.999:
            outcome_b = "NO_IMPROVE"
            entry_b   = entry_a
            exit_r_b  = exit_r_a
        else:
            # Look for pullback in next PULLBACK_BARS 5m candles
            fwd_bars = h5_all[i5:i5 + PULLBACK_BARS]
            pullback_ts    = None
            pullback_price = None

            for c in fwd_bars:
                high = float(c[2])
                low  = float(c[3])
                if side == "buy" and low <= ema_level:
                    pullback_ts    = int(c[0])
                    pullback_price = ema_level
                    break
                elif side == "sell" and high >= ema_level:
                    pullback_ts    = int(c[0])
                    pullback_price = ema_level
                    break

            if pullback_price is None:
                outcome_b = "NO_PULLBACK"
                entry_b   = entry_a
                exit_r_b  = 0.0
            else:
                entry_b   = pullback_price
                sl_dist_b = abs(entry_b - sl_orig)
                if sl_dist_b <= 0:
                    outcome_b = "SKIP"
                    exit_r_b  = 0.0
                else:
                    tp_b = (entry_b + TP_R * sl_dist_b) if side == "buy" else (entry_b - TP_R * sl_dist_b)
                    outcome_b, _, exit_price_b, _ = _simulate_exit(
                        h5_all, h5_ts, pullback_ts,
                        entry_b, sl_orig, tp_b, side, max_hold_ms,
                    )
                    if outcome_b == "TP":
                        exit_r_b = TP_R
                    elif outcome_b == "STOP":
                        exit_r_b = -1.0
                    elif outcome_b == "TIME_EXIT" and exit_price_b:
                        sl_dist_b_safe = sl_dist_b if sl_dist_b > 0 else 1e-9
                        d = (1 if side == "buy" else -1)
                        exit_r_b = round(d * (exit_price_b - entry_b) / sl_dist_b_safe, 2)
                    else:
                        exit_r_b = 0.0

        dt  = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        ts_str = dt.strftime("%m-%d %H:%M")
        entry_diff_pct = round((entry_b - entry_a) / entry_a * 100, 3) if entry_a else 0

        rows.append({
            "ts":        ts_str,
            "symbol":    symbol,
            "entry_a":   entry_a,
            "ema20":     round(ema_level, 4),
            "entry_b":   round(entry_b, 4),
            "diff_pct":  entry_diff_pct,
            "outcome_a": outcome_a,
            "exit_r_a":  exit_r_a,
            "outcome_b": outcome_b,
            "exit_r_b":  round(exit_r_b, 2),
        })

    # ── Print table ────────────────────────────────────────────────────────────
    print(f"{'Time':>11} {'Symbol':>10} {'EntryA':>9} {'EMA20':>9} {'EntryB':>9}"
          f" {'Diff%':>6}  {'Out_A':>10} {'R_A':>5}  {'Out_B':>12} {'R_B':>5}")
    print("-" * 100)
    for r in rows:
        print(f"{r['ts']:>11} {r['symbol']:>10} {r['entry_a']:>9.4f} {r['ema20']:>9.4f}"
              f" {r['entry_b']:>9.4f} {r['diff_pct']:>+6.3f}  {r['outcome_a']:>10}"
              f" {r['exit_r_a']:>5.2f}  {r['outcome_b']:>12} {r['exit_r_b']:>5.2f}")

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)

    def _stats(rows_subset, label):
        if not rows_subset:
            print(f"  {label}: нет данных")
            return
        tp  = [r for r in rows_subset if r["outcome_b" if "B" in label else "outcome_a"] == "TP"]
        sl  = [r for r in rows_subset if r["outcome_b" if "B" in label else "outcome_a"] == "STOP"]
        te  = [r for r in rows_subset if r["outcome_b" if "B" in label else "outcome_a"] == "TIME_EXIT"]
        total_r = len(tp) + len(sl)
        wr = len(tp) * 100 // total_r if total_r else 0
        key = "exit_r_b" if "B" in label else "exit_r_a"
        total_rsum = sum(r[key] for r in rows_subset)
        pf_w = sum(r[key] for r in rows_subset if r[key] > 0)
        pf_l = abs(sum(r[key] for r in rows_subset if r[key] < 0))
        pf = round(pf_w / pf_l, 2) if pf_l > 0 else 999.0
        print(f"  {label}: n={len(rows_subset)}  TP={len(tp)} SL={len(sl)} TE={len(te)}"
              f"  WR={wr}%  ΣR={total_rsum:+.2f}  PF={pf}")

    def _stats_generic(rows_s, outcome_key, exit_r_key, label):
        tp  = [r for r in rows_s if r[outcome_key] == "TP"]
        sl  = [r for r in rows_s if r[outcome_key] == "STOP"]
        te  = [r for r in rows_s if r[outcome_key] == "TIME_EXIT"]
        total_r = len(tp) + len(sl)
        wr = len(tp) * 100 // total_r if total_r else 0
        pf_w = sum(r[exit_r_key] for r in rows_s if r[exit_r_key] > 0)
        pf_l = abs(sum(r[exit_r_key] for r in rows_s if r[exit_r_key] < 0))
        pf = round(pf_w / pf_l, 2) if pf_l > 0 else 999.0
        total_rsum = sum(r[exit_r_key] for r in rows_s)
        print(f"  {label}: n={len(rows_s)}  TP={len(tp)} SL={len(sl)} TE={len(te)}"
              f"  WR={wr}%  sumR={total_rsum:+.2f}  PF={pf}")

    # Method A: all labeled signals
    _stats_generic(rows, "outcome_a", "exit_r_a", "Метод A (текущий, все)")

    # Method B: only those where pullback was found (tradeable)
    rows_b_tradeable = [r for r in rows if r["outcome_b"] not in ("NO_PULLBACK", "NO_IMPROVE", "SKIP", "NO_DATA")]
    _stats_generic(rows_b_tradeable, "outcome_b", "exit_r_b", "Метод B (только с откатом к EMA)")

    rows_b_nopb = [r for r in rows if r["outcome_b"] == "NO_PULLBACK"]
    print(f"\n  No pullback (would skip): {len(rows_b_nopb)} of {len(rows)}"
          f" ({len(rows_b_nopb)*100//len(rows) if rows else 0}%)")

    improved = [r for r in rows_b_tradeable if r["exit_r_b"] > r["exit_r_a"]]
    worsened = [r for r in rows_b_tradeable if r["exit_r_b"] < r["exit_r_a"]]
    same     = [r for r in rows_b_tradeable if r["exit_r_b"] == r["exit_r_a"]]
    print(f"\n  R improved: {len(improved)}  worsened: {len(worsened)}  same: {len(same)}")

    entry_diffs = [abs(r["diff_pct"]) for r in rows_b_tradeable]
    if entry_diffs:
        print(f"  Entry price improvement: avg={np.mean(entry_diffs):.3f}%  max={max(entry_diffs):.3f}%")


if __name__ == "__main__":
    main()
