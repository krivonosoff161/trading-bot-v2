# -*- coding: utf-8 -*-
"""Held-out OOS validation of strategies on the LIVE-MOVER universe (research-only).

The bounded cycle found momentum_breakout posts a positive IN-SAMPLE median on 4h live movers. In-sample
is not edge. This module re-tests the families on the mover universe with a strict time split: in-sample
= the first (1-oos_frac) of each mover's bars, OOS = the held-out tail. It NEVER uses future bars in a
signal (the split is by entry index). It reports IS vs OOS median net per (family, timeframe), the share
of movers that stay OOS-positive, and an honest verdict.

This is a HELD-OUT TAIL pseudo-OOS (the only data we have until forward bars accrue) — weaker than true
forward; the same candidates are also registered to the true_forward collector for new bars. Nothing is
edge or paper-ready. Keyless public candles, read-only; no order/money/live path.
"""
from __future__ import annotations

import glob
import json
import time
from pathlib import Path
from statistics import median
from typing import Any

from src.research_lab.experiment import generate_signals, load_candles, simulate_trades
from src.research_lab.param_schemas import executable_exit_params

FEES_BPS = 7.0
SLIP_BPS = 3.0
OOS_FRAC = 0.35
MIN_TRADES = 4
DEFAULT_FAMILIES = ("momentum_breakout", "sfp_liquidity_sweep", "mean_reversion_fade")
DEFAULT_TFS = ("1h", "4h")


def _mover_symbols(private_root: Path, *, limit: int) -> list[str]:
    """fresh_movers + high_beta from the live-universe snapshot (movement-ranked)."""
    try:
        snap = json.loads((Path(private_root) / "discovery" / "live_universe.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    groups = snap.get("groups") or {}
    out: list[str] = []
    for g in ("fresh_movers", "high_beta", "btc_eth_tactical", "core", "meme"):
        out.extend(groups.get(g) or [])
    seen: list[str] = []
    for s in out:
        if s not in seen:
            seen.append(s)
    return seen[:limit]


def ensure_candles(private_root: Path, symbol: str, timeframe: str, *, provider, days: int = 30) -> Path | None:
    """Return a candle file for symbol/tf, fetching keyless public candles if absent (bounded)."""
    private_root = Path(private_root)
    existing = glob.glob(str(private_root / "market_data" / timeframe / f"{symbol}_*_{timeframe}.json"))
    if existing:
        return Path(existing[0])
    if provider is None:
        return None
    now = int(time.time() * 1000)
    try:
        candles = provider.fetch_ohlcv(symbol, timeframe, now - days * 86_400_000, now)
    except Exception:  # noqa: BLE001 - network/parse error must not crash the cycle
        return None
    if len(candles) < 60:
        return None
    d = private_root / "market_data" / timeframe
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{symbol}_{candles[0]['ts']}_{candles[-1]['ts']}_{timeframe}.json"
    path.write_text(json.dumps(candles), encoding="utf-8")
    return path


def _split_nets(candles: list[dict[str, Any]], family: str, params: dict[str, Any],
                oos_frac: float) -> tuple[list[float], list[float]]:
    """(in-sample nets, oos nets) — split by entry index, no look-ahead."""
    cut = int(len(candles) * (1.0 - oos_frac))
    sigs = generate_signals(candles, family, params)
    is_sigs = [s for s in sigs if int(s["idx"]) < cut]
    oos_sigs = [s for s in sigs if int(s["idx"]) >= cut]
    is_tr = simulate_trades(candles, is_sigs, params, fees_bps=FEES_BPS, slippage_bps=SLIP_BPS) if is_sigs else []
    oos_tr = simulate_trades(candles, oos_sigs, params, fees_bps=FEES_BPS, slippage_bps=SLIP_BPS) if oos_sigs else []
    return ([float(t.get("net_pct") or 0.0) for t in is_tr],
            [float(t.get("net_pct") or 0.0) for t in oos_tr])


def run(private_root: Path, *, families: tuple[str, ...] = DEFAULT_FAMILIES,
        timeframes: tuple[str, ...] = DEFAULT_TFS, limit_symbols: int = 20,
        oos_frac: float = OOS_FRAC, provider: Any = None) -> dict[str, Any]:
    private_root = Path(private_root)
    if provider is None:
        from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
        provider = OkxPublicMarketDataProvider()
    symbols = _mover_symbols(private_root, limit=limit_symbols)
    cells: dict[str, dict[str, Any]] = {}
    per_symbol: list[dict[str, Any]] = []
    for sym in symbols:
        for tf in timeframes:
            path = ensure_candles(private_root, sym, tf, provider=provider)
            if not path:
                continue
            candles = load_candles(path)
            if len(candles) < 80:
                continue
            for fam in families:
                params = executable_exit_params(fam)
                is_nets, oos_nets = _split_nets(candles, fam, params, oos_frac)
                if len(is_nets) < MIN_TRADES or len(oos_nets) < MIN_TRADES:
                    continue
                key = f"{fam}::{tf}"
                cell = cells.setdefault(key, {"family": fam, "timeframe": tf, "symbols": 0,
                                              "is_medians": [], "oos_medians": [], "oos_positive": 0})
                ism, oosm = median(is_nets), median(oos_nets)
                cell["symbols"] += 1
                cell["is_medians"].append(ism)
                cell["oos_medians"].append(oosm)
                cell["oos_positive"] += int(oosm > 0)
                per_symbol.append({"symbol": sym, "family": fam, "timeframe": tf,
                                   "is_median_net": round(ism, 3), "oos_median_net": round(oosm, 3),
                                   "is_n": len(is_nets), "oos_n": len(oos_nets)})
    return {"symbols_considered": len(symbols), "evaluated_cells": len(per_symbol),
            "summary": _summarize(cells), "per_symbol": per_symbol}


def _summarize(cells: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    for cell in cells.values():
        n = cell["symbols"]
        out.append({
            "family": cell["family"], "timeframe": cell["timeframe"], "symbols": n,
            "is_median_net": round(median(cell["is_medians"]), 3) if cell["is_medians"] else 0.0,
            "oos_median_net": round(median(cell["oos_medians"]), 3) if cell["oos_medians"] else 0.0,
            "oos_positive_share": round(cell["oos_positive"] / n, 3) if n else 0.0,
            "verdict": _verdict(cell),
        })
    return {"by_cell": sorted(out, key=lambda c: -c["oos_median_net"]),
            "note": "held-out-tail pseudo-OOS on live movers (not genuinely new bars). oos_positive_share "
                    "= fraction of movers OOS-positive. NOT edge; nothing paper-ready"}


def _verdict(cell: dict[str, Any]) -> str:
    n = cell["symbols"]
    if n < 4:
        return "underpowered_few_symbols"
    is_med = median(cell["is_medians"])
    oos_med = median(cell["oos_medians"])
    share = cell["oos_positive"] / n
    if oos_med > 0 and share >= 0.55 and is_med > 0:
        return "holds_oos_candidate"            # IS+ and OOS+ across a majority of movers -> worth forward
    if is_med > 0 and oos_med <= 0:
        return "in_sample_only"                 # the classic overfit: IS+ collapses OOS
    if oos_med <= 0:
        return "weak_or_negative"
    return "mixed"


def write_snapshot(private_root: Path, report: dict[str, Any]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "mover_validation.json"
    payload = {"schema": "mover_validation.v1",
               "disclaimer": "Held-out-tail OOS of families on the live-mover universe. Research-only; "
                             "pseudo-OOS (not new bars); holds_oos_candidate != edge; nothing paper-ready.",
               "symbols_considered": report.get("symbols_considered"),
               "evaluated_cells": report.get("evaluated_cells"), "summary": report.get("summary"),
               "per_symbol": report.get("per_symbol")}
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
    ap = argparse.ArgumentParser(description="Held-out OOS validation on the live-mover universe (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--limit-symbols", type=int, default=20)
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    report = run(Path(args.private_root), limit_symbols=args.limit_symbols)
    print(json.dumps({"symbols_considered": report["symbols_considered"],
                      "evaluated_cells": report["evaluated_cells"], "summary": report["summary"]},
                     ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
