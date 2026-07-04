# -*- coding: utf-8 -*-
"""Three-mode sanity check over the EXISTING compute ledger — why does everything look 'dead'?

Reads farm_results (the already-computed sweeps; no new compute) and decomposes each result through
three lenses, so we can see WHERE the edge dies instead of just declaring "no edge":

  * naive    — gross in-sample (costs added back): do simple backtests look pretty? (most do)
  * realistic — net in-sample AND net out-of-sample (the stored test split): what costs + the split do
  * strict   — the honest validator verdict (hard PAPER_FORWARD_READY: OOS + multiple-testing + CI)

It also runs a COST sensitivity (taker vs maker vs zero) because the ledger shows costs are the dominant
killer: a large pool flips positive under maker fills. That is a research hypothesis (maker fills are NOT
guaranteed), never an edge claim — nothing here is promotable.

Per family it emits a verdict: weak_generator (signal mostly absent even gross) / cost_bound (gross
signal, taker costs kill it) / split_overfit (in-sample only) / underpowered_tactical (survives costs +
split but n<POWER_FLOOR) / has_strict_pass. Pure read-only; no money/order/live path.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from statistics import median
from typing import Any

from src.research_lab.state_db import default_db_path

# Per-trade round-trip cost in percent points. The stored avg_net_pct already has TAKER in it
# (fees 7bps + slippage 3bps = 0.10pp), so gross = net + TAKER, and any cost c gives net_c = gross - c.
TAKER_PP = 0.10
MAKER_PP = 0.02
POWER_FLOOR = 10  # the honest validator has no power below this trade count


def _f(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def load_results(private_root: Path) -> list[dict[str, Any]]:
    """Read-only pull of the computed ledger (no new compute)."""
    db = default_db_path(Path(private_root))
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT family, timeframe, symbol, n_trades, avg_net_pct, test_avg_net_pct, hard_status "
            "FROM farm_results").fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _net_positive_at(row: dict[str, Any], cost_pp: float) -> bool:
    """In-sample AND OOS net-positive at an arbitrary per-trade cost (gross = stored net + TAKER)."""
    nis = _f(row.get("avg_net_pct"))
    if nis is None:
        return False
    g_is = nis + TAKER_PP - cost_pp
    oos = _f(row.get("test_avg_net_pct"))
    if oos is None:
        return g_is > 0
    return g_is > 0 and (oos + TAKER_PP - cost_pp) > 0


def classify_row(row: dict[str, Any]) -> dict[str, Any]:
    nis = _f(row.get("avg_net_pct")) or 0.0
    oos = _f(row.get("test_avg_net_pct"))
    n = int(row.get("n_trades") or 0)
    naive = (nis + TAKER_PP) > 0                       # gross in-sample positive
    real_is = nis > 0                                  # net in-sample positive (taker)
    real = _net_positive_at(row, TAKER_PP)             # net IS AND OOS positive (taker)
    strict = str(row.get("hard_status") or "") == "PAPER_FORWARD_READY"
    return {"naive": naive, "real_is": real_is, "real": real, "strict": strict,
            "maker": _net_positive_at(row, MAKER_PP), "n": n, "nis": nis, "oos": oos}


def _verdict(a: dict[str, int]) -> str:
    n = a["n"] or 1
    if a["naive"] / n < 0.35:
        return "weak_generator"            # little signal even gross -> generator/data problem
    # Cost-binding is judged BEFORE a stray strict pass: with only a handful of strict passes in the
    # whole ledger (noise floor), a strong maker unlock is the honest dominant story for the family.
    if a["maker"] >= 2 * max(1, a["real"]):
        return "cost_bound"                # taker costs are the binding killer (maker unlocks a lot)
    if a["strict"] >= POWER_FLOOR:
        return "has_strict_pass"           # only call it that with a non-trivial number of passes
    if a["real_is"] >= 2 * max(1, a["real"]):
        return "split_overfit"             # in-sample only; OOS kills it
    if a["real"] > 0:
        return "underpowered_tactical"     # survives costs + split but below the power floor
    return "dead_after_costs_and_split"


def _aggregate(rows: list[dict[str, Any]], keyf) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(keyf(r), []).append(r)
    out: list[dict[str, Any]] = []
    for key, rs in groups.items():
        cl = [classify_row(r) for r in rs]
        a = {
            "n": len(rs),
            "naive": sum(1 for x in cl if x["naive"]),
            "real_is": sum(1 for x in cl if x["real_is"]),
            "real": sum(1 for x in cl if x["real"]),
            "strict": sum(1 for x in cl if x["strict"]),
            "maker": sum(1 for x in cl if x["maker"]),
            "killed_by_costs": sum(1 for x in cl if x["naive"] and not x["real_is"]),
            "killed_by_split": sum(1 for x in cl if x["real_is"] and x["oos"] is not None and x["oos"] <= 0),
            "killed_by_n": sum(1 for x in cl if x["real"] and not x["strict"] and x["n"] < POWER_FLOOR),
            "median_net_is": round(median([x["nis"] for x in cl]), 4),
        }
        a["maker_unlock"] = a["maker"] - a["real"]
        a["key"] = key
        a["verdict"] = _verdict(a)
        out.append(a)
    return sorted(out, key=lambda d: -d["n"])


def analyze(private_root: Path) -> dict[str, Any]:
    rows = load_results(Path(private_root))
    cl = [classify_row(r) for r in rows]
    funnel = {
        "results": len(rows),
        "naive_positive": sum(1 for x in cl if x["naive"]),
        "realistic_is_positive": sum(1 for x in cl if x["real_is"]),
        "realistic_is_and_oos_positive": sum(1 for x in cl if x["real"]),
        "strict_pass": sum(1 for x in cl if x["strict"]),
    }
    cost = {"taker_0.10pp": sum(1 for x in cl if x["real"]),
            "maker_0.02pp": sum(1 for x in cl if x["maker"]),
            "maker_unlock_vs_taker": sum(1 for x in cl if x["maker"]) - sum(1 for x in cl if x["real"])}
    return {
        "funnel": funnel,
        "cost_sensitivity": cost,
        "by_family": _aggregate(rows, lambda r: str(r.get("family") or "")),
        "by_timeframe": _aggregate(rows, lambda r: str(r.get("timeframe") or "")),
        "cost_bound_families": [a["key"] for a in _aggregate(rows, lambda r: str(r.get("family") or ""))
                                if a["verdict"] == "cost_bound"],
        "note": "read-only 3-mode decomposition of the existing ledger; maker unlock is a hypothesis "
                "(fills not guaranteed), NOT edge; nothing here is paper-ready",
    }


def write_snapshot(private_root: Path, report: dict[str, Any] | None = None) -> Path:
    report = report if report is not None else analyze(private_root)
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "cost_mode_analysis.json"
    payload = {"schema": "cost_mode_analysis.v1",
               "disclaimer": "Three-mode (naive/realistic/strict) read-only mining of farm_results. "
                             "Costs are the dominant killer; maker unlock is a research hypothesis, "
                             "never edge or paper-ready.",
               "report": report}
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
    ap = argparse.ArgumentParser(description="Three-mode cost/edge decomposition of the ledger (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    report = analyze(Path(args.private_root))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
