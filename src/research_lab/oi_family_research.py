# -*- coding: utf-8 -*-
"""OI-family bounded research — run the dormant OI families on OI-enriched DENSE timeframes only.

Gated by the Block-3 OI resolution audit: OI is dense on 1h/4h (1 raw point per bar, no look-ahead)
but only delta_coarse on 15m. So the OI families (already in the registry — NOT a second catalog) run
ONLY on OI-enriched 1h/4h files here. Each result is honest-validated through the same bridge and kept
in a SEPARATE outcome namespace (``oi_*``) so it never mixes with the candle-only families.

Research-only: registry params (executable RR), read-only candle files, honest-bridge verdict, no
promotion, no money/order/live path. OI availability proves nothing about edge.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.experiment import (
    choose_symbol_file,
    generate_signals,
    load_candles,
    simulate_trades,
)
from src.research_lab.param_schemas import executable_exit_params
from src.research_lab.paths import market_data_glob
from src.research_lab.strategy_registry import REGISTRY, get_strategy

FEES_BPS = 7.0
SLIP_BPS = 3.0
# OI-only families (funding-squeeze needs funding, which is sparse -> deferred). All in the registry.
OI_FAMILIES = ("oi_price_quadrant", "oi_price_quadrant_continuation", "oi_price_quadrant_trap_fade")
DENSE_TFS = ("1h", "4h")  # from the Block-3 OI resolution audit; 15m is delta_coarse -> excluded


def _oi_enriched_symbols(private_root: Path, timeframe: str, *, limit: int | None) -> list[str]:
    """Symbols whose candle file actually carries the merged ``oi`` field (Phase-C enrichment)."""
    out: list[str] = []
    tf_dir = Path(private_root) / "market_data" / timeframe
    for path in sorted(tf_dir.glob("*.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(rows, list) and rows and isinstance(rows[-1], dict) and rows[-1].get("oi") is not None:
            out.append(path.stem.rsplit("_", 3)[0])  # {symbol}_{start}_{end}_{tf} -> symbol
    return out[:limit] if limit else out


def _required_data_present(candles: list[dict[str, Any]], family: str) -> bool:
    required = set(get_strategy(family).required_data)
    last = candles[-1] if candles else {}
    if "oi" in required and last.get("oi") is None:
        return False
    if "funding" in required and last.get("funding") is None:
        return False
    return True


def run_oi_family_one(private_root: Path, *, family: str, symbol: str, timeframe: str) -> dict[str, Any]:
    """Default-param OI-family run on one enriched symbol, honest-validated (separate oi_* class)."""
    path = choose_symbol_file(market_data_glob(private_root, timeframe), symbol, timeframe=timeframe)
    if not path:
        return {"symbol": symbol, "timeframe": timeframe, "family": family, "skipped": "no_file"}
    candles = load_candles(path)
    if not _required_data_present(candles, family):
        return {"symbol": symbol, "timeframe": timeframe, "family": family, "skipped": "oi_data_absent"}
    params = executable_exit_params(family)
    signals = generate_signals(candles, family, params)
    if not signals:
        return {"symbol": symbol, "timeframe": timeframe, "family": family, "skipped": "no_signals"}
    trades = simulate_trades(candles, signals, params, fees_bps=FEES_BPS, slippage_bps=SLIP_BPS)
    nets = [float(t.get("net_pct") or 0.0) for t in trades]
    avg_net = round(sum(nets) / len(nets), 4) if nets else 0.0
    status = _bridge_status(symbol, timeframe, family, params, trades)
    return {"symbol": symbol, "timeframe": timeframe, "family": family,
            "n_trades": len(trades), "avg_net_pct": avg_net,
            "hard_status": status, "outcome_class": f"oi_{status.lower()}", "paper_forward_ready": False}


def _bridge_status(symbol: str, tf: str, family: str, params: dict[str, Any],
                   trades: list[dict[str, Any]]) -> str:
    from src.research_lab.hard_validation_contract import CandidateForValidation
    from src.research_lab.honest_backtest_bridge import run_validation
    cand = CandidateForValidation.from_dict({
        "candidate_id": f"oi::{symbol}::{tf}::{family}", "source_run_id": "oi_family_research",
        "symbol": symbol, "normalized_symbol": symbol, "timeframe": tf, "strategy_id": family,
        "params": params, "fees_bps": FEES_BPS, "slippage_bps": SLIP_BPS, "lite_status": "FORWARD_PAPER",
        "metrics": {"n_trades": len(trades), "runtime": {"n_variants_evaluated": 1}},
        "trades": [{"net_pct": float(t.get("net_pct") or 0.0)} for t in trades]})
    return str(run_validation(cand, Path("."), dry_run=True).get("hard_status") or "")


def plan_oi_family_research(private_root: Path, *, limit: int | None = None) -> dict[str, Any]:
    by_tf = {tf: len(_oi_enriched_symbols(private_root, tf, limit=limit)) for tf in DENSE_TFS}
    return {"oi_families": list(OI_FAMILIES), "dense_timeframes": list(DENSE_TFS),
            "enriched_symbols_by_tf": by_tf,
            "excluded": {"15m": "delta_coarse (OI resolution audit) - not run"}}


def run_oi_family_research(private_root: Path, *, limit: int | None = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tf in DENSE_TFS:
        for symbol in _oi_enriched_symbols(private_root, tf, limit=limit):
            for family in OI_FAMILIES:
                if family in REGISTRY:
                    rows.append(run_oi_family_one(private_root, family=family, symbol=symbol, timeframe=tf))
    return rows


def summarize_oi_family(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if "outcome_class" in r]
    by_class: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for r in rows:
        if "outcome_class" in r:
            by_class[r["outcome_class"]] = by_class.get(r["outcome_class"], 0) + 1
        else:
            skipped[str(r.get("skipped"))] = skipped.get(str(r.get("skipped")), 0) + 1
    passed = sum(1 for r in scored if r.get("hard_status") == "PAPER_FORWARD_READY")
    return {"evaluated": len(scored), "skipped_total": len(rows) - len(scored), "skipped": skipped,
            "by_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1])), "honest_passed": passed,
            "note": "separate oi_* namespace; honest-validated; OI availability != edge"}


def write_oi_family_snapshot(private_root: Path, rows: list[dict[str, Any]]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "oi_family_research.json"
    payload = {"schema": "oi_family_research.v1",
               "disclaimer": "OI families on OI-enriched 1h/4h only (15m delta_coarse excluded). "
                             "Separate oi_* class, honest-validated, never paper-ready. Not edge.",
               "summary": summarize_oi_family(rows), "rows": rows}
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
    ap = argparse.ArgumentParser(description="OI-family bounded research on dense 1h/4h (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    if args.plan:
        print(json.dumps(plan_oi_family_research(Path(args.private_root), limit=args.limit),
                         ensure_ascii=False, indent=2))
        return
    rows = run_oi_family_research(Path(args.private_root), limit=args.limit)
    print(json.dumps(summarize_oi_family(rows), ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_oi_family_snapshot(Path(args.private_root), rows))


if __name__ == "__main__":
    main()
