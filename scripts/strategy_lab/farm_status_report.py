# -*- coding: utf-8 -*-
"""Read-only farm status report — what the calculation farm computed and what's next.

Reads strategy_lab.sqlite (runs / candidates / farm_results / runtime_stats) and
prints a clear operator summary: how much was computed, which assets/groups/timeframes,
the CPU/GPU backend split, the decision + validation breakdown, what was promoted vs
rejected and why, the queue state + age, flow coverage, and which candidates are ready
for hard validation (deduped). Migration-safe: it runs init_db so an old DB without the
v3 tables is upgraded non-destructively instead of crashing.

    python -m scripts.strategy_lab.farm_status_report
    python -m scripts.strategy_lab.farm_status_report --json

Never writes results, never touches the network, orders, .env, or private endpoints.
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
from src.research_lab.state_db import default_db_path, init_db  # noqa: E402

READY_FOR_VALIDATION = ("FORWARD_PAPER", "REGIME_SPECIFIC")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _counts(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    return {str(r[0] or "UNKNOWN"): int(r[1]) for r in conn.execute(sql)}


def _scalar(conn: sqlite3.Connection, sql: str, default=0):
    row = conn.execute(sql).fetchone()
    return row[0] if row and row[0] is not None else default


def _has_rows(conn: sqlite3.Connection, table: str) -> bool:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) > 0
    except sqlite3.Error:
        return False


def _flow_coverage(db_path: Path) -> dict:
    """Funding/OI enrichment coverage from the loop's state file (if present)."""
    path = db_path.parent / "flow_enrich_state.json"
    if not path.exists():
        return {"tracked": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"tracked": False}
    counts: dict[str, int] = {}
    for entry in (data.get("entries") or {}).values():
        s = str(entry.get("status") or "unknown")
        counts[s] = counts.get(s, 0) + 1
    return {"tracked": True, "funding": counts}


def _ready_for_validation(conn: sqlite3.Connection) -> list[dict]:
    """Deduped FORWARD_PAPER/REGIME_SPECIFIC candidates, latest per (symbol, family, tf)."""
    if _has_rows(conn, "farm_results"):
        rows = conn.execute(
            "SELECT symbol, family, timeframe, validation_status, next_action, MAX(created_at) latest "
            "FROM farm_results WHERE validation_status IN (?, ?) "
            "GROUP BY symbol, family, timeframe ORDER BY validation_status DESC, latest DESC LIMIT 40",
            READY_FOR_VALIDATION).fetchall()
        return [dict(r) for r in rows]
    rows = conn.execute(
        "SELECT symbol, family, '' timeframe, validation_status, next_action "
        "FROM candidates WHERE validation_status IN (?, ?) "
        "GROUP BY symbol, family ORDER BY validation_status DESC LIMIT 40",
        READY_FOR_VALIDATION).fetchall()
    return [dict(r) for r in rows]


def collect(db_path: Path) -> dict:
    if not db_path.exists():
        return {"exists": False, "db": str(db_path)}
    conn = _connect(db_path)
    init_db(conn)  # migration-safe: upgrade old DBs (adds v3 tables), non-destructive
    try:
        unique_table = "farm_results" if _has_rows(conn, "farm_results") else "candidates"
        report: dict = {
            "exists": True,
            "schema_version": int(_scalar(conn, "SELECT value FROM meta WHERE key='schema_version'", 0)),
            "totals": {
                "runs": int(_scalar(conn, "SELECT COUNT(*) FROM runs")),
                "candidates": int(_scalar(conn, "SELECT COUNT(*) FROM candidates")),
                "farm_results": int(_scalar(conn, "SELECT COUNT(*) FROM farm_results")),
                "unique_candidates": int(_scalar(
                    conn, f"SELECT COUNT(DISTINCT symbol || '|' || family) FROM {unique_table}")),
            },
            "queue": _counts(conn, "SELECT status, COUNT(*) FROM queue GROUP BY status"),
            "queue_age": {
                "oldest_queued": _scalar(conn, "SELECT MIN(created_at) FROM queue WHERE status='queued'", None),
                "newest_queued": _scalar(conn, "SELECT MAX(created_at) FROM queue WHERE status='queued'", None),
            },
            "latest_run": dict(conn.execute(
                "SELECT run_id, created_at FROM runs ORDER BY run_id DESC LIMIT 1").fetchone() or {}),
            "decisions": _counts(conn, "SELECT decision, COUNT(*) FROM candidates GROUP BY decision"),
            "validation": _counts(conn, "SELECT validation_status, COUNT(*) FROM candidates GROUP BY validation_status"),
            "flow_coverage": _flow_coverage(db_path),
        }
        if _has_rows(conn, "farm_results"):
            report["by_group"] = [dict(r) for r in conn.execute(
                "SELECT asset_group, family, decision, COUNT(*) n, ROUND(AVG(avg_net_pct),4) avg_net "
                "FROM farm_results GROUP BY asset_group, family, decision ORDER BY asset_group, family")]
            report["by_timeframe"] = _counts(conn, "SELECT timeframe, COUNT(*) FROM farm_results GROUP BY timeframe")
            report["data_quality"] = _counts(conn, "SELECT data_quality, COUNT(*) FROM farm_results GROUP BY data_quality")
            report["handoff"] = {
                "validation_exported": int(_scalar(
                    conn, "SELECT COUNT(*) FROM farm_results WHERE validation_exported = 1")),
                "hard_status": _counts(
                    conn, "SELECT hard_status, COUNT(*) FROM farm_results WHERE hard_status <> '' GROUP BY hard_status"),
                "paper_status": _counts(
                    conn, "SELECT paper_status, COUNT(*) FROM farm_results WHERE paper_status <> '' GROUP BY paper_status"),
                "paper_outcomes": int(_scalar(conn, "SELECT COUNT(*) FROM paper_outcomes")),
                "gpu_signal_rows": int(_scalar(
                    conn, "SELECT COALESCE(SUM(gpu_signal_supported), 0) FROM farm_results")),
                "needs_data": _counts(
                    conn, "SELECT decision, COUNT(*) FROM farm_results WHERE decision LIKE 'NEEDS_%' GROUP BY decision"),
            }
        if _has_rows(conn, "runtime_stats"):
            report["backend"] = [dict(r) for r in conn.execute(
                "SELECT effective_backend, signal_backend, simulation_backend, "
                "SUM(gpu_available) gpu_runs, COUNT(*) runs, "
                "GROUP_CONCAT(DISTINCT fallback_reason) fallbacks "
                "FROM runtime_stats GROUP BY effective_backend, signal_backend, simulation_backend")]
        report["ready_for_validation"] = _ready_for_validation(conn)
        try:  # the continuous-farm lifecycle (farm_tasks.sqlite), if the new loop has run
            from src.research_lab.farm_cockpit import _lifecycle_section
            report["lifecycle"] = _lifecycle_section(db_path.parent.parent)
        except Exception:  # noqa: BLE001 - report must never crash on the optional new DB
            report["lifecycle"] = {"available": False}
        report["recent_runs"] = [dict(r) for r in conn.execute(
            "SELECT run_id, candidate_count, promote_count, observe_count, reject_count "
            "FROM runs ORDER BY run_id DESC LIMIT 8")]
        return report
    finally:
        conn.close()


def _print(report: dict) -> None:
    if not report.get("exists"):
        print(f"no farm DB yet at {report.get('db')} - run the farm first: "
              "python -m scripts.strategy_lab.farm_loop --once --apply --run-worker")
        return
    t = report["totals"]
    print(f"FARM STATUS - schema v{report['schema_version']} | runs={t['runs']} candidates={t['candidates']} "
          f"farm_results={t['farm_results']} unique={t['unique_candidates']}")
    lr = report.get("latest_run") or {}
    print(f"  latest run: {lr.get('run_id', '-')} @ {lr.get('created_at', '-')}")
    print(f"  queue: {report['queue'] or '(empty)'}  age[{report['queue_age']['oldest_queued'] or '-'} .. "
          f"{report['queue_age']['newest_queued'] or '-'}]")
    print(f"  decisions: {report['decisions'] or '(none)'}")
    print(f"  validation: {report['validation'] or '(none)'}")
    if report.get("by_timeframe"):
        print(f"  timeframes: {report['by_timeframe']}")
    if report.get("data_quality"):
        print(f"  data quality: {report['data_quality']}")
    fc = report.get("flow_coverage") or {}
    print(f"  flow coverage: {fc.get('funding') if fc.get('tracked') else 'not tracked yet'}")
    lc = report.get("lifecycle") or {}
    if lc.get("available"):
        print(f"  lifecycle tasks: {lc.get('by_state') or '(none)'}")
        print(f"    by type: {lc.get('by_task_type') or '(none)'}")
        if lc.get("blocked_reasons"):
            print(f"    blocked: {lc['blocked_reasons']}")
        if lc.get("deferred_reasons"):
            print(f"    deferred: {lc['deferred_reasons']}")
        print(f"    intake_unconsumed={lc.get('intake_unconsumed', 0)} "
              f"calcs_today={lc.get('calcs_completed_today', 0)} "
              f"unique_candidates={lc.get('unique_candidates', 0)} "
              f"validation={lc.get('validation') or '(none)'}")
    ho = report.get("handoff")
    if ho:
        print(f"  validation handoff: exported={ho['validation_exported']} "
              f"hard_status={ho['hard_status'] or '(none)'} needs_data={ho['needs_data'] or '(none)'} "
              f"gpu_signal_rows={ho['gpu_signal_rows']}")
        print(f"  paper handoff: outcomes={ho.get('paper_outcomes', 0)} "
              f"paper_status={ho.get('paper_status') or '(none)'}")
    for b in report.get("backend", []):
        print(f"  backend eff={b['effective_backend'] or '?'} signal={b['signal_backend'] or '?'} "
              f"sim={b['simulation_backend'] or '?'} gpu_runs={b['gpu_runs']}/{b['runs']} "
              f"fallback={b.get('fallbacks') or '-'}")
    rows = report.get("by_group") or []
    if rows:
        print("  by group/family/decision:")
        for r in rows[:30]:
            print(f"    {r['asset_group'] or '-':16s} {r['family']:32s} {r['decision']:24s} "
                  f"n={r['n']} avg_net={r['avg_net']}")
    ready = report.get("ready_for_validation") or []
    print(f"  ready for hard validation (deduped): {len(ready)}")
    for r in ready[:12]:
        print(f"    {r['symbol']} {r['family']} {r.get('timeframe') or ''} -> {r['validation_status']}")
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
