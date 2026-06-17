# -*- coding: utf-8 -*-
"""Persistent state for the continuous scanner -> farm coordinator.

A tiny SQLite store (its own file under the private ``state/`` dir, override for
tests) so the loop survives restarts without reprocessing: it remembers which
scanner watches it has consumed, which (instrument, timeframe, family) jobs it has
queued, which candles it prepared, every skip + reason, and per-cycle counters.

State only — it never fetches, queues, or touches an order path. Writes happen
only in --apply runs (the CLI passes a real path); --dry-run uses an in-memory
path so nothing is persisted.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = "scanner_farm_loop_state.v1"


def state_db_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "scanner_farm_loop.sqlite"


class PipelineState:
    """SQLite-backed checkpoint/dedup/counters for the scanner->farm loop."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS processed_watches (
                watch_id TEXT PRIMARY KEY, okx_inst TEXT, timeframe TEXT, cycle INTEGER, ts REAL
            );
            CREATE TABLE IF NOT EXISTS queued_jobs (
                job_key TEXT PRIMARY KEY, symbol TEXT, timeframe TEXT, family TEXT,
                experiment_id TEXT, cycle INTEGER, ts REAL
            );
            CREATE TABLE IF NOT EXISTS prepared_data (
                key TEXT PRIMARY KEY, okx_inst TEXT, timeframe TEXT, rows INTEGER, cycle INTEGER, ts REAL
            );
            CREATE TABLE IF NOT EXISTS skips (
                id INTEGER PRIMARY KEY AUTOINCREMENT, cycle INTEGER, symbol TEXT,
                timeframe TEXT, reason TEXT, ts REAL
            );
            CREATE TABLE IF NOT EXISTS cycles (
                cycle INTEGER PRIMARY KEY AUTOINCREMENT, started_ts REAL, finished_ts REAL,
                counters_json TEXT
            );
            """
        )
        self._conn.commit()

    # ── cycles ────────────────────────────────────────────────────────────────
    def start_cycle(self) -> int:
        cur = self._conn.execute("INSERT INTO cycles(started_ts) VALUES (?)", (time.time(),))
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_cycle(self, cycle: int, counters: dict[str, Any]) -> None:
        self._conn.execute(
            "UPDATE cycles SET finished_ts=?, counters_json=? WHERE cycle=?",
            (time.time(), json.dumps(counters, sort_keys=True), int(cycle)),
        )
        self._conn.commit()

    # ── checkpoint / dedup ──────────────────────────────────────────────────────
    def is_watch_processed(self, watch_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM processed_watches WHERE watch_id=?", (str(watch_id),)
        ).fetchone() is not None

    def mark_watch_processed(self, watch_id: str, *, okx_inst: str | None, timeframe: str | None,
                             cycle: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO processed_watches(watch_id, okx_inst, timeframe, cycle, ts)"
            " VALUES (?, ?, ?, ?, ?)",
            (str(watch_id), okx_inst, timeframe, int(cycle), time.time()),
        )
        self._conn.commit()

    def is_job_queued(self, job_key: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM queued_jobs WHERE job_key=?", (str(job_key),)
        ).fetchone() is not None

    def mark_job_queued(self, job_key: str, *, symbol: str | None, timeframe: str | None,
                        family: str | None, experiment_id: str | None, cycle: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO queued_jobs(job_key, symbol, timeframe, family, experiment_id,"
            " cycle, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(job_key), symbol, timeframe, family, experiment_id, int(cycle), time.time()),
        )
        self._conn.commit()

    def queued_insts(self) -> set[str]:
        return {str(r["symbol"]).upper() for r in self._conn.execute(
            "SELECT symbol FROM queued_jobs WHERE symbol IS NOT NULL")}

    def record_prepared(self, okx_inst: str, timeframe: str, *, rows: int, cycle: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO prepared_data(key, okx_inst, timeframe, rows, cycle, ts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (f"{okx_inst}::{timeframe}", okx_inst, timeframe, int(rows), int(cycle), time.time()),
        )
        self._conn.commit()

    def record_skip(self, cycle: int, *, symbol: str | None, timeframe: str | None, reason: str) -> None:
        self._conn.execute(
            "INSERT INTO skips(cycle, symbol, timeframe, reason, ts) VALUES (?, ?, ?, ?, ?)",
            (int(cycle), symbol, timeframe, str(reason), time.time()),
        )
        self._conn.commit()

    # ── status ──────────────────────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        def _scalar(sql: str) -> int:
            return int(self._conn.execute(sql).fetchone()[0])

        skip_reasons = {str(r["reason"]): int(r["n"]) for r in self._conn.execute(
            "SELECT reason, COUNT(*) AS n FROM skips GROUP BY reason ORDER BY n DESC")}
        last = self._conn.execute(
            "SELECT cycle, started_ts, finished_ts, counters_json FROM cycles"
            " WHERE finished_ts IS NOT NULL ORDER BY cycle DESC LIMIT 1").fetchone()
        last_cycle = None
        if last is not None:
            last_cycle = {
                "cycle": int(last["cycle"]),
                "finished_ts": last["finished_ts"],
                "age_seconds": round(time.time() - float(last["finished_ts"]), 1),
                "counters": json.loads(last["counters_json"] or "{}"),
            }
        return {
            "schema": SCHEMA,
            "db": self.path,
            "processed_watches": _scalar("SELECT COUNT(*) FROM processed_watches"),
            "queued_jobs": _scalar("SELECT COUNT(*) FROM queued_jobs"),
            "prepared_data": _scalar("SELECT COUNT(*) FROM prepared_data"),
            "total_cycles": _scalar("SELECT COUNT(*) FROM cycles"),
            "top_skip_reasons": skip_reasons,
            "last_cycle": last_cycle,
        }

    def close(self) -> None:
        self._conn.close()
