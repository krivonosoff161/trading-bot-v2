# -*- coding: utf-8 -*-
"""farm_tasks.sqlite — the coordinator BRAIN for the continuous research farm.

This is the lifecycle store that decides *what and when* to do. It is deliberately
separate from ``strategy_lab.sqlite`` (the proven compute queue the worker drains):
a ``run_sweep`` task here MATERIALIZES into that queue and links back via
``materialized_queue_job_id``. Everything else (intake, data planning, enrichment,
classification, validation, follow-ups) is tracked here as typed tasks with explicit
states and machine-readable reasons.

Why this kills the ``already_queued`` saturation:
  * a ``run_sweep`` task_key embeds the BASE-candle data fingerprint, so identical
    data within a TTL is never recomputed, but NEW candles (changed fingerprint)
    legitimately re-arm a fresh task;
  * ``deferred`` carries a concrete ``deferred_until`` so ``too_short`` / fresh
    listings stop being retried every cycle;
  * ``blocked`` carries a machine reason (e.g. ``NEEDS_OI_DATA``) and is flipped
    back to ``queued`` by an explicit unblock step when the gate clears.

State only. No fetch, no compute, no order path. Paper/research only.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.research_lab.farm_priority import priority_value

SCHEMA = "farm_tasks.v1"

# Task types in the research lifecycle (full graph; the coordinator creates the
# ones it needs each cycle). intake_event/resolve_instrument are reserved for
# forward compatibility — ingestion currently uses the intake_events table.
TASK_TYPES = (
    "intake_event", "resolve_instrument", "prepare_data", "enrich_funding",
    "enrich_oi", "run_sweep", "classify_result", "export_validation",
    "run_or_refresh_validation", "schedule_followup", "schedule_advisor_sweep",
)
TERMINAL_STATES = ("completed", "skipped", "failed")
ACTIVE_STATES = ("queued", "running", "deferred", "blocked")
ALL_STATES = ACTIVE_STATES + TERMINAL_STATES

DEFAULT_TTL_SECONDS = 12 * 3600  # identical data is not recomputed within this window


def tasks_db_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "farm_tasks.sqlite"


class FarmTasksDB:
    """SQLite-backed typed-task lifecycle + intake events + unique candidates."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        # Optional audit hook: set to a callable(record: dict) to log every state change.
        self.on_transition = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_key TEXT NOT NULL,
                task_type TEXT NOT NULL,
                state TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 100,
                symbol TEXT, asset_group TEXT, timeframe TEXT, family TEXT,
                params_hash TEXT, data_fingerprint TEXT,
                depends_on INTEGER,
                machine_reason TEXT NOT NULL DEFAULT '',
                deferred_until REAL,
                attempts INTEGER NOT NULL DEFAULT 0,
                source_event_id TEXT,
                materialized_queue_job_id INTEGER,
                last_result_ref TEXT,
                run_dir_label TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_active_key
                ON tasks(task_key)
                WHERE state IN ('queued', 'running', 'deferred', 'blocked');
            CREATE INDEX IF NOT EXISTS idx_tasks_state_pri
                ON tasks(state, priority, created_at);
            CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(task_type);

            CREATE TABLE IF NOT EXISTS intake_events (
                event_id TEXT PRIMARY KEY,
                symbol TEXT, source TEXT, reason TEXT,
                observed_at REAL, priority INTEGER,
                asset_class TEXT, suggested_timeframes TEXT,
                evidence_json TEXT, raw_ref_json TEXT,
                ingested_at REAL NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS unique_candidates (
                uc_key TEXT PRIMARY KEY,
                symbol TEXT, timeframe TEXT, family TEXT,
                params_hash TEXT, data_fingerprint TEXT,
                decision TEXT, validation_status TEXT, hard_status TEXT,
                n_trades INTEGER NOT NULL DEFAULT 0,
                avg_net_pct REAL NOT NULL DEFAULT 0,
                candidate_id TEXT, run_dir_label TEXT,
                params_json TEXT NOT NULL DEFAULT '{}',
                task_id INTEGER, paper_status TEXT NOT NULL DEFAULT '',
                regime_bucket TEXT NOT NULL DEFAULT '',
                search_family_id TEXT NOT NULL DEFAULT '',
                search_trial_id TEXT NOT NULL DEFAULT '',
                effective_n_trials INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS farm_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._migrate_priority_scale()
        self._migrate_unique_candidate_columns()
        self._conn.commit()

    def _migrate_priority_scale(self) -> None:
        """Translate legacy 1..4 rows; the new scale never emits those values."""
        mapping = "CASE priority WHEN 1 THEN 20 WHEN 2 THEN 30 WHEN 3 THEN 40 WHEN 4 THEN 90 END"
        self._conn.execute(
            f"UPDATE tasks SET priority={mapping} WHERE priority BETWEEN 1 AND 4"
        )
        self._conn.execute(
            f"UPDATE intake_events SET priority={mapping} WHERE priority BETWEEN 1 AND 4"
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO farm_meta(key, value) VALUES('priority_scale', 'v2')"
        )

    def _migrate_unique_candidate_columns(self) -> None:
        existing = {str(row["name"]) for row in self._conn.execute("PRAGMA table_info(unique_candidates)")}
        if "paper_status" not in existing:
            self._conn.execute("ALTER TABLE unique_candidates ADD COLUMN paper_status TEXT NOT NULL DEFAULT ''")
        if "params_json" not in existing:
            self._conn.execute("ALTER TABLE unique_candidates ADD COLUMN params_json TEXT NOT NULL DEFAULT '{}'")
        if "regime_bucket" not in existing:
            self._conn.execute("ALTER TABLE unique_candidates ADD COLUMN regime_bucket TEXT NOT NULL DEFAULT ''")
        if "search_family_id" not in existing:
            self._conn.execute("ALTER TABLE unique_candidates ADD COLUMN search_family_id TEXT NOT NULL DEFAULT ''")
        if "search_trial_id" not in existing:
            self._conn.execute("ALTER TABLE unique_candidates ADD COLUMN search_trial_id TEXT NOT NULL DEFAULT ''")
        if "effective_n_trials" not in existing:
            self._conn.execute("ALTER TABLE unique_candidates ADD COLUMN effective_n_trials INTEGER NOT NULL DEFAULT 0")

    # ── intake events ───────────────────────────────────────────────────────
    def upsert_intake_event(self, event: dict[str, Any], *, now: float | None = None) -> tuple[str, bool]:
        """Insert an intake event keyed by its dedup id. Returns (event_id, created)."""
        now = time.time() if now is None else now
        eid = str(event["event_id"])
        if self._conn.execute("SELECT 1 FROM intake_events WHERE event_id=?", (eid,)).fetchone():
            return eid, False
        self._conn.execute(
            """INSERT INTO intake_events(event_id, symbol, source, reason, observed_at,
                 priority, asset_class, suggested_timeframes, evidence_json, raw_ref_json,
                 ingested_at, consumed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
            (eid, event.get("symbol"), event.get("source"), event.get("reason"),
             event.get("observed_at"), priority_value(event.get("priority")),
             event.get("asset_class"), json.dumps(event.get("suggested_timeframes") or []),
             json.dumps(event.get("evidence") or {}), json.dumps(event.get("raw_ref") or {}), now),
        )
        self._conn.commit()
        return eid, True

    def unconsumed_events(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM intake_events WHERE consumed=0 ORDER BY priority ASC, observed_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [self._event_row(r) for r in rows]

    def mark_event_consumed(self, event_id: str) -> None:
        self._conn.execute("UPDATE intake_events SET consumed=1 WHERE event_id=?", (str(event_id),))
        self._conn.commit()

    @staticmethod
    def _event_row(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": r["event_id"], "symbol": r["symbol"], "source": r["source"],
            "reason": r["reason"], "observed_at": r["observed_at"], "priority": r["priority"],
            "asset_class": r["asset_class"],
            "suggested_timeframes": json.loads(r["suggested_timeframes"] or "[]"),
            "evidence": json.loads(r["evidence_json"] or "{}"),
            "raw_ref": json.loads(r["raw_ref_json"] or "{}"),
        }

    # ── task lifecycle ────────────────────────────────────────────────────────
    def enqueue_task(
        self, *, task_type: str, task_key: str, priority: int = 100,
        symbol: str | None = None, asset_group: str | None = None, timeframe: str | None = None,
        family: str | None = None, params_hash: str | None = None, data_fingerprint: str | None = None,
        depends_on: int | None = None, source_event_id: str | None = None,
        payload: dict[str, Any] | None = None, state: str = "queued", machine_reason: str = "",
        deferred_until: float | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: float | None = None,
    ) -> tuple[int, bool]:
        """Idempotent create with RE-ARM semantics. Returns (task_id, created).

        created=False when an ACTIVE task with this key exists, or when an identical
        task completed within ``ttl_seconds`` (no needless recompute). created=True
        otherwise — including a stale completion past TTL (re-arm) or a new key.
        """
        now = time.time() if now is None else now
        active = self._conn.execute(
            f"SELECT task_id, priority FROM tasks WHERE task_key=? AND state IN {ACTIVE_STATES} "
            "ORDER BY task_id ASC LIMIT 1", (task_key,),
        ).fetchone()
        if active is not None:
            if int(priority) < int(active["priority"]):
                self._conn.execute(
                    "UPDATE tasks SET priority=?, source_event_id=COALESCE(?, source_event_id), "
                    "updated_at=? WHERE task_id=?",
                    (int(priority), source_event_id, now, int(active["task_id"])),
                )
                self._conn.commit()
            return int(active["task_id"]), False
        done = self._conn.execute(
            "SELECT task_id, updated_at FROM tasks WHERE task_key=? AND state='completed' "
            "ORDER BY updated_at DESC LIMIT 1", (task_key,),
        ).fetchone()
        if done is not None and (now - float(done["updated_at"])) < ttl_seconds:
            return int(done["task_id"]), False
        try:
            cur = self._conn.execute(
                """INSERT INTO tasks(task_key, task_type, state, priority, symbol, asset_group,
                     timeframe, family, params_hash, data_fingerprint, depends_on, machine_reason,
                     deferred_until, source_event_id, payload_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_key, task_type, state, int(priority), symbol, asset_group, timeframe, family,
                 params_hash, data_fingerprint, depends_on, machine_reason, deferred_until,
                 source_event_id, json.dumps(payload or {}), now, now),
            )
        except sqlite3.IntegrityError:
            active = self._conn.execute(
                f"SELECT task_id, priority FROM tasks WHERE task_key=? AND state IN {ACTIVE_STATES} "
                "ORDER BY task_id ASC LIMIT 1", (task_key,),
            ).fetchone()
            if active is not None:
                if int(priority) < int(active["priority"]):
                    self._conn.execute(
                        "UPDATE tasks SET priority=?, source_event_id=COALESCE(?, source_event_id), "
                        "updated_at=? WHERE task_id=?",
                        (int(priority), source_event_id, now, int(active["task_id"])),
                    )
                    self._conn.commit()
                return int(active["task_id"]), False
            raise
        self._conn.commit()
        return int(cur.lastrowid), True

    def claim_next_task(self, *, task_types: tuple[str, ...] | None = None,
                        now: float | None = None) -> dict[str, Any] | None:
        """Claim the highest-priority eligible task and flip it to running.

        Eligible = queued, or deferred whose deferred_until has elapsed; and whose
        depends_on (if any) is completed. Never returns blocked/running tasks.
        """
        now = time.time() if now is None else now
        type_clause, params = "", [now]
        if task_types:
            type_clause = f" AND task_type IN ({','.join('?' * len(task_types))})"
            params.extend(task_types)
        row = self._conn.execute(
            f"""SELECT * FROM tasks
                WHERE (state='queued' OR (state='deferred' AND deferred_until<=?))
                  AND (depends_on IS NULL OR depends_on IN (SELECT task_id FROM tasks WHERE state='completed'))
                  {type_clause}
                ORDER BY priority ASC, created_at ASC, task_id ASC LIMIT 1""",
            params,
        ).fetchone()
        if row is None:
            return None
        tid = int(row["task_id"])
        self._conn.execute(
            "UPDATE tasks SET state='running', attempts=attempts+1, updated_at=? WHERE task_id=?",
            (now, tid),
        )
        self._conn.commit()
        self._emit_transition(tid, "running", "claimed", now)
        return self.get_task(tid)

    def eligible_count(self, now: float | None = None, *, task_types: tuple[str, ...] | None = None) -> int:
        """How many tasks are claimable right now (no mutation) — for pivot decisions."""
        now = time.time() if now is None else now
        clause, params = "", [now]
        if task_types:
            clause = f" AND task_type IN ({','.join('?' * len(task_types))})"
            params.extend(task_types)
        row = self._conn.execute(
            f"""SELECT COUNT(*) AS n FROM tasks
                WHERE (state='queued' OR (state='deferred' AND deferred_until<=?))
                  AND (depends_on IS NULL OR depends_on IN (SELECT task_id FROM tasks WHERE state='completed'))
                  {clause}""",
            params,
        ).fetchone()
        return int(row["n"])

    def active_symbols(self) -> set[str]:
        """Symbols with any active (queued/running/deferred/blocked) task."""
        rows = self._conn.execute(
            f"SELECT DISTINCT symbol FROM tasks WHERE symbol IS NOT NULL AND state IN {ACTIVE_STATES}"
        ).fetchall()
        return {str(r["symbol"]).upper() for r in rows}

    def status_counts(self) -> dict[str, Any]:
        """Operator-visible lifecycle counts: by state, by type, blocked/deferred reasons."""
        def _kv(sql: str) -> dict[str, int]:
            return {str(r[0] or "?"): int(r[1]) for r in self._conn.execute(sql)}
        return {
            "by_state": _kv("SELECT state, COUNT(*) FROM tasks GROUP BY state"),
            "by_type": _kv("SELECT task_type, COUNT(*) FROM tasks GROUP BY task_type"),
            "blocked_reasons": _kv(
                "SELECT machine_reason, COUNT(*) FROM tasks WHERE state='blocked' GROUP BY machine_reason"),
            "deferred_reasons": _kv(
                "SELECT machine_reason, COUNT(*) FROM tasks WHERE state='deferred' GROUP BY machine_reason"),
            "intake_unconsumed": int(self._conn.execute(
                "SELECT COUNT(*) FROM intake_events WHERE consumed=0").fetchone()[0]),
            "unique_candidates": int(self._conn.execute(
                "SELECT COUNT(*) FROM unique_candidates").fetchone()[0]),
        }

    def _set_state(self, task_id: int, state: str, *, reason: str = "",
                   now: float | None = None, **fields: Any) -> None:
        now = time.time() if now is None else now
        cols = ["state=?", "machine_reason=?", "updated_at=?"]
        vals: list[Any] = [state, reason, now]
        for key, value in fields.items():
            cols.append(f"{key}=?")
            vals.append(value)
        vals.append(int(task_id))
        self._conn.execute(f"UPDATE tasks SET {', '.join(cols)} WHERE task_id=?", vals)
        self._conn.commit()
        self._emit_transition(int(task_id), state, reason, now)

    def _emit_transition(self, task_id: int, state: str, reason: str, now: float) -> None:
        """Fire the optional audit hook with the post-update task identity (best-effort)."""
        if self.on_transition is None:
            return
        row = self._conn.execute(
            "SELECT task_key, task_type FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        try:
            self.on_transition({
                "ts": round(float(now), 3), "task_id": task_id,
                "task_key": row["task_key"] if row else None,
                "task_type": row["task_type"] if row else None,
                "to_state": state, "reason": reason,
            })
        except Exception:  # noqa: BLE001 - logging must never break a state transition
            pass

    def complete_task(self, task_id: int, *, last_result_ref: str | None = None,
                      run_dir_label: str | None = None, materialized_queue_job_id: int | None = None,
                      reason: str = "", now: float | None = None) -> None:
        self._set_state(task_id, "completed", reason=reason, now=now,
                        last_result_ref=last_result_ref, run_dir_label=run_dir_label,
                        materialized_queue_job_id=materialized_queue_job_id)

    def materialize_task(self, task_id: int, queue_job_id: int, *, now: float | None = None) -> None:
        """Mark a run_sweep task as materialized into the compute queue (awaiting worker)."""
        self._set_state(task_id, "running", reason="materialized_awaiting_worker", now=now,
                        materialized_queue_job_id=int(queue_job_id))

    def skip_task(self, task_id: int, reason: str, *, now: float | None = None) -> None:
        self._set_state(task_id, "skipped", reason=reason, now=now)

    def block_task(self, task_id: int, reason: str, *, now: float | None = None) -> None:
        self._set_state(task_id, "blocked", reason=reason, now=now)

    def fail_task(self, task_id: int, error: str, *, now: float | None = None) -> None:
        self._set_state(task_id, "failed", reason=str(error)[:500], now=now)

    def defer_task(self, task_id: int, until: float, reason: str, *, now: float | None = None) -> None:
        self._set_state(task_id, "deferred", reason=reason, now=now, deferred_until=float(until))

    def requeue_task(self, task_id: int, *, reason: str = "unblocked", now: float | None = None) -> None:
        self._set_state(task_id, "queued", reason=reason, now=now, deferred_until=None)

    def reconcile_orphan_running(self, *, reason: str = "orphan_running_requeued",
                                 now: float | None = None) -> int:
        """Requeue tasks stuck in 'running' left by a previous process stop. Safe ONLY when no worker
        is active (e.g. at loop startup): the single-process loop has no live worker at boot, so any
        'running' task is stale (materialized into the compute queue but never finished). Returns the
        count requeued so the next cycle re-drains them instead of masking them as active work. The
        worker's compute-memory dedup skips anything already computed, so this never double-counts."""
        now = time.time() if now is None else now
        rows = self._conn.execute("SELECT task_id FROM tasks WHERE state='running'").fetchall()
        for r in rows:
            self.requeue_task(int(r["task_id"]), reason=reason, now=now)
        return len(rows)

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE task_id=?", (int(task_id),)).fetchone()
        return dict(row) if row is not None else None

    def tasks_for_role_environment(self, environment_id: str) -> list[dict[str, Any]]:
        """Return tasks explicitly bound to one adaptive environment request."""
        needle = str(environment_id or "")
        if not needle:
            return []
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE payload_json LIKE ? ORDER BY task_id ASC",
            (f'%"role_environment_id": "{needle}"%',),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                payload = json.loads(item.get("payload_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if str(payload.get("role_environment_id") or "") == needle:
                out.append(item)
        return out

    def tasks_in_state(self, state: str, *, task_type: str | None = None) -> list[dict[str, Any]]:
        if task_type:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE state=? AND task_type=? ORDER BY task_id ASC",
                (state, task_type)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE state=? ORDER BY task_id ASC", (state,)).fetchall()
        return [dict(r) for r in rows]

    # ── unique candidates ───────────────────────────────────────────────────
    def upsert_unique_candidate(self, cand: dict[str, Any], *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._conn.execute(
            """INSERT INTO unique_candidates(uc_key, symbol, timeframe, family, params_hash,
                 data_fingerprint, decision, validation_status, hard_status, n_trades,
                 avg_net_pct, candidate_id, run_dir_label, params_json, task_id, paper_status,
                 regime_bucket, search_family_id, search_trial_id, effective_n_trials, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(uc_key) DO UPDATE SET
                 decision=excluded.decision, validation_status=excluded.validation_status,
                 hard_status=excluded.hard_status, n_trades=excluded.n_trades,
                 avg_net_pct=excluded.avg_net_pct, candidate_id=excluded.candidate_id,
                 run_dir_label=excluded.run_dir_label, task_id=excluded.task_id,
                 params_json=excluded.params_json,
                 paper_status=COALESCE(NULLIF(excluded.paper_status, ''), unique_candidates.paper_status),
                 regime_bucket=COALESCE(NULLIF(excluded.regime_bucket, ''), unique_candidates.regime_bucket),
                 search_family_id=COALESCE(NULLIF(excluded.search_family_id, ''), unique_candidates.search_family_id),
                 search_trial_id=COALESCE(NULLIF(excluded.search_trial_id, ''), unique_candidates.search_trial_id),
                 effective_n_trials=CASE WHEN excluded.effective_n_trials > 0
                   THEN excluded.effective_n_trials ELSE unique_candidates.effective_n_trials END,
                 updated_at=excluded.updated_at""",
            (cand["uc_key"], cand.get("symbol"), cand.get("timeframe"), cand.get("family"),
             cand.get("params_hash"), cand.get("data_fingerprint"), cand.get("decision"),
             cand.get("validation_status"), cand.get("hard_status"), int(cand.get("n_trades") or 0),
             float(cand.get("avg_net_pct") or 0), cand.get("candidate_id"),
             cand.get("run_dir_label"), json.dumps(cand.get("params") or {}),
             cand.get("task_id"), cand.get("paper_status") or "",
             str(cand.get("regime_bucket") or ""),
             str(cand.get("search_family_id") or ""),
             str(cand.get("search_trial_id") or ""),
             int(cand.get("effective_n_trials") or 0), now),
        )
        self._conn.commit()

    def set_candidate_hard_status(self, candidate_id: str, hard_status: str, *,
                                  now: float | None = None) -> int:
        now = time.time() if now is None else now
        cur = self._conn.execute(
            "UPDATE unique_candidates SET hard_status=?, updated_at=? WHERE candidate_id=?",
            (hard_status, now, str(candidate_id)))
        self._conn.commit()
        return int(cur.rowcount or 0)

    def set_unique_hard_status(self, uc_key: str, hard_status: str, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        cur = self._conn.execute(
            "UPDATE unique_candidates SET hard_status=?, updated_at=? WHERE uc_key=?",
            (hard_status, now, str(uc_key)))
        self._conn.commit()
        return int(cur.rowcount or 0)

    def set_unique_paper_status(self, uc_key: str, paper_status: str, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        cur = self._conn.execute(
            "UPDATE unique_candidates SET paper_status=?, updated_at=? WHERE uc_key=?",
            (paper_status, now, str(uc_key)))
        self._conn.commit()
        return int(cur.rowcount or 0)

    def prune_terminal_tasks(self, *, keep: int = 5000, apply: bool = False) -> int:
        """Bound history: keep the newest ``keep`` terminal tasks, drop older. Returns removed."""
        total = int(self._conn.execute(
            f"SELECT COUNT(*) FROM tasks WHERE state IN {TERMINAL_STATES}").fetchone()[0])
        excess = max(0, total - int(keep))
        if not excess:
            return 0
        if not apply:
            return excess
        self._conn.execute(
            f"""DELETE FROM tasks WHERE task_id IN (
                  SELECT task_id FROM tasks WHERE state IN {TERMINAL_STATES}
                  ORDER BY updated_at ASC, task_id ASC LIMIT ?)""",
            (excess,))
        self._conn.execute("DELETE FROM intake_events WHERE consumed=1")
        self._conn.commit()
        return excess

    def prune_unique_candidates(self, *, keep: int = 5000, apply: bool = False) -> int:
        """Bound the unique-candidate history (re-arm adds a row per new fingerprint)."""
        total = int(self._conn.execute("SELECT COUNT(*) FROM unique_candidates").fetchone()[0])
        excess = max(0, total - int(keep))
        if not excess or not apply:
            return excess
        self._conn.execute(
            """DELETE FROM unique_candidates WHERE uc_key IN (
                 SELECT uc_key FROM unique_candidates ORDER BY updated_at ASC LIMIT ?)""",
            (excess,))
        self._conn.commit()
        return excess

    def unique_candidates_for_gate(self) -> list[dict[str, Any]]:
        """Lean read of every candidate (the read-through gate aggregates per fingerprint/cell)."""
        rows = self._conn.execute(
            "SELECT symbol, timeframe, family, params_hash, data_fingerprint, decision, "
            "validation_status, hard_status, n_trades, avg_net_pct, regime_bucket, updated_at "
            "FROM unique_candidates").fetchall()
        return [dict(r) for r in rows]

    def latest_unique_candidates(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Freshest result per (symbol, timeframe, family, params_hash) — no dups."""
        rows = self._conn.execute(
            """SELECT uc.* FROM unique_candidates uc
               JOIN (SELECT symbol, timeframe, family, params_hash, MAX(updated_at) AS mu
                     FROM unique_candidates GROUP BY symbol, timeframe, family, params_hash) latest
               ON uc.symbol=latest.symbol AND uc.timeframe=latest.timeframe
                  AND uc.family=latest.family AND uc.params_hash=latest.params_hash
                  AND uc.updated_at=latest.mu
               ORDER BY uc.updated_at DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
