# -*- coding: utf-8 -*-
"""Read-only farm status report — what the calculation farm computed and what's next.

Reads strategy_lab.sqlite (runs / candidates / farm_results / runtime_stats) and
prints a clear operator summary: how much was computed, which assets/groups/timeframes,
the CPU/GPU backend split, the decision + validation breakdown, what was promoted vs
rejected and why, the queue state, and which candidates are ready for hard validation.

    python -m scripts.strategy_lab.farm_status_report
    python -m scripts.strategy_lab.farm_status_report --json

Never writes anything, never touches the network, orders, .env, or private endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402
from src.research_lab.state_db import default_db_path  # noqa: E402

READY_FOR_VALIDATION = ("FORWARD_PAPER", "REGIME_SPECIFIC")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _counts(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    return {str(r[0] or "UNKNOWN"): int(r[1]) for r in conn.execute(sql)}


def _has_rows(conn: sqlite3.Connection, table: str) -> bool:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) > 0
    except sqlite3.Error:
        return False


def collect(db_path: Path) -> dict:
    if not db_path.exists():
        return {"exists": False, "db": str(db_path)}
    conn = _connect(db_path)
    try:
        report: dict = {
            "exists": True,
            "totals": {
                "runs": int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]),
                "candidates": int(conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]),
                "farm_results": int(conn.execute("SELECT COUNT(*) FROM farm_results").fetchone()[0]),
            },
            "queue": _counts(conn, "SELECT status, COUNT(*) FROM queue GROUP BY status"),
            "decisions": _counts(conn, "SELECT decision, COUNT(*) FROM candidates GROUP BY decision"),
            "validation": _counts(conn, "SELECT validation_status, COUNT(*) FROM candidates GROUP BY validation_status"),
        }
        if _has_rows(conn, "farm_results"):
            report["by_group"] = [dict(r) for r in conn.execute(
                "SELECT asset_group, family, decision, COUNT(*) n, ROUND(AVG(avg_net_pct),4) avg_net "
                "FROM farm_results GROUP BY asset_group, family, decision ORDER BY asset_group, family")]
            report["by_timeframe"] = _counts(conn, "SELECT timeframe, COUNT(*) FROM farm_results GROUP BY timeframe")
            report["data_quality"] = _counts(conn, "SELECT data_quality, COUNT(*) FROM farm_results GROUP BY data_quality")
        if _has_rows(conn, "runtime_stats"):
            report["backend"] = [dict(r) for r in conn.execute(
                "SELECT effective_backend, signal_backend, simulation_backend, "
                "SUM(gpu_available) gpu_runs, COUNT(*) runs, "
                "GROUP_CONCAT(DISTINCT fallback_reason) fallbacks "
                "FROM runtime_stats GROUP BY effective_backend, signal_backend, simulation_backend")]
        report["ready_for_validation"] = [dict(r) for r in conn.execute(
            "SELECT symbol, family, validation_status, next_action FROM candidates "
            "WHERE validation_status IN (?, ?) ORDER BY validation_status DESC LIMIT 30",
            READY_FOR_VALIDATION)]
        report["recent_runs"] = [dict(r) for r in conn.execute(
            "SELECT run_id, candidate_count, promote_count, observe_count, reject_count "
            "FROM runs ORDER BY run_id DESC LIMIT 8")]
        return report
    finally:
        conn.close()


def _print(report: dict) -> None:
    if not report.get("exists"):
        print(f"no farm DB yet at {report.get('db')} — run universe_farm_loop --apply first")
        return
    t = report["totals"]
    print(f"FARM STATUS - runs={t['runs']} candidates={t['candidates']} farm_results={t['farm_results']}")
    print(f"  queue: {report['queue'] or '(empty)'}")
    print(f"  decisions: {report['decisions'] or '(none)'}")
    print(f"  validation: {report['validation'] or '(none)'}")
    if report.get("by_timeframe"):
        print(f"  timeframes: {report['by_timeframe']}")
    if report.get("data_quality"):
        print(f"  data quality: {report['data_quality']}")
    for b in report.get("backend", []):
        fb = b.get("fallbacks") or "-"
        print(f"  backend eff={b['effective_backend'] or '?'} signal={b['signal_backend'] or '?'} "
              f"sim={b['simulation_backend'] or '?'} gpu_runs={b['gpu_runs']}/{b['runs']} fallback={fb}")
    rows = report.get("by_group") or []
    if rows:
        print("  by group/family/decision:")
        for r in rows[:30]:
            print(f"    {r['asset_group'] or '-':16s} {r['family']:32s} {r['decision']:24s} "
                  f"n={r['n']} avg_net={r['avg_net']}")
    ready = report.get("ready_for_validation") or []
    print(f"  ready for hard validation: {len(ready)}")
    for r in ready[:12]:
        print(f"    {r['symbol']} {r['family']} -> {r['validation_status']} ({r['next_action']})")
    if not ready:
        print("    (none yet - all candidates rejected/observe; expected on weak/synthetic data)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--json", action="store_true", help="emit the raw report as JSON")
    args = ap.parse_args()
    report = collect(default_db_path(Path(args.private_root)))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print(report)


if __name__ == "__main__":
    main()
