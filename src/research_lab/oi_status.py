# -*- coding: utf-8 -*-
"""Honest open-interest coverage status — keyless public OKX OI, no fake-pass, no eternal pending.

OKX OI history (rubik open-interest-history) is keyless-public but retention-bounded, so a long
candle window can never be fully covered. This module MEASURES real coverage per (symbol, tf) and
assigns a stable, honest status instead of an indefinite NEEDS_OI_DATA:

  * ``oi_available``   — OI fetched AND merged coverage >= min_coverage (the only state that lets an
                         OI family run; the merge is written only here, never with thin coverage);
  * ``oi_partial``     — OI points exist but cover < min_coverage of the window (informational, NOT
                         merged — an OI family stays honestly blocked, never fake-passed);
  * ``oi_unmeasured``  — no OI points for this instrument/window (structural: retention < window or
                         no series). Terminal — recorded so the farm stops re-polling forever;
  * ``oi_fetch_failed``— transient provider/network error (retried under a cap, then oi_unmeasured).

OI NEVER promotes a setup to PAPER_FORWARD_READY: it only gates whether an OI family's sweep may run.
Public market-data endpoint only — no keys, no account, no orders, no money/live path. Writes only
the candle file's ``oi`` field (when oi_available) and the derived oi_status snapshot, in the private
research root.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.research_lab.experiment import choose_symbol_file
from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.flow_enrich import DEFAULT_OI_MIN_COVERAGE
from src.research_lab.flow_merge import coverage as _coverage
from src.research_lab.flow_merge import merge_oi
from src.research_lab.paths import market_data_glob

# Closed OI status vocabulary. Disjoint from any paper/validation status by construction —
# OI is a DATA state, never a trade verdict.
OI_STATUSES = ("oi_available", "oi_partial", "oi_unmeasured", "oi_fetch_failed")
_STRUCTURAL = {"oi_unmeasured"}  # terminal: do not re-poll forever
OI_MAX_ATTEMPTS = 3


def classify_oi_status(coverage_frac: float, points: int, *, fetch_ok: bool,
                       min_coverage: float = DEFAULT_OI_MIN_COVERAGE) -> str:
    """Honest OI status from a real measurement. Never fake-passes thin coverage."""
    if not fetch_ok:
        return "oi_fetch_failed"
    if points <= 0:
        return "oi_unmeasured"
    if coverage_frac >= min_coverage:
        return "oi_available"
    if coverage_frac > 0:
        return "oi_partial"
    return "oi_unmeasured"


def _read_rows(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = [r for r in data if isinstance(r, dict) and "ts" in r] if isinstance(data, list) else []
    return sorted(rows, key=lambda r: int(r["ts"]))


def measure_oi(path: Path, symbol: str, timeframe: str, *, provider, now_ms: int,
               min_coverage: float = DEFAULT_OI_MIN_COVERAGE, apply: bool = False) -> dict[str, Any]:
    """Fetch public OI, measure coverage, assign an honest status. Merges the file ONLY when
    oi_available (>= min_coverage) — partial/unmeasured never write a thin/empty OI field."""
    rows = _read_rows(path)
    if not rows:
        return {"symbol": symbol, "timeframe": timeframe, "status": "oi_unmeasured",
                "coverage_pct": 0.0, "points": 0, "merged_written": False, "reason": "no_candles"}
    try:
        from src.research_lab.providers.okx_flow import FlowDataError
        points = provider.fetch_open_interest(symbol, timeframe, int(rows[0]["ts"]), int(rows[-1]["ts"]))
        fetch_ok = True
    except FlowDataError:
        points = []
        fetch_ok = False
    cov = _coverage(merge_oi(rows, points), "oi")["coverage_pct"] if points else 0.0
    status = classify_oi_status(cov / 100.0, len(points), fetch_ok=fetch_ok, min_coverage=min_coverage)
    written = False
    if apply and status == "oi_available":
        path.write_text(json.dumps(merge_oi(rows, points), ensure_ascii=False), encoding="utf-8")
        written = True
    return {"symbol": symbol, "timeframe": timeframe, "status": status, "coverage_pct": round(cov, 2),
            "points": len(points), "merged_written": written, "now_ms": int(now_ms)}


def _symbols_for_timeframe(private_root: Path, timeframe: str, limit: int) -> list[str]:
    """Distinct symbols the farm has candle data for at this timeframe (from the brain)."""
    db = tasks_db_path(private_root)
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM unique_candidates WHERE timeframe=? ORDER BY symbol LIMIT ?",
            (timeframe, int(limit))).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [str(r[0]) for r in rows if r[0]]


def run_oi_enrichment(private_root: Path, *, timeframes: tuple[str, ...] = ("1h", "4h"),
                      limit: int = 12, apply: bool = False, provider=None,
                      now_ms: int = 0) -> dict[str, Any]:
    """Bounded honest OI measurement across timeframes. Writes a stable oi_status snapshot."""
    if provider is None:
        from src.research_lab.providers.okx_flow import OkxPublicOpenInterestProvider
        provider = OkxPublicOpenInterestProvider()
    rows: list[dict[str, Any]] = []
    for tf in timeframes:
        for symbol in _symbols_for_timeframe(private_root, tf, limit):
            path = choose_symbol_file(market_data_glob(private_root, tf), symbol, timeframe=tf)
            if not path:
                rows.append({"symbol": symbol, "timeframe": tf, "status": "oi_unmeasured",
                             "coverage_pct": 0.0, "points": 0, "merged_written": False, "reason": "no_file"})
                continue
            rows.append(measure_oi(path, symbol, tf, provider=provider, now_ms=now_ms, apply=apply))
    snapshot = write_oi_status_snapshot(private_root, rows)
    return {"summary": summarize_oi(rows), "snapshot": str(snapshot), "rows": rows}


def summarize_oi(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_tf_status: dict[str, dict[str, int]] = {}
    by_status: dict[str, int] = {}
    for r in rows:
        st = str(r.get("status") or "")
        by_status[st] = by_status.get(st, 0) + 1
        by_tf_status.setdefault(str(r.get("timeframe") or ""), {})
        tf = by_tf_status[str(r.get("timeframe") or "")]
        tf[st] = tf.get(st, 0) + 1
    avail = [r for r in rows if r.get("status") == "oi_available"]
    return {
        "measured": len(rows), "by_status": by_status, "by_timeframe": by_tf_status,
        "available": len(avail), "merged_written": sum(1 for r in rows if r.get("merged_written")),
        "median_coverage_available": _median([r["coverage_pct"] for r in avail]),
    }


def _median(vals: list[float]) -> float:
    s = sorted(float(v) for v in vals)
    if not s:
        return 0.0
    mid = len(s) // 2
    return round(s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2, 2)


def write_oi_status_snapshot(private_root: Path, rows: list[dict[str, Any]]) -> Path:
    """Stable per-(symbol,tf) OI status so the farm stops re-polling structurally-absent OI."""
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "oi_status.json"
    by_key = {f"{r['symbol']}::{r['timeframe']}": r for r in rows}
    payload = {
        "schema": "oi_status.v1",
        "disclaimer": "Keyless public OKX OI coverage measurement. oi_available is the ONLY state "
                      "that lets an OI family run; OI never grants PAPER_FORWARD_READY. No fake-pass.",
        "summary": summarize_oi(rows), "by_symbol_timeframe": by_key,
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
    ap = argparse.ArgumentParser(description="Honest keyless OI coverage measurement (1h/4h).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--timeframes", default="1h,4h", help="comma list, e.g. 1h,4h,15m")
    ap.add_argument("--limit", type=int, default=12, help="symbols per timeframe (bounded)")
    ap.add_argument("--apply", action="store_true", help="write the oi field onto files when oi_available")
    args = ap.parse_args()
    tfs = tuple(t.strip() for t in args.timeframes.split(",") if t.strip())
    out = run_oi_enrichment(Path(args.private_root), timeframes=tfs, limit=args.limit, apply=args.apply)
    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    print("snapshot:", out["snapshot"])


if __name__ == "__main__":
    main()
