# -*- coding: utf-8 -*-
"""Bounded OOS / shadow-forward evaluation for re-validation & OI survivors (research-only).

A survivor that cleared an in-sample, multiple-testing-deflated honest check is NOT edge. Before
anything, it must be re-tested on bars it did NOT contribute to at validation time. We have no
genuinely NEW bars yet, so the only available out-of-sample is a HELD-OUT TAIL of the same enriched
window (a pseudo-OOS, weaker than true forward) — this module runs exactly that, honestly labelled.

For each survivor it:
  * reads the candidate from the EXISTING registries (shadow_forward.json + oi_family_research.json) —
    no second brain/registry is created here;
  * splits the candle file by TIME: in-sample = the first (1-oos_frac), OOS = the held-out tail;
  * trades only signals whose ENTRY falls in the OOS region (indicator lookback uses prior bars, which
    is causal and how a live system would see it — no look-ahead);
  * logs the full metric block for BOTH slices: bar/trade count, net (sum+avg), gross, MFE/MAE,
    TP-before-SL, time-to-MFE, win-rate;
  * classifies the OOS result into one of five research-only classes:
        shadow_survived | shadow_failed_costs | shadow_failed_oos | shadow_underpowered | shadow_noise_floor

Nothing here is paper-ready or a trade signal. No money/order/live path. The OOS honest-bridge check is
deflated by the number of candidates in the pass (multiple testing). "shadow_survived" means only
"deserves a real forward watch", never "edge".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.exit_phase2 import _exit_modes, simulate_exit_mode
from src.research_lab.experiment import (
    choose_symbol_file,
    generate_signals,
    load_candles,
    simulate_trades,
)
from src.research_lab.param_schemas import executable_exit_params
from src.research_lab.paths import market_data_glob

FEES_BPS = 7.0
SLIP_BPS = 3.0
COST_PCT_PER_TRADE = (FEES_BPS + SLIP_BPS) / 100.0  # net = gross - this (percent points)
OOS_FRAC = 0.35          # held-out tail fraction
MIN_POWER = 10           # below this OOS trade count -> underpowered, no verdict
MIN_IS_FOR_SPLIT = 40    # need enough bars to split at all


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def collect_candidates(private_root: Path) -> list[dict[str, Any]]:
    """Survivor candidates from the EXISTING derived registries (deduped by symbol|tf|family|exit)."""
    private_root = Path(private_root)
    derived = private_root / "state" / "derived"
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    # 1) re-validation / exit-phase2 survivors already registered as shadow_forward_candidate
    for uc, row in (_read_json(derived / "shadow_forward.json").get("by_uc_key") or {}).items():
        if not isinstance(row, dict):
            continue
        exit_name = str(row.get("recovered_exit") or "baseline")
        key = (str(row.get("symbol")), str(row.get("timeframe")), str(row.get("family")), exit_name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"uc_key": str(uc), "symbol": str(row.get("symbol") or ""),
                    "timeframe": str(row.get("timeframe") or ""), "family": str(row.get("family") or ""),
                    "exit": exit_name, "params": dict(row.get("params") or {}),
                    "source": str(row.get("source") or "shadow_forward")})

    # 2) OI-family honest-passed survivors (note: families that produce identical trades dedupe here)
    for row in (_read_json(derived / "oi_family_research.json").get("rows") or []):
        if not isinstance(row, dict) or row.get("hard_status") != "PAPER_FORWARD_READY":
            continue
        key = (str(row.get("symbol")), str(row.get("timeframe")), str(row.get("family")), "baseline")
        if key in seen:
            continue
        seen.add(key)
        out.append({"uc_key": "", "symbol": str(row.get("symbol") or ""),
                    "timeframe": str(row.get("timeframe") or ""), "family": str(row.get("family") or ""),
                    "exit": "baseline", "params": {}, "source": "oi_family_honest"})
    return out


def _simulate(candles: list[dict[str, Any]], signals: list[dict[str, Any]], family: str,
              params: dict[str, Any], exit_name: str) -> list[dict[str, Any]]:
    """Run the candidate's own exit on a set of signals (no look-ahead — reuses the audited sims)."""
    if not signals:
        return []
    if exit_name and exit_name != "baseline":
        mode = dict(_exit_modes(params)).get(exit_name)
        if mode is not None:
            return simulate_exit_mode(candles, signals, params, mode, fees_bps=FEES_BPS, slip_bps=SLIP_BPS)
    return simulate_trades(candles, signals, params, fees_bps=FEES_BPS, slippage_bps=SLIP_BPS)


def _metrics(trades: list[dict[str, Any]], bar_count: int) -> dict[str, Any]:
    n = len(trades)
    base = {"bar_count": int(bar_count), "n_trades": n, "net_sum_pct": 0.0, "avg_net_pct": 0.0,
            "avg_gross_pct": 0.0, "avg_mfe_pct": 0.0, "avg_mae_pct": 0.0, "tp_before_sl": 0,
            "sl_before_tp": 0, "avg_time_to_mfe": 0.0, "win_rate": 0.0}
    if not n:
        return base
    nets = [float(t.get("net_pct") or 0.0) for t in trades]
    mfes = [float(t.get("mfe_pct") or 0.0) for t in trades]
    maes = [float(t.get("mae_pct") or 0.0) for t in trades]
    ttm = [int(t.get("time_to_mfe") or 0) for t in trades]
    base.update({
        "net_sum_pct": round(sum(nets), 4), "avg_net_pct": round(sum(nets) / n, 4),
        "avg_gross_pct": round(sum(nets) / n + COST_PCT_PER_TRADE, 4),
        "avg_mfe_pct": round(sum(mfes) / n, 4), "avg_mae_pct": round(sum(maes) / n, 4),
        "tp_before_sl": sum(1 for t in trades if t.get("tp_before_sl") is True),
        "sl_before_tp": sum(1 for t in trades if t.get("tp_before_sl") is False),
        "avg_time_to_mfe": round(sum(ttm) / n, 2), "win_rate": round(sum(1 for x in nets if x > 0) / n, 4)})
    return base


def _oos_bridge_status(cand: dict[str, Any], oos_trades: list[dict[str, Any]], n_trials: int) -> str:
    from src.research_lab.hard_validation_contract import CandidateForValidation
    from src.research_lab.honest_backtest_bridge import run_validation
    c = CandidateForValidation.from_dict({
        "candidate_id": f"oos::{cand['symbol']}::{cand['timeframe']}::{cand['family']}::{cand['exit']}",
        "source_run_id": "shadow_oos", "symbol": cand["symbol"], "normalized_symbol": cand["symbol"],
        "timeframe": cand["timeframe"], "strategy_id": cand["family"], "params": cand.get("params") or {},
        "fees_bps": FEES_BPS, "slippage_bps": SLIP_BPS, "lite_status": "FORWARD_PAPER",
        "metrics": {"n_trades": len(oos_trades), "runtime": {"n_variants_evaluated": int(max(1, n_trials))}},
        "trades": [{"net_pct": float(t.get("net_pct") or 0.0)} for t in oos_trades]})
    return str(run_validation(c, Path("."), dry_run=True).get("hard_status") or "")


def _classify(is_m: dict[str, Any], oos_m: dict[str, Any], bridge: str) -> tuple[str, str]:
    n = oos_m["n_trades"]
    if n == 0:
        return "shadow_underpowered", "no forward signals in the held-out tail"
    if n < MIN_POWER:
        return "shadow_underpowered", f"only {n} OOS trades (< {MIN_POWER}) - not powered for a verdict"
    net, gross = oos_m["avg_net_pct"], oos_m["avg_gross_pct"]
    if net <= 0:
        if gross > 0:
            return "shadow_failed_costs", f"OOS gross +{gross:.3f}% but net {net:.3f}% - costs eat the edge"
        return "shadow_failed_oos", f"OOS net {net:.3f}% (gross {gross:.3f}%) - no edge out of sample"
    if bridge == "PAPER_FORWARD_READY" and is_m.get("avg_net_pct", 0.0) > 0:
        return "shadow_survived", f"OOS net +{net:.3f}% over {n} trades, IS-consistent, deflated-bridge pass"
    return "shadow_noise_floor", (f"OOS net +{net:.3f}% but bridge={bridge or 'n/a'} / IS sign weak - "
                                  "not distinguishable from noise")


def evaluate_candidate(private_root: Path, cand: dict[str, Any], *, n_trials: int,
                       oos_frac: float = OOS_FRAC) -> dict[str, Any]:
    private_root = Path(private_root)
    path = choose_symbol_file(market_data_glob(private_root, cand["timeframe"]), cand["symbol"],
                              timeframe=cand["timeframe"])
    if not path:
        return {**_id(cand), "skipped": "no_candles"}
    candles = load_candles(path)
    if len(candles) < MIN_IS_FOR_SPLIT:
        return {**_id(cand), "skipped": f"too_few_bars ({len(candles)})"}
    cut = int(len(candles) * (1.0 - oos_frac))
    params = dict(cand.get("params") or {}) or executable_exit_params(cand["family"])
    signals = generate_signals(candles, cand["family"], params)
    is_sigs = [s for s in signals if int(s["idx"]) < cut]
    oos_sigs = [s for s in signals if int(s["idx"]) >= cut]
    is_trades = _simulate(candles, is_sigs, cand["family"], params, cand["exit"])
    oos_trades = _simulate(candles, oos_sigs, cand["family"], params, cand["exit"])
    is_m = _metrics(is_trades, cut)
    oos_m = _metrics(oos_trades, len(candles) - cut)
    bridge = _oos_bridge_status(cand, oos_trades, n_trials) if oos_m["n_trades"] >= MIN_POWER else ""
    cls, reason = _classify(is_m, oos_m, bridge)
    return {**_id(cand), "params": params, "oos_window_bars": len(candles) - cut,
            "in_sample": is_m, "oos": oos_m, "oos_bridge_status": bridge,
            "outcome_class": cls, "failure_reason": reason, "paper_forward_ready": False}


def _id(cand: dict[str, Any]) -> dict[str, Any]:
    return {"uc_key": cand.get("uc_key", ""), "symbol": cand["symbol"], "timeframe": cand["timeframe"],
            "family": cand["family"], "exit": cand["exit"], "source": cand.get("source", "")}


def run_shadow_oos(private_root: Path, *, oos_frac: float = OOS_FRAC) -> list[dict[str, Any]]:
    cands = collect_candidates(Path(private_root))
    n_trials = max(1, len(cands))
    return [evaluate_candidate(Path(private_root), c, n_trials=n_trials, oos_frac=oos_frac) for c in cands]


def summarize_shadow_oos(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if "outcome_class" in r]
    by_class: dict[str, int] = {}
    for r in scored:
        by_class[r["outcome_class"]] = by_class.get(r["outcome_class"], 0) + 1
    survived = [_id(r) for r in scored if r["outcome_class"] == "shadow_survived"]
    return {"evaluated": len(scored), "skipped": len(rows) - len(scored),
            "by_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
            "survived": survived, "all_research_only": all(not r.get("paper_forward_ready") for r in rows),
            "note": "held-out-tail pseudo-OOS (not genuinely new bars); shadow_survived != edge"}


def write_snapshot(private_root: Path, rows: list[dict[str, Any]]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "shadow_oos.json"
    payload = {"schema": "shadow_oos.v1",
               "disclaimer": "Bounded OOS on a HELD-OUT TAIL of the same enriched window (pseudo-OOS, "
                             "NOT genuinely new forward data). No look-ahead, honest-bridge deflated by "
                             "the pass size. Nothing is paper-ready; shadow_survived means 'deserves a "
                             "real forward watch', never edge. No money/order/live path.",
               "summary": summarize_shadow_oos(rows), "rows": rows}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> None:
    import argparse
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="Bounded OOS / shadow-forward for survivors (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--oos-frac", type=float, default=OOS_FRAC)
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    if args.plan:
        cands = collect_candidates(Path(args.private_root))
        print(json.dumps({"candidates": [_id(c) for c in cands]}, ensure_ascii=False, indent=2))
        return
    rows = run_shadow_oos(Path(args.private_root), oos_frac=args.oos_frac)
    print(json.dumps(summarize_shadow_oos(rows), ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), rows))


if __name__ == "__main__":
    main()
