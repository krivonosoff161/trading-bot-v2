# -*- coding: utf-8 -*-
"""Bounded trade-path backfill for recyclable rejects (read-only re-simulation).

The 849 historical metrics.json runs were computed before the trade-path instrumentation
(time-to-MFE/MAE, tp-before-sl, path_quality) existed. To answer *why* a setup was rejected
along the PATH of the trade — not just the final net — we regenerate the SAME entry signals on
the SAME local candles and re-run ``simulate_trades`` (which now emits the path fields). We
aggregate the per-trade path into one record per candidate and write a derived snapshot the
Setup Outcome Memory can attach.

It changes NO exit/validation logic (same params, same fixed-barrier exits), recomputes nothing
that promotes anything, touches no money/order/live path, and is bounded by ``--limit``. The
path fields are descriptive over the already-decided hold window (no look-ahead).
"""
from __future__ import annotations

import json
from collections import Counter
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
    _RECYCLABLE,
    _index_run_results,
    _load_rejected_uc,
    _trade_path_facts,
    classify_subreason,
    oi_micro_families,
)

FEES_BPS = 7.0
SLIP_BPS = 3.0
# wrong_exit first (its path IS the story), then the thin/power buckets.
SUBREASON_PRIORITY = ("wrong_exit", "wrong_timeframe", "validator_too_strict",
                      "tactical_candidate", "wrong_costs", "missing_oi_micro")


def _recyclable_with_params(private_root: Path, *, subreasons: tuple[str, ...] | None,
                            limit: int | None) -> list[dict[str, Any]]:
    """Recyclable rejects that have stored params to re-simulate, ordered by sub-reason."""
    oi_micro = oi_micro_families()
    cache: dict[str, dict[str, dict[str, Any]]] = {}
    out: list[dict[str, Any]] = []
    for r in _load_rejected_uc(private_root):
        label = str(r.get("run_dir_label") or "")
        if label not in cache:
            cache[label] = _index_run_results(private_root, label)
        result = cache[label].get(str(r.get("params_hash") or ""))
        if not result:
            continue
        sub = classify_subreason(_trade_path_facts(result), str(r.get("hard_status") or ""),
                                 str(r.get("family") or ""), oi_micro)
        if sub not in _RECYCLABLE or (subreasons and sub not in subreasons):
            continue
        out.append({"uc_key": str(r.get("uc_key") or ""), "symbol": str(r.get("symbol") or ""),
                    "timeframe": str(r.get("timeframe") or ""), "family": str(r.get("family") or ""),
                    "params": dict(result.get("params") or {}), "subreason": sub})
    rank = {s: i for i, s in enumerate(SUBREASON_PRIORITY)}
    out.sort(key=lambda c: rank.get(c["subreason"], 99))
    return out[:limit] if limit else out


def _path_agg(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the per-trade path fields into a candidate path signature."""
    n = len(trades)
    if not n:
        return {"n_trades": 0}
    tp = sum(1 for t in trades if t.get("tp_before_sl") is True)
    sl = sum(1 for t in trades if t.get("tp_before_sl") is False)

    def _avg(key: str) -> float:
        return round(sum(float(t.get(key) or 0.0) for t in trades) / n, 4)

    return {
        "n_trades": n,
        "avg_time_to_mfe": _avg("time_to_mfe"), "avg_time_to_mae": _avg("time_to_mae"),
        "avg_bars_held": _avg("bars_held"), "avg_capture": _avg("capture_of_mfe"),
        "avg_mfe_pct": _avg("mfe_pct"), "avg_mae_pct": _avg("mae_pct"),
        "tp_before_sl_share": round(tp / n, 4), "sl_before_tp_share": round(sl / n, 4),
        "timeout_share": round((n - tp - sl) / n, 4),
        "adverse_first_rate": round(sum(1 for t in trades if t.get("adverse_before_favorable")) / n, 4),
        "path_quality": dict(Counter(str(t.get("path_quality") or "") for t in trades)),
    }


def backfill_one(private_root: Path, item: dict[str, Any]) -> dict[str, Any]:
    """Re-simulate one candidate's signals and return its aggregated path record."""
    path = choose_symbol_file(market_data_glob(private_root, item["timeframe"]),
                              item["symbol"], timeframe=item["timeframe"])
    if not path:
        return {"uc_key": item["uc_key"], "skipped": "no_candles"}
    candles = load_candles(path)
    signals = generate_signals(candles, item["family"], item["params"])
    if not signals:
        return {"uc_key": item["uc_key"], "skipped": "no_signals"}
    trades = simulate_trades(candles, signals, item["params"], fees_bps=FEES_BPS, slippage_bps=SLIP_BPS)
    return {"uc_key": item["uc_key"], "symbol": item["symbol"], "timeframe": item["timeframe"],
            "family": item["family"], "subreason": item["subreason"], **_path_agg(trades)}


def run_backfill(private_root: Path, *, subreasons: tuple[str, ...] | None = None,
                 limit: int | None = 200) -> list[dict[str, Any]]:
    """Bounded re-simulation over recyclable rejects. Returns per-candidate path records."""
    return [backfill_one(private_root, it)
            for it in _recyclable_with_params(private_root, subreasons=subreasons, limit=limit)]


def plan_backfill(private_root: Path) -> dict[str, Any]:
    """Dry-run: how many recyclable-with-params candidates per sub-reason (no re-sim)."""
    items = _recyclable_with_params(private_root, subreasons=None, limit=None)
    by_sub: dict[str, int] = {}
    for it in items:
        by_sub[it["subreason"]] = by_sub.get(it["subreason"], 0) + 1
    return {"total_recyclable_with_params": len(items),
            "by_subreason": dict(sorted(by_sub.items(), key=lambda kv: -kv[1]))}


def summarize_backfill(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate path-quality distribution + per-sub-reason means over the backfilled sample."""
    scored = [r for r in rows if r.get("n_trades")]
    pq: Counter[str] = Counter()
    by_sub: dict[str, dict[str, float]] = {}
    for r in scored:
        pq.update(r.get("path_quality") or {})
        sub = by_sub.setdefault(r["subreason"], {"n": 0, "capture": 0.0, "t_mfe": 0.0, "tp_share": 0.0})
        sub["n"] += 1
        sub["capture"] += float(r.get("avg_capture") or 0.0)
        sub["t_mfe"] += float(r.get("avg_time_to_mfe") or 0.0)
        sub["tp_share"] += float(r.get("tp_before_sl_share") or 0.0)
    for sub in by_sub.values():
        k = max(1, sub["n"])
        sub["avg_capture"] = round(sub.pop("capture") / k, 4)
        sub["avg_time_to_mfe"] = round(sub.pop("t_mfe") / k, 4)
        sub["avg_tp_before_sl_share"] = round(sub.pop("tp_share") / k, 4)
    return {"evaluated": len(scored), "skipped": len(rows) - len(scored),
            "path_quality_distribution": dict(pq.most_common()),
            "by_subreason": by_sub}


def write_backfill_snapshot(private_root: Path, rows: list[dict[str, Any]]) -> Path:
    """Write the derived path-backfill artifact, keyed by uc_key (Setup Outcome Memory attaches it)."""
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "trade_path_backfill.json"
    by_uc = {r["uc_key"]: r for r in rows if r.get("uc_key") and r.get("n_trades")}
    payload = {
        "schema": "trade_path_backfill.v1",
        "disclaimer": "Read-only re-simulation of recyclable rejects to attach trade-path metrics. "
                      "Descriptive only; no exit/validation change, not paper-ready, not a signal.",
        "summary": summarize_backfill(rows), "by_uc_key": by_uc,
    }
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
    ap = argparse.ArgumentParser(description="Bounded trade-path backfill (read-only re-sim).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--plan", action="store_true", help="dry-run: count recyclable-with-params, no re-sim")
    ap.add_argument("--subreason", default="", help="restrict to one sub-reason")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--snapshot", action="store_true", help="write the derived backfill artifact")
    args = ap.parse_args()
    subs = (args.subreason,) if args.subreason else None
    if args.plan:
        print(json.dumps(plan_backfill(Path(args.private_root)), ensure_ascii=False, indent=2))
        return
    rows = run_backfill(Path(args.private_root), subreasons=subs, limit=args.limit)
    print(json.dumps(summarize_backfill(rows), ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_backfill_snapshot(Path(args.private_root), rows))


if __name__ == "__main__":
    main()
