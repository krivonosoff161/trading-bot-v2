"""
bt_slippage_sim.py — Entry delay impact simulator.

Reads backtest_results_latest.json + candle cache.
Simulates entering N bars AFTER the signal bar (5m bars = 5 min each).
Compares P&L vs ideal (instant) entry.

Does NOT touch any prod files. Read-only.

Usage:
    python scripts/bt_slippage_sim.py
"""
import json
import pickle
import sys
import numpy as np
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

RESULTS_FILE = Path(__file__).parent / "backtest_runs" / "backtest_results_latest.json"
CACHE_FILE   = Path(__file__).parent / "backtest_candle_cache_35d.pkl"

# Delays to test (in 5m bars)
DELAYS = [0, 1, 2, 3, 6]   # 0=ideal, 1=+5min, 2=+10min, 3=+15min, 6=+30min


def _parse_5m(raw: list) -> tuple:
    """Returns (ts_array, open_array, close_array) chronological."""
    ts     = np.array([int(c[0])   for c in raw])
    opens  = np.array([float(c[1]) for c in raw])
    closes = np.array([float(c[4]) for c in raw])
    return ts, opens, closes


def _get_entry_price(ts5, opens, closes, signal_ts_ms: int, delay_bars: int) -> float | None:
    """Return open price of the bar `delay_bars` after signal bar."""
    idx = np.searchsorted(ts5, signal_ts_ms, side="left")
    target = idx + delay_bars
    if target >= len(opens):
        return None
    # Use open of the delayed bar as realistic fill price
    return float(opens[target])


def _calc_pnl_r(side: str, entry: float, sl: float, tp: float,
                delayed_entry: float) -> float | None:
    """
    Recalculate exit_r using delayed entry price.
    SL/TP prices stay the same (set by strategy at signal time).
    If delayed entry already past SL or TP → return None (invalid).
    Returns R relative to original sl_dist for fair comparison.
    """
    sl_dist_orig = abs(entry - sl)
    if sl_dist_orig == 0:
        return None

    if side == "buy":
        # Check if delayed entry is already past SL or TP
        if delayed_entry <= sl or delayed_entry >= tp:
            return None
        # New sl_dist from delayed entry
        new_sl_dist = delayed_entry - sl
        new_tp_dist = tp - delayed_entry
    else:
        if delayed_entry >= sl or delayed_entry <= tp:
            return None
        new_sl_dist = sl - delayed_entry
        new_tp_dist = delayed_entry - tp

    if new_sl_dist <= 0:
        return None

    # R:R ratio changes — TP in R units from delayed entry
    tp_r = new_tp_dist / new_sl_dist
    sl_r = -1.0

    # Original outcome R (from backtest) scaled to new sl_dist
    # We return tp_r if TP hit, sl_r if SL hit — outcome stays same
    return tp_r, sl_r, new_sl_dist / sl_dist_orig


def simulate_delay(trades: list, cache: dict, delay_bars: int) -> dict:
    """Simulate all trades with `delay_bars` entry delay."""
    results = []

    # Preparse 5m data per symbol
    candles_5m = {}
    for sym, data in cache.items():
        if sym.startswith("_"):
            continue
        raw = data.get("5m", [])
        if raw:
            candles_5m[sym] = _parse_5m(raw)

    for t in trades:
        sym     = t["symbol"]
        side    = t["side"]
        entry   = t["close"]
        sl      = t["sl"]
        tp      = t["tp"]
        outcome = t["outcome"]
        exit_r  = t.get("exit_r") or 0.0
        ts_ms   = t["ts_ms"]

        if not (entry and sl and tp):
            continue
        if sym not in candles_5m:
            continue

        ts5, opens, closes = candles_5m[sym]

        if delay_bars == 0:
            # Ideal entry
            results.append({
                "outcome": outcome,
                "exit_r":  exit_r,
                "valid":   True,
                "skipped": False,
            })
            continue

        delayed_price = _get_entry_price(ts5, opens, closes, ts_ms, delay_bars)
        if delayed_price is None:
            results.append({"outcome": outcome, "exit_r": exit_r,
                            "valid": False, "skipped": True})
            continue

        calc = _calc_pnl_r(side, entry, sl, tp, delayed_price)
        if calc is None:
            # Delayed entry already blew past SL or TP — skip this trade
            results.append({"outcome": outcome, "exit_r": exit_r,
                            "valid": False, "skipped": True})
            continue

        tp_r, sl_r, sl_ratio = calc

        # Outcome stays the same (TP/STOP/TIME_EXIT from backtest)
        # but R changes because entry price shifted
        if outcome == "TP":
            new_exit_r = round(tp_r, 2)
        elif outcome == "STOP":
            new_exit_r = round(sl_r, 2)
        else:  # TIME_EXIT
            # Scale original exit_r by sl_ratio (sl_dist changed)
            new_exit_r = round(exit_r / sl_ratio, 2) if sl_ratio > 0 else exit_r

        results.append({
            "outcome":       outcome,
            "exit_r":        new_exit_r,
            "exit_r_orig":   exit_r,
            "delayed_price": delayed_price,
            "valid":         True,
            "skipped":       False,
        })

    return results


def _stats(results: list) -> dict:
    valid = [r for r in results if r["valid"] and not r["skipped"]]
    if not valid:
        return {}
    tp   = [r for r in valid if r["outcome"] == "TP"]
    sl   = [r for r in valid if r["outcome"] == "STOP"]
    te   = [r for r in valid if r["outcome"] == "TIME_EXIT"]
    total_rs = len(tp) + len(sl)
    wr   = len(tp) / total_rs * 100 if total_rs > 0 else 0

    gross_w = sum(abs(r["exit_r"]) for r in tp)
    gross_l = sum(abs(r["exit_r"]) for r in sl)
    pf = gross_w / max(0.001, gross_l)

    avg_r = np.mean([r["exit_r"] for r in valid]) if valid else 0
    skipped = sum(1 for r in results if r["skipped"])

    return {
        "n":        len(valid),
        "skipped":  skipped,
        "wr":       round(wr, 1),
        "pf":       round(pf, 2),
        "avg_r":    round(float(avg_r), 3),
        "tp":       len(tp),
        "sl":       len(sl),
        "te":       len(te),
    }


def main():
    print("Загрузка результатов бэктеста...")
    with open(RESULTS_FILE) as f:
        all_trades = json.load(f)

    trades = [t for t in all_trades
              if t.get("outcome") in ("TP", "STOP", "TIME_EXIT")
              and t.get("close") and t.get("sl") and t.get("tp")]

    print(f"Сделок для анализа: {len(trades)}")

    print("Загрузка кэша свечей...")
    with open(CACHE_FILE, "rb") as f:
        cache = pickle.load(f)

    print()
    print("=" * 62)
    print("  ВЛИЯНИЕ ЗАДЕРЖКИ ВХОДА НА РЕЗУЛЬТАТ")
    print("=" * 62)
    print(f"  {'Задержка':<14} {'n':>4}  {'WR':>5}  {'PF':>5}  {'avg_R':>7}  {'Пропущено':>10}")
    print("-" * 62)

    baseline = None
    for delay in DELAYS:
        results = simulate_delay(trades, cache, delay)
        s = _stats(results)
        if not s:
            continue

        label = "0 (идеал)" if delay == 0 else f"+{delay*5} мин"

        if baseline is None:
            baseline = s
            pf_diff = ""
            wr_diff = ""
        else:
            pf_diff = f"  Δ PF {s['pf']-baseline['pf']:+.2f}"
            wr_diff = f"  Δ WR {s['wr']-baseline['wr']:+.1f}%"

        print(f"  {label:<14} {s['n']:>4}  {s['wr']:>4.1f}%  {s['pf']:>5.2f}"
              f"  {s['avg_r']:>+7.3f}  {s['skipped']:>4} пропущено"
              f"  {pf_diff}{wr_diff}")

    print("=" * 62)
    print()
    print("Пропущено = вход после задержки уже за SL или TP (не исполнимо).")
    print("n         = реально исполнимые сделки с задержкой.")


if __name__ == "__main__":
    main()
