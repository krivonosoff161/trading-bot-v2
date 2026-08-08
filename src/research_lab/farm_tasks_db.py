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
  * scheduled ``deferred`` work carries a concrete ``deferred_until`` so
    ``too_short`` / fresh listings stop being retried every cycle; a durably
    materialized sweep is instead parked with ``deferred_until=NULL`` while
    the independent fenced compute queue owns execution;
  * ``blocked`` carries a machine reason (e.g. ``NEEDS_OI_DATA``) and is flipped
    back to ``queued`` by an explicit unblock step when the gate clears.

State only. No fetch, no compute, no order path. Paper/research only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from src.research_lab.farm_priority import priority_value

SCHEMA = "farm_tasks.v2"


class StaleTaskClaimError(RuntimeError):
    """The caller's task fence is absent, replaced, or expired."""


class FarmFencingMigrationRequired(RuntimeError):
    """An existing brain DB needs an explicitly authorized v2 activation."""


_TASK_FENCING_COLUMNS = {
    "claim_owner",
    "claim_expires_at",
    "fencing_token",
    "mutation_protocol",
    "mutation_seq",
}

# Task types in the research lifecycle (full graph; the coordinator creates the
# ones it needs each cycle). intake_event/resolve_instrument are reserved for
# forward compatibility — ingestion currently uses the intake_events table.
TASK_TYPES = (
    "intake_event",
    "resolve_instrument",
    "prepare_data",
    "enrich_funding",
    "enrich_oi",
    "run_sweep",
    "classify_result",
    "export_validation",
    "run_or_refresh_validation",
    "schedule_followup",
    "schedule_advisor_sweep",
)
TERMINAL_STATES = ("completed", "skipped", "failed")
ACTIVE_STATES = ("queued", "running", "deferred", "blocked")
ALL_STATES = ACTIVE_STATES + TERMINAL_STATES

DEFAULT_TTL_SECONDS = 12 * 3600  # identical data is not recomputed within this window


def tasks_db_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "farm_tasks.sqlite"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _farm_fencing_marker(conn: sqlite3.Connection) -> str | None:
    if not _table_exists(conn, "farm_meta"):
        return None
    row = conn.execute(
        "SELECT value FROM farm_meta WHERE key='fencing_protocol'"
    ).fetchone()
    return None if row is None else str(row[0])


def _require_farm_fencing(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "tasks"):
        return
    missing = _TASK_FENCING_COLUMNS - _column_names(conn, "tasks")
    triggers = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    }
    required_triggers = {"tasks_fenced_v2_insert_guard", "tasks_fenced_v2_guard"}
    missing_triggers = required_triggers - triggers
    if missing or missing_triggers or _farm_fencing_marker(conn) != "v2":
        if missing:
            detail = ",".join(sorted(missing))
        elif missing_triggers:
            detail = "triggers:" + ",".join(sorted(missing_triggers))
        else:
            detail = "capability_marker"
        raise FarmFencingMigrationRequired(
            "brain DB requires explicit fencing v2 activation: " + detail
        )


def _install_farm_fencing_triggers(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS tasks_fenced_v2_insert_guard
           BEFORE INSERT ON tasks
           WHEN NEW.mutation_protocol != 'fenced.v2'
           BEGIN
               SELECT RAISE(ABORT, 'fenced v2 writer required');
           END"""
    )
    conn.execute(
        """CREATE TRIGGER IF NOT EXISTS tasks_fenced_v2_guard
           BEFORE UPDATE ON tasks
           WHEN (
               NEW.state IS NOT OLD.state OR
               NEW.attempts IS NOT OLD.attempts OR
               NEW.claim_owner IS NOT OLD.claim_owner OR
               NEW.claim_expires_at IS NOT OLD.claim_expires_at OR
               NEW.fencing_token IS NOT OLD.fencing_token OR
               NEW.materialized_queue_job_id IS NOT OLD.materialized_queue_job_id OR
               NEW.last_result_ref IS NOT OLD.last_result_ref OR
               NEW.run_dir_label IS NOT OLD.run_dir_label
           ) AND (
               NEW.mutation_protocol != 'fenced.v2' OR
               NEW.mutation_seq != OLD.mutation_seq + 1
           )
           BEGIN
               SELECT RAISE(ABORT, 'fenced v2 writer required');
           END"""
    )


def activate_farm_fencing_v2(path: Path | str, *, clock: Any = time.time) -> None:
    """Explicitly activate brain fencing after an authorized quiesce.

    Runtime constructors and status readers deliberately never call this.
    """
    db_path = str(path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")
        if not _table_exists(conn, "tasks"):
            raise FarmFencingMigrationRequired(
                "brain DB has no legacy tasks to activate"
            )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS farm_meta (
                   key TEXT PRIMARY KEY,
                   value TEXT NOT NULL
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS task_transitions (
                   transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                   task_id INTEGER NOT NULL,
                   from_state TEXT NOT NULL,
                   to_state TEXT NOT NULL,
                   reason TEXT NOT NULL DEFAULT '',
                   owner_id TEXT NOT NULL DEFAULT '',
                   fencing_token INTEGER NOT NULL DEFAULT 0,
                   mutation_seq INTEGER NOT NULL,
                   transitioned_at REAL NOT NULL
               )"""
        )
        existing = _column_names(conn, "tasks")
        additions = {
            "claim_owner": "TEXT",
            "claim_expires_at": "REAL",
            "fencing_token": "INTEGER NOT NULL DEFAULT 0",
            "mutation_protocol": "TEXT NOT NULL DEFAULT 'legacy.v1'",
            "mutation_seq": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, declaration in additions.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {declaration}")
        now = float(clock())
        rows = conn.execute(
            """SELECT task_id, mutation_seq, fencing_token FROM tasks
               WHERE state='running' AND claim_owner IS NULL
                 AND mutation_protocol='legacy.v1'"""
        ).fetchall()
        for row in rows:
            seq = int(row["mutation_seq"] or 0) + 1
            cur = conn.execute(
                """UPDATE tasks SET state='blocked',
                       machine_reason='legacy_running_unfenced',
                       mutation_protocol='fenced.v2', mutation_seq=?
                   WHERE task_id=? AND state='running' AND claim_owner IS NULL
                     AND mutation_protocol='legacy.v1' AND mutation_seq=?""",
                (seq, int(row["task_id"]), int(row["mutation_seq"] or 0)),
            )
            if cur.rowcount != 1:
                raise FarmFencingMigrationRequired(
                    "brain DB changed during explicit fencing activation"
                )
            conn.execute(
                """INSERT INTO task_transitions(
                       task_id, from_state, to_state, reason, owner_id,
                       fencing_token, mutation_seq, transitioned_at)
                   VALUES(?, 'running', 'blocked', 'legacy_running_unfenced',
                          'migration', ?, ?, ?)""",
                (int(row["task_id"]), int(row["fencing_token"] or 0), seq, now),
            )
        conn.execute(
            """UPDATE tasks SET mutation_protocol='fenced.v2',
                      mutation_seq=mutation_seq+1
               WHERE mutation_protocol='legacy.v1'"""
        )
        conn.execute("DROP TRIGGER IF EXISTS tasks_fenced_v2_guard")
        conn.execute("DROP TRIGGER IF EXISTS tasks_fenced_v2_insert_guard")
        _install_farm_fencing_triggers(conn)
        conn.execute(
            "INSERT OR REPLACE INTO farm_meta(key, value) VALUES('fencing_protocol', 'v2')"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class FarmTasksDB:
    """SQLite-backed typed-task lifecycle + intake events + unique candidates."""

    def __init__(
        self,
        path: Path | str = ":memory:",
        *,
        owner_id: str | None = None,
        lease_seconds: float = 300.0,
        clock: Any = time.time,
        read_only: bool = False,
    ) -> None:
        self.path = str(path)
        self.owner_id = owner_id or f"farm-{uuid.uuid4().hex}"
        self.lease_seconds = float(lease_seconds)
        self._clock = clock
        self._authority_time_floor = float(clock())
        self.read_only = bool(read_only)
        self._claims: dict[int, int] = {}
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if self.read_only and self.path == ":memory:":
            raise ValueError("read-only FarmTasksDB requires a filesystem path")
        if self.path != ":memory:" and not self.read_only and Path(self.path).exists():
            uri = Path(self.path).resolve().as_posix()
            preflight = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
            preflight.row_factory = sqlite3.Row
            try:
                _require_farm_fencing(preflight)
            finally:
                preflight.close()
        if self.path != ":memory:" and not self.read_only:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        if self.read_only:
            uri = Path(self.path).resolve().as_posix()
            self._conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
        else:
            self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 30000")
        if not self.read_only:
            self._conn.execute("PRAGMA journal_mode = WAL")
        # Optional audit hook: set to a callable(record: dict) to log every state change.
        self.on_transition = None
        if not self.read_only:
            self._init_db()

    @property
    def raw_connection(self) -> sqlite3.Connection:
        return self._conn

    def _effective_now(self, supplied: float | None) -> float:
        """Never let a cached cycle timestamp roll lease authority backward."""
        observed = float(self._clock())
        candidate = observed if supplied is None else float(supplied)
        self._authority_time_floor = max(
            self._authority_time_floor, observed, candidate
        )
        return self._authority_time_floor

    def _init_db(self) -> None:
        _require_farm_fencing(self._conn)
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
                updated_at REAL NOT NULL,
                claim_owner TEXT,
                claim_expires_at REAL,
                fencing_token INTEGER NOT NULL DEFAULT 0,
                mutation_protocol TEXT NOT NULL DEFAULT 'legacy.v1',
                mutation_seq INTEGER NOT NULL DEFAULT 0
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

            CREATE TABLE IF NOT EXISTS task_transitions (
                transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                owner_id TEXT NOT NULL DEFAULT '',
                fencing_token INTEGER NOT NULL DEFAULT 0,
                mutation_seq INTEGER NOT NULL,
                transitioned_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS materialization_outbox (
                materialization_id TEXT PRIMARY KEY,
                task_id INTEGER NOT NULL,
                task_fencing_token INTEGER NOT NULL,
                spec_path TEXT NOT NULL,
                spec_digest TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                priority INTEGER NOT NULL,
                state TEXT NOT NULL,
                queue_job_id INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        self._install_fencing_trigger()
        self._migrate_priority_scale()
        self._migrate_unique_candidate_columns()
        self._conn.execute(
            "INSERT OR REPLACE INTO farm_meta(key, value) VALUES('fencing_protocol', 'v2')"
        )
        self._conn.commit()

    def _migrate_fencing_columns(self) -> None:
        existing = {
            str(row["name"]) for row in self._conn.execute("PRAGMA table_info(tasks)")
        }
        additions = {
            "claim_owner": "TEXT",
            "claim_expires_at": "REAL",
            "fencing_token": "INTEGER NOT NULL DEFAULT 0",
            "mutation_protocol": "TEXT NOT NULL DEFAULT 'legacy.v1'",
            "mutation_seq": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, declaration in additions.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {declaration}")
        legacy_running = self._conn.execute(
            """SELECT task_id, mutation_seq, fencing_token FROM tasks
               WHERE state='running' AND claim_owner IS NULL
                 AND mutation_protocol='legacy.v1'"""
        ).fetchall()
        for row in legacy_running:
            seq = int(row["mutation_seq"] or 0) + 1
            self._conn.execute(
                """UPDATE tasks SET state='blocked',
                       machine_reason='legacy_running_unfenced',
                       mutation_protocol='fenced.v2', mutation_seq=?
                   WHERE task_id=?""",
                (seq, int(row["task_id"])),
            )
            self._record_transition(
                int(row["task_id"]),
                "running",
                "blocked",
                "legacy_running_unfenced",
                float(self._clock()),
                "migration",
                int(row["fencing_token"] or 0),
                seq,
            )

    def _install_fencing_trigger(self) -> None:
        _install_farm_fencing_triggers(self._conn)

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
        existing = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(unique_candidates)")
        }
        if "paper_status" not in existing:
            self._conn.execute(
                "ALTER TABLE unique_candidates ADD COLUMN paper_status TEXT NOT NULL DEFAULT ''"
            )
        if "params_json" not in existing:
            self._conn.execute(
                "ALTER TABLE unique_candidates ADD COLUMN params_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "regime_bucket" not in existing:
            self._conn.execute(
                "ALTER TABLE unique_candidates ADD COLUMN regime_bucket TEXT NOT NULL DEFAULT ''"
            )
        if "search_family_id" not in existing:
            self._conn.execute(
                "ALTER TABLE unique_candidates ADD COLUMN search_family_id TEXT NOT NULL DEFAULT ''"
            )
        if "search_trial_id" not in existing:
            self._conn.execute(
                "ALTER TABLE unique_candidates ADD COLUMN search_trial_id TEXT NOT NULL DEFAULT ''"
            )
        if "effective_n_trials" not in existing:
            self._conn.execute(
                "ALTER TABLE unique_candidates ADD COLUMN effective_n_trials INTEGER NOT NULL DEFAULT 0"
            )

    # ── intake events ───────────────────────────────────────────────────────
    def upsert_intake_event(
        self, event: dict[str, Any], *, now: float | None = None
    ) -> tuple[str, bool]:
        """Insert an intake event keyed by its dedup id. Returns (event_id, created)."""
        now = time.time() if now is None else now
        eid = str(event["event_id"])
        if self._conn.execute(
            "SELECT 1 FROM intake_events WHERE event_id=?", (eid,)
        ).fetchone():
            return eid, False
        self._conn.execute(
            """INSERT INTO intake_events(event_id, symbol, source, reason, observed_at,
                 priority, asset_class, suggested_timeframes, evidence_json, raw_ref_json,
                 ingested_at, consumed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
            (
                eid,
                event.get("symbol"),
                event.get("source"),
                event.get("reason"),
                event.get("observed_at"),
                priority_value(event.get("priority")),
                event.get("asset_class"),
                json.dumps(event.get("suggested_timeframes") or []),
                json.dumps(event.get("evidence") or {}),
                json.dumps(event.get("raw_ref") or {}),
                now,
            ),
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
        self._conn.execute(
            "UPDATE intake_events SET consumed=1 WHERE event_id=?", (str(event_id),)
        )
        self._conn.commit()

    @staticmethod
    def _event_row(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": r["event_id"],
            "symbol": r["symbol"],
            "source": r["source"],
            "reason": r["reason"],
            "observed_at": r["observed_at"],
            "priority": r["priority"],
            "asset_class": r["asset_class"],
            "suggested_timeframes": json.loads(r["suggested_timeframes"] or "[]"),
            "evidence": json.loads(r["evidence_json"] or "{}"),
            "raw_ref": json.loads(r["raw_ref_json"] or "{}"),
        }

    # ── task lifecycle ────────────────────────────────────────────────────────
    def enqueue_task(
        self,
        *,
        task_type: str,
        task_key: str,
        priority: int = 100,
        symbol: str | None = None,
        asset_group: str | None = None,
        timeframe: str | None = None,
        family: str | None = None,
        params_hash: str | None = None,
        data_fingerprint: str | None = None,
        depends_on: int | None = None,
        source_event_id: str | None = None,
        payload: dict[str, Any] | None = None,
        state: str = "queued",
        machine_reason: str = "",
        deferred_until: float | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
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
            "ORDER BY task_id ASC LIMIT 1",
            (task_key,),
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
            "ORDER BY updated_at DESC LIMIT 1",
            (task_key,),
        ).fetchone()
        if done is not None and (now - float(done["updated_at"])) < ttl_seconds:
            return int(done["task_id"]), False
        try:
            cur = self._conn.execute(
                """INSERT INTO tasks(task_key, task_type, state, priority, symbol, asset_group,
                     timeframe, family, params_hash, data_fingerprint, depends_on, machine_reason,
                     deferred_until, source_event_id, payload_json, created_at, updated_at,
                     mutation_protocol)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'fenced.v2')""",
                (
                    task_key,
                    task_type,
                    state,
                    int(priority),
                    symbol,
                    asset_group,
                    timeframe,
                    family,
                    params_hash,
                    data_fingerprint,
                    depends_on,
                    machine_reason,
                    deferred_until,
                    source_event_id,
                    json.dumps(payload or {}),
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            active = self._conn.execute(
                f"SELECT task_id, priority FROM tasks WHERE task_key=? AND state IN {ACTIVE_STATES} "
                "ORDER BY task_id ASC LIMIT 1",
                (task_key,),
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
        if cur.lastrowid is None:
            raise RuntimeError("task insert did not return an id")
        return int(cur.lastrowid), True

    def claim_next_task(
        self, *, task_types: tuple[str, ...] | None = None, now: float | None = None
    ) -> dict[str, Any] | None:
        """Claim the highest-priority eligible task and flip it to running.

        Eligible = queued, or deferred whose deferred_until has elapsed; and whose
        depends_on (if any) is completed. Never returns blocked/running tasks.
        """
        now = self._effective_now(now)
        type_clause = ""
        params: list[Any] = [now]
        if task_types:
            type_clause = f" AND task_type IN ({','.join('?' * len(task_types))})"
            params.extend(task_types)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            now = self._effective_now(now)
            row = self._conn.execute(
                f"""SELECT * FROM tasks
                    WHERE (state='queued' OR (state='deferred' AND deferred_until<=?))
                      AND (depends_on IS NULL OR depends_on IN (SELECT task_id FROM tasks WHERE state='completed'))
                      {type_clause}
                    ORDER BY priority ASC, created_at ASC, task_id ASC LIMIT 1""",
                params,
            ).fetchone()
            if row is None:
                self._conn.commit()
                return None
            tid = int(row["task_id"])
            fence = int(row["fencing_token"] or 0) + 1
            mutation_seq = int(row["mutation_seq"] or 0) + 1
            expires_at = now + self.lease_seconds
            cur = self._conn.execute(
                """UPDATE tasks
                   SET state='running', attempts=attempts+1, updated_at=?,
                       claim_owner=?, claim_expires_at=?, fencing_token=?,
                       mutation_protocol='fenced.v2', mutation_seq=?
                   WHERE task_id=? AND state=? AND claim_owner IS NULL
                     AND fencing_token=? AND mutation_protocol='fenced.v2'
                     AND mutation_seq=?""",
                (
                    now,
                    self.owner_id,
                    expires_at,
                    fence,
                    mutation_seq,
                    tid,
                    str(row["state"]),
                    int(row["fencing_token"] or 0),
                    int(row["mutation_seq"] or 0),
                ),
            )
            if cur.rowcount != 1:
                raise StaleTaskClaimError("task changed during claim")
            self._record_transition(
                tid,
                str(row["state"]),
                "running",
                "claimed",
                now,
                self.owner_id,
                fence,
                mutation_seq,
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        self._claims[tid] = fence
        self._emit_transition(tid, "running", "claimed", now)
        return self.get_task(tid)

    def eligible_count(
        self, now: float | None = None, *, task_types: tuple[str, ...] | None = None
    ) -> int:
        """How many tasks are claimable right now (no mutation) — for pivot decisions."""
        now = time.time() if now is None else now
        clause = ""
        params: list[Any] = [now]
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
                "SELECT machine_reason, COUNT(*) FROM tasks WHERE state='blocked' GROUP BY machine_reason"
            ),
            "deferred_reasons": _kv(
                "SELECT machine_reason, COUNT(*) FROM tasks WHERE state='deferred' GROUP BY machine_reason"
            ),
            "intake_unconsumed": int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM intake_events WHERE consumed=0"
                ).fetchone()[0]
            ),
            "unique_candidates": int(
                self._conn.execute("SELECT COUNT(*) FROM unique_candidates").fetchone()[
                    0
                ]
            ),
            "validation_backlog": self.validation_backlog_metrics(),
        }

    def validation_backlog_metrics(
        self,
        *,
        now: float | None = None,
        window_seconds: float = 3600.0,
    ) -> dict[str, Any]:
        """Return bounded aggregate evidence for export-validation capacity.

        The metrics deliberately expose counts and timing only.  They never read task
        payloads or validation artifacts, so status surfaces can measure arrival/service
        pressure without leaking private research rows.
        """

        current = self._effective_now(now)
        window = max(1.0, float(window_seconds))
        since = current - window
        active_placeholders = ",".join("?" for _ in ACTIVE_STATES)
        state_rows = self._conn.execute(
            f"""SELECT state, COUNT(*) AS n
                FROM tasks
                WHERE task_type='export_validation' AND state IN ({active_placeholders})
                GROUP BY state""",
            ACTIVE_STATES,
        ).fetchall()
        by_state = {str(row["state"]): int(row["n"]) for row in state_rows}
        active = sum(by_state.values())
        oldest_row = self._conn.execute(
            f"""SELECT MIN(created_at) AS oldest
                FROM tasks
                WHERE task_type='export_validation' AND state IN ({active_placeholders})""",
            ACTIVE_STATES,
        ).fetchone()
        oldest_created_at = (
            None
            if oldest_row is None or oldest_row["oldest"] is None
            else float(oldest_row["oldest"])
        )
        arrivals = int(
            self._conn.execute(
                """SELECT COUNT(*) FROM tasks
                   WHERE task_type='export_validation' AND created_at>=?""",
                (since,),
            ).fetchone()[0]
        )
        terminal = int(
            self._conn.execute(
                """SELECT COUNT(*) FROM tasks
                   WHERE task_type='export_validation'
                     AND state IN ('completed','skipped','failed')
                     AND updated_at>=?""",
                (since,),
            ).fetchone()[0]
        )
        hours = window / 3600.0
        arrival_rate = arrivals / hours
        service_rate = terminal / hours
        net_drain_rate = service_rate - arrival_rate
        return {
            "active": int(active),
            "eligible": self.eligible_count(
                current, task_types=("export_validation",)
            ),
            "by_state": by_state,
            "oldest_age_seconds": (
                0.0
                if oldest_created_at is None
                else round(max(0.0, current - oldest_created_at), 3)
            ),
            "window_seconds": window,
            "arrivals": arrivals,
            "terminal": terminal,
            "arrival_count_method": "task_created_in_window",
            "service_count_method": "current_terminal_state_updated_in_window",
            "arrival_rate_per_hour": round(arrival_rate, 3),
            "service_rate_per_hour": round(service_rate, 3),
            "net_drain_rate_per_hour": round(net_drain_rate, 3),
            "drain_eta_hours": (
                round(active / net_drain_rate, 3)
                if active and net_drain_rate > 0
                else 0.0
                if not active
                else None
            ),
        }

    def _set_state(
        self,
        task_id: int,
        state: str,
        *,
        reason: str = "",
        now: float | None = None,
        **fields: Any,
    ) -> None:
        now = self._effective_now(now)
        tid = int(task_id)
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE task_id=?", (tid,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown task {tid}")
        claim_owner = row["claim_owner"]
        fence = int(row["fencing_token"] or 0)
        if claim_owner is None and (row["state"] == "running" or state == "running"):
            raise StaleTaskClaimError(
                f"running transition for task {tid} requires a fenced claim"
            )
        if claim_owner is not None:
            if (
                str(claim_owner) != self.owner_id
                or self._claims.get(tid) != fence
                or float(row["claim_expires_at"] or 0) <= now
            ):
                raise StaleTaskClaimError(f"stale claim for task {tid}")
        mutation_seq = int(row["mutation_seq"] or 0) + 1
        now = self._effective_now(now)
        cols = [
            "state=?",
            "machine_reason=?",
            "updated_at=?",
            "mutation_protocol='fenced.v2'",
            "mutation_seq=?",
        ]
        vals: list[Any] = [state, reason, now, mutation_seq]
        for key, value in fields.items():
            cols.append(f"{key}=?")
            vals.append(value)
        if state != "running":
            cols.extend(["claim_owner=NULL", "claim_expires_at=NULL"])
        predicate = (
            "task_id=? AND state=? AND fencing_token=? "
            "AND mutation_protocol='fenced.v2' AND mutation_seq=?"
        )
        vals.extend(
            [
                tid,
                str(row["state"]),
                fence,
                int(row["mutation_seq"] or 0),
            ]
        )
        if claim_owner is None:
            predicate += " AND claim_owner IS NULL"
        else:
            predicate += " AND claim_owner=? AND claim_expires_at>?"
            vals.extend([self.owner_id, now])
        try:
            cur = self._conn.execute(
                f"UPDATE tasks SET {', '.join(cols)} WHERE {predicate}", vals
            )
            if cur.rowcount != 1:
                raise StaleTaskClaimError(f"task {tid} changed during transition")
            self._record_transition(
                tid,
                str(row["state"]),
                state,
                reason,
                now,
                str(claim_owner or self.owner_id),
                fence,
                mutation_seq,
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        if state != "running":
            self._claims.pop(tid, None)
        self._emit_transition(tid, state, reason, now)

    def _record_transition(
        self,
        task_id: int,
        from_state: str,
        to_state: str,
        reason: str,
        now: float,
        owner_id: str,
        fencing_token: int,
        mutation_seq: int,
    ) -> None:
        self._conn.execute(
            """INSERT INTO task_transitions(
                   task_id, from_state, to_state, reason, owner_id,
                   fencing_token, mutation_seq, transitioned_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                task_id,
                from_state,
                to_state,
                reason,
                owner_id,
                fencing_token,
                mutation_seq,
                now,
            ),
        )

    def _emit_transition(
        self, task_id: int, state: str, reason: str, now: float
    ) -> None:
        """Fire the optional audit hook with the post-update task identity (best-effort)."""
        if self.on_transition is None:
            return
        row = self._conn.execute(
            "SELECT task_key, task_type FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        try:
            self.on_transition(
                {
                    "ts": round(float(now), 3),
                    "task_id": task_id,
                    "task_key": row["task_key"] if row else None,
                    "task_type": row["task_type"] if row else None,
                    "to_state": state,
                    "reason": reason,
                }
            )
        except Exception:  # noqa: BLE001 - logging must never break a state transition
            pass

    def complete_task(
        self,
        task_id: int,
        *,
        last_result_ref: str | None = None,
        run_dir_label: str | None = None,
        materialized_queue_job_id: int | None = None,
        reason: str = "",
        now: float | None = None,
    ) -> None:
        self._set_state(
            task_id,
            "completed",
            reason=reason,
            now=now,
            last_result_ref=last_result_ref,
            run_dir_label=run_dir_label,
            materialized_queue_job_id=materialized_queue_job_id,
        )

    def materialize_task(
        self, task_id: int, queue_job_id: int, *, now: float | None = None
    ) -> None:
        """Park a run_sweep task after durable compute-queue materialization."""
        row = self._conn.execute(
            "SELECT state, claim_owner FROM tasks WHERE task_id=?",
            (int(task_id),),
        ).fetchone()
        if row is None or row["state"] != "running" or row["claim_owner"] is None:
            raise StaleTaskClaimError(
                f"materialization for task {int(task_id)} requires a fenced claim"
            )
        self._set_state(
            task_id,
            "deferred",
            reason="materialized_awaiting_worker",
            now=now,
            deferred_until=None,
            materialized_queue_job_id=int(queue_job_id),
        )

    def prepare_materialization(
        self,
        task_id: int,
        *,
        materialization_id: str,
        spec_path: str,
        spec_digest: str,
        spec_json: str,
        priority: int,
        now: float | None = None,
    ) -> None:
        """Persist a content-bound intent before any spec/compute side effect."""
        current = self._effective_now(now)
        tid = int(task_id)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            current = self._effective_now(current)
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id=?", (tid,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown task {tid}")
            fence = int(row["fencing_token"] or 0)
            if (
                row["state"] != "running"
                or str(row["claim_owner"] or "") != self.owner_id
                or self._claims.get(tid) != fence
                or float(row["claim_expires_at"] or 0) <= current
            ):
                raise StaleTaskClaimError(f"stale claim for task {tid}")
            existing = self._conn.execute(
                "SELECT * FROM materialization_outbox WHERE materialization_id=?",
                (materialization_id,),
            ).fetchone()
            if existing is not None:
                if (
                    int(existing["task_id"]) != tid
                    or int(existing["task_fencing_token"]) != fence
                    or str(existing["spec_digest"]) != spec_digest
                    or str(existing["spec_path"]) != spec_path
                ):
                    raise ValueError(
                        "materialization id is already bound to different content"
                    )
                self._conn.commit()
                return
            self._conn.execute(
                """UPDATE materialization_outbox SET state='superseded', updated_at=?
                   WHERE task_id=? AND state IN ('pending','dispatched')
                     AND task_fencing_token<>?""",
                (current, tid, fence),
            )
            self._conn.execute(
                """INSERT INTO materialization_outbox(
                       materialization_id, task_id, task_fencing_token, spec_path,
                       spec_digest, spec_json, priority, state, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,'pending',?,?)""",
                (
                    materialization_id,
                    tid,
                    fence,
                    spec_path,
                    spec_digest,
                    spec_json,
                    int(priority),
                    current,
                    current,
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def commit_materialization(
        self,
        task_id: int,
        *,
        materialization_id: str,
        queue_job_id: int,
        now: float | None = None,
    ) -> None:
        """Atomically link the brain task and mark its durable outbox delivered."""
        current = self._effective_now(now)
        tid = int(task_id)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            current = self._effective_now(current)
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id=?", (tid,)
            ).fetchone()
            outbox = self._conn.execute(
                "SELECT * FROM materialization_outbox WHERE materialization_id=?",
                (materialization_id,),
            ).fetchone()
            if row is None or outbox is None or int(outbox["task_id"]) != tid:
                raise StaleTaskClaimError(
                    "materialization intent is missing or mismatched"
                )
            fence = int(row["fencing_token"] or 0)
            if (
                row["state"] != "running"
                or str(row["claim_owner"] or "") != self.owner_id
                or self._claims.get(tid) != fence
                or int(outbox["task_fencing_token"]) != fence
                or float(row["claim_expires_at"] or 0) <= current
            ):
                raise StaleTaskClaimError(f"stale materialization claim for task {tid}")
            seq = int(row["mutation_seq"] or 0) + 1
            cur = self._conn.execute(
                """UPDATE tasks SET state='deferred', materialized_queue_job_id=?,
                       machine_reason='materialized_awaiting_worker', updated_at=?,
                       deferred_until=NULL, claim_owner=NULL, claim_expires_at=NULL,
                       mutation_protocol='fenced.v2', mutation_seq=?
                   WHERE task_id=? AND state='running' AND claim_owner=?
                     AND fencing_token=? AND claim_expires_at>?
                     AND mutation_protocol='fenced.v2' AND mutation_seq=?""",
                (
                    int(queue_job_id),
                    current,
                    seq,
                    tid,
                    self.owner_id,
                    fence,
                    current,
                    int(row["mutation_seq"] or 0),
                ),
            )
            if cur.rowcount != 1:
                raise StaleTaskClaimError("task changed during materialization commit")
            outbox_cur = self._conn.execute(
                """UPDATE materialization_outbox SET state='acknowledged',
                       queue_job_id=?, updated_at=?, spec_json=''
                   WHERE materialization_id=? AND task_id=?
                     AND task_fencing_token=? AND state IN ('pending','dispatched')""",
                (int(queue_job_id), current, materialization_id, tid, fence),
            )
            if outbox_cur.rowcount != 1:
                raise StaleTaskClaimError(
                    "materialization outbox changed during commit"
                )
            self._record_transition(
                tid,
                "running",
                "deferred",
                "materialized_awaiting_worker",
                current,
                self.owner_id,
                fence,
                seq,
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        self._claims.pop(tid, None)
        self._emit_transition(tid, "deferred", "materialized_awaiting_worker", current)

    def finish_materialized_task(
        self,
        task_id: int,
        *,
        materialization_id: str,
        queue_job_id: int,
        queue_status: str,
        last_result_ref: str | None = None,
        run_dir_label: str | None = None,
        now: float | None = None,
    ) -> None:
        """Finish one parked task through its exact acknowledged fence binding.

        A materialized task deliberately holds no renewable claim while the
        independent compute queue owns execution.  Completion authority comes
        from the immutable acknowledged outbox generation plus a compare-and-
        swap over the task fence and mutation sequence.
        """

        if queue_status not in {"completed", "failed"}:
            raise ValueError("queue_status must be terminal")
        current = self._effective_now(now)
        tid = int(task_id)
        job_id = int(queue_job_id)
        target_state = "completed" if queue_status == "completed" else "failed"
        reason = (
            "compute_completed" if queue_status == "completed" else "compute_failed"
        )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            current = self._effective_now(current)
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id=?",
                (tid,),
            ).fetchone()
            outbox = self._conn.execute(
                """SELECT * FROM materialization_outbox
                   WHERE materialization_id=? AND task_id=? AND queue_job_id=?
                     AND state='acknowledged'""",
                (materialization_id, tid, job_id),
            ).fetchone()
            if (
                row is None
                or outbox is None
                or row["state"] != "deferred"
                or str(row["machine_reason"] or "") != "materialized_awaiting_worker"
                or row["claim_owner"] is not None
                or row["claim_expires_at"] is not None
                or int(row["materialized_queue_job_id"] or 0) != job_id
                or int(row["fencing_token"] or 0)
                != int(outbox["task_fencing_token"] or 0)
            ):
                raise StaleTaskClaimError(
                    f"materialized task generation changed for task {tid}"
                )
            seq = int(row["mutation_seq"] or 0) + 1
            cur = self._conn.execute(
                """UPDATE tasks SET state=?, machine_reason=?, updated_at=?,
                       last_result_ref=?, run_dir_label=?,
                       materialized_queue_job_id=?, deferred_until=NULL,
                       mutation_protocol='fenced.v2', mutation_seq=?
                   WHERE task_id=? AND state='deferred'
                     AND machine_reason='materialized_awaiting_worker'
                     AND claim_owner IS NULL AND claim_expires_at IS NULL
                     AND fencing_token=? AND mutation_protocol='fenced.v2'
                     AND mutation_seq=?""",
                (
                    target_state,
                    reason,
                    current,
                    last_result_ref,
                    run_dir_label,
                    job_id,
                    seq,
                    tid,
                    int(row["fencing_token"] or 0),
                    int(row["mutation_seq"] or 0),
                ),
            )
            if cur.rowcount != 1:
                raise StaleTaskClaimError(
                    f"materialized task changed during finish for task {tid}"
                )
            self._record_transition(
                tid,
                "deferred",
                target_state,
                reason,
                current,
                self.owner_id,
                int(row["fencing_token"] or 0),
                seq,
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        self._emit_transition(tid, target_state, reason, current)

    def mark_materialization_dispatched(
        self, materialization_id: str, queue_job_id: int, *, now: float | None = None
    ) -> None:
        current = self._effective_now(now)
        self._assert_materialization_authority(materialization_id, current)
        current = self._effective_now(current)
        cur = self._conn.execute(
            """UPDATE materialization_outbox SET state='dispatched',
                   queue_job_id=?, updated_at=?
               WHERE materialization_id=? AND state IN ('pending','dispatched')
                 AND EXISTS(
                     SELECT 1 FROM tasks t
                     WHERE t.task_id=materialization_outbox.task_id
                       AND t.state='running' AND t.claim_owner=?
                       AND t.fencing_token=materialization_outbox.task_fencing_token
                       AND t.claim_expires_at>?
                       AND t.mutation_protocol='fenced.v2'
                 )""",
            (int(queue_job_id), current, materialization_id, self.owner_id, current),
        )
        if cur.rowcount != 1:
            self._conn.rollback()
            raise StaleTaskClaimError("materialization cannot be marked dispatched")
        self._conn.commit()

    def mark_materialization_ambiguous(
        self, materialization_id: str, *, now: float | None = None
    ) -> None:
        current = self._effective_now(now)
        self._assert_materialization_authority(materialization_id, current)
        current = self._effective_now(current)
        cur = self._conn.execute(
            """UPDATE materialization_outbox SET state='ambiguous', updated_at=?
               WHERE materialization_id=? AND state IN ('pending','dispatched')
                 AND EXISTS(
                     SELECT 1 FROM tasks t
                     WHERE t.task_id=materialization_outbox.task_id
                       AND t.state='running' AND t.claim_owner=?
                       AND t.fencing_token=materialization_outbox.task_fencing_token
                       AND t.claim_expires_at>?
                       AND t.mutation_protocol='fenced.v2'
                 )""",
            (current, materialization_id, self.owner_id, current),
        )
        if cur.rowcount != 1:
            self._conn.rollback()
            raise StaleTaskClaimError("materialization cannot be marked ambiguous")
        self._conn.commit()

    def _assert_materialization_authority(
        self, materialization_id: str, now: float
    ) -> None:
        row = self._conn.execute(
            """SELECT o.task_id, o.task_fencing_token, t.claim_owner,
                      t.claim_expires_at, t.fencing_token
               FROM materialization_outbox o
               JOIN tasks t ON t.task_id=o.task_id
               WHERE o.materialization_id=?""",
            (materialization_id,),
        ).fetchone()
        if row is None:
            raise StaleTaskClaimError("materialization intent is missing")
        tid = int(row["task_id"])
        fence = int(row["task_fencing_token"])
        if (
            str(row["claim_owner"] or "") != self.owner_id
            or int(row["fencing_token"] or 0) != fence
            or self._claims.get(tid) != fence
            or float(row["claim_expires_at"] or 0) <= now
        ):
            raise StaleTaskClaimError("materialization intent has a stale task fence")

    def authorize_materialization_replay(
        self, materialization_id: str, *, now: float | None = None
    ) -> dict[str, Any]:
        """Return the current intent only while its exact task fence is valid."""
        current = self._effective_now(now)
        self._assert_materialization_authority(materialization_id, current)
        row = self._conn.execute(
            """SELECT * FROM materialization_outbox
               WHERE materialization_id=? AND state IN ('pending','dispatched')""",
            (materialization_id,),
        ).fetchone()
        if row is None:
            raise StaleTaskClaimError("materialization intent is not replayable")
        return dict(row)

    def pending_materializations(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM materialization_outbox
               WHERE state IN ('pending','dispatched') ORDER BY created_at"""
        ).fetchall()
        return [dict(row) for row in rows]

    def release_acknowledged_materialization_payloads(
        self,
        *,
        apply: bool = False,
        expected_plan_digest: str = "",
        compact: bool = False,
    ) -> dict[str, Any]:
        """Verify and release terminal replay payload copies.

        ``spec_json`` is recovery authority only until the content-bound spec file
        and compute-queue binding are durably acknowledged.  Afterwards the exact
        spec digest/path and queue binding remain evidence, while retaining the
        complete JSON in the brain DB only duplicates the immutable spec artifact.

        The apply path holds ``BEGIN IMMEDIATE`` across verification and compare-
        and-swap updates.  It is intended for a quiescent, backed-up operational
        migration; this method does not infer that authority.  ``compact`` is an
        explicit post-commit storage operation and is accepted only with an exact
        dry-run plan digest.
        """

        if self.read_only and apply:
            raise RuntimeError("payload release requires a writable farm DB")
        if compact and not apply:
            raise ValueError("storage compaction requires apply mode")
        if apply and not expected_plan_digest:
            raise ValueError("apply requires the exact dry-run plan digest")
        if self.path == ":memory:":
            allowed_root: Path | None = None
        else:
            private_root = Path(self.path).resolve().parent.parent
            allowed_root = (private_root / "plans" / "event_specs").resolve()
        if apply:
            self._conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._conn.execute(
                """SELECT materialization_id, spec_path, spec_digest, spec_json
                   FROM materialization_outbox
                   WHERE state='acknowledged' AND LENGTH(spec_json)>0
                   ORDER BY materialization_id"""
            )
            released_bytes = 0
            plan_digest = hashlib.sha256()
            planned_updates: list[tuple[str, str, int]] = []
            for row in cursor:
                payload = str(row["spec_json"])
                payload_bytes = payload.encode("utf-8")
                digest = "sha256:" + hashlib.sha256(payload_bytes).hexdigest()
                if digest != str(row["spec_digest"]):
                    raise ValueError(
                        "acknowledged materialization payload digest mismatch"
                    )
                spec_path = Path(str(row["spec_path"]))
                if self.path != ":memory:":
                    if not spec_path.is_absolute() or not spec_path.is_file():
                        raise ValueError(
                            "acknowledged materialization spec artifact is absent"
                        )
                    resolved = spec_path.resolve(strict=True)
                    assert allowed_root is not None
                    try:
                        resolved.relative_to(allowed_root)
                    except ValueError as exc:
                        raise ValueError(
                            "acknowledged materialization spec escapes event-spec root"
                        ) from exc
                    file_digest = hashlib.sha256()
                    with resolved.open(
                        "r", encoding="utf-8", newline=None
                    ) as handle:
                        while chunk := handle.read(8 * 1024 * 1024):
                            file_digest.update(chunk.encode("utf-8"))
                    if "sha256:" + file_digest.hexdigest() != digest:
                        raise ValueError(
                            "acknowledged materialization spec artifact digest mismatch"
                        )
                identity = json.dumps(
                    {
                        "materialization_id": str(row["materialization_id"]),
                        "spec_digest": digest,
                        "payload_bytes": len(payload_bytes),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                plan_digest.update(len(identity).to_bytes(8, "big"))
                plan_digest.update(identity)
                released_bytes += len(payload_bytes)
                planned_updates.append(
                    (
                        str(row["materialization_id"]),
                        digest,
                        len(payload),
                    )
                )
            cursor.close()
            if apply:
                actual_plan_digest = "sha256:" + plan_digest.hexdigest()
                if actual_plan_digest != expected_plan_digest:
                    raise ValueError("materialization payload release plan changed")
                for materialization_id, digest, payload_length in planned_updates:
                    cur = self._conn.execute(
                        """UPDATE materialization_outbox SET spec_json=''
                           WHERE materialization_id=? AND state='acknowledged'
                             AND spec_digest=? AND LENGTH(spec_json)=?""",
                        (
                            materialization_id,
                            digest,
                            payload_length,
                        ),
                    )
                    if cur.rowcount != 1:
                        raise RuntimeError(
                            "materialization payload changed during release"
                        )
                self._conn.commit()
                if compact:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    self._conn.execute("VACUUM")
                    integrity = str(
                        self._conn.execute("PRAGMA integrity_check").fetchone()[0]
                    )
                    if integrity != "ok":
                        raise RuntimeError(
                            "farm task database integrity failed after compaction"
                        )
            return {
                "schema": "MaterializationPayloadRelease.v1",
                "mode": "apply" if apply else "dry_run",
                "eligible_rows": len(planned_updates),
                "released_payload_bytes": released_bytes,
                "plan_digest": "sha256:" + plan_digest.hexdigest(),
                "storage_compacted": bool(compact),
            }
        except Exception:
            if apply:
                self._conn.rollback()
            raise

    def renew_task_claim(
        self,
        task_id: int,
        *,
        lease_seconds: float | None = None,
        now: float | None = None,
    ) -> float:
        tid = int(task_id)
        fence = self._claims.get(tid)
        if fence is None:
            raise StaleTaskClaimError(f"stale claim for task {tid}")
        return self.renew_task_claim_token(
            tid,
            fencing_token=fence,
            lease_seconds=lease_seconds,
            now=now,
        )

    def assert_task_claim(
        self,
        task_id: int,
        *,
        fencing_token: int,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Return the exact live running claim or fail closed.

        Unlike foreground transitions this check accepts an explicit immutable
        fence, so a separate heartbeat connection never has to forge the
        connection-local ``_claims`` cache.
        """
        current = self._effective_now(now)
        row = self._conn.execute(
            """SELECT * FROM tasks
               WHERE task_id=? AND state='running' AND claim_owner=?
                 AND fencing_token=? AND claim_expires_at>?
                 AND mutation_protocol='fenced.v2'""",
            (int(task_id), self.owner_id, int(fencing_token), current),
        ).fetchone()
        if row is None:
            raise StaleTaskClaimError(f"stale claim for task {int(task_id)}")
        return dict(row)

    def renew_task_claim_token(
        self,
        task_id: int,
        *,
        fencing_token: int,
        lease_seconds: float | None = None,
        now: float | None = None,
    ) -> float:
        """Atomically renew one explicit owner/fence generation.

        This is the only renewal surface suitable for an independent heartbeat
        connection.  The transaction serializes renewal with reclaim and
        reconciliation; both cannot win the same generation.
        """
        tid = int(task_id)
        fence = int(fencing_token)
        duration = float(lease_seconds or self.lease_seconds)
        if duration <= 0:
            raise ValueError("lease_seconds must be positive")
        current = self._effective_now(now)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            current = self._effective_now(current)
            row = self._conn.execute(
                """SELECT mutation_seq, claim_expires_at FROM tasks
                   WHERE task_id=? AND state='running' AND claim_owner=?
                     AND fencing_token=? AND mutation_protocol='fenced.v2'""",
                (tid, self.owner_id, fence),
            ).fetchone()
            if row is None or float(row["claim_expires_at"] or 0) <= current:
                raise StaleTaskClaimError(f"stale claim for task {tid}")
            expires = current + duration
            cur = self._conn.execute(
                """UPDATE tasks SET claim_expires_at=?, updated_at=?,
                       mutation_protocol='fenced.v2', mutation_seq=mutation_seq+1
                   WHERE task_id=? AND state='running' AND claim_owner=?
                     AND fencing_token=? AND claim_expires_at>?
                     AND mutation_protocol='fenced.v2' AND mutation_seq=?""",
                (
                    expires,
                    current,
                    tid,
                    self.owner_id,
                    fence,
                    current,
                    int(row["mutation_seq"] or 0),
                ),
            )
            if cur.rowcount != 1:
                raise StaleTaskClaimError(f"task {tid} changed during renewal")
            self._conn.commit()
            return expires
        except Exception:
            self._conn.rollback()
            raise

    def skip_task(self, task_id: int, reason: str, *, now: float | None = None) -> None:
        self._set_state(task_id, "skipped", reason=reason, now=now)

    def classify_export_validation_task(
        self,
        task: dict[str, Any],
        *,
        now: float | None = None,
        missing_grace_seconds: float = 600.0,
    ) -> dict[str, str]:
        """Classify a claimed validation task before it can revoke authority.

        A recently created task whose candidate row is not visible is deferred to
        tolerate the coordinator/priority-worker commit boundary.  An older missing
        row, a malformed payload, or a candidate that is no longer validation-eligible
        is terminally skipped by the caller under the task's existing fence.
        """
        if str(task.get("task_type") or "") != "export_validation":
            raise ValueError("task is not export_validation")
        current = self._effective_now(now)
        try:
            payload = json.loads(str(task.get("payload_json") or "{}"))
        except (TypeError, json.JSONDecodeError):
            payload = {}
        uc_key = str(payload.get("uc_key") or "") if isinstance(payload, dict) else ""
        if not uc_key:
            return {"action": "skip", "reason": "validation_task_missing_uc_key"}
        row = self._conn.execute(
            "SELECT validation_status FROM unique_candidates WHERE uc_key=?",
            (uc_key,),
        ).fetchone()
        if row is None:
            age = max(0.0, current - float(task.get("created_at") or current))
            if age < max(0.0, float(missing_grace_seconds)):
                return {
                    "action": "defer",
                    "reason": "validation_candidate_not_yet_visible",
                }
            return {
                "action": "skip",
                "reason": "validation_orphan_missing_unique_candidate",
            }
        if str(row["validation_status"] or "") not in {
            "FORWARD_PAPER",
            "REGIME_SPECIFIC",
        }:
            return {
                "action": "skip",
                "reason": "validation_candidate_no_longer_eligible",
            }
        return {"action": "eligible", "reason": ""}

    def apply_export_validation_disposition_plan(
        self,
        entries: list[dict[str, Any]],
        *,
        now: float | None = None,
    ) -> int:
        """Atomically apply an exact, hash-bound orphan disposition plan.

        Every row must still match its planned state, fence, mutation sequence and
        payload digest.  Reapplying the same plan after a complete commit changes zero
        rows.  Any other drift fails closed before the first transition.
        """
        current = self._effective_now(now)
        pending: list[tuple[sqlite3.Row, dict[str, Any]]] = []
        already_applied = 0
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            for item in entries:
                task_id = int(item["task_id"])
                row = self._conn.execute(
                    "SELECT * FROM tasks WHERE task_id=?", (task_id,)
                ).fetchone()
                if row is None or str(row["task_type"] or "") != "export_validation":
                    raise StaleTaskClaimError("validation disposition task identity changed")
                payload_digest = hashlib.sha256(
                    str(row["payload_json"] or "").encode("utf-8")
                ).hexdigest()
                expected_seq = int(item["mutation_seq"])
                expected_reason = str(item["reason"])
                if (
                    str(row["state"]) == "skipped"
                    and str(row["machine_reason"] or "") == expected_reason
                    and int(row["fencing_token"] or 0) == int(item["fencing_token"])
                    and int(row["mutation_seq"] or 0) == expected_seq + 1
                    and payload_digest == str(item["payload_sha256"])
                    and row["claim_owner"] is None
                ):
                    already_applied += 1
                    continue
                if not (
                    str(row["state"]) == str(item["state"])
                    and str(row["state"]) in {"queued", "deferred"}
                    and int(row["fencing_token"] or 0) == int(item["fencing_token"])
                    and int(row["mutation_seq"] or 0) == expected_seq
                    and payload_digest == str(item["payload_sha256"])
                    and row["claim_owner"] is None
                ):
                    raise StaleTaskClaimError("validation disposition plan is stale")
                pending.append((row, item))
            if already_applied and pending:
                raise StaleTaskClaimError("validation disposition plan is partially applied")
            emitted: list[tuple[int, str]] = []
            for row, item in pending:
                task_id = int(row["task_id"])
                mutation_seq = int(row["mutation_seq"] or 0) + 1
                reason = str(item["reason"])
                cur = self._conn.execute(
                    """UPDATE tasks
                       SET state='skipped', machine_reason=?, updated_at=?,
                           mutation_protocol='fenced.v2', mutation_seq=?,
                           claim_owner=NULL, claim_expires_at=NULL
                       WHERE task_id=? AND state=? AND fencing_token=?
                         AND mutation_protocol='fenced.v2' AND mutation_seq=?
                         AND claim_owner IS NULL""",
                    (
                        reason,
                        current,
                        mutation_seq,
                        task_id,
                        str(row["state"]),
                        int(row["fencing_token"] or 0),
                        int(row["mutation_seq"] or 0),
                    ),
                )
                if cur.rowcount != 1:
                    raise StaleTaskClaimError("validation disposition changed during apply")
                self._record_transition(
                    task_id,
                    str(row["state"]),
                    "skipped",
                    reason,
                    current,
                    self.owner_id,
                    int(row["fencing_token"] or 0),
                    mutation_seq,
                )
                emitted.append((task_id, reason))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        for task_id, reason in emitted:
            self._emit_transition(task_id, "skipped", reason, current)
        return len(pending)

    def block_task(
        self, task_id: int, reason: str, *, now: float | None = None
    ) -> None:
        self._set_state(task_id, "blocked", reason=reason, now=now)

    def fail_task(self, task_id: int, error: str, *, now: float | None = None) -> None:
        self._set_state(task_id, "failed", reason=str(error)[:500], now=now)

    def defer_task(
        self, task_id: int, until: float, reason: str, *, now: float | None = None
    ) -> None:
        self._set_state(
            task_id, "deferred", reason=reason, now=now, deferred_until=float(until)
        )

    def requeue_task(
        self, task_id: int, *, reason: str = "unblocked", now: float | None = None
    ) -> None:
        self._set_state(task_id, "queued", reason=reason, now=now, deferred_until=None)

    def reconcile_orphan_running(
        self, *, reason: str = "orphan_running_requeued", now: float | None = None
    ) -> int:
        """Requeue only exact expired fenced claims under one write transaction."""
        now = self._effective_now(now)
        requeued = 0
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            now = self._effective_now(now)
            rows = self._conn.execute(
                """SELECT * FROM tasks
                   WHERE state='running' AND mutation_protocol='fenced.v2'
                     AND claim_owner IS NOT NULL AND claim_expires_at<=?""",
                (now,),
            ).fetchall()
            for row in rows:
                tid = int(row["task_id"])
                owner = str(row["claim_owner"])
                fence = int(row["fencing_token"] or 0)
                seq = int(row["mutation_seq"] or 0) + 1
                cur = self._conn.execute(
                    """UPDATE tasks SET state='queued', machine_reason=?, updated_at=?,
                           claim_owner=NULL, claim_expires_at=NULL,
                           mutation_protocol='fenced.v2', mutation_seq=?
                       WHERE task_id=? AND state='running' AND claim_owner=?
                         AND fencing_token=? AND claim_expires_at<=?
                         AND mutation_protocol='fenced.v2' AND mutation_seq=?""",
                    (
                        reason,
                        now,
                        seq,
                        tid,
                        owner,
                        fence,
                        now,
                        int(row["mutation_seq"] or 0),
                    ),
                )
                if cur.rowcount != 1:
                    continue
                self._record_transition(
                    tid,
                    "running",
                    "queued",
                    reason,
                    now,
                    owner,
                    fence,
                    seq,
                )
                self._claims.pop(tid, None)
                requeued += 1
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return requeued

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE task_id=?", (int(task_id),)
        ).fetchone()
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

    def tasks_in_state(
        self, state: str, *, task_type: str | None = None
    ) -> list[dict[str, Any]]:
        if task_type:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE state=? AND task_type=? ORDER BY task_id ASC",
                (state, task_type),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE state=? ORDER BY task_id ASC", (state,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── unique candidates ───────────────────────────────────────────────────
    def upsert_unique_candidate(
        self, cand: dict[str, Any], *, now: float | None = None
    ) -> None:
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
            (
                cand["uc_key"],
                cand.get("symbol"),
                cand.get("timeframe"),
                cand.get("family"),
                cand.get("params_hash"),
                cand.get("data_fingerprint"),
                cand.get("decision"),
                cand.get("validation_status"),
                cand.get("hard_status"),
                int(cand.get("n_trades") or 0),
                float(cand.get("avg_net_pct") or 0),
                cand.get("candidate_id"),
                cand.get("run_dir_label"),
                json.dumps(cand.get("params") or {}),
                cand.get("task_id"),
                cand.get("paper_status") or "",
                str(cand.get("regime_bucket") or ""),
                str(cand.get("search_family_id") or ""),
                str(cand.get("search_trial_id") or ""),
                int(cand.get("effective_n_trials") or 0),
                now,
            ),
        )
        self._conn.commit()

    def set_candidate_hard_status(
        self, candidate_id: str, hard_status: str, *, now: float | None = None
    ) -> int:
        now = time.time() if now is None else now
        cur = self._conn.execute(
            "UPDATE unique_candidates SET hard_status=?, updated_at=? WHERE candidate_id=?",
            (hard_status, now, str(candidate_id)),
        )
        self._conn.commit()
        return int(cur.rowcount or 0)

    def set_unique_hard_status(
        self, uc_key: str, hard_status: str, *, now: float | None = None
    ) -> int:
        now = time.time() if now is None else now
        cur = self._conn.execute(
            "UPDATE unique_candidates SET hard_status=?, updated_at=? WHERE uc_key=?",
            (hard_status, now, str(uc_key)),
        )
        self._conn.commit()
        return int(cur.rowcount or 0)

    def set_unique_paper_status(
        self, uc_key: str, paper_status: str, *, now: float | None = None
    ) -> int:
        now = time.time() if now is None else now
        cur = self._conn.execute(
            "UPDATE unique_candidates SET paper_status=?, updated_at=? WHERE uc_key=?",
            (paper_status, now, str(uc_key)),
        )
        self._conn.commit()
        return int(cur.rowcount or 0)

    def prune_terminal_tasks(self, *, keep: int = 5000, apply: bool = False) -> int:
        """Bound history: keep the newest ``keep`` terminal tasks, drop older. Returns removed."""
        if apply:
            raise ValueError("terminal task pruning is report-only")
        total = int(
            self._conn.execute(
                f"SELECT COUNT(*) FROM tasks WHERE state IN {TERMINAL_STATES}"
            ).fetchone()[0]
        )
        excess = max(0, total - int(keep))
        if not excess:
            return 0
        return excess

    def prune_unique_candidates(self, *, keep: int = 5000, apply: bool = False) -> int:
        """Bound the unique-candidate history (re-arm adds a row per new fingerprint)."""
        if apply:
            raise ValueError("unique candidate pruning is report-only")
        total = int(
            self._conn.execute("SELECT COUNT(*) FROM unique_candidates").fetchone()[0]
        )
        excess = max(0, total - int(keep))
        return excess

    def unique_candidates_for_gate(self) -> list[dict[str, Any]]:
        """Lean read of every candidate (the read-through gate aggregates per fingerprint/cell)."""
        rows = self._conn.execute(
            "SELECT symbol, timeframe, family, params_hash, data_fingerprint, decision, "
            "validation_status, hard_status, n_trades, avg_net_pct, regime_bucket, updated_at "
            "FROM unique_candidates"
        ).fetchall()
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
