# -*- coding: utf-8 -*-
"""Operator cockpit view of the calculation farm (read-only, dashboard-safe).

Aggregates, without writing anything, the farm's operating state so the dashboard
can answer "what is running, what was calculated, what is next" without log-reading:
loop state, data readiness, GPU/CPU split, results + validation handoff, and universe
coverage (manual vs OKX-discovered). Defensive: a missing table/column/file degrades
to an empty section instead of crashing, so it works on old and new DBs alike. No
secrets, no absolute paths (labels only), no network.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.research_lab.paths import market_data_dir
from src.research_lab.state_db import default_db_path


def _ro_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _try_counts(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    try:
        return {str(r[0] or "UNKNOWN"): int(r[1]) for r in conn.execute(sql)}
    except sqlite3.Error:
        return {}


def _try_scalar(conn: sqlite3.Connection, sql: str, default: Any = 0) -> Any:
    try:
        row = conn.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    except sqlite3.Error:
        return default


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _results_section(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False}
    try:
        conn = _ro_conn(db_path)
    except sqlite3.Error:
        return {"available": False}
    try:
        return {
            "available": True,
            "decisions": _try_counts(conn, "SELECT decision, COUNT(*) FROM farm_results GROUP BY decision"),
            "validation": _try_counts(
                conn, "SELECT validation_status, COUNT(*) FROM farm_results GROUP BY validation_status"),
            "needs_data": _try_counts(
                conn, "SELECT decision, COUNT(*) FROM farm_results WHERE decision LIKE 'NEEDS_%' GROUP BY decision"),
            "exported": int(_try_scalar(conn, "SELECT COUNT(*) FROM farm_results WHERE validation_exported=1")),
            "hard_status": _try_counts(
                conn, "SELECT hard_status, COUNT(*) FROM farm_results WHERE hard_status<>'' GROUP BY hard_status"),
            "paper_status": _try_counts(
                conn, "SELECT paper_status, COUNT(*) FROM farm_results WHERE paper_status<>'' GROUP BY paper_status"),
            "paper_outcomes": int(_try_scalar(conn, "SELECT COUNT(*) FROM paper_outcomes")),
            "unique_symbols": int(_try_scalar(conn, "SELECT COUNT(DISTINCT symbol) FROM farm_results")),
            "by_group": _try_counts(conn, "SELECT asset_group, COUNT(*) FROM farm_results GROUP BY asset_group"),
        }
    finally:
        conn.close()


def _gpu_cpu_section(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False}
    try:
        conn = _ro_conn(db_path)
    except sqlite3.Error:
        return {"available": False}
    try:
        rows = []
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT effective_backend, signal_backend, simulation_backend, "
                "SUM(gpu_available) gpu_runs, COUNT(*) runs FROM runtime_stats "
                "GROUP BY effective_backend, signal_backend, simulation_backend")]
        except sqlite3.Error:
            rows = []
        return {"available": bool(rows), "backends": rows,
                "gpu_signal_rows": int(_try_scalar(
                    conn, "SELECT COALESCE(SUM(gpu_signal_supported),0) FROM farm_results"))}
    finally:
        conn.close()


def _data_readiness(private_root: Path) -> dict[str, Any]:
    prepared = {}
    for tf in ("15m", "1h", "4h", "1d", "1m"):
        d = market_data_dir(private_root, tf)
        prepared[tf] = len(list(d.glob("*.json"))) if d.exists() else 0
    flow = _read_json(private_root / "state" / "flow_enrich_state.json")
    funding_status: dict[str, int] = {}
    for entry in (flow.get("entries") or {}).values():
        s = str(entry.get("status") or "unknown")
        funding_status[s] = funding_status.get(s, 0) + 1
    oi_dir = private_root / "market_data" / "oi"
    return {
        "prepared_files_by_timeframe": prepared,
        "funding_enrich_status": funding_status,
        "oi_slot_files": len(list(oi_dir.glob("*_oi.*"))) if oi_dir.exists() else 0,
    }


def _loop_state(private_root: Path) -> dict[str, Any]:
    refill = _read_json(private_root / "state" / "universe_refill_state.json")
    return {
        "refill_cursor": int(refill.get("cursor") or 0),
        "refill_backoff_symbols": len(refill.get("failures") or {}),
    }


def _universe_coverage(private_root: Path, db_path: Path) -> dict[str, Any]:
    from src.research_lab.instrument_discovery import load_snapshot
    manual = {"groups": 0, "symbols": 0}
    try:
        from src.research_lab.universe import load_universe
        uni = load_universe()
        manual = {"groups": len(uni.groups), "symbols": len(uni.all_symbols())}
    except Exception:  # noqa: BLE001 - config optional
        pass
    snap = load_snapshot(private_root)
    discovered = {"count": int(snap.get("count") or 0),
                  "group_sizes": {g: len(v) for g, v in (snap.get("groups") or {}).items()},
                  "generated_at": str(snap.get("generated_at") or "")}
    processed = 0
    if db_path.exists():
        try:
            conn = _ro_conn(db_path)
            processed = int(_try_scalar(conn, "SELECT COUNT(DISTINCT symbol) FROM farm_results"))
            conn.close()
        except sqlite3.Error:
            processed = 0
    unprocessed_discovered = max(0, discovered["count"] - processed)
    return {"manual": manual, "discovered": discovered,
            "symbols_processed": processed, "discovered_not_yet_processed": unprocessed_discovered}


def _start_of_utc_day(now: float) -> float:
    import datetime as dt
    d = dt.datetime.fromtimestamp(now, dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return d.timestamp()


def _lifecycle_section(private_root: Path) -> dict[str, Any]:
    """The continuous-farm task lifecycle from farm_tasks.sqlite (intake -> validation)."""
    from src.research_lab.farm_tasks_db import tasks_db_path
    db_path = tasks_db_path(private_root)
    if not db_path.exists():
        return {"available": False}
    try:
        conn = _ro_conn(db_path)
    except sqlite3.Error:
        return {"available": False}
    try:
        import time as _time
        today = _start_of_utc_day(_time.time())
        calcs_today = _try_scalar(
            conn, "SELECT COUNT(*) FROM tasks WHERE task_type='run_sweep' AND state='completed' "
                  f"AND updated_at>={today}")
        return {
            "available": True,
            "by_state": _try_counts(conn, "SELECT state, COUNT(*) FROM tasks GROUP BY state"),
            "by_task_type": _try_counts(conn, "SELECT task_type, COUNT(*) FROM tasks GROUP BY task_type"),
            "blocked_reasons": _try_counts(
                conn, "SELECT machine_reason, COUNT(*) FROM tasks WHERE state='blocked' GROUP BY machine_reason"),
            "deferred_reasons": _try_counts(
                conn, "SELECT machine_reason, COUNT(*) FROM tasks WHERE state='deferred' GROUP BY machine_reason"),
            "intake_unconsumed": int(_try_scalar(conn, "SELECT COUNT(*) FROM intake_events WHERE consumed=0")),
            "calcs_completed_today": int(calcs_today),
            "unique_candidates": int(_try_scalar(conn, "SELECT COUNT(*) FROM unique_candidates")),
            "validation": _try_counts(
                conn, "SELECT hard_status, COUNT(*) FROM unique_candidates WHERE hard_status<>'' "
                      "GROUP BY hard_status"),
            "paper_status": _try_counts(
                conn, "SELECT paper_status, COUNT(*) FROM unique_candidates WHERE paper_status<>'' "
                      "GROUP BY paper_status"),
            "export_followups": int(_try_scalar(
                conn, "SELECT COUNT(*) FROM tasks WHERE task_type='export_validation'")),
        }
    finally:
        conn.close()


def _farm_activity_section(private_root: Path) -> dict[str, Any]:
    """Recent cycle history, heartbeat, skipped stages, discovery freshness and errors.

    Surfaces the structured farm logs (cycle_log/errors) that were previously written
    but read by nothing, so an operator can see the loop is alive and what it skipped.
    """
    from src.research_lab.farm_journal import (
        read_recent_cycles,
        read_recent_errors,
        skipped_stages,
    )
    cycles = read_recent_cycles(private_root, limit=10)
    errors = read_recent_errors(private_root, limit=10)
    if not cycles:
        return {"available": False, "recent_errors": errors[-5:], "error_count": len(errors)}
    import time as _time
    last = cycles[-1]
    age = int(_time.time() - float(last.get("ts") or 0))
    return {
        "available": True,
        "last_cycle_age_seconds": age,
        "heartbeat_ok": age < 3600,  # a cycle within the last hour = alive
        "last_pivot": last.get("pivot"),
        "last_mode": last.get("mode"),
        "skipped_stages": skipped_stages(last),
        "discovery": last.get("discovery") or {},
        "recent_cycles": [
            {"ts": c.get("ts"), "pivot": c.get("pivot"),
             "counters": c.get("counters"), "active_tasks": c.get("active_tasks")}
            for c in cycles[-8:]
        ],
        "recent_errors": [
            {"ts": e.get("ts"), "where": e.get("where"), "error": (e.get("error") or "")[:160]}
            for e in errors[-5:]
        ],
        "error_count": len(errors),
    }


def _paper_pnl_section(private_root: Path) -> dict[str, Any]:
    """Aggregate paper P&L/win-rate from the append-only paper journal (read-only).

    Research evidence only - net_pct is a backtest-window outcome, not a profit claim.
    """
    from src.research_lab.paper_journal import read_paper_outcomes
    rows = read_paper_outcomes(private_root)
    closed = [r for r in rows if r.get("net_pct") is not None]
    n = len(closed)
    if not n:
        return {"available": False, "n_trades": 0}
    nets = [float(r.get("net_pct") or 0.0) for r in closed]
    rs = [float(r.get("r_multiple") or 0.0) for r in closed]
    wins = sum(1 for x in nets if x > 0)
    return {
        "available": True,
        "n_trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": round(wins / n, 4),
        "avg_net_pct": round(sum(nets) / n, 4),
        "net_sum_pct": round(sum(nets), 4),
        "avg_r_multiple": round(sum(rs) / n, 4),
    }


def build_cockpit(private_root: Path) -> dict[str, Any]:
    """Read-only operator cockpit snapshot for the calculation farm."""
    private_root = Path(private_root).expanduser()
    db_path = default_db_path(private_root)
    return {
        "schema": "strategy_lab_farm_cockpit.v2",
        "loop_state": _loop_state(private_root),
        "farm_activity": _farm_activity_section(private_root),
        "lifecycle": _lifecycle_section(private_root),
        "paper_pnl": _paper_pnl_section(private_root),
        "data_readiness": _data_readiness(private_root),
        "gpu_cpu": _gpu_cpu_section(db_path),
        "results": _results_section(db_path),
        "universe_coverage": _universe_coverage(private_root, db_path),
        "safety": {"read_only": True, "secrets_exposed": False, "network": False,
                   "live_trading": False},
    }
