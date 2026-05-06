"""
bt_sweep_scanner.py - sweep DRIFT scanner hypotheses.

Hypotheses:
1. BTC late-entry veto via BT_DRIFT_BTC_VOL_MAX
2. Shorter DRIFT FAST hold via BT_FAST_HOLD_MIN
3. Higher DRIFT TP1 via BT_DRIFT_TP1_K

Grid:
  btc_vol_max: [None, 4.0, 3.5, 3.0]
  hold_min:    [60, 75, 90]
  tp1_k:       [0.4, 0.5, 0.6]

Output:
  DRIFT-only table sorted by PF desc:
  btc_vol_max | hold_min | tp1_k | n | WR | PF | sim% | DD%
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent.parent
BACKTEST_PY = SCRIPT_DIR / "backtest_simulate.py"
RUNS_DIR = SCRIPT_DIR / "backtest_runs"

BTC_VOL_MAX_VALUES = [None, 4.0, 3.5, 3.0]
HOLD_MIN_VALUES = [60, 75, 90]
TP1_K_VALUES = [0.4, 0.5, 0.6]

START_BALANCE = 1000.0
NOTIONAL_RATIO = 1.5
DRIFT_REGIMES = {"DRIFT", "WEAK_TREND"}


def tag_for_config(btc_vol_max: float | None, hold_min: int, tp1_k: float) -> str:
    btc_tag = "none" if btc_vol_max is None else str(btc_vol_max).replace(".", "p")
    tp_tag = str(tp1_k).replace(".", "p")
    return f"scanner_btc{btc_tag}_hold{hold_min}_tp{tp_tag}"


def calc_pnl(outcome: str, side: str, entry: float, sl: float, tp: float, exit_price: float | None, balance: float) -> float:
    if entry <= 0:
        return 0.0
    notional = balance * NOTIONAL_RATIO
    direction = 1 if side == "buy" else -1
    sl_dist = abs(entry - sl)
    sl_pct = sl_dist / entry if entry > 0 else 0.0

    if outcome == "STOP":
        return -sl_pct * notional
    if outcome == "TP":
        tp_dist = abs(tp - entry)
        return (tp_dist / entry) * notional
    if outcome == "TIME_EXIT":
        if exit_price is None:
            return 0.0
        price_move = direction * (exit_price - entry)
        tp_dist = abs(tp - entry)
        price_move = max(-sl_dist, min(tp_dist, price_move))
        return (price_move / entry) * notional
    return 0.0


def compute_drift_metrics(trades: list[dict], hold_min: int) -> dict:
    drift = [t for t in trades if t.get("regime") in DRIFT_REGIMES and t.get("outcome") in {"TP", "STOP", "TIME_EXIT"}]
    wins = [t for t in drift if t["outcome"] == "TP"]
    losses = [t for t in drift if t["outcome"] == "STOP"]
    total_decisive = len(wins) + len(losses)
    wr = round(len(wins) / total_decisive * 100, 1) if total_decisive else 0.0

    def r_mult(t: dict) -> float:
        sl_dist = abs(t["close"] - t["sl"])
        tp_dist = abs(t["tp"] - t["close"]) if t.get("tp") else sl_dist
        return tp_dist / sl_dist if sl_dist > 0 else 1.0

    gross_w = sum(r_mult(t) for t in wins)
    gross_l = len(losses)
    pf = round(gross_w / gross_l, 2) if gross_l > 0 else 99.0

    balance = START_BALANCE
    peak = START_BALANCE
    max_dd = 0.0
    executed = 0
    pos_close_ms: dict[str, int] = {}

    for t in sorted(drift, key=lambda x: x.get("ts_ms", 0)):
        sym = t["symbol"]
        ts_ms = int(t.get("ts_ms", 0))
        if pos_close_ms.get(sym, 0) > ts_ms:
            continue

        elapsed_m = t.get("elapsed_m")
        if elapsed_m is not None:
            close_at = ts_ms + int(elapsed_m) * 60 * 1000
        elif t.get("style") == "FAST":
            close_at = ts_ms + hold_min * 60 * 1000
        else:
            close_at = ts_ms + 300 * 60 * 1000
        pos_close_ms[sym] = close_at

        pnl = calc_pnl(
            t["outcome"],
            t["side"],
            float(t["close"]),
            float(t["sl"]),
            float(t["tp"]),
            t.get("exit_price"),
            balance,
        )
        balance += pnl
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd:
            max_dd = dd
        executed += 1

    sim_pct = round((balance - START_BALANCE) / START_BALANCE * 100, 1)
    return {
        "n": len(drift),
        "wr": wr,
        "pf": pf,
        "sim": sim_pct,
        "dd": round(max_dd, 1),
        "executed": executed,
    }


def run_config(btc_vol_max: float | None, hold_min: int, tp1_k: float) -> tuple[str, bool]:
    tag = tag_for_config(btc_vol_max, hold_min, tp1_k)
    env = os.environ.copy()
    env["BT_RUN_TAG"] = tag
    env["BT_FAST_HOLD_MIN"] = str(hold_min)
    env["BT_DRIFT_TP1_K"] = str(tp1_k)
    if btc_vol_max is None:
        env.pop("BT_DRIFT_BTC_VOL_MAX", None)
    else:
        env["BT_DRIFT_BTC_VOL_MAX"] = str(btc_vol_max)

    print(f"Running {tag}: btc_vol_max={btc_vol_max} hold={hold_min} tp1={tp1_k}")
    result = subprocess.run(
        [sys.executable, str(BACKTEST_PY)],
        env=env,
        cwd=str(ROOT_DIR),
    )
    return tag, result.returncode == 0


def load_metrics(tag: str, hold_min: int) -> dict | None:
    json_path = RUNS_DIR / f"backtest_results_{tag}.json"
    if not json_path.exists():
        return None
    with open(json_path, encoding="utf-8") as f:
        trades = json.load(f)
    return compute_drift_metrics(trades, hold_min)


def build_report(rows: list[dict]) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Scanner DRIFT Sweep - {stamp}",
        "",
        "Reference baseline for comparison: `n=146, WR=89%, PF=3.51, sim=+144.1%, DD=6.3%` (DRIFT-only).",
        "",
        "| btc_vol_max | hold_min | tp1_k | n | WR | PF | sim% | DD% |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        btc_label = "None" if row["btc_vol_max"] is None else row["btc_vol_max"]
        lines.append(
            f"| {btc_label} | {row['hold_min']} | {row['tp1_k']} | {row['n']} | "
            f"{row['wr']} | {row['pf']} | {row['sim']:+.1f} | {row['dd']:.1f} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    if "--dry-run" in sys.argv:
        for btc_vol_max, hold_min, tp1_k in itertools.product(BTC_VOL_MAX_VALUES, HOLD_MIN_VALUES, TP1_K_VALUES):
            print(tag_for_config(btc_vol_max, hold_min, tp1_k))
        return

    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    failed: list[str] = []
    rows: list[dict] = []
    combos = list(itertools.product(BTC_VOL_MAX_VALUES, HOLD_MIN_VALUES, TP1_K_VALUES))
    print(f"Scanner DRIFT sweep: {len(combos)} configs")

    for btc_vol_max, hold_min, tp1_k in combos:
        tag, ok = run_config(btc_vol_max, hold_min, tp1_k)
        if not ok:
            failed.append(tag)
            continue
        metrics = load_metrics(tag, hold_min)
        if metrics is None:
            failed.append(tag)
            continue
        row = {
            "tag": tag,
            "btc_vol_max": btc_vol_max,
            "hold_min": hold_min,
            "tp1_k": tp1_k,
            **metrics,
        }
        rows.append(row)
        print(
            f"  -> n={row['n']:3d} WR={row['wr']:5.1f}% PF={row['pf']:5.2f} "
            f"sim={row['sim']:+6.1f}% DD={row['dd']:4.1f}%"
        )

    rows.sort(key=lambda r: (-r["pf"], -r["wr"], -r["sim"], r["dd"], -r["n"]))

    report = build_report(rows)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_path = RUNS_DIR / f"sweep_scanner_{stamp}.md"
    out_path.write_text(report, encoding="utf-8")

    print()
    print(report)
    print(f"Saved -> {out_path}")
    if failed:
        print(f"Failed: {failed}")


if __name__ == "__main__":
    main()
