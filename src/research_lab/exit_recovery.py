# -*- coding: utf-8 -*-
"""T3-A: exit-recovery research for wrong_exit rejects (bounded re-simulation).

Hypothesis to test: a wrong_exit reject (the move happened — positive MFE — but the exit
gave it back) becomes economic under a DIFFERENT exit. For each candidate we regenerate the
SAME entry signals on the SAME local candles (no new edge, no look-ahead — finalize barrier
scan only uses bars from entry forward) and re-simulate a per-family grid of fixed-barrier
exits (earlier take-profit, tighter/looser timeout, asymmetric RR>=2). We compare the
re-simulated BASELINE (original exit) against the best variant, and report cost sensitivity.

It is research-only: it reads prepared candle files + run metrics, re-simulates in-process,
and returns tables. It writes nothing back, touches no farm loop / validator / setup card /
paper / money path, and produces NO promotion — only an exit_recovered_candidate research
label. Trailing-stop / break-even exits are NOT supported by simulate_trades (fixed-barrier
only) and are deferred to a phase-2 exit mode.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.research_lab.experiment import (
    choose_symbol_file,
    generate_signals,
    load_candles,
    simulate_trades,
)
from src.research_lab.paths import market_data_glob
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
FAMILY_PRIORITY = ("momentum_breakout", "bb_volume_fade", "mean_reversion_fade")
RECOVER_MARGIN = 0.05  # best variant must beat baseline net by at least this (pp) after cost
# A "recovered" claim on 1-2 trades is noise (and the best-of-grid choice is in-sample);
# require a minimum trade count so recovered means "worth honest re-validation", not "edge".
MIN_TRADES_RECOVER = 5


def _exit_grid(base: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Fixed-barrier exit variants around the candidate's own params (supported modes only)."""
    stop = float(base.get("stop_pct") or 0.0) or 1.0
    take = float(base.get("take_pct") or 0.0) or (stop * 2)
    hold = int(base.get("hold_bars") or 5)
    grid: list[tuple[str, dict[str, Any]]] = [("baseline", {})]
    # Earlier take-profit: capture the move sooner (the core wrong_exit fix).
    grid.append(("tp_half", {"take_pct": round(take * 0.5, 4)}))
    grid.append(("tp_0.66", {"take_pct": round(take * 0.66, 4)}))
    # Asymmetric RR>=2 around the original stop.
    grid.append(("rr2", {"take_pct": round(stop * 2, 4)}))
    grid.append(("rr3", {"take_pct": round(stop * 3, 4)}))
    # Tighter stop (cut the give-back faster) keeping RR>=2.
    grid.append(("stop_tight_rr2", {"stop_pct": round(stop * 0.6, 4), "take_pct": round(stop * 0.6 * 2, 4)}))
    # Timeout variations.
    grid.append(("hold_short", {"hold_bars": max(2, hold // 2)}))
    grid.append(("hold_long", {"hold_bars": hold * 2}))
    return grid


def _agg(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"n_trades": 0, "net": 0.0, "avg_capture": 0.0, "avg_mfe": 0.0,
                "n_tp": 0, "n_sl": 0, "n_timeout": 0}
    nets = [float(t.get("net_pct") or 0.0) for t in trades]
    caps = [float(t.get("capture_of_mfe") or 0.0) for t in trades]
    mfes = [float(t.get("mfe_pct") or 0.0) for t in trades]
    outs = [str(t.get("outcome") or "") for t in trades]
    return {
        "n_trades": len(trades), "net": round(sum(nets), 4),
        "avg_net": round(sum(nets) / len(nets), 4),
        "avg_capture": round(sum(caps) / len(caps), 4),
        "avg_mfe": round(sum(mfes) / len(mfes), 4),
        "n_tp": sum(1 for o in outs if o == "take"),
        "n_sl": sum(1 for o in outs if o in ("stop", "sl")),
        "n_timeout": sum(1 for o in outs if o in ("time_exit", "timeout")),
    }


def _sim(candles, signals, base, override, *, fees=FEES_BPS, slip=SLIP_BPS) -> dict[str, Any]:
    return _agg(simulate_trades(candles, signals, {**base, **override}, fees_bps=fees, slippage_bps=slip))


def recover_candidate(private_root: Path, *, symbol: str, timeframe: str, family: str,
                      params: dict[str, Any]) -> dict[str, Any] | None:
    """Re-simulate baseline + exit grid on the same signals; return a comparison row."""
    path = choose_symbol_file(market_data_glob(private_root, timeframe), symbol, timeframe=timeframe)
    if not path:
        return {"symbol": symbol, "timeframe": timeframe, "family": family, "skipped": "no_candles"}
    candles = load_candles(path)
    signals = generate_signals(candles, family, params)
    if not signals:
        return {"symbol": symbol, "timeframe": timeframe, "family": family, "skipped": "no_signals"}
    variants = {name: _sim(candles, signals, params, ov) for name, ov in _exit_grid(params)}
    baseline = variants["baseline"]
    # Best non-baseline variant by net (must clear the cost floor to count).
    others = {k: v for k, v in variants.items() if k != "baseline"}
    best_name = max(others, key=lambda k: others[k]["avg_net"])
    best = others[best_name]
    # Recovered = economic after cost, beats baseline, AND enough trades to not be noise.
    # The exit improvement is real but in-sample (best of the grid): recovered means
    # "worth honest re-validation with multiple-testing", never "edge proven".
    recovered = (best["avg_net"] > COST_PCT
                 and best["avg_net"] > baseline["avg_net"] + RECOVER_MARGIN
                 and int(baseline["n_trades"]) >= MIN_TRADES_RECOVER)
    thin = (best["avg_net"] > COST_PCT and best["avg_net"] > baseline["avg_net"] + RECOVER_MARGIN
            and int(baseline["n_trades"]) < MIN_TRADES_RECOVER)
    # Cost sensitivity on the best variant: 0 / 0.1% / 0.2% round-trip.
    ov = dict(_exit_grid(params)[[n for n, _ in _exit_grid(params)].index(best_name)][1])
    cost_sens = {
        "net_0bps": _sim(candles, signals, params, ov, fees=0.0, slip=0.0)["avg_net"],
        "net_10bps": best["avg_net"],
        "net_20bps": _sim(candles, signals, params, ov, fees=14.0, slip=6.0)["avg_net"],
    }
    return {
        "symbol": symbol, "timeframe": timeframe, "family": family,
        "baseline_net": baseline["avg_net"], "baseline_capture": baseline["avg_capture"],
        "best_variant": best_name, "best_net": best["avg_net"], "best_capture": best["avg_capture"],
        "n_trades": baseline["n_trades"], "recovered": recovered, "thin_recovered": thin,
        "cost_sensitivity": cost_sens, "avg_mfe": baseline["avg_mfe"],
    }


def _wrong_exit_candidates(private_root: Path, *, families: tuple[str, ...] | None,
                           limit: int | None) -> list[dict[str, Any]]:
    """wrong_exit recyclable rejects with their params, ordered by family priority."""
    oi_micro = oi_micro_families()
    out: list[dict[str, Any]] = []
    cache: dict[str, dict[str, dict[str, Any]]] = {}
    for r in _load_rejected_uc(private_root):
        fam = str(r.get("family") or "")
        if families and fam not in families:
            continue
        label = str(r.get("run_dir_label") or "")
        if label not in cache:
            cache[label] = _index_run_results(private_root, label)
        result = cache[label].get(str(r.get("params_hash") or ""))
        if not result:
            continue
        facts = _trade_path_facts(result)
        if classify_subreason(facts, str(r.get("hard_status") or ""), fam, oi_micro) != "wrong_exit":
            continue
        out.append({"symbol": str(r.get("symbol") or ""), "timeframe": str(r.get("timeframe") or ""),
                    "family": fam, "params": dict(result.get("params") or {})})
    rank = {f: i for i, f in enumerate(FAMILY_PRIORITY)}
    out.sort(key=lambda c: rank.get(c["family"], 99))
    return out[:limit] if limit else out


def plan_exit_recovery(private_root: Path, *, families: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Dry-run: how many wrong_exit candidates per family, no simulation."""
    cands = _wrong_exit_candidates(private_root, families=families, limit=None)
    by_fam: dict[str, int] = {}
    for c in cands:
        by_fam[c["family"]] = by_fam.get(c["family"], 0) + 1
    return {"total_wrong_exit_with_params": len(cands),
            "by_family": dict(sorted(by_fam.items(), key=lambda kv: -kv[1])),
            "exit_grid": [n for n, _ in _exit_grid({"stop_pct": 1.0, "take_pct": 2.0, "hold_bars": 5})]}


def run_exit_recovery(private_root: Path, *, families: tuple[str, ...] | None = None,
                      limit: int | None = 50) -> list[dict[str, Any]]:
    """Bounded re-simulation over wrong_exit candidates. Returns comparison rows."""
    rows: list[dict[str, Any]] = []
    for c in _wrong_exit_candidates(private_root, families=families, limit=limit):
        res = recover_candidate(private_root, symbol=c["symbol"], timeframe=c["timeframe"],
                                family=c["family"], params=c["params"])
        if res:
            rows.append(res)
    return rows


def summarize_recovery(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if "recovered" in r]
    recovered = [r for r in scored if r["recovered"]]
    thin = [r for r in scored if r.get("thin_recovered")]
    by_variant: dict[str, int] = {}
    for r in recovered:
        by_variant[r["best_variant"]] = by_variant.get(r["best_variant"], 0) + 1
    return {
        "evaluated": len(scored), "skipped": len(rows) - len(scored),
        "recovered": len(recovered),  # n_trades>=MIN_TRADES_RECOVER, worth re-validation
        "recovered_pct": round(100 * len(recovered) / len(scored), 1) if scored else 0.0,
        "thin_recovered": len(thin),  # improved but on 1-4 trades = noise, NOT counted
        "best_variant_counts": dict(sorted(by_variant.items(), key=lambda kv: -kv[1])),
        "median_baseline_net": _median(scored, "baseline_net"),
        "median_best_net": _median(scored, "best_net"),
        "median_recovered_net": _median(recovered, "best_net"),
    }


def _median(rows: list[dict[str, Any]], key: str) -> float:
    vals = sorted(float(r.get(key) or 0.0) for r in rows)
    if not vals:
        return 0.0
    mid = len(vals) // 2
    return round(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2, 4)


def exit_recovered_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The research-only exit_recovered_candidate class: improved exit, n>=5, NOT proven edge.

    These are candidates worth honest re-validation under the recovered exit (with the
    multiple-testing correction), NOT paper-ready and NOT a trade signal.
    """
    return [{
        "symbol": r["symbol"], "timeframe": r["timeframe"], "family": r["family"],
        "recovered_exit": r["best_variant"], "recovered_net": r["best_net"],
        "baseline_net": r["baseline_net"], "n_trades": r["n_trades"],
        "research_class": "exit_recovered_candidate", "paper_forward_ready": False,
    } for r in rows if r.get("recovered")]


def write_exit_recovery_snapshot(private_root: Path, rows: list[dict[str, Any]]) -> Path:
    """Write a research artifact (NOT a source of truth, never read by farm/paper/validator)."""
    import json
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "exit_recovery.json"
    payload = {
        "schema": "exit_recovery.v1",
        "disclaimer": "Research only. Recovered = improved in-sample exit on >=5 trades; "
                      "needs honest re-validation. Not paper-ready, not a trade signal.",
        "summary": summarize_recovery(rows),
        "exit_recovered_candidates": exit_recovered_candidates(rows),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> None:
    import argparse
    import json
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="T3-A exit-recovery research (read-only re-sim).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--plan", action="store_true", help="dry-run: count wrong_exit candidates, no sim")
    ap.add_argument("--family", default="", help="restrict to one family")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--snapshot", action="store_true", help="write the research snapshot artifact")
    args = ap.parse_args()
    fams = (args.family,) if args.family else None
    if args.plan:
        print(json.dumps(plan_exit_recovery(Path(args.private_root), families=fams), ensure_ascii=False, indent=2))
        return
    rows = run_exit_recovery(Path(args.private_root), families=fams, limit=args.limit)
    print(json.dumps(summarize_recovery(rows), ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_exit_recovery_snapshot(Path(args.private_root), rows))


if __name__ == "__main__":
    main()
