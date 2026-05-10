"""
bt_sweep_drift.py — sweep for DRIFT quality improvements.

Tests 5 configs vs baseline:
  baseline  — current prod params (DRIFT TP1=0.4R, hold=150m, no vol cap)
  fix_tp1   — DRIFT TP1=0.8R (fix R/R: 0.4:1 breakeven 71% → 0.8:1 breakeven 56%)
  fix_hold  — DRIFT max_hold=90m (cut TIME_EXIT: all 16 live TE were >=124m)
  fix_vol   — DRIFT max vol_ratio=3.0 (vol>3x had WR 60% vs 73% at 1-1.5x)
  fix_all   — all three combined

Usage:
  python scripts/bt_sweep_drift.py
  python scripts/bt_sweep_drift.py --dry-run
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
    # 0. BASELINE: текущий прод (D2+B3+D3+FVG), TP1=0.4R, hold=75m
    {
        "tag": "BASE_prod",
        "desc": "Prod: D2+B3+D3+FVG, TP1=0.4R, hold=75m",
        "drift_tp1_k": 0.4, "drift_btc_vol_max": 3.0, "drift_vol_decay_min": 0.9,
        "drift_eth_block_hours": "22,23,0,1", "drift_hold_m": 75, "trending_require_fvg": "1",
    },
    # 1. TP1=0.5R: ранний фикс прибыли
    {
        "tag": "TP1_0.5R",
        "desc": "TP1=0.5R (был 0.4R), все фильтры prod",
        "drift_tp1_k": 0.5, "drift_btc_vol_max": 3.0, "drift_vol_decay_min": 0.9,
        "drift_eth_block_hours": "22,23,0,1", "drift_hold_m": 75, "trending_require_fvg": "1",
    },
    # 2. Ablation D2: выключаем vol_decay фильтр
    {
        "tag": "NO_D2_vol",
        "desc": "Без D2 (vol_decay выключен)",
        "drift_tp1_k": 0.4, "drift_btc_vol_max": 3.0, "drift_vol_decay_min": 0.0,
        "drift_eth_block_hours": "22,23,0,1", "drift_hold_m": 75, "trending_require_fvg": "1",
    },
    # 3. Ablation B3: выключаем блок ночных часов ETH
    {
        "tag": "NO_B3_ETH",
        "desc": "Без B3 (ETH ночные часы не блокируются)",
        "drift_tp1_k": 0.4, "drift_btc_vol_max": 3.0, "drift_vol_decay_min": 0.9,
        "drift_eth_block_hours": "", "drift_hold_m": 75, "trending_require_fvg": "1",
    },
    # 4. Ablation D3: выключаем BTC vol cap
    {
        "tag": "NO_D3_BTCvol",
        "desc": "Без D3 (BTC vol>3.0 не блокирует)",
        "drift_tp1_k": 0.4, "drift_btc_vol_max": 999, "drift_vol_decay_min": 0.9,
        "drift_eth_block_hours": "22,23,0,1", "drift_hold_m": 75, "trending_require_fvg": "1",
    },
    # 5. Ablation FVG: выключаем FVG для TRENDING
    {
        "tag": "NO_FVG",
        "desc": "Без FVG фильтра (TRENDING FAST входит везде)",
        "drift_tp1_k": 0.4, "drift_btc_vol_max": 3.0, "drift_vol_decay_min": 0.9,
        "drift_eth_block_hours": "22,23,0,1", "drift_hold_m": 75, "trending_require_fvg": "0",
    },
    # 6. Грязный бэктест: все фильтры выключены
    {
        "tag": "NO_FILTERS",
        "desc": "Без всех фильтров D2+B3+D3+FVG (dirty baseline)",
        "drift_tp1_k": 0.4, "drift_btc_vol_max": 999, "drift_vol_decay_min": 0.0,
        "drift_eth_block_hours": "", "drift_hold_m": 75, "trending_require_fvg": "0",
    },
    # 7. Hold=90m: дольше держим
    {
        "tag": "HOLD_90m",
        "desc": "Hold=90m вместо 75m, prod фильтры",
        "drift_tp1_k": 0.4, "drift_btc_vol_max": 3.0, "drift_vol_decay_min": 0.9,
        "drift_eth_block_hours": "22,23,0,1", "drift_hold_m": 90, "trending_require_fvg": "1",
    },
    # 8. Комбо: TP1=0.5R + hold=75m
    {
        "tag": "AGG_0.5R_75m",
        "desc": "TP1=0.5R + Hold=75m, prod фильтры",
        "drift_tp1_k": 0.5, "drift_btc_vol_max": 3.0, "drift_vol_decay_min": 0.9,
        "drift_eth_block_hours": "22,23,0,1", "drift_hold_m": 75, "trending_require_fvg": "1",
    },
    # 9. Vol фильтры без FVG
    {
        "tag": "VOL_noFVG",
        "desc": "D2+B3+D3 включены, FVG выключен",
        "drift_tp1_k": 0.4, "drift_btc_vol_max": 3.0, "drift_vol_decay_min": 0.9,
        "drift_eth_block_hours": "22,23,0,1", "drift_hold_m": 75, "trending_require_fvg": "0",
    },
]


def compute_metrics(
    trades: list,
    days_back: int = 63,
    fee_rt_pct: float = 0.10,
    position_usd: float = 100.0,
) -> dict:
    if not trades:
        return {}
    trades = [t for t in trades if t.get("executed", True) and "pnl" in t]
    if not trades:
        return {}

    tp_t    = [t for t in trades if t["outcome"] == "TP"]
    sl_t    = [t for t in trades if t["outcome"] == "STOP"]
    te_t    = [t for t in trades if t["outcome"] == "TIME_EXIT"]
    n_total = len(trades)

    denom      = len(tp_t) + len(sl_t)
    wr         = round(len(tp_t) / denom * 100, 1) if denom else 0.0
    honest_wr  = round(len(tp_t) / n_total * 100, 1) if n_total else 0.0

    # PF by exit_r — R-normalized, independent of position size/instrument
    gw_r = sum(t["exit_r"] for t in tp_t if t.get("exit_r") is not None)
    gl_r = abs(sum(t["exit_r"] for t in sl_t if t.get("exit_r") is not None))
    pf   = round(gw_r / gl_r, 2) if gl_r > 0 else 99.0

    # Sim/DD by pnl (USD) with round-trip fee deduction
    fee_per_trade = position_usd * fee_rt_pct / 100
    balance = 1000.0
    peak    = balance
    max_dd  = 0.0
    for t in trades:
        balance += t["pnl"] - fee_per_trade
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd:
            max_dd = dd

    final = 1000.0
    for t in trades:
        final += t["pnl"] - fee_per_trade
    sim_gain = round((final - 1000.0) / 1000.0 * 100, 1)

    def avg(lst, key):
        vals = [t.get(key) for t in lst if t.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    # DRIFT-specific breakdown
    drift_t  = [t for t in trades if t.get("regime") == "DRIFT"]
    drift_tp = [t for t in drift_t if t["outcome"] == "TP"]
    drift_sl = [t for t in drift_t if t["outcome"] == "STOP"]
    drift_te = [t for t in drift_t if t["outcome"] == "TIME_EXIT"]
    drift_denom = len(drift_tp) + len(drift_sl)
    drift_wr = round(len(drift_tp) / drift_denom * 100, 1) if drift_denom else 0.0
    dgw_r    = sum(t["exit_r"] for t in drift_tp if t.get("exit_r") is not None)
    dgl_r    = abs(sum(t["exit_r"] for t in drift_sl if t.get("exit_r") is not None))
    drift_pf = round(dgw_r / dgl_r, 2) if dgl_r > 0 else 99.0
    drift_avg_r = avg(drift_t, "exit_r")

    # Per-regime
    by_regime = {}
    for reg in ("TRENDING", "DRIFT", "RANGING"):
        rt   = [t for t in trades if t.get("regime") == reg]
        rtp  = [t for t in rt if t["outcome"] == "TP"]
        rsl  = [t for t in rt if t["outcome"] == "STOP"]
        rte  = [t for t in rt if t["outcome"] == "TIME_EXIT"]
        rdenom = len(rtp) + len(rsl)
        rgw_r  = sum(t["exit_r"] for t in rtp if t.get("exit_r") is not None)
        rgl_r  = abs(sum(t["exit_r"] for t in rsl if t.get("exit_r") is not None))
        rpf    = round(rgw_r / rgl_r, 2) if rgl_r > 0 else 99.0
        by_regime[reg] = {
            "n":   len(rt),
            "wr":  round(len(rtp) / rdenom * 100, 1) if rdenom else 0.0,
            "pf":  rpf,
            "te":  len(rte),
        }

    return {
        "n":             n_total,
        "n_per_day":     round(n_total / days_back, 1),
        "n_tp":          len(tp_t),
        "n_sl":          len(sl_t),
        "n_te":          len(te_t),
        "wr":            wr,
        "honest_wr":     honest_wr,
        "pf":            pf,
        "sim":           sim_gain,
        "dd":            round(max_dd, 1),
        "te_pct":        round(len(te_t) / n_total * 100) if n_total else 0,
        "drift_n":       len(drift_t),
        "drift_wr":      drift_wr,
        "drift_pf":      drift_pf,
        "drift_avg_r":   drift_avg_r,
        "drift_te":      len(drift_te),
        "by_regime":     by_regime,
        "avg_exit_r_tp": avg(tp_t, "exit_r"),
        "avg_exit_r_sl": avg(sl_t, "exit_r"),
    }


def run_config(cfg: dict) -> bool:
    tag = cfg["tag"]
    print(f"\n{'='*60}")
    print(f"  {tag}  —  {cfg['desc']}")
    print(
        f"  tp1={cfg['drift_tp1_k']}R  hold={cfg['drift_hold_m']}m"
        f"  D2={cfg['drift_vol_decay_min']}  D3_btc={cfg['drift_btc_vol_max']}"
        f"  B3_eth={cfg['drift_eth_block_hours'] or 'off'}  FVG={cfg['trending_require_fvg']}"
    )
    print("=" * 60)

    env = os.environ.copy()
    env["BT_DRIFT_TP1_K"]           = str(cfg["drift_tp1_k"])
    env["BT_DRIFT_BTC_VOL_MAX"]     = str(cfg["drift_btc_vol_max"])
    env["BT_DRIFT_VOL_DECAY_MIN"]   = str(cfg["drift_vol_decay_min"])
    env["BT_DRIFT_ETH_BLOCK_HOURS"] = str(cfg["drift_eth_block_hours"])
    env["BT_FAST_HOLD_MIN"]         = str(cfg["drift_hold_m"])
    env["BT_TRENDING_REQUIRE_FVG"]  = str(cfg["trending_require_fvg"])
    env["BT_RUN_TAG"]               = tag

    result = subprocess.run(
        [sys.executable, str(BACKTEST_PY)],
        env=env,
        cwd=str(ROOT_DIR),
    )
    return result.returncode == 0


def build_table(all_metrics: dict) -> str:
    lines = ["# DRIFT Quality Sweep — " + datetime.now().strftime("%Y-%m-%d %H:%M"), ""]

    lines.append("## Configs")
    for cfg in CONFIGS:
        lines.append(f"- **{cfg['tag']}**: {cfg['desc']}")
    lines.append("")

    # ── Overall summary ──────────────────────────────────────────────────────
    lines.append("## Overall")
    lines.append("")
    lines.append("| Config | n/day | WR% | honest_WR% | PF(R) | sim% | DD% | TE% | TP | SL | TE |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for cfg in CONFIGS:
        m = all_metrics.get(cfg["tag"])
        if not m:
            lines.append(f"| {cfg['tag']} | — | — | — | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {cfg['tag']} | {m['n_per_day']} | {m['wr']} | {m['honest_wr']} | {m['pf']}"
            f" | {m['sim']} | {m['dd']} | {m['te_pct']}"
            f" | {m['n_tp']} | {m['n_sl']} | {m['n_te']} |"
        )
    lines.append("")

    # ── DRIFT-specific ───────────────────────────────────────────────────────
    lines.append("## DRIFT only")
    lines.append("")
    lines.append("| Config | DRIFT n | WR% | PF | avg_R | TE |")
    lines.append("|---|---|---|---|---|---|")
    for cfg in CONFIGS:
        m = all_metrics.get(cfg["tag"])
        if not m:
            lines.append(f"| {cfg['tag']} | — | — | — | — | — |")
            continue
        ar = m['drift_avg_r']
        avg_r_str = f"{ar:+.3f}" if ar is not None else "—"
        lines.append(
            f"| {cfg['tag']} | {m['drift_n']} | {m['drift_wr']} | {m['drift_pf']}"
            f" | {avg_r_str} | {m['drift_te']} |"
        )
    lines.append("")

    # ── By regime ────────────────────────────────────────────────────────────
    lines.append("## By regime (n / WR% / PF)")
    lines.append("")
    lines.append("| Config | TRENDING | DRIFT | RANGING |")
    lines.append("|---|---|---|---|")
    for cfg in CONFIGS:
        m = all_metrics.get(cfg["tag"])
        if not m:
            continue
        def rs(r):
            d = m["by_regime"].get(r, {})
            return f"{d.get('n','?')}/{d.get('wr','?')}%/{d.get('pf','?')}"
        lines.append(f"| {cfg['tag']} | {rs('TRENDING')} | {rs('DRIFT')} | {rs('RANGING')} |")
    lines.append("")

    # ── Reading guide ────────────────────────────────────────────────────────
    lines.append("## How to read")
    lines.append("")
    lines.append("- **fix_tp1**: DRIFT PF up + sim up = raise TP1 in prod")
    lines.append("- **fix_hold**: TE% down + DD down = shorten hold in prod")
    lines.append("- **fix_vol**: DRIFT n down + WR up = vol cap helps")
    lines.append("- **fix_all**: if better than each alone = combine all")
    lines.append("- Key trade-off: n/day vs PF (fewer signals = higher quality?)")
    lines.append("")

    return "\n".join(lines)


def main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("DRY RUN — configs:")
        for cfg in CONFIGS:
            print(
                f"  {cfg['tag']:20s}  tp1={cfg['drift_tp1_k']}R  hold={cfg['drift_hold_m']}m"
                f"  D2={cfg['drift_vol_decay_min']}  D3={cfg['drift_btc_vol_max']}"
                f"  FVG={cfg['trending_require_fvg']}"
            )
            print(f"    {cfg['desc']}")
        return

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"DRIFT quality sweep — {len(CONFIGS)} configs")

    failed = []
    for cfg in CONFIGS:
        if not run_config(cfg):
            failed.append(cfg["tag"])

    print("\nCollecting results...")
    all_metrics = {}
    for cfg in CONFIGS:
        tag       = cfg["tag"]
        json_path = RUNS_DIR / f"backtest_results_{tag}.json"
        if not json_path.exists():
            print(f"  [{tag}] JSON not found")
            continue
        with open(json_path, encoding="utf-8") as f:
            trades = json.load(f)
        all_metrics[tag] = compute_metrics(trades)
        m = all_metrics[tag]
        print(
            f"  {tag:12s}  n={m['n']:3d}  WR={m['wr']:5.1f}%  PF={m['pf']:5.2f}"
            f"  sim={m['sim']:+6.1f}%  DD={m['dd']:4.1f}%"
            f"  DRIFT: n={m['drift_n']} WR={m['drift_wr']}% PF={m['drift_pf']}"
        )

    table_md = build_table(all_metrics)
    stamp    = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_path = RUNS_DIR / f"sweep_drift_{stamp}.md"
    out_path.write_text(table_md, encoding="utf-8")
    print(f"\nSaved → {out_path.name}")
    if failed:
        print(f"Failed: {failed}")
    print()
    print(table_md)


if __name__ == "__main__":
    main()
