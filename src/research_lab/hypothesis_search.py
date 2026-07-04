# -*- coding: utf-8 -*-
"""Hypothesis search — author parameter/filter/exit variations, test them honestly on movers, rank.

This is the self-driving generation step: a creative GRID of (family x parameter variation x exit mode)
authored up front, each evaluated on the live-mover universe with a strict held-out-tail OOS (no look-
ahead) and the exit-first lesson baked in (the default fixed RR2 is often the wrong exit). Combos are
ranked by OOS median net across movers and the OOS-positive share, classified honestly:

  holds_oos_candidate (IS+ and OOS+ on a majority)  |  in_sample_only (overfit)  |  weak_or_negative

Nothing is edge or paper-ready — survivors are forward-watch candidates. Read-only, keyless public
candles via the mover-validation fetcher; no order/money/live path.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from typing import Any

from src.research_lab.exit_phase2 import _exit_modes, simulate_exit_mode
from src.research_lab.experiment import generate_signals, load_candles, simulate_trades
from src.research_lab.mover_validation import _mover_symbols, ensure_candles

FEES_BPS = 7.0
SLIP_BPS = 3.0
OOS_FRAC = 0.35
MIN_TRADES = 4
# A held-out-tail positive on a FEW movers is a selection artifact (mover payoffs are bimodal: each
# symbol roughly either trends to the take or reverses to the stop, ~coin-flip which). Require a broad
# symbol base before calling a cell a candidate, or small samples manufacture mirages.
MIN_HOLD_SYMBOLS = 15


def _grid() -> list[dict[str, Any]]:
    """The authored creative grid: parameter variations x exit modes, incl. the new exhaustion_fade.
    Exit modes other than baseline encode the exit-first lesson (fixed RR2 was killing real moves)."""
    grid: list[dict[str, Any]] = []
    # momentum: vary lookback, and crucially the EXIT (trailing/early_tp/hold_long vs fixed baseline)
    for lb in (10, 20, 30):
        for ex in ("baseline", "trailing_tight", "early_tp", "hold_long"):
            grid.append({"family": "momentum_breakout", "exit": ex,
                         "params": {"lookback": lb, "hold_bars": 6, "stop_pct": 6, "take_pct": 12}})
    # exhaustion_fade (new family): vary the run threshold + climax; fade exits should be quick
    for run_pct in (12.0, 18.0, 25.0):
        for ex in ("early_tp", "trailing_tight"):
            grid.append({"family": "exhaustion_fade", "exit": ex,
                         "params": {"run_lookback": 6, "run_pct": run_pct, "vol_climax_mult": 1.3,
                                    "hold_bars": 6, "stop_pct": 6, "take_pct": 12}})
    # sfp + mrf: only with the better exits (baseline proven bad on movers)
    for fam in ("sfp_liquidity_sweep", "mean_reversion_fade"):
        for ex in ("early_tp", "trailing_tight"):
            grid.append({"family": fam, "exit": ex,
                         "params": {"lookback": 20, "hold_bars": 8, "stop_pct": 5, "take_pct": 10}})
    return grid


def _label(combo: dict[str, Any]) -> str:
    p = combo["params"]
    var = f"lb{p.get('lookback')}" if "lookback" in p else f"run{p.get('run_pct')}"
    return f"{combo['family']}|{var}|{combo['exit']}"


def _simulate(candles: list[dict[str, Any]], signals: list[dict[str, Any]], params: dict[str, Any],
              exit_mode: str) -> list[float]:
    if not signals:
        return []
    if exit_mode == "baseline":
        trades = simulate_trades(candles, signals, params, fees_bps=FEES_BPS, slippage_bps=SLIP_BPS)
    else:
        mode = dict(_exit_modes(params)).get(exit_mode)
        if mode is None:
            return []
        trades = simulate_exit_mode(candles, signals, params, mode, fees_bps=FEES_BPS, slip_bps=SLIP_BPS)
    return [float(t.get("net_pct") or 0.0) for t in trades]


def _eval_combo(candles: list[dict[str, Any]], combo: dict[str, Any], oos_frac: float) -> tuple[float, float] | None:
    cut = int(len(candles) * (1.0 - oos_frac))
    sigs = generate_signals(candles, combo["family"], combo["params"])
    is_sigs = [s for s in sigs if int(s["idx"]) < cut]
    oos_sigs = [s for s in sigs if int(s["idx"]) >= cut]
    is_nets = _simulate(candles, is_sigs, combo["params"], combo["exit"])
    oos_nets = _simulate(candles, oos_sigs, combo["params"], combo["exit"])
    if len(is_nets) < MIN_TRADES or len(oos_nets) < MIN_TRADES:
        return None
    return median(is_nets), median(oos_nets)


def run(private_root: Path, *, limit_symbols: int = 16, timeframes: tuple[str, ...] = ("1h", "4h"),
        oos_frac: float = OOS_FRAC, provider: Any = None) -> dict[str, Any]:
    private_root = Path(private_root)
    if provider is None:
        from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
        provider = OkxPublicMarketDataProvider()
    symbols = _mover_symbols(private_root, limit=limit_symbols)
    grid = _grid()
    acc: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        for tf in timeframes:
            path = ensure_candles(private_root, sym, tf, provider=provider)
            if not path:
                continue
            candles = load_candles(path)
            if len(candles) < 80:
                continue
            for combo in grid:
                res = _eval_combo(candles, combo, oos_frac)
                if res is None:
                    continue
                key = f"{_label(combo)}::{tf}"
                cell = acc.setdefault(key, {"label": _label(combo), "family": combo["family"],
                                            "timeframe": tf, "exit": combo["exit"], "symbols": 0,
                                            "is": [], "oos": [], "oos_pos": 0})
                cell["symbols"] += 1
                cell["is"].append(res[0])
                cell["oos"].append(res[1])
                cell["oos_pos"] += int(res[1] > 0)
    return {"symbols": len(symbols), "combos": len(grid), "ranked": _rank(acc)}


def _rank(acc: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cell in acc.values():
        n = cell["symbols"]
        if n < 4:
            continue
        is_med, oos_med, share = median(cell["is"]), median(cell["oos"]), cell["oos_pos"] / n
        out.append({"label": cell["label"], "family": cell["family"], "timeframe": cell["timeframe"],
                    "exit": cell["exit"], "symbols": n, "is_median": round(is_med, 3),
                    "oos_median": round(oos_med, 3), "oos_positive_share": round(share, 3),
                    "verdict": _verdict(is_med, oos_med, share, n), "paper_forward_ready": False})
    return sorted(out, key=lambda c: -c["oos_median"])


def _verdict(is_med: float, oos_med: float, share: float, n: int) -> str:
    if oos_med > 0 and share >= 0.55 and is_med > 0:
        # only a candidate if confirmed on a BROAD symbol base; few-symbol positives are mirages
        return "holds_oos_candidate" if n >= MIN_HOLD_SYMBOLS else "small_sample_positive"
    if is_med > 0 and oos_med <= 0:
        return "in_sample_only"
    return "weak_or_negative"


def write_snapshot(private_root: Path, report: dict[str, Any]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "hypothesis_search.json"
    survivors = [r for r in report["ranked"] if r["verdict"] == "holds_oos_candidate"]
    payload = {"schema": "hypothesis_search.v1",
               "disclaimer": "Authored parameter/exit variation grid on the live-mover universe, held-out "
                             "OOS. Research-only; holds_oos_candidate = forward-watch, never edge/paper-ready.",
               "symbols": report["symbols"], "combos": report["combos"],
               "holds_oos": len(survivors), "ranked": report["ranked"]}
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
    ap = argparse.ArgumentParser(description="Authored hypothesis/variation search on movers (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--limit-symbols", type=int, default=16)
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    report = run(Path(args.private_root), limit_symbols=args.limit_symbols)
    top = report["ranked"][:12]
    print(f"symbols={report['symbols']} combos={report['combos']}  TOP by OOS median:")
    for r in top:
        print(f"  {r['label']:42s} {r['timeframe']:3s} n={r['symbols']:2d} IS={r['is_median']:+6.2f} "
              f"OOS={r['oos_median']:+6.2f} share={r['oos_positive_share']:.2f}  {r['verdict']}")
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
