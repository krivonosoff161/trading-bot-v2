"""
bt_sweep_scanner_d2b3.py - DRIFT scanner sweep with D2+B3 fixed.

Fixed filters for every config:
  BT_DRIFT_VOL_DECAY_MIN=0.9
  BT_DRIFT_ETH_BLOCK_HOURS=22,23,0,1

Grid:
  btc_vol_max in [None, 3.0]
  hold_min    in [60, 75, 90]
  tp1_k       in [0.4, 0.5, 0.6]

Output columns:
  btc_vol_max | hold_min | tp1_k | n | WR | PF | sim% | DD%

Notes:
- n / WR / PF are DRIFT-only metrics.
- sim% / DD% are computed from the full portfolio simulation, matching backtest_simulate.py.
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

BTC_VOL_MAX_VALUES = [None, 3.0]
HOLD_MIN_VALUES = [60, 75, 90]
TP1_K_VALUES = [0.4, 0.5, 0.6]

DRIFT_REGIMES = {"DRIFT", "WEAK_TREND"}
HOLD_SWING_M = 300
REFERENCE = {
    "btc_vol_max": None,
    "hold_min": 90,
    "tp1_k": 0.4,
    "n": 146,
    "wr": 89.0,
    "pf": 3.51,
    "sim": 144.1,
    "dd": 7.3,
}


def tag_for_config(btc_vol_max: float | None, hold_min: int, tp1_k: float) -> str:
    btc_tag = "none" if btc_vol_max is None else str(btc_vol_max).replace(".", "p")
    tp_tag = str(tp1_k).replace(".", "p")
    return f"scanner_d2b3_btc{btc_tag}_hold{hold_min}_tp{tp_tag}"


def compute_metrics(trades: list[dict]) -> dict:
    drift = [t for t in trades if t.get("regime") in DRIFT_REGIMES and t.get("outcome") in {"TP", "STOP", "TIME_EXIT"}]
    wins = [t for t in drift if t["outcome"] == "TP"]
    losses = [t for t in drift if t["outcome"] == "STOP"]
    decisive = len(wins) + len(losses)
    wr = round(len(wins) / decisive * 100, 1) if decisive else 0.0

    def r_mult(t: dict) -> float:
        sl_dist = abs(t["close"] - t["sl"])
        tp_dist = abs(t["tp"] - t["close"]) if t.get("tp") else sl_dist
        return tp_dist / sl_dist if sl_dist > 0 else 1.0

    gross_w = sum(r_mult(t) for t in wins)
    gross_l = len(losses)
    pf = round(gross_w / gross_l, 2) if gross_l > 0 else 99.0

    executed = [t for t in trades if t.get("executed") and t.get("pnl") is not None]
    balance = 1000.0 + sum(float(t["pnl"]) for t in executed)
    peak = 1000.0
    cur = 1000.0
    max_dd = 0.0
    for t in executed:
        cur += float(t["pnl"])
        if cur > peak:
            peak = cur
        dd = (peak - cur) / peak * 100
        if dd > max_dd:
            max_dd = dd
    sim_pct = round((balance - 1000.0) / 1000.0 * 100, 1)

    return {
        "n": len(drift),
        "wr": wr,
        "pf": pf,
        "sim": sim_pct,
        "dd": round(max_dd, 1),
    }


def run_config(btc_vol_max: float | None, hold_min: int, tp1_k: float) -> tuple[str, bool]:
    tag = tag_for_config(btc_vol_max, hold_min, tp1_k)
    env = os.environ.copy()
    env["BT_RUN_TAG"] = tag
    env["BT_FAST_HOLD_MIN"] = str(hold_min)
    env["BT_DRIFT_TP1_K"] = str(tp1_k)
    env["BT_DRIFT_VOL_DECAY_MIN"] = "0.9"
    env["BT_DRIFT_ETH_BLOCK_HOURS"] = "22,23,0,1"
    if btc_vol_max is None:
        env.pop("BT_DRIFT_BTC_VOL_MAX", None)
    else:
        env["BT_DRIFT_BTC_VOL_MAX"] = str(btc_vol_max)

    print(
        f"Running {tag}: btc_vol_max={btc_vol_max} hold={hold_min} tp1={tp1_k} "
        f"| vol_decay=0.9 eth_block=22,23,0,1"
    )
    result = subprocess.run(
        [sys.executable, str(BACKTEST_PY)],
        env=env,
        cwd=str(ROOT_DIR),
    )
    return tag, result.returncode == 0


def load_row(btc_vol_max: float | None, hold_min: int, tp1_k: float) -> dict | None:
    tag = tag_for_config(btc_vol_max, hold_min, tp1_k)
    json_path = RUNS_DIR / f"backtest_results_{tag}.json"
    if not json_path.exists():
        return None
    trades = json.loads(json_path.read_text(encoding="utf-8"))
    return {
        "btc_vol_max": btc_vol_max,
        "hold_min": hold_min,
        "tp1_k": tp1_k,
        "tag": tag,
        **compute_metrics(trades),
    }


def build_report(rows: list[dict], baseline: dict | None) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Scanner D2+B3 Sweep - {stamp}",
        "",
        "Fixed filters:",
        "- `BT_DRIFT_VOL_DECAY_MIN=0.9`",
        "- `BT_DRIFT_ETH_BLOCK_HOURS=22,23,0,1`",
        "",
        "Reference row requested by user:",
        f"- `btc_vol_max=None | hold=90 | tp1=0.4 | n=146 | WR=89% | PF=3.51 | sim=+144.1% | DD=7.3%`",
        "",
    ]
    if baseline:
        lines.append("Reproduced baseline in current run:")
        lines.append(
            f"- `btc_vol_max=None | hold=90 | tp1=0.4 | n={baseline['n']} | WR={baseline['wr']}% | "
            f"PF={baseline['pf']} | sim={baseline['sim']:+.1f}% | DD={baseline['dd']}%`"
        )
        lines.append("")
    lines.append("| btc_vol_max | hold_min | tp1_k | n | WR | PF | sim% | DD% |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in rows:
        btc_label = "None" if row["btc_vol_max"] is None else row["btc_vol_max"]
        marker = " <= baseline" if row["btc_vol_max"] is None and row["hold_min"] == 90 and row["tp1_k"] == 0.4 else ""
        lines.append(
            f"| {btc_label} | {row['hold_min']} | {row['tp1_k']} | {row['n']} | "
            f"{row['wr']} | {row['pf']} | {row['sim']:+.1f} | {row['dd']:.1f} |{marker}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    if "--dry-run" in sys.argv:
        for btc_vol_max, hold_min, tp1_k in itertools.product(BTC_VOL_MAX_VALUES, HOLD_MIN_VALUES, TP1_K_VALUES):
            print(tag_for_config(btc_vol_max, hold_min, tp1_k))
        return

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    combos = list(itertools.product(BTC_VOL_MAX_VALUES, HOLD_MIN_VALUES, TP1_K_VALUES))
    print(f"Scanner D2+B3 sweep: {len(combos)} configs")

    failed: list[str] = []
    rows: list[dict] = []
    for btc_vol_max, hold_min, tp1_k in combos:
        tag, ok = run_config(btc_vol_max, hold_min, tp1_k)
        if not ok:
            failed.append(tag)
            continue
        row = load_row(btc_vol_max, hold_min, tp1_k)
        if row is None:
            failed.append(tag)
            continue
        rows.append(row)
        print(
            f"  -> DRIFT n={row['n']:3d} WR={row['wr']:5.1f}% PF={row['pf']:5.2f} "
            f"sim={row['sim']:+6.1f}% DD={row['dd']:4.1f}%"
        )

    rows.sort(key=lambda r: (-r["pf"], -r["wr"], -r["sim"], r["dd"], -r["n"]))
    baseline = next((r for r in rows if r["btc_vol_max"] is None and r["hold_min"] == 90 and r["tp1_k"] == 0.4), None)

    report = build_report(rows, baseline)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_path = RUNS_DIR / f"sweep_scanner_d2b3_{stamp}.md"
    out_path.write_text(report, encoding="utf-8")

    print()
    print(report)
    print(f"Saved -> {out_path}")
    if failed:
        print(f"Failed: {failed}")


if __name__ == "__main__":
    main()
