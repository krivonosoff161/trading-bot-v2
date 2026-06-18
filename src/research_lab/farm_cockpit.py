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


def build_cockpit(private_root: Path) -> dict[str, Any]:
    """Read-only operator cockpit snapshot for the calculation farm."""
    private_root = Path(private_root).expanduser()
    db_path = default_db_path(private_root)
    return {
        "schema": "strategy_lab_farm_cockpit.v1",
        "loop_state": _loop_state(private_root),
        "data_readiness": _data_readiness(private_root),
        "gpu_cpu": _gpu_cpu_section(db_path),
        "results": _results_section(db_path),
        "universe_coverage": _universe_coverage(private_root, db_path),
        "safety": {"read_only": True, "secrets_exposed": False, "network": False},
    }
