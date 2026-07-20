# -*- coding: utf-8 -*-
"""Exit Phase-2 — dynamic exit modes the fixed-barrier sim could not test (research-only re-sim).

T3-A proved wrong_exit is real but could only test FIXED barriers (stop/take/hold). This module adds
the dynamic exits, on the SAME entry signals, with NO look-ahead — the stop/take level used to judge
bar j is computed from bars strictly BEFORE j (running high/low through j-1), then updated AFTER bar j:

  * trailing      — stop trails the running favorable extreme by trail_pct;
  * break_even    — move the stop to entry once MFE clears be_trigger_pct;
  * time_decay    — the stop ratchets toward entry as the hold elapses (punish stagnation);
  * partial_tp    — scale out half at tp1, ride the rest to tp2 / break-even / timeout;
  * early_tp      — a closer fixed take (the T3-A mean-reversion fix);
  * hold_long     — a longer fixed hold.

mean_reversion_fade first. Bounded (plan -> smoke -> capped). EVERY mode's net is logged, not just the
best. The best-of-grid choice is in-sample and DEFLATED by the grid size when re-validated through the
honest bridge. Output classes: exit_recovered_candidate / needs_forward_only / still_bad / thin_noise.
NOTHING here is paper-ready or a trade signal; no money/order/live path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.experiment import (
    choose_symbol_file,
    finalize_trade,
    generate_signals,
    load_candles,
)
from src.research_lab.paths import market_data_glob
from src.research_lab.simulator_contract import (
    build_cost_ledger,
    build_trade_quantity_ledger,
    legacy_fixture_manifest,
    reconcile_partial_fills,
)
from src.research_lab.trade_path_diagnostics import (
    _index_run_results,
    _load_rejected_uc,
    _trade_path_facts,
    classify_subreason,
    oi_micro_families,
)

FEES_BPS = 7.0
SLIP_BPS = 3.0
COST_PCT = 0.1
RECOVER_MARGIN = 0.05
MIN_TRADES_RECOVER = 5
TARGET_FAMILY = "mean_reversion_fade"


def _exit_modes(params: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Baseline + the dynamic/fixed exit grid around the candidate's own stop/take/hold."""
    stop = float(params.get("stop_pct") or 0.0) or 1.0
    take = float(params.get("take_pct") or 0.0) or (stop * 2)
    hold = int(params.get("hold_bars") or 5)
    return [
        ("baseline", {"kind": "fixed"}),
        ("early_tp", {"kind": "fixed", "take_pct": round(take * 0.5, 4)}),
        ("trailing", {"kind": "trailing", "trail_pct": round(stop, 4)}),
        ("trailing_tight", {"kind": "trailing", "trail_pct": round(stop * 0.5, 4)}),
        ("break_even", {"kind": "break_even", "be_trigger_pct": round(stop, 4)}),
        ("time_decay", {"kind": "time_decay"}),
        ("partial_tp", {"kind": "partial", "tp1_pct": round(take * 0.5, 4), "tp2_pct": round(take, 4)}),
        ("hold_long", {"kind": "fixed", "hold_bars": hold * 2}),
    ]


def _levels(entry: float, pct: float, *, long_: bool) -> float | None:
    if pct <= 0:
        return None
    return entry * (1 + pct / 100) if long_ else entry * (1 - pct / 100)


def _scan(candles: list[dict[str, Any]], idx: int, cap: int, entry: float, side: str,
          stop_pct: float, take_pct: float, mode: dict[str, Any]) -> tuple[float, str, int]:
    """Single-exit scan (trailing / break_even / time_decay / fixed). No look-ahead: the stop/take
    for bar j is set from bars < j, updated only AFTER bar j is judged."""
    long_ = side == "long"
    kind = mode["kind"]
    take_pct = float(mode.get("take_pct", take_pct))
    init_stop = _levels(entry, stop_pct, long_=not long_) if stop_pct > 0 else None
    take = _levels(entry, take_pct, long_=long_)
    stop = init_stop
    water = entry  # running favorable extreme (high for long, low for short)
    span = max(1, cap - idx)
    for j in range(idx, cap + 1):
        hi, lo = float(candles[j]["high"]), float(candles[j]["low"])
        if stop is not None and ((long_ and lo <= stop) or (not long_ and hi >= stop)):
            return stop, ("trail" if kind == "trailing" else "stop"), j
        if take is not None and ((long_ and hi >= take) or (not long_ and lo <= take)):
            return take, "take", j
        # update the dynamic stop for the NEXT bar from data through bar j (no intra-bar look-ahead)
        water = max(water, hi) if long_ else min(water, lo)
        if kind == "trailing":
            trail = _levels(water, mode["trail_pct"], long_=not long_)
            stop = max(init_stop or trail, trail) if long_ else min(init_stop or trail, trail)
        elif kind == "break_even":
            be = _levels(entry, mode["be_trigger_pct"], long_=long_)
            if be is not None and ((long_ and water >= be) or (not long_ and water <= be)):
                stop = entry
        elif kind == "time_decay":
            d = stop_pct * max(0.0, 1 - (j - idx) / span)
            stop = _levels(entry, d, long_=not long_)
    return float(candles[cap]["close"]), "time_exit", cap


def _partial_net(candles: list[dict[str, Any]], idx: int, cap: int, entry: float, side: str,
                 stop_pct: float, mode: dict[str, Any], cost_pct: float) -> dict[str, Any] | None:
    """Scale out half at tp1, move the rest to break-even, ride to tp2 / BE-stop / timeout."""
    long_ = side == "long"
    direction = 1 if long_ else -1
    tp1 = _levels(entry, mode["tp1_pct"], long_=long_)
    tp2 = _levels(entry, mode["tp2_pct"], long_=long_)
    stop = _levels(entry, stop_pct, long_=not long_) if stop_pct > 0 else None
    filled1 = False
    for j in range(idx, cap + 1):
        hi, lo = float(candles[j]["high"]), float(candles[j]["low"])
        if not filled1:
            if stop is not None and ((long_ and lo <= stop) or (not long_ and hi >= stop)):
                return _mk_partial(candles, idx, j, side, entry, stop, stop, 0.0, cost_pct, "stop")
            if tp1 is not None and ((long_ and hi >= tp1) or (not long_ and lo <= tp1)):
                filled1, stop = True, entry  # half off, rest to break-even; rest trades from NEXT bar
                continue
        else:  # the remaining half: tp2 / break-even stop (no same-bar look-ahead on the fill bar)
            if tp2 is not None and ((long_ and hi >= tp2) or (not long_ and lo <= tp2)):
                return _mk_partial(candles, idx, j, side, entry, tp1, tp2, 0.5, cost_pct, "take")
            if stop is not None and ((long_ and lo <= stop) or (not long_ and hi >= stop)):
                return _mk_partial(candles, idx, j, side, entry, tp1, stop, 0.5, cost_pct, "partial_be")
    exitp = float(candles[cap]["close"])
    p1 = tp1 if filled1 else exitp
    frac = 0.5 if filled1 else 0.0
    _ = direction  # net computed inside _mk_partial
    return _mk_partial(candles, idx, cap, side, entry, p1, exitp, frac, cost_pct, "time_exit")


def _mk_partial(candles, idx, exit_idx, side, entry, p1, p2, frac1, cost_pct, outcome) -> dict[str, Any]:
    direction = 1 if side == "long" else -1
    r1 = direction * (p1 / entry - 1) * 100
    r2 = direction * (p2 / entry - 1) * 100
    ret = frac1 * r1 + (1 - frac1) * r2 if frac1 else r2
    net = ret - cost_pct * 100
    quantities = [float(frac1), 1.0 - float(frac1)] if frac1 else [1.0]
    prices = [float(p1), float(p2)] if frac1 else [float(p2)]
    reconciliation = reconcile_partial_fills(
        1.0,
        [
            {"quantity": quantity, "price": price, "cost_pct": cost_pct * 100.0}
            for quantity, price in zip(quantities, prices)
        ],
        entry_price=float(entry), side=str(side),
    )
    if abs(float(reconciliation["net_return_pct"]) - net) > 1e-9:
        raise ValueError("partial fill reconciliation does not match trade return")
    return {"entry_ts": candles[idx]["ts"], "exit_ts": candles[exit_idx]["ts"], "side": side,
            "gross_pct": round(ret, 4), "net_pct": round(net, 4), "outcome": outcome,
            "mfe_pct": round(max(0.0, r2, r1), 4),
            "mae_pct": 0.0, "capture_of_mfe": 0.0,
            "partial_exit_fraction": float(frac1),
            "partial_leg_prices": prices,
            "fill_reconciliation": reconciliation}


def simulate_exit_mode(candles: list[dict[str, Any]], signals: list[dict[str, Any]],
                       params: dict[str, Any], mode: dict[str, Any], *, fees_bps: float = FEES_BPS,
                       slip_bps: float = SLIP_BPS,
                       require_complete_horizon: bool = False) -> list[dict[str, Any]]:
    """Re-simulate the SAME signals under one dynamic/fixed exit mode (no look-ahead)."""
    hold = int(mode.get("hold_bars", params.get("hold_bars", 5)))
    stop_pct = float(params.get("stop_pct") or 0.0)
    take_pct = float(params.get("take_pct") or 0.0)
    cost_pct = (fees_bps + slip_bps) / 10000.0
    trades: list[dict[str, Any]] = []
    for sig in signals:
        idx = int(sig["idx"])
        horizon_end = idx + hold
        cap = min(horizon_end, len(candles) - 1)
        if idx >= len(candles) or cap <= idx:
            continue
        entry = float(candles[idx]["open"])
        if entry <= 0:
            continue
        if mode["kind"] == "partial":
            t = _partial_net(candles, idx, cap, entry, str(sig["side"]), stop_pct, mode, cost_pct)
            if t:
                if require_complete_horizon and t.get("outcome") == "time_exit" and horizon_end >= len(candles):
                    continue
                manifest = legacy_fixture_manifest()
                t.update({
                    "simulator_manifest": manifest,
                    "simulator_model_id": manifest["simulator_model_id"],
                    "simulator_evidence_tier": manifest["evidence_tier"],
                    "unsupported_simulator_dimensions": list(manifest["unsupported_dimensions"]),
                    "cost_ledger": build_cost_ledger(
                        fees_bps=fees_bps, slippage_bps=slip_bps,
                    ),
                    "quantity_ledger": build_trade_quantity_ledger(
                        closed_legs=(
                            (float(t["partial_exit_fraction"]), 1.0 - float(t["partial_exit_fraction"]))
                            if float(t.get("partial_exit_fraction") or 0.0) > 0
                            else (1.0,)
                        ),
                    ),
                })
                trades.append(t)
            continue
        exit_price, outcome, exit_idx = _scan(candles, idx, cap, entry, str(sig["side"]),
                                              stop_pct, take_pct, mode)
        if require_complete_horizon and outcome == "time_exit" and horizon_end >= len(candles):
            continue
        trades.append(finalize_trade(candles, idx, exit_idx, str(sig["side"]), entry,
                                     exit_price, outcome, sig, cost_pct,
                                     simulator_manifest=legacy_fixture_manifest(),
                                     cost_ledger=build_cost_ledger(
                                         fees_bps=fees_bps, slippage_bps=slip_bps,
                                     )))
    return trades


def _avg_net(trades: list[dict[str, Any]]) -> float:
    nets = [float(t.get("net_pct") or 0.0) for t in trades]
    return round(sum(nets) / len(nets), 4) if nets else 0.0


def _bridge_status(symbol: str, tf: str, family: str, params: dict[str, Any],
                   trades: list[dict[str, Any]], n_trials: int) -> str:
    """Honest-bridge verdict on the best mode's trades, deflated by the exit-grid size (in-sample)."""
    from src.research_lab.hard_validation_contract import (
        CONTRACT_VERSION, CandidateForValidation, trade_evidence_hash,
    )
    from src.research_lab.honest_backtest_bridge import run_validation
    manifest = legacy_fixture_manifest()
    evidence = [dict(t) for t in trades]
    cand = CandidateForValidation.from_dict({
        "contract_version": CONTRACT_VERSION,
        "candidate_id": f"exit2::{symbol}::{tf}::{family}", "source_run_id": "exit_phase2",
        "symbol": symbol, "normalized_symbol": symbol, "timeframe": tf, "strategy_id": family,
        "params": params, "fees_bps": FEES_BPS, "slippage_bps": SLIP_BPS, "lite_status": "FORWARD_PAPER",
        "metrics": {"n_trades": len(trades), "data_fingerprint": "selection_only:exit_phase2",
                    "returns_basis": "net_pct", "costs_applied": True,
                    "validation_epoch": {"schema": "ValidationEpoch.v1", "evidence_stage": "selection_only",
                        "selection_data_fingerprint": "selection_only:exit_phase2",
                        "selection_evidence_hash": trade_evidence_hash(evidence),
                        "selection_evidence": evidence,
                        "evaluation_data_fingerprint": "", "evaluation_evidence_hash": "",
                        "hypothesis_frozen_at": "", "evaluation_started_at": ""},
                    "runtime": {"n_variants_evaluated": int(n_trials)},
                    "simulator_manifest": manifest,
                    "simulator_model_id": manifest["simulator_model_id"],
                    "unsupported_simulator_dimensions": manifest["unsupported_dimensions"]},
        "trades": evidence,
        "simulator_manifest": manifest,
        "unsupported_simulator_dimensions": manifest["unsupported_dimensions"]})
    return str(run_validation(cand, Path("."), dry_run=True).get("hard_status") or "")


def recover_with_phase2(private_root: Path, *, symbol: str, timeframe: str, family: str,
                        params: dict[str, Any], validate: bool = True) -> dict[str, Any] | None:
    """Re-sim baseline + the exit-mode grid on the same signals; classify the best mode honestly."""
    path = choose_symbol_file(market_data_glob(private_root, timeframe), symbol, timeframe=timeframe)
    if not path:
        return {"symbol": symbol, "timeframe": timeframe, "family": family, "skipped": "no_candles"}
    candles = load_candles(path)
    signals = generate_signals(candles, family, params)
    if not signals:
        return {"symbol": symbol, "timeframe": timeframe, "family": family, "skipped": "no_signals"}
    modes = _exit_modes(params)
    variants = {name: simulate_exit_mode(candles, signals, params, m) for name, m in modes}
    baseline = variants["baseline"]
    base_net, n = _avg_net(baseline), len(baseline)
    others = {k: v for k, v in variants.items() if k != "baseline"}
    best = max(others, key=lambda k: _avg_net(others[k]))
    best_net = _avg_net(others[best])
    improved = best_net > COST_PCT and best_net > base_net + RECOVER_MARGIN
    if not improved:
        cls = "still_bad"
    elif n < MIN_TRADES_RECOVER:
        cls = "thin_noise"
    else:
        status = _bridge_status(symbol, timeframe, family, params, others[best], len(modes)) if validate else ""
        cls = "needs_forward_only" if status == "PAPER_FORWARD_READY" else "exit_recovered_candidate"
    return {
        "symbol": symbol, "timeframe": timeframe, "family": family, "baseline_net": base_net,
        "best_mode": best, "best_net": best_net, "n_trades": n, "outcome_class": cls,
        "all_modes": {name: _avg_net(v) for name, v in variants.items()},  # every mode logged, not just best
        "paper_forward_ready": False,
    }


def _mean_rev_wrong_exit(private_root: Path, *, limit: int | None) -> list[dict[str, Any]]:
    oi_micro = oi_micro_families()
    cache: dict[str, dict[str, dict[str, Any]]] = {}
    out: list[dict[str, Any]] = []
    for r in _load_rejected_uc(private_root):
        if str(r.get("family") or "") != TARGET_FAMILY:
            continue
        label = str(r.get("run_dir_label") or "")
        if label not in cache:
            cache[label] = _index_run_results(private_root, label)
        result = cache[label].get(str(r.get("params_hash") or ""))
        if not result:
            continue
        if classify_subreason(_trade_path_facts(result), str(r.get("hard_status") or ""),
                              TARGET_FAMILY, oi_micro) != "wrong_exit":
            continue
        out.append({"uc_key": str(r.get("uc_key") or ""), "symbol": str(r.get("symbol") or ""),
                    "timeframe": str(r.get("timeframe") or ""), "params": dict(result.get("params") or {})})
    return out[:limit] if limit else out


def plan_exit_phase2(private_root: Path) -> dict[str, Any]:
    items = _mean_rev_wrong_exit(private_root, limit=None)
    by_tf: dict[str, int] = {}
    for it in items:
        by_tf[it["timeframe"]] = by_tf.get(it["timeframe"], 0) + 1
    return {"family": TARGET_FAMILY, "wrong_exit_with_params": len(items), "by_timeframe": by_tf,
            "exit_modes": [n for n, _ in _exit_modes({"stop_pct": 1, "take_pct": 2, "hold_bars": 5})]}


def run_exit_phase2(private_root: Path, *, limit: int | None = 60,
                    validate: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for it in _mean_rev_wrong_exit(private_root, limit=limit):
        res = recover_with_phase2(private_root, symbol=it["symbol"], timeframe=it["timeframe"],
                                  family=TARGET_FAMILY, params=it["params"], validate=validate)
        if res:
            res["uc_key"] = it["uc_key"]
            rows.append(res)
    return rows


def summarize_exit_phase2(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if "outcome_class" in r]
    by_class: dict[str, int] = {}
    by_mode: dict[str, int] = {}
    for r in scored:
        by_class[r["outcome_class"]] = by_class.get(r["outcome_class"], 0) + 1
        if r["outcome_class"] in ("exit_recovered_candidate", "needs_forward_only"):
            by_mode[r["best_mode"]] = by_mode.get(r["best_mode"], 0) + 1
    return {
        "evaluated": len(scored), "skipped": len(rows) - len(scored),
        "by_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
        "best_mode_of_recovered": dict(sorted(by_mode.items(), key=lambda kv: -kv[1])),
        "recovered": by_class.get("exit_recovered_candidate", 0) + by_class.get("needs_forward_only", 0),
        "needs_forward_only": by_class.get("needs_forward_only", 0),
    }


def write_exit_phase2_snapshot(private_root: Path, rows: list[dict[str, Any]]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "exit_phase2.json"
    payload = {"schema": "exit_phase2.v1",
               "disclaimer": "Research-only dynamic-exit re-sim (no look-ahead). Recovered = improved "
                             "in-sample best-of-grid; needs_forward_only survived a deflated honest "
                             "check but is NOT paper-ready (forward shadow only). No money path.",
               "summary": summarize_exit_phase2(rows),
               "by_uc_key": {r["uc_key"]: r for r in rows if r.get("uc_key") and "outcome_class" in r}}
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
    ap = argparse.ArgumentParser(description="Exit Phase-2 dynamic-exit re-sim (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--no-validate", action="store_true", help="skip the honest-bridge deflation pass")
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    if args.plan:
        print(json.dumps(plan_exit_phase2(Path(args.private_root)), ensure_ascii=False, indent=2))
        return
    rows = run_exit_phase2(Path(args.private_root), limit=args.limit, validate=not args.no_validate)
    print(json.dumps(summarize_exit_phase2(rows), ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_exit_phase2_snapshot(Path(args.private_root), rows))


if __name__ == "__main__":
    main()
