"""
bt_param_sweep.py - parametric backtest sweep.

Runs backtest_simulate.py with different env vars, each config saves
its own named JSON + txt log to backtest_runs/.
After all runs builds a comparison table saved to backtest_runs/sweep_{date}.md.

What we test:
  1. slope_min  - angle threshold for 15m (FAST) and 1H (SWING)
                  Hypothesis: raising from 30 to 35/40 cuts weak signals
                  that produce TIME_EXIT or neutral stops (STOP avg slope=22)
  2. hold_fast  - max hold time for FAST positions (minutes)
                  Hypothesis: 33% of TIME_EXIT reach TP1 within 2h after exit,
                  so 210/240m may convert TIME_EXIT into TP
  3. combination - slope_35 + hold_210, to see synergy or conflict

Configs:
  baseline    slope=30  hold=150  (current prod)
  slope_35    slope=35  hold=150
  slope_40    slope=40  hold=150
  hold_210    slope=30  hold=210
  hold_240    slope=30  hold=240
  s35_h210    slope=35  hold=210

Table metrics:
  n/day  WR(TP/SL)  PF  DD  TIME_EXIT%  slope_TP  slope_SL  n_TP  n_SL  n_TIME

Usage:
  python scripts/bt_param_sweep.py
  python scripts/bt_param_sweep.py --dry-run
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent
ROOT_DIR    = SCRIPT_DIR.parent.parent
BACKTEST_PY = SCRIPT_DIR / "backtest_simulate.py"
RUNS_DIR    = SCRIPT_DIR / "backtest_runs"

CONFIGS = [
    {
        "tag":          "baseline",
        "slope_min":    30,
        "hold_fast_m":  150,
        "hold_swing_m": 300,
        "desc":         "Current prod: slope>=30, hold=150m",
    },
    {
        "tag":          "slope_35",
        "slope_min":    35,
        "hold_fast_m":  150,
        "hold_swing_m": 300,
        "desc":         "Raise slope threshold to 35: cut weak DRIFT signals (STOP avg slope=22)",
    },
    {
        "tag":          "slope_40",
        "slope_min":    40,
        "hold_fast_m":  150,
        "hold_swing_m": 300,
        "desc":         "Raise slope threshold to 40: aggressive filtering",
    },
    {
        "tag":          "hold_210",
        "slope_min":    30,
        "hold_fast_m":  210,
        "hold_swing_m": 360,
        "desc":         "Extend hold to 210m: capture 33% TIME_EXIT that reach TP in next 2h",
    },
    {
        "tag":          "hold_240",
        "slope_min":    30,
        "hold_fast_m":  240,
        "hold_swing_m": 420,
        "desc":         "Extend hold to 240m: more aggressive extension",
    },
    {
        "tag":          "s35_h210",
        "slope_min":    35,
        "hold_fast_m":  210,
        "hold_swing_m": 360,
        "desc":         "Combination: slope>=35 + hold=210m",
    },
]


def compute_metrics(trades: list, days_back: int = 63) -> dict:
    """Compute summary metrics from per-trade JSON list."""
    if not trades:
        return {}

    tp_trades   = [t for t in trades if t["outcome"] == "TP"]
    sl_trades   = [t for t in trades if t["outcome"] == "STOP"]
    time_trades = [t for t in trades if t["outcome"] == "TIME_EXIT"]

    n_total = len(trades)
    n_tp    = len(tp_trades)
    n_sl    = len(sl_trades)
    n_time  = len(time_trades)

    wr = n_tp / (n_tp + n_sl) * 100 if (n_tp + n_sl) > 0 else 0.0

    gross_w = sum(t["pnl"] for t in tp_trades)
    gross_l = abs(sum(t["pnl"] for t in sl_trades))
    pf      = round(gross_w / gross_l, 2) if gross_l > 0 else 99.0

    # Max drawdown from equity curve
    balance = 1000.0
    peak    = balance
    max_dd  = 0.0
    for t in trades:
        balance += t["pnl"]
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Simulated balance gain %
    final_balance = 1000.0
    for t in trades:
        final_balance += t["pnl"]
    sim_gain = round((final_balance - 1000.0) / 1000.0 * 100, 1)

    signals_per_day = round(n_total / days_back, 1)
    time_exit_pct   = round(n_time / n_total * 100) if n_total else 0

    def avg(lst, key):
        vals = [t.get(key) for t in lst if t.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    # Per-regime breakdown
    by_regime = {}
    for regime in ("TRENDING", "DRIFT", "RANGING"):
        reg_t  = [t for t in trades if t.get("regime") == regime]
        reg_tp = [t for t in reg_t if t["outcome"] == "TP"]
        reg_sl = [t for t in reg_t if t["outcome"] == "STOP"]
        reg_te = [t for t in reg_t if t["outcome"] == "TIME_EXIT"]
        denom  = len(reg_tp) + len(reg_sl)
        by_regime[regime] = {
            "n":    len(reg_t),
            "wr":   round(len(reg_tp) / denom * 100) if denom else 0,
            "time": len(reg_te),
        }

    # Per-pair breakdown
    by_pair = {}
    for sym in ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT", "ADA-USDT"):
        pt  = [t for t in trades if t.get("symbol") == sym]
        ptp = [t for t in pt if t["outcome"] == "TP"]
        psl = [t for t in pt if t["outcome"] == "STOP"]
        pte = [t for t in pt if t["outcome"] == "TIME_EXIT"]
        d   = len(ptp) + len(psl)
        if pt:
            by_pair[sym] = {
                "n":    len(pt),
                "wr":   round(len(ptp) / d * 100) if d else 0,
                "time": len(pte),
            }

    return {
        "n_total":          n_total,
        "signals_per_day":  signals_per_day,
        "n_tp":             n_tp,
        "n_sl":             n_sl,
        "n_time":           n_time,
        "wr":               round(wr, 1),
        "pf":               pf,
        "max_dd":           round(max_dd, 1),
        "sim_gain":         sim_gain,
        "time_exit_pct":    time_exit_pct,
        "avg_mfe":          avg(trades,   "mfe_r"),
        "avg_mae":          avg(sl_trades, "mfe_r"),
        "avg_slope_tp":     avg(tp_trades,    "slope_15m"),
        "avg_slope_stop":   avg(sl_trades,    "slope_15m"),
        "avg_elapsed_tp":   avg(tp_trades,    "elapsed_m"),
        "avg_elapsed_time": avg(time_trades,  "elapsed_m"),
        "by_regime":        by_regime,
        "by_pair":          by_pair,
    }


def run_config(cfg: dict) -> bool:
    """Run one backtest configuration. Returns True on success."""
    tag = cfg["tag"]
    sep = "=" * 60
    print(sep)
    print("  Config:  " + tag)
    print("  " + cfg["desc"])
    print("  slope_min=" + str(cfg["slope_min"]) +
          "  hold_fast=" + str(cfg["hold_fast_m"]) + "m" +
          "  hold_swing=" + str(cfg["hold_swing_m"]) + "m")
    print(sep)

    env = os.environ.copy()
    env["BT_SLOPE_MIN"]    = str(cfg["slope_min"])
    env["BT_HOLD_FAST_M"]  = str(cfg["hold_fast_m"])
    env["BT_HOLD_SWING_M"] = str(cfg["hold_swing_m"])
    env["BT_RUN_TAG"]      = tag

    result = subprocess.run(
        [sys.executable, str(BACKTEST_PY)],
        env=env,
        cwd=str(ROOT_DIR),
    )
    return result.returncode == 0


def build_table(all_metrics: dict) -> str:
    """Build Markdown comparison table from all config results."""
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines.append("# Backtest Param Sweep — " + now)
    lines.append("")
    lines.append("## Configs tested")
    lines.append("")
    for cfg in CONFIGS:
        lines.append("- **" + cfg["tag"] + "**: " + cfg["desc"])
    lines.append("")

    lines.append("## Summary table")
    lines.append("")
    lines.append("| Config | n/day | WR% | PF | sim% | DD% | TIME_EXIT% | TP | SL | TIME | slope_TP | slope_SL |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")

    for cfg in CONFIGS:
        tag = cfg["tag"]
        m   = all_metrics.get(tag)
        if not m:
            lines.append("| " + tag + " | — | — | — | — | — | — | — | — | — | — | — |")
            continue
        row = (
            "| " + tag +
            " | " + str(m["signals_per_day"]) +
            " | " + str(m["wr"]) +
            " | " + str(m["pf"]) +
            " | " + str(m["sim_gain"]) +
            " | " + str(m["max_dd"]) +
            " | " + str(m["time_exit_pct"]) +
            " | " + str(m["n_tp"]) +
            " | " + str(m["n_sl"]) +
            " | " + str(m["n_time"]) +
            " | " + str(m["avg_slope_tp"] or "—") +
            " | " + str(m["avg_slope_stop"] or "—") +
            " |"
        )
        lines.append(row)

    lines.append("")
    lines.append("## By regime")
    lines.append("")
    lines.append("| Config | TRENDING n/WR/TIME | DRIFT n/WR/TIME | RANGING n/WR/TIME |")
    lines.append("|---|---|---|---|")

    for cfg in CONFIGS:
        tag = cfg["tag"]
        m   = all_metrics.get(tag)
        if not m:
            continue

        def reg_str(r):
            d = m["by_regime"].get(r, {})
            return str(d.get("n", "?")) + "/" + str(d.get("wr", "?")) + "%/" + str(d.get("time", "?"))

        lines.append(
            "| " + tag +
            " | " + reg_str("TRENDING") +
            " | " + reg_str("DRIFT") +
            " | " + reg_str("RANGING") +
            " |"
        )

    lines.append("")
    lines.append("## By pair")
    lines.append("")
    lines.append("| Config | BTC | ETH | SOL | XRP | DOGE |")
    lines.append("|---|---|---|---|---|---|")

    for cfg in CONFIGS:
        tag = cfg["tag"]
        m   = all_metrics.get(tag)
        if not m:
            continue

        def pair_str(sym):
            d = m["by_pair"].get(sym, {})
            if not d:
                return "—"
            return str(d["n"]) + "/" + str(d["wr"]) + "%"

        lines.append(
            "| " + tag +
            " | " + pair_str("BTC-USDT") +
            " | " + pair_str("ETH-USDT") +
            " | " + pair_str("SOL-USDT") +
            " | " + pair_str("XRP-USDT") +
            " | " + pair_str("DOGE-USDT") +
            " |"
        )

    lines.append("")
    lines.append("## How to read")
    lines.append("")
    lines.append("- **slope_35 vs baseline**: WR up + PF up = threshold 35 is better")
    lines.append("- **slope_40 vs slope_35**: if n/day drops below 3 = too aggressive")
    lines.append("- **hold_210 vs baseline**: TIME_EXIT% down + WR up = hold was too short")
    lines.append("- **hold_240 vs hold_210**: check if DD grows = longer hold hurts on losers")
    lines.append("- **s35_h210 vs both**: combined effect better or worse than sum of parts?")
    lines.append("- **sim%**: overall balance simulation — the bottom line")
    lines.append("")
    lines.append("## Files saved per config")
    lines.append("")
    lines.append("Each config saved:")
    lines.append("- `backtest_runs/backtest_results_{tag}.json` — per-trade data (312+ records)")
    lines.append("- `backtest_runs/backtest_auto_{timestamp}.txt` — full console output")

    return "\n".join(lines)


def main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("DRY RUN - configs to run:")
        print("")
        for cfg in CONFIGS:
            print(
                "  " + cfg["tag"].ljust(15) +
                " slope=" + str(cfg["slope_min"]).ljust(4) +
                " hold_fast=" + str(cfg["hold_fast_m"]) + "m" +
                " hold_swing=" + str(cfg["hold_swing_m"]) + "m"
            )
            print("    " + cfg["desc"])
        return

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    print("Param sweep: " + str(len(CONFIGS)) + " configs")
    print("Output dir: " + str(RUNS_DIR))
    print("")

    failed = []
    for cfg in CONFIGS:
        ok = run_config(cfg)
        if not ok:
            failed.append(cfg["tag"])
            print("  [ERROR] " + cfg["tag"] + " failed")

    # Collect results from saved JSONs
    print("")
    print("Collecting results...")
    all_metrics = {}
    for cfg in CONFIGS:
        tag       = cfg["tag"]
        json_path = RUNS_DIR / ("backtest_results_" + tag + ".json")
        if not json_path.exists():
            print("  [" + tag + "] JSON not found: " + json_path.name)
            continue
        with open(json_path, encoding="utf-8") as f:
            trades = json.load(f)
        all_metrics[tag] = compute_metrics(trades)
        m = all_metrics[tag]
        print(
            "  " + tag.ljust(15) +
            " n=" + str(m["n_total"]).rjust(3) +
            " WR=" + str(m["wr"]).rjust(5) + "%" +
            " PF=" + str(m["pf"]).rjust(5) +
            " sim=" + str(m["sim_gain"]).rjust(6) + "%" +
            " DD=" + str(m["max_dd"]).rjust(4) + "%" +
            " TIME=" + str(m["time_exit_pct"]).rjust(2) + "%"
        )

    # Build and save table
    table_md = build_table(all_metrics)
    stamp    = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_path = RUNS_DIR / ("sweep_" + stamp + ".md")
    out_path.write_text(table_md, encoding="utf-8")
    print("")
    print("Table saved -> " + out_path.name)

    if failed:
        print("Failed configs: " + str(failed))

    print("")
    print(table_md)


if __name__ == "__main__":
    main()
