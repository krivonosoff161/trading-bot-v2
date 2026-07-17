# -*- coding: utf-8 -*-
"""Honest maker-fill model — stress-test the 'cost-bound, maker unlocks it' hypothesis (research-only).

The cost-mode mining flipped ~1112 taker-dead results positive by ARITHMETIC: net + saved cost. That is
optimistic and possibly a mirage, because a maker (limit) entry can simply NOT FILL, and a stop exit is
always a taker. This module replaces the arithmetic with a deterministic OHLC touch fixture:

  * entry = a limit at the prior bar's close. It fills on the next bar ONLY if the bar trades through
    the limit (low<=limit for long, high>=limit for short); otherwise NO-FILL and the trade is skipped
    (the taker would have entered at market — the maker misses it). This is the honest cost of being
    passive: momentum entries that run away are missed.
  * exit costs are mixed: a take-profit fills as a maker (cheap), but a stop or timeout is a taker
    (full fee + slippage). So the maker saving is real only when you also EXIT on a limit.

Per side: taker 5bps (fee+slip blended), maker 2bps (fee, no slip) -> taker round-trip 0.10pp matches
the ledger; maker+take = 0.04pp, maker+stop = 0.07pp. Skeptic framing: we look for where the hypothesis
breaks. A wick touch still does not prove quantity, queue order, or intrabar event
order. Nothing here is edge or paper-ready; read-only, no order/money/live path.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.research_lab.experiment import generate_signals, load_candles
from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.paths import market_data_glob
from src.research_lab.simulator_contract import legacy_fixture_manifest

TAKER_SIDE_BPS = 5.0   # fee + slippage, per side (blended; taker round-trip = 0.10pp = the ledger cost)
MAKER_SIDE_BPS = 2.0   # maker fee, per side, no slippage (filled at the limit)
# A candidate is in the maker-unlock pool when taker net is in (-0.08, 0]: dead under taker, but the
# naive arithmetic (net + 0.08) would flip it positive. That arithmetic is what we now test honestly.
POOL_LO, POOL_HI = -0.08, 0.0


def _f(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def simulate_maker(candles: list[dict[str, Any]], signals: list[dict[str, Any]],
                   params: dict[str, Any]) -> dict[str, Any]:
    """Re-simulate signals with a passive limit entry (no-fill possible) and mixed exit costs."""
    hold = int(params.get("hold_bars", 5) or 5)
    stop_pct = float(params.get("stop_pct", 0.0) or 0.0)
    take_pct = float(params.get("take_pct", 0.0) or 0.0)
    nets: list[float] = []
    filled = 0
    for sig in signals:
        idx = int(sig["idx"])
        if idx <= 0 or idx >= len(candles):
            continue
        side = str(sig["side"])
        limit = float(candles[idx - 1]["close"])           # decision price = prior bar close
        bar = candles[idx]
        lo, hi = float(bar["low"]), float(bar["high"])
        long_ = side == "long"
        if (long_ and lo > limit) or (not long_ and hi < limit):
            continue                                         # NO-FILL: price never came back to the limit
        filled += 1
        entry = limit
        cap = min(idx + hold, len(candles) - 1)
        exit_price, outcome = _exit(candles, idx, cap, entry, long_, stop_pct, take_pct)
        ret = (exit_price / entry - 1) * 100 * (1 if long_ else -1)
        exit_bps = MAKER_SIDE_BPS if outcome == "take" else TAKER_SIDE_BPS
        cost_pp = (MAKER_SIDE_BPS + exit_bps) / 100.0       # bps/100 -> percent points
        nets.append(ret - cost_pp)
    n_sig = len(signals)
    avg = round(sum(nets) / len(nets), 4) if nets else 0.0
    return {"n_signals": n_sig, "n_filled": filled, "fill_rate": round(filled / n_sig, 4) if n_sig else 0.0,
            "maker_avg_net_pct": avg, "n_trades": len(nets)}


def simulate_taker(candles: list[dict[str, Any]], signals: list[dict[str, Any]],
                   params: dict[str, Any]) -> dict[str, Any]:
    """Taker baseline on the SAME signals (market entry at open[idx], taker both sides) — so the maker
    vs taker comparison is apples-to-apples on one re-sim basis, not vs the stored ledger value."""
    hold = int(params.get("hold_bars", 5) or 5)
    stop_pct = float(params.get("stop_pct", 0.0) or 0.0)
    take_pct = float(params.get("take_pct", 0.0) or 0.0)
    nets: list[float] = []
    for sig in signals:
        idx = int(sig["idx"])
        if idx >= len(candles):
            continue
        side = str(sig["side"])
        long_ = side == "long"
        entry = float(candles[idx]["open"])
        if entry <= 0:
            continue
        cap = min(idx + hold, len(candles) - 1)
        exit_price, _ = _exit(candles, idx, cap, entry, long_, stop_pct, take_pct)
        ret = (exit_price / entry - 1) * 100 * (1 if long_ else -1)
        nets.append(ret - (TAKER_SIDE_BPS + TAKER_SIDE_BPS) / 100.0)   # taker both sides = 0.10pp
    return {"taker_resim_net_pct": round(sum(nets) / len(nets), 4) if nets else 0.0, "n_trades": len(nets)}


def _exit(candles: list[dict[str, Any]], idx: int, cap: int, entry: float, long_: bool,
          stop_pct: float, take_pct: float) -> tuple[float, str]:
    stop = entry * (1 - stop_pct / 100) if long_ else entry * (1 + stop_pct / 100)
    take = entry * (1 + take_pct / 100) if long_ else entry * (1 - take_pct / 100)
    for j in range(idx, cap + 1):
        hi, lo = float(candles[j]["high"]), float(candles[j]["low"])
        if stop_pct > 0 and ((long_ and lo <= stop) or (not long_ and hi >= stop)):
            return stop, "stop"
        if take_pct > 0 and ((long_ and hi >= take) or (not long_ and lo <= take)):
            return take, "take"
    return float(candles[cap]["close"]), "timeout"


def _pool(private_root: Path, *, limit: int | None) -> list[dict[str, Any]]:
    """unique_candidates in the maker-unlock band (taker-dead but arithmetic-maker positive)."""
    db = tasks_db_path(private_root)
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT symbol,timeframe,family,avg_net_pct,params_json,n_trades "
                            "FROM unique_candidates").fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        net = _f(r["avg_net_pct"])
        if net is None or not (POOL_LO < net <= POOL_HI):
            continue
        try:
            params = json.loads(r["params_json"]) if r["params_json"] else {}
        except (TypeError, json.JSONDecodeError):
            params = {}
        if not params:
            continue
        out.append({"symbol": r["symbol"], "timeframe": r["timeframe"], "family": r["family"],
                    "taker_net": net, "params": params, "n_trades": int(r["n_trades"] or 0)})
    return out[:limit] if limit else out


def run(private_root: Path, *, limit: int | None = None) -> dict[str, Any]:
    """Re-simulate the whole maker-unlock pool honestly; report survival vs the arithmetic claim."""
    private_root = Path(private_root)
    from src.research_lab.experiment import choose_symbol_file
    pool = _pool(private_root, limit=limit)
    rows: list[dict[str, Any]] = []
    for c in pool:
        path = choose_symbol_file(market_data_glob(private_root, c["timeframe"]), c["symbol"],
                                  timeframe=c["timeframe"])
        if not path:
            continue
        candles = load_candles(path)
        sigs = generate_signals(candles, c["family"], c["params"])
        if not sigs:
            continue
        mk = simulate_maker(candles, sigs, c["params"])
        tk = simulate_taker(candles, sigs, c["params"])
        rows.append({**{k: c[k] for k in ("symbol", "timeframe", "family", "taker_net")},
                     "arithmetic_maker_net": round(c["taker_net"] + 0.08, 4),
                     "taker_resim_net": tk["taker_resim_net_pct"], **mk,
                     "honest_maker_positive": mk["maker_avg_net_pct"] > 0,
                     # the real flip: taker-dead on the SAME re-sim basis, but honest-maker positive
                     "honest_flip": tk["taker_resim_net_pct"] <= 0 and mk["maker_avg_net_pct"] > 0})
    manifest = legacy_fixture_manifest()
    return {"pool_size": len(pool), "evaluated": len(rows), "summary": _summarize(rows), "rows": rows,
            "simulator_manifest": manifest,
            "unsupported_simulator_dimensions": manifest["unsupported_dimensions"]}


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"evaluated": 0}
    honest_pos = sum(1 for r in rows if r["honest_maker_positive"])
    arith_pos = sum(1 for r in rows if r["arithmetic_maker_net"] > 0)  # ~all by construction
    taker_pos = sum(1 for r in rows if r["taker_resim_net"] > 0)
    flips = sum(1 for r in rows if r["honest_flip"])
    avg_fill = round(sum(r["fill_rate"] for r in rows) / n, 4)
    by_fam: dict[str, dict[str, int]] = {}
    for r in rows:
        d = by_fam.setdefault(r["family"], {"n": 0, "honest_pos": 0, "flips": 0})
        d["n"] += 1
        d["honest_pos"] += int(r["honest_maker_positive"])
        d["flips"] += int(r["honest_flip"])
    return {"evaluated": n, "arithmetic_positive": arith_pos, "taker_resim_positive": taker_pos,
            "honest_maker_positive": honest_pos, "honest_flips_taker_neg_to_maker_pos": flips,
            "survival_rate": round(honest_pos / max(1, arith_pos), 4), "avg_fill_rate": avg_fill,
            "by_family": {k: v for k, v in sorted(by_fam.items(), key=lambda kv: -kv[1]["n"])},
            "note": "honest maker re-sim (no-fill + mixed exit costs) vs taker on the SAME basis. flips = "
                    "taker-dead but honest-maker-positive. NOT edge; nothing paper-ready"}


def write_snapshot(private_root: Path, report: dict[str, Any] | None = None) -> Path:
    report = report if report is not None else run(private_root)
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "maker_fill_model.json"
    manifest = legacy_fixture_manifest()
    payload = {"schema": "maker_fill_model.v1",
               "disclaimer": "Deterministic OHLC maker-touch stress fixture. A wick is not observed "
                             "quantity, queue order or execution. Survival != edge; nothing paper-ready.",
               "simulator_manifest": manifest,
               "unsupported_simulator_dimensions": manifest["unsupported_dimensions"],
               "pool_size": report.get("pool_size"), "summary": report.get("summary")}
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
    ap = argparse.ArgumentParser(description="Honest maker-fill stress test (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    report = run(Path(args.private_root), limit=args.limit)
    print(json.dumps({"pool_size": report["pool_size"], "evaluated": report["evaluated"],
                      "summary": report["summary"]}, ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
