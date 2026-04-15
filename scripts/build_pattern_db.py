"""
build_pattern_db.py — extract full feature vectors for ALL ticks from candle cache.

Unlike backtest_simulate.py (only ENTRY rows), this records EVERY scan tick
including NO_TRADE, with all indicators + forward returns.

Output: scripts/pattern_db.csv  (~17000 rows × ~40 columns)

Usage:
    python scripts/build_pattern_db.py

Requires backtest_candle_cache_35d.pkl — run backtest_simulate.py first.
"""
import bisect
import csv
import os
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from scripts.backtest_simulate import (
    SYMBOLS, HOLD_FAST_M, HOLD_SWING_M,
    compute_signal, check_outcome,
    _get_hist_funding, _get_oi_delta, _candle_vol_delta,
    _calc_basis_and_perp_div,
)

CACHE_FILE    = Path(__file__).parent / "backtest_candle_cache_65d.pkl"
CACHE_MI_FILE = Path(__file__).parent / "backtest_mark_index_cache_65d.pkl"
OUTPUT_CSV    = Path(__file__).parent / "pattern_db.csv"
INTERVAL_M    = 15


def _fwd_return(h15_all, h15_ts, ts_ms, minutes):
    """% price change from ts_ms close to close at ts_ms+minutes."""
    i0 = bisect.bisect_left(h15_ts, ts_ms)
    if i0 == 0:
        return None
    entry_close = float(h15_all[i0 - 1][4])
    fwd_ms = ts_ms + minutes * 60_000
    i1 = bisect.bisect_left(h15_ts, fwd_ms)
    if i1 == 0 or i1 > len(h15_all) - 1:
        return None
    fwd_close = float(h15_all[i1 - 1][4])
    return round((fwd_close - entry_close) / entry_close * 100, 3) if entry_close > 0 else None


def _slice_fwd_5m(h5_all, ts_ms, max_hours=8):
    """Forward 5m candles newest-first (as OKX API — required by check_outcome)."""
    fwd_end_ms = ts_ms + max_hours * 3_600_000
    fwd = [c for c in h5_all if ts_ms < int(c[0]) <= fwd_end_ms]
    fwd.sort(key=lambda c: int(c[0]), reverse=True)
    return fwd


def main():
    if not CACHE_FILE.exists():
        print(f"Кэш не найден: {CACHE_FILE}")
        print("Запустите: python scripts/backtest_simulate.py")
        return

    print("Загрузка кэша свечей...")
    with open(CACHE_FILE, "rb") as f:
        candle_cache = pickle.load(f)

    mi_cache: dict = {}
    if CACHE_MI_FILE.exists():
        with open(CACHE_MI_FILE, "rb") as f:
            mi_cache = pickle.load(f)

    # Derive tick range from 15m candle timestamps across all symbols
    all_ts_flat = []
    for sym in SYMBOLS:
        if sym in candle_cache:
            all_ts_flat.extend(int(c[0]) for c in candle_cache[sym].get("15m", []))
    if not all_ts_flat:
        print("Нет данных в кэше.")
        return

    step_ms = INTERVAL_M * 60_000
    t_start = min(all_ts_flat)
    t_end   = max(all_ts_flat)
    all_ts  = list(range(t_start, t_end, step_ms))

    hold_fast_ms  = HOLD_FAST_M  * 60_000
    hold_swing_ms = HOLD_SWING_M * 60_000

    d_start = datetime.fromtimestamp(t_start / 1000, tz=timezone.utc).date()
    d_end   = datetime.fromtimestamp(t_end   / 1000, tz=timezone.utc).date()
    print(f"Диапазон: {d_start} → {d_end}  |  тиков/пару: {len(all_ts)}")

    rows = []
    for symbol in SYMBOLS:
        if symbol not in candle_cache:
            continue
        sym_data     = candle_cache[symbol]
        funding_hist = sym_data.get("funding_history", [])
        oi_hist      = sym_data.get("oi_history", [])
        h4_all       = sym_data["4h"]
        h1_all       = sym_data["1h"]
        h15_all      = sym_data["15m"]
        h5_all       = sym_data.get("5m", [])
        mark15_all   = mi_cache.get(symbol, {}).get("mark_15m", [])
        idx15_all    = mi_cache.get(symbol, {}).get("index_15m", [])

        h4_ts    = [int(c[0]) for c in h4_all]
        h1_ts    = [int(c[0]) for c in h1_all]
        h15_ts   = [int(c[0]) for c in h15_all]
        h5_ts    = [int(c[0]) for c in h5_all]
        mark_ts  = [int(c[0]) for c in mark15_all]
        idx_ts   = [int(c[0]) for c in idx15_all]

        sym_rows = 0
        for ts_ms in all_ts:
            i4  = bisect.bisect_left(h4_ts,  ts_ms)
            i1  = bisect.bisect_left(h1_ts,  ts_ms)
            i15 = bisect.bisect_left(h15_ts, ts_ms)
            i5  = bisect.bisect_left(h5_ts,  ts_ms)
            im  = bisect.bisect_left(mark_ts, ts_ms)
            ii  = bisect.bisect_left(idx_ts,  ts_ms)

            raw_4h  = list(reversed(h4_all [max(0, i4  - 60):i4 ]))
            raw_1h  = list(reversed(h1_all [max(0, i1  - 60):i1 ]))
            raw_15m = list(reversed(h15_all[max(0, i15 - 96):i15]))
            raw_5m  = list(reversed(h5_all [max(0, i5  - 30):i5 ])) if h5_all else None
            raw_mark = list(reversed(mark15_all[max(0, im - 10):im])) if mark15_all else []
            raw_idx  = list(reversed(idx15_all [max(0, ii - 10):ii])) if idx15_all else []

            if not raw_4h or len(raw_4h) < 51: continue   # EMA-50 on 4H needs 50+ bars
            if not raw_1h or len(raw_1h) < 20: continue
            if not raw_15m or len(raw_15m) < 30: continue

            funding         = _get_hist_funding(funding_hist, ts_ms)
            oi_delta        = _get_oi_delta(oi_hist, ts_ms)
            trade_delta_15m = _candle_vol_delta(raw_5m)
            last_price      = float(raw_15m[0][4]) if raw_15m else 0.0
            basis_pct, perp_div = _calc_basis_and_perp_div(
                raw_mark, raw_idx, last_price, raw_15m)

            sig = compute_signal(
                raw_4h, raw_1h, raw_15m,
                funding=funding, symbol=symbol, mode="COMBINED",
                night_filter=False, oi_delta=oi_delta,
                raw_5m=raw_5m, trade_delta_15m=trade_delta_15m,
                _debug=True,
            )
            if sig is None:
                continue

            # Forward price returns (direction-neutral, always computed)
            fwd_60m  = _fwd_return(h15_all, h15_ts, ts_ms, 60)
            fwd_150m = _fwd_return(h15_all, h15_ts, ts_ms, 150)
            fwd_300m = _fwd_return(h15_all, h15_ts, ts_ms, 300)

            # Forward outcome (only for ENTRY ticks with valid SL/TP)
            fwd_outcome_fast  = ""
            fwd_outcome_swing = ""
            if sig["entry_signal"] == "ENTRY" and sig["sl"] and sig["tp"] and h5_all:
                fwd_5m = _slice_fwd_5m(h5_all, ts_ms)
                if fwd_5m:
                    out_f, _, _, _ = check_outcome(
                        fwd_5m, sig["side"], sig["sl"], sig["tp"],
                        ts_ms, hold_fast_ms, entry_price=sig["close"])
                    out_s, _, _, _ = check_outcome(
                        fwd_5m, sig["side"], sig["sl"], sig["tp"],
                        ts_ms, hold_swing_ms, entry_price=sig["close"])
                    fwd_outcome_fast  = out_f
                    fwd_outcome_swing = out_s

            ts_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            rows.append({
                "ts_ms":            ts_ms,
                "ts_dt":            ts_dt,
                "symbol":           symbol,
                "entry_signal":     sig["entry_signal"],
                "drop_reason":      sig.get("drop_reason") or "",
                "trade_style":      sig["trade_style"],
                "regime":           sig["regime"],
                "side":             sig.get("side") or "",
                "close":            sig["close"],
                "adx_1h":           sig["adx_1h"],
                "adx_1h_rising":    sig["adx_1h_rising"],
                "adx_4h":           sig["adx_4h"],
                "di_spread_1h":     sig.get("di_spread_1h", ""),
                "di_spread_4h":     sig.get("di_spread_4h", ""),
                "bias_1h":          sig["bias_1h"],
                "bias_4h":          sig.get("bias_4h", ""),
                "ema_drift_dir":    sig.get("ema_drift_dir", ""),
                "vol_ratio":        sig["vol_ratio"],
                "bb_width":         sig["bb_width"],
                "bb_pct_b":         sig.get("bb_pct_b", ""),
                "rsi_1h":           sig["rsi_1h"],
                "slope_1h":         sig["slope_1h"],
                "slope_15m":        sig["slope_15m"],
                "vwap_ok":          sig.get("vwap_ok", ""),
                "vwap":             sig.get("vwap", ""),
                "day_position":     sig.get("day_position", ""),
                "signal_hour":      sig.get("signal_hour", ""),
                "fourch_veto":      sig["fourch_veto"],
                "strong_4h_veto":   sig["strong_4h_veto"],
                "drift_adx1h_veto": sig.get("drift_adx1h_veto", ""),
                "oi_weak":          sig["oi_weak"],
                "late_move":        sig["late_move"],
                "is_night":         sig["is_night"],
                "funding":          sig["funding"],
                "oi_delta":         sig["oi_delta"],
                "trade_delta_15m":  round(trade_delta_15m, 3),
                "basis_pct":        round(basis_pct, 3) if basis_pct is not None else "",
                "perp_div":         round(perp_div, 3) if perp_div is not None else "",
                "fwd_ret_60m":      fwd_60m if fwd_60m is not None else "",
                "fwd_ret_150m":     fwd_150m if fwd_150m is not None else "",
                "fwd_ret_300m":     fwd_300m if fwd_300m is not None else "",
                "fwd_outcome_fast": fwd_outcome_fast,
                "fwd_outcome_swing": fwd_outcome_swing,
            })
            sym_rows += 1

        print(f"  {symbol}: {sym_rows} тиков")

    if not rows:
        print("Нет данных.")
        return

    fieldnames = list(rows[0].keys())
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    total   = len(rows)
    entries = sum(1 for r in rows if r["entry_signal"] == "ENTRY")
    print(f"\nГотово: {total} строк → {OUTPUT_CSV.name}")
    print(f"  ENTRY: {entries}  NO_TRADE: {total - entries}  ({entries / total * 100:.1f}% сигналов)")


if __name__ == "__main__":
    main()
