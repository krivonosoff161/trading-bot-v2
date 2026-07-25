# -*- coding: utf-8 -*-
"""SQLite state store for Strategy Research Lab.

The DB stores run indexes and queue state. Raw result artifacts remain in the
private research workspace.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 6


class StaleJobClaimError(RuntimeError):
    """The worker's queue fence is absent, replaced, or expired."""


class FencingMigrationRequired(RuntimeError):
    """An existing compute DB needs an explicitly authorized v2 activation."""


_COMPAT_OWNERS: dict[int, str] = {}

_QUEUE_FENCING_COLUMNS = {
    "claim_owner",
    "claim_expires_at",
    "fencing_token",
    "mutation_protocol",
    "mutation_seq",
    "materialization_id",
    "materialization_digest",
}


class ResearchStateConnection(sqlite3.Connection):
    """SQLite connection carrying the monotonic authority clock for tests/runtime."""

    authority_clock: Any
    authority_time_floor: float


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def default_db_path(private_root: Path) -> Path:
    return private_root / "state" / "strategy_lab.sqlite"


def connect(db_path: Path, *, clock: Any = time.time) -> sqlite3.Connection:
    if db_path.exists():
        uri = db_path.resolve().as_posix()
        preflight = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
        preflight.row_factory = sqlite3.Row
        try:
            _require_activated_fencing(preflight)
        finally:
            preflight.close()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), factory=ResearchStateConnection)
    conn.authority_clock = clock
    conn.authority_time_floor = float(clock())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _fencing_marker(conn: sqlite3.Connection) -> str | None:
    if not _table_exists(conn, "meta"):
        return None
    row = conn.execute("SELECT value FROM meta WHERE key='fencing_protocol'").fetchone()
    return None if row is None else str(row[0])


def _trigger_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    }


def _require_activated_fencing(conn: sqlite3.Connection) -> None:
    """Reject legacy runtime DBs without mutating them."""
    if not _table_exists(conn, "queue"):
        return
    missing = _QUEUE_FENCING_COLUMNS - _column_names(conn, "queue")
    required_triggers = {"queue_fenced_v2_insert_guard", "queue_fenced_v2_guard"}
    missing_triggers = required_triggers - _trigger_names(conn)
    if missing or missing_triggers or _fencing_marker(conn) != "v2":
        if missing:
            detail = ",".join(sorted(missing))
        elif missing_triggers:
            detail = "triggers:" + ",".join(sorted(missing_triggers))
        else:
            detail = "capability_marker"
        raise FencingMigrationRequired(
            "compute DB requires explicit fencing v2 activation: " + detail
        )


def init_db(conn: sqlite3.Connection) -> None:
    _require_activated_fencing(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            artifact_label TEXT NOT NULL,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            promote_count INTEGER NOT NULL DEFAULT 0,
            observe_count INTEGER NOT NULL DEFAULT 0,
            reject_count INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS candidates (
            run_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            family TEXT NOT NULL,
            decision TEXT NOT NULL,
            reasons TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            params_json TEXT NOT NULL,
            validation_status TEXT NOT NULL DEFAULT '',
            validation_reasons TEXT NOT NULL DEFAULT '',
            next_action TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (run_id, candidate_id),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_candidates_decision ON candidates(decision);
        CREATE INDEX IF NOT EXISTS idx_candidates_symbol_family ON candidates(symbol, family);

        CREATE TABLE IF NOT EXISTS queue (
            job_id INTEGER PRIMARY KEY AUTOINCREMENT,
            spec_path TEXT NOT NULL,
            status TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 100,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            run_dir_label TEXT,
            last_error TEXT,
            claim_owner TEXT,
            claim_expires_at REAL,
            fencing_token INTEGER NOT NULL DEFAULT 0,
            mutation_protocol TEXT NOT NULL DEFAULT 'legacy.v1',
            mutation_seq INTEGER NOT NULL DEFAULT 0,
            materialization_id TEXT,
            materialization_digest TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_queue_status_priority
            ON queue(status, priority, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_spec_active
            ON queue(spec_path)
            WHERE status IN ('queued', 'running');
        CREATE TABLE IF NOT EXISTS job_attempts (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            owner_id TEXT NOT NULL,
            fencing_token INTEGER NOT NULL,
            state TEXT NOT NULL,
            claimed_at REAL NOT NULL,
            executing_at REAL,
            finished_at REAL,
            detail TEXT NOT NULL DEFAULT '',
            UNIQUE(job_id, fencing_token),
            FOREIGN KEY(job_id) REFERENCES queue(job_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS artifact_publications (
            publication_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            fencing_token INTEGER NOT NULL,
            provisional_path TEXT NOT NULL,
            final_label TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at REAL NOT NULL,
            published_at REAL,
            UNIQUE(job_id, fencing_token),
            FOREIGN KEY(job_id) REFERENCES queue(job_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS queue_materializations (
            materialization_id TEXT PRIMARY KEY,
            job_id INTEGER NOT NULL,
            spec_path TEXT NOT NULL,
            spec_digest TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES queue(job_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS stale_claim_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            owner_id TEXT NOT NULL,
            fencing_token INTEGER NOT NULL,
            operation TEXT NOT NULL,
            observed_at REAL NOT NULL,
            FOREIGN KEY(job_id) REFERENCES queue(job_id) ON DELETE CASCADE
        );

        -- Schema v3 (additive): a richer per-candidate result row for the
        -- universe farm + a per-run backend/runtime ledger. New tables only, so
        -- existing DBs gain them empty on next init_db (no destructive migration).
        CREATE TABLE IF NOT EXISTS farm_results (
            run_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            asset_group TEXT NOT NULL DEFAULT '',
            timeframe TEXT NOT NULL DEFAULT '',
            family TEXT NOT NULL,
            decision TEXT NOT NULL,
            validation_status TEXT NOT NULL DEFAULT '',
            backend TEXT NOT NULL DEFAULT '',
            n_trades INTEGER NOT NULL DEFAULT 0,
            win_rate REAL NOT NULL DEFAULT 0,
            avg_net_pct REAL NOT NULL DEFAULT 0,
            test_avg_net_pct REAL NOT NULL DEFAULT 0,
            profit_factor REAL NOT NULL DEFAULT 0,
            profit_factor_state_json TEXT NOT NULL DEFAULT '{}',
            data_file TEXT NOT NULL DEFAULT '',
            data_quality TEXT NOT NULL DEFAULT '',
            next_action TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            -- schema v4: richer decision-machine tracking
            max_drawdown_pct REAL NOT NULL DEFAULT 0,
            gpu_signal_supported INTEGER NOT NULL DEFAULT 0,
            hard_status TEXT NOT NULL DEFAULT '',
            validation_exported INTEGER NOT NULL DEFAULT 0,
            paper_status TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (run_id, candidate_id),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_farm_results_group_family
            ON farm_results(asset_group, family);
        CREATE INDEX IF NOT EXISTS idx_farm_results_decision ON farm_results(decision);

        CREATE TABLE IF NOT EXISTS runtime_stats (
            run_id TEXT PRIMARY KEY,
            requested_backend TEXT NOT NULL DEFAULT '',
            effective_backend TEXT NOT NULL DEFAULT '',
            signal_backend TEXT NOT NULL DEFAULT '',
            simulation_backend TEXT NOT NULL DEFAULT '',
            gpu_available INTEGER NOT NULL DEFAULT 0,
            fallback_reason TEXT NOT NULL DEFAULT '',
            accelerated_runs INTEGER NOT NULL DEFAULT 0,
            elapsed_ms REAL NOT NULL DEFAULT 0,
            timeframe TEXT NOT NULL DEFAULT '',
            n_results INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS paper_outcomes (
            trade_id TEXT PRIMARY KEY,
            setup_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            family TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL DEFAULT '',
            opened_at TEXT NOT NULL DEFAULT '',
            closed_at TEXT NOT NULL DEFAULT '',
            net_pct REAL NOT NULL DEFAULT 0,
            r_multiple REAL NOT NULL DEFAULT 0,
            data_fingerprint TEXT NOT NULL DEFAULT '',
            params_hash TEXT NOT NULL DEFAULT '',
            recorded_at TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_paper_outcomes_candidate
            ON paper_outcomes(candidate_id, recorded_at);
        """
    )
    _install_queue_fencing_trigger(conn)
    _migrate_candidate_columns(conn)
    _migrate_farm_results_columns(conn)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('fencing_protocol', 'v2')"
    )
    conn.commit()


def activate_fencing_v2(conn: sqlite3.Connection) -> None:
    """Explicitly activate compute fencing after a separately authorized quiesce.

    Runtime launchers and status readers must never call this function.  It is a
    deliberately separate rollout operation so a live private DB is not migrated
    merely because a process or dashboard opened it.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not _table_exists(conn, "queue"):
            raise FencingMigrationRequired("compute DB has no legacy queue to activate")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        _migrate_queue_fencing(conn)
        conn.execute("DROP TRIGGER IF EXISTS queue_fenced_v2_guard")
        conn.execute("DROP TRIGGER IF EXISTS queue_fenced_v2_insert_guard")
        _install_queue_fencing_trigger(conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('fencing_protocol', 'v2')"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate_queue_fencing(conn: sqlite3.Connection) -> None:
    existing = _column_names(conn, "queue")
    additions = {
        "claim_owner": "TEXT",
        "claim_expires_at": "REAL",
        "fencing_token": "INTEGER NOT NULL DEFAULT 0",
        "mutation_protocol": "TEXT NOT NULL DEFAULT 'legacy.v1'",
        "mutation_seq": "INTEGER NOT NULL DEFAULT 0",
        "materialization_id": "TEXT",
        "materialization_digest": "TEXT",
    }
    for name, declaration in additions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE queue ADD COLUMN {name} {declaration}")
    conn.execute(
        """UPDATE queue SET status='legacy_running_unfenced',
                  last_error='legacy_running_unfenced',
                  mutation_protocol='fenced.v2', mutation_seq=mutation_seq+1
           WHERE status='running' AND claim_owner IS NULL
             AND mutation_protocol='legacy.v1'"""
    )
    conn.execute(
        """UPDATE queue SET mutation_protocol='fenced.v2', mutation_seq=mutation_seq+1
           WHERE mutation_protocol='legacy.v1'"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_materialization
           ON queue(materialization_id) WHERE materialization_id IS NOT NULL"""
    )


def _install_queue_fencing_trigger(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS queue_fenced_v2_insert_guard
        BEFORE INSERT ON queue
        WHEN NEW.mutation_protocol != 'fenced.v2'
        BEGIN
            SELECT RAISE(ABORT, 'fenced v2 writer required');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS queue_fenced_v2_guard
        BEFORE UPDATE ON queue
        WHEN (
            NEW.status IS NOT OLD.status OR
            NEW.attempts IS NOT OLD.attempts OR
            NEW.claim_owner IS NOT OLD.claim_owner OR
            NEW.claim_expires_at IS NOT OLD.claim_expires_at OR
            NEW.fencing_token IS NOT OLD.fencing_token OR
            NEW.materialization_id IS NOT OLD.materialization_id OR
            NEW.materialization_digest IS NOT OLD.materialization_digest OR
            NEW.run_dir_label IS NOT OLD.run_dir_label OR
            NEW.last_error IS NOT OLD.last_error
        ) AND (
            NEW.mutation_protocol != 'fenced.v2' OR
            NEW.mutation_seq != OLD.mutation_seq + 1
        )
        BEGIN
            SELECT RAISE(ABORT, 'fenced v2 writer required');
        END
        """
    )


def _migrate_candidate_columns(conn: sqlite3.Connection) -> None:
    """Schema v1 -> v2: add validation columns to pre-existing candidates tables."""
    existing = {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(candidates)")
    }
    for column in ("validation_status", "validation_reasons", "next_action"):
        if column not in existing:
            conn.execute(
                f"ALTER TABLE candidates ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
            )


def _migrate_farm_results_columns(conn: sqlite3.Connection) -> None:
    """Schema v3 -> v4: add decision-machine columns to a pre-existing farm_results."""
    if not list(conn.execute("PRAGMA table_info(farm_results)")):
        return  # table created fresh with v4 columns already
    existing = {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(farm_results)")
    }
    additions = {
        "max_drawdown_pct": "REAL NOT NULL DEFAULT 0",
        "gpu_signal_supported": "INTEGER NOT NULL DEFAULT 0",
        "hard_status": "TEXT NOT NULL DEFAULT ''",
        "validation_exported": "INTEGER NOT NULL DEFAULT 0",
        "paper_status": "TEXT NOT NULL DEFAULT ''",
        "profit_factor_state_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for column, decl in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE farm_results ADD COLUMN {column} {decl}")


def import_completed_runs(
    private_root: Path, db_path: Path | None = None
) -> dict[str, int]:
    private_root = private_root.expanduser().resolve()
    db_path = db_path or default_db_path(private_root)
    completed_root = private_root / "experiments" / "completed"
    conn = connect(db_path)
    init_db(conn)
    stats = {"seen": 0, "imported": 0, "candidates": 0}
    if not completed_root.exists():
        conn.close()
        return stats
    for run_dir in sorted(p for p in completed_root.iterdir() if p.is_dir()):
        stats["seen"] += 1
        imported = import_run_dir(conn, private_root, run_dir)
        if imported:
            stats["imported"] += 1
            stats["candidates"] += imported
    conn.commit()
    conn.close()
    return stats


def import_run_dir(
    conn: sqlite3.Connection,
    private_root: Path,
    run_dir: Path,
    *,
    artifact_label_override: str | None = None,
) -> int:
    run_dir = run_dir.resolve()
    run_dir.relative_to(private_root.resolve())
    payload = _load_json(run_dir / "metrics.json")
    rows = _extract_results(payload)
    if not rows:
        rows = _load_candidate_rows(run_dir / "candidates.csv")
    counts = _decision_counts(rows)
    run_id = run_dir.name
    experiment_id = str(
        payload.get("experiment_id") or _experiment_from_run_name(run_id)
    )
    created_at = str(payload.get("created_at") or "")
    artifact_label = artifact_label_override or _artifact_label(private_root, run_dir)
    now = utc_now()
    conn.execute(
        """
        INSERT INTO runs(
            run_id, experiment_id, created_at, artifact_label, candidate_count,
            promote_count, observe_count, reject_count, imported_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            experiment_id=excluded.experiment_id,
            created_at=excluded.created_at,
            artifact_label=excluded.artifact_label,
            candidate_count=excluded.candidate_count,
            promote_count=excluded.promote_count,
            observe_count=excluded.observe_count,
            reject_count=excluded.reject_count,
            imported_at=excluded.imported_at
        """,
        (
            run_id,
            experiment_id,
            created_at,
            artifact_label,
            len(rows),
            counts.get("PROMOTE_FOR_PRESSURE_TEST", 0),
            counts.get("OBSERVE", 0),
            counts.get("REJECT", 0),
            now,
        ),
    )
    conn.execute("DELETE FROM candidates WHERE run_id = ?", (run_id,))
    for row in rows:
        conn.execute(
            """
            INSERT INTO candidates(
                run_id, candidate_id, symbol, family, decision, reasons,
                metrics_json, params_json, validation_status, validation_reasons,
                next_action
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                str(row.get("run_id") or row.get("candidate_id") or ""),
                str(row.get("symbol") or ""),
                str(row.get("family") or ""),
                str(row.get("decision") or "UNKNOWN"),
                _reasons_text(row),
                json.dumps(
                    row.get("metrics") or _metrics_from_csv_row(row), sort_keys=True
                ),
                json.dumps(row.get("params") or {}, sort_keys=True),
                str(row.get("validation_status") or ""),
                _validation_reasons_text(row),
                str(row.get("next_action") or ""),
            ),
        )
    _import_farm_results(conn, run_id, payload, rows)
    _import_runtime_stats(conn, run_id, payload, len(rows))
    return len(rows)


def enqueue_experiment(
    conn: sqlite3.Connection,
    spec_path: Path,
    *,
    priority: int = 100,
    status: str = "queued",
    materialization_id: str | None = None,
    materialization_digest: str | None = None,
) -> int:
    now = utc_now()
    cur = conn.execute(
        """
        INSERT INTO queue(
            spec_path, status, priority, created_at, mutation_protocol,
            materialization_id, materialization_digest)
        VALUES (?, ?, ?, ?, 'fenced.v2', ?, ?)
        """,
        (
            str(spec_path),
            status,
            int(priority),
            now,
            materialization_id,
            materialization_digest,
        ),
    )
    if cur.lastrowid is None:
        raise RuntimeError("queue insert did not return an id")
    job_id = int(cur.lastrowid)
    if materialization_id is not None:
        conn.execute(
            """INSERT INTO queue_materializations(
                   materialization_id, job_id, spec_path, spec_digest, created_at)
               VALUES(?,?,?,?,?)""",
            (
                materialization_id,
                job_id,
                str(spec_path),
                str(materialization_digest or ""),
                now,
            ),
        )
    conn.commit()
    return job_id


def ensure_experiment_queued(
    conn: sqlite3.Connection,
    spec_path: Path,
    *,
    priority: int = 100,
    materialization_id: str | None = None,
    materialization_digest: str | None = None,
) -> tuple[int, bool]:
    normalized = str(spec_path)
    incoming_digest = str(materialization_digest or "")

    def assert_current_content() -> None:
        if materialization_id is None:
            return
        try:
            actual = (
                "sha256:"
                + hashlib.sha256(
                    Path(normalized).read_text(encoding="utf-8").encode("utf-8")
                ).hexdigest()
            )
        except (OSError, UnicodeError) as exc:
            raise ValueError("materialization spec content is unreadable") from exc
        if actual != incoming_digest:
            raise ValueError("materialization digest does not match spec content")

    try:
        conn.execute("BEGIN IMMEDIATE")
        if materialization_id is not None:
            binding = conn.execute(
                """SELECT qm.job_id, qm.spec_path, qm.spec_digest,
                          q.materialization_digest AS queue_digest
                   FROM queue_materializations qm
                   JOIN queue q ON q.job_id=qm.job_id
                   WHERE qm.materialization_id=?""",
                (materialization_id,),
            ).fetchone()
            if binding is not None:
                if (
                    str(binding["spec_path"]) != normalized
                    or str(binding["spec_digest"]) != str(materialization_digest or "")
                    or str(binding["queue_digest"] or "") != incoming_digest
                ):
                    raise ValueError(
                        "materialization id is bound to different spec content"
                    )
                assert_current_content()
                conn.commit()
                return int(binding["job_id"]), False
            assert_current_content()
        if materialization_id is not None:
            active = conn.execute(
                """SELECT job_id, status, materialization_digest FROM queue
                   WHERE spec_path=? AND status IN ('queued','running')
                   ORDER BY job_id ASC LIMIT 1""",
                (normalized,),
            ).fetchone()
            if active is not None:
                if str(active["materialization_digest"] or "") != incoming_digest:
                    raise ValueError(
                        "active spec path is bound to different materialization content"
                    )
                row = active
            else:
                row = conn.execute(
                    """SELECT job_id, status, materialization_digest FROM queue
                       WHERE spec_path=? AND status='completed'
                         AND materialization_digest=?
                       ORDER BY job_id ASC LIMIT 1""",
                    (normalized, incoming_digest),
                ).fetchone()
        else:
            row = conn.execute(
                """SELECT job_id, status, materialization_digest FROM queue
                   WHERE spec_path=? AND status IN ('queued','running','completed')
                   ORDER BY job_id ASC LIMIT 1""",
                (normalized,),
            ).fetchone()
        if row is not None:
            if materialization_id is not None:
                conn.execute(
                    """INSERT INTO queue_materializations(
                           materialization_id, job_id, spec_path, spec_digest, created_at)
                       VALUES(?,?,?,?,?)""",
                    (
                        materialization_id,
                        int(row["job_id"]),
                        normalized,
                        incoming_digest,
                        utc_now(),
                    ),
                )
            conn.commit()
            return int(row["job_id"]), False
        now = utc_now()
        cur = conn.execute(
            """
            INSERT INTO queue(
                spec_path, status, priority, created_at,
                mutation_protocol, materialization_id, materialization_digest)
            VALUES (?, 'queued', ?, ?, 'fenced.v2', ?, ?)
            """,
            (
                normalized,
                int(priority),
                now,
                materialization_id,
                incoming_digest if materialization_id else None,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("queue insert did not return an id")
        job_id = int(cur.lastrowid)
        if materialization_id is not None:
            conn.execute(
                """INSERT INTO queue_materializations(
                       materialization_id, job_id, spec_path, spec_digest, created_at)
                   VALUES(?,?,?,?,?)""",
                (
                    materialization_id,
                    job_id,
                    normalized,
                    incoming_digest,
                    now,
                ),
            )
        conn.commit()
        return job_id, True
    except sqlite3.IntegrityError:
        conn.rollback()
        if materialization_id is not None:
            binding = conn.execute(
                """SELECT qm.job_id, qm.spec_path, qm.spec_digest,
                          q.materialization_digest AS queue_digest
                   FROM queue_materializations qm
                   JOIN queue q ON q.job_id=qm.job_id
                   WHERE qm.materialization_id=?""",
                (materialization_id,),
            ).fetchone()
            if binding is not None:
                if (
                    str(binding["spec_path"]) != normalized
                    or str(binding["spec_digest"]) != str(materialization_digest or "")
                    or str(binding["queue_digest"] or "") != incoming_digest
                ):
                    raise ValueError(
                        "materialization id is bound to different spec content"
                    )
                assert_current_content()
                return int(binding["job_id"]), False
            assert_current_content()
        if materialization_id is not None:
            active = conn.execute(
                """SELECT job_id, materialization_digest FROM queue
                   WHERE spec_path=? AND status IN ('queued','running')
                   ORDER BY job_id LIMIT 1""",
                (normalized,),
            ).fetchone()
            if active is not None:
                if str(active["materialization_digest"] or "") != incoming_digest:
                    raise ValueError(
                        "active spec path is bound to different materialization content"
                    )
                row = active
            else:
                row = conn.execute(
                    """SELECT job_id FROM queue
                       WHERE spec_path=? AND status='completed'
                         AND materialization_digest=?
                       ORDER BY job_id LIMIT 1""",
                    (normalized, incoming_digest),
                ).fetchone()
        else:
            row = conn.execute(
                """SELECT job_id FROM queue
                   WHERE spec_path=? AND status IN ('queued','running','completed')
                   ORDER BY job_id LIMIT 1""",
                (normalized,),
            ).fetchone()
        if row is None:
            raise
        if materialization_id is not None:
            conn.execute(
                """INSERT OR IGNORE INTO queue_materializations(
                       materialization_id, job_id, spec_path, spec_digest, created_at)
                   VALUES(?,?,?,?,?)""",
                (
                    materialization_id,
                    int(row["job_id"]),
                    normalized,
                    incoming_digest,
                    utc_now(),
                ),
            )
            conn.commit()
            binding = conn.execute(
                """SELECT qm.job_id, qm.spec_path, qm.spec_digest,
                          q.materialization_digest AS queue_digest
                   FROM queue_materializations qm
                   JOIN queue q ON q.job_id=qm.job_id
                   WHERE qm.materialization_id=?""",
                (materialization_id,),
            ).fetchone()
            if binding is None or (
                str(binding["spec_path"]) != normalized
                or str(binding["spec_digest"]) != str(materialization_digest or "")
                or str(binding["queue_digest"] or "") != incoming_digest
            ):
                raise ValueError(
                    "materialization id is bound to different spec content"
                )
            return int(binding["job_id"]), False
        return int(row["job_id"]), False
    except Exception:
        conn.rollback()
        raise


def _compat_owner(conn: sqlite3.Connection) -> str:
    return _COMPAT_OWNERS.setdefault(id(conn), f"compat-worker-{uuid.uuid4().hex}")


def _epoch(conn: sqlite3.Connection, now: float | None) -> float:
    clock = getattr(conn, "authority_clock", time.time)
    observed = float(clock())
    supplied = observed if now is None else float(now)
    current = max(
        observed, supplied, float(getattr(conn, "authority_time_floor", observed))
    )
    if hasattr(conn, "authority_time_floor"):
        conn.authority_time_floor = current
    return current


def claim_next_job(
    conn: sqlite3.Connection,
    *,
    owner_id: str | None = None,
    lease_seconds: float = 300.0,
    now: float | None = None,
) -> dict[str, Any] | None:
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    owner = owner_id or _compat_owner(conn)
    current = _epoch(conn, now)
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = _epoch(conn, current)
        row = conn.execute(
            """
            SELECT * FROM queue
            WHERE status = 'queued'
            ORDER BY priority ASC, created_at ASC, job_id ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        job_id = int(row["job_id"])
        fence = int(row["fencing_token"] or 0) + 1
        mutation_seq = int(row["mutation_seq"] or 0) + 1
        cur = conn.execute(
            """
            UPDATE queue
            SET status = 'running', started_at = ?, attempts = attempts + 1,
                claim_owner=?, claim_expires_at=?, fencing_token=?,
                mutation_protocol='fenced.v2', mutation_seq=?
            WHERE job_id = ? AND status = 'queued' AND claim_owner IS NULL
              AND fencing_token=? AND mutation_protocol='fenced.v2'
              AND mutation_seq=?
            """,
            (
                utc_now(),
                owner,
                current + float(lease_seconds),
                fence,
                mutation_seq,
                job_id,
                int(row["fencing_token"] or 0),
                int(row["mutation_seq"] or 0),
            ),
        )
        if cur.rowcount != 1:
            raise StaleJobClaimError("job changed during claim")
        conn.execute(
            """INSERT INTO job_attempts(
                   job_id, owner_id, fencing_token, state, claimed_at)
               VALUES(?,?,?,'claimed',?)""",
            (job_id, owner, fence, current),
        )
        conn.commit()
        claimed = conn.execute(
            "SELECT * FROM queue WHERE job_id = ?", (job_id,)
        ).fetchone()
        return dict(claimed) if claimed is not None else None
    except Exception:
        conn.rollback()
        raise


def _assert_job_claim(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    owner_id: str,
    fencing_token: int,
    now: float,
) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM queue WHERE job_id=?", (int(job_id),)).fetchone()
    if (
        row is None
        or row["status"] != "running"
        or str(row["claim_owner"] or "") != owner_id
        or int(row["fencing_token"] or 0) != int(fencing_token)
        or float(row["claim_expires_at"] or 0) <= now
    ):
        raise StaleJobClaimError(f"stale claim for job {job_id}")
    return row


def _record_stale_claim(
    conn: sqlite3.Connection,
    job_id: int,
    owner_id: str,
    fencing_token: int,
    operation: str,
    now: float,
) -> None:
    conn.execute(
        """INSERT INTO stale_claim_events(
               job_id, owner_id, fencing_token, operation, observed_at)
           VALUES(?,?,?,?,?)""",
        (int(job_id), owner_id, int(fencing_token), operation, now),
    )
    conn.commit()


def mark_job_executing(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    owner_id: str,
    fencing_token: int,
    now: float | None = None,
) -> None:
    current = _epoch(conn, now)
    _assert_job_claim(
        conn,
        job_id,
        owner_id=owner_id,
        fencing_token=fencing_token,
        now=current,
    )
    current = _epoch(conn, current)
    cur = conn.execute(
        """UPDATE job_attempts SET state='executing', executing_at=?
           WHERE job_id=? AND owner_id=? AND fencing_token=? AND state='claimed'
             AND EXISTS(
                 SELECT 1 FROM queue q WHERE q.job_id=job_attempts.job_id
                   AND q.status='running' AND q.claim_owner=?
                   AND q.fencing_token=? AND q.claim_expires_at>?
                   AND q.mutation_protocol='fenced.v2'
             )""",
        (
            current,
            int(job_id),
            owner_id,
            int(fencing_token),
            owner_id,
            int(fencing_token),
            current,
        ),
    )
    if cur.rowcount != 1:
        conn.rollback()
        raise StaleJobClaimError(f"attempt changed for job {job_id}")
    conn.commit()


def renew_job_lease(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    owner_id: str,
    fencing_token: int,
    lease_seconds: float,
    now: float | None = None,
) -> float:
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    current = _epoch(conn, now)
    row = _assert_job_claim(
        conn,
        job_id,
        owner_id=owner_id,
        fencing_token=fencing_token,
        now=current,
    )
    expires = current + float(lease_seconds)
    current = _epoch(conn, current)
    _assert_job_claim(
        conn,
        job_id,
        owner_id=owner_id,
        fencing_token=fencing_token,
        now=current,
    )
    expires = current + float(lease_seconds)
    cur = conn.execute(
        """UPDATE queue SET claim_expires_at=?, mutation_protocol='fenced.v2',
                  mutation_seq=mutation_seq+1
           WHERE job_id=? AND status='running' AND claim_owner=?
             AND fencing_token=? AND claim_expires_at>?
             AND mutation_protocol='fenced.v2' AND mutation_seq=?""",
        (
            expires,
            int(job_id),
            owner_id,
            int(fencing_token),
            current,
            int(row["mutation_seq"] or 0),
        ),
    )
    if cur.rowcount != 1:
        conn.rollback()
        raise StaleJobClaimError(f"job {job_id} changed during renewal")
    conn.commit()
    return expires


def reap_stale_jobs(
    conn: sqlite3.Connection,
    *,
    max_age_seconds: int = 3600,
    now: float | None = None,
) -> int:
    """Requeue only expired claims, retaining executing attempts as ambiguous."""
    current = _epoch(conn, now)
    count = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = _epoch(conn, current)
        rows = conn.execute(
            """SELECT * FROM queue
               WHERE status='running' AND mutation_protocol='fenced.v2'
                 AND claim_owner IS NOT NULL AND claim_expires_at<=?""",
            (current,),
        ).fetchall()
        for row in rows:
            job_id = int(row["job_id"])
            fence = int(row["fencing_token"] or 0)
            owner = str(row["claim_owner"])
            seq = int(row["mutation_seq"] or 0) + 1
            cur = conn.execute(
                """UPDATE queue SET status='queued', started_at=NULL,
                       claim_owner=NULL, claim_expires_at=NULL,
                       last_error=?, mutation_protocol='fenced.v2', mutation_seq=?
                   WHERE job_id=? AND status='running' AND claim_owner=?
                     AND fencing_token=? AND claim_expires_at<=?
                     AND mutation_protocol='fenced.v2' AND mutation_seq=?""",
                (
                    "requeued stale running job: expired fenced lease",
                    seq,
                    job_id,
                    owner,
                    fence,
                    current,
                    int(row["mutation_seq"] or 0),
                ),
            )
            if cur.rowcount != 1:
                continue
            attempt = conn.execute(
                "SELECT state FROM job_attempts WHERE job_id=? AND fencing_token=?",
                (job_id, fence),
            ).fetchone()
            if attempt is not None:
                next_attempt_state = (
                    "ambiguous" if attempt["state"] == "executing" else "superseded"
                )
                conn.execute(
                    """UPDATE job_attempts SET state=?, finished_at=?, detail=?
                       WHERE job_id=? AND fencing_token=?""",
                    (
                        next_attempt_state,
                        current,
                        "lease expired before terminal publication",
                        job_id,
                        fence,
                    ),
                )
            count += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return count


def complete_job(
    conn: sqlite3.Connection,
    job_id: int,
    run_dir_label: str,
    *,
    owner_id: str,
    fencing_token: int,
    now: float | None = None,
) -> None:
    _finish_job(
        conn,
        job_id,
        "completed",
        run_dir_label=run_dir_label,
        owner_id=owner_id,
        fencing_token=fencing_token,
        now=now,
    )


def fail_job(
    conn: sqlite3.Connection,
    job_id: int,
    error: str,
    *,
    owner_id: str,
    fencing_token: int,
    now: float | None = None,
) -> None:
    _finish_job(
        conn,
        job_id,
        "failed",
        error=error,
        owner_id=owner_id,
        fencing_token=fencing_token,
        now=now,
    )


def _finish_job(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    *,
    run_dir_label: str | None = None,
    error: str = "",
    owner_id: str,
    fencing_token: int,
    now: float | None,
) -> None:
    current = _epoch(conn, now)
    row = conn.execute("SELECT * FROM queue WHERE job_id=?", (int(job_id),)).fetchone()
    if row is None:
        raise KeyError(f"unknown job {job_id}")
    owner = str(owner_id)
    fence = int(fencing_token)
    try:
        row = _assert_job_claim(
            conn,
            job_id,
            owner_id=owner,
            fencing_token=fence,
            now=current,
        )
    except StaleJobClaimError:
        _record_stale_claim(
            conn,
            job_id,
            owner,
            fence,
            status,
            current,
        )
        raise
    seq = int(row["mutation_seq"] or 0) + 1
    try:
        current = _epoch(conn, current)
        cur = conn.execute(
            """UPDATE queue
               SET status=?, finished_at=?, run_dir_label=?, last_error=?,
                   claim_owner=NULL, claim_expires_at=NULL,
                   mutation_protocol='fenced.v2', mutation_seq=?
               WHERE job_id=? AND status='running' AND claim_owner=?
                 AND fencing_token=? AND claim_expires_at>?
                 AND mutation_protocol='fenced.v2' AND mutation_seq=?""",
            (
                status,
                utc_now(),
                run_dir_label,
                None if status == "completed" else error[:1000],
                seq,
                int(job_id),
                owner,
                fence,
                current,
                int(row["mutation_seq"] or 0),
            ),
        )
        if cur.rowcount != 1:
            raise StaleJobClaimError(f"job {job_id} changed during finish")
        conn.execute(
            """UPDATE job_attempts SET state=?, finished_at=?, detail=?
               WHERE job_id=? AND fencing_token=? AND state IN ('claimed','executing')""",
            (
                status,
                current,
                error[:1000],
                int(job_id),
                int(row["fencing_token"] or 0),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def publish_completed_job(
    conn: sqlite3.Connection,
    private_root: Path,
    provisional_dir: Path,
    *,
    job_id: int,
    owner_id: str,
    fencing_token: int,
    now: float | None = None,
) -> tuple[Path, int]:
    """Atomically import a provisional run and complete its fenced queue row.

    The filesystem promotion follows the database commit and is tracked by a
    durable publication row, so a crash cannot make an unfenced worker's output
    authoritative and an interrupted final rename remains recoverable.
    """
    current = _epoch(conn, now)
    private_root = Path(private_root).resolve()
    provisional_dir = Path(provisional_dir).resolve()
    provisional_dir.relative_to(
        (private_root / "experiments" / "provisional").resolve()
    )
    generation_path = provisional_dir / "publication_generation.json"
    try:
        generation = json.loads(generation_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise StaleJobClaimError("provisional generation identity is missing") from exc
    if (
        generation.get("schema") != "strategy_lab_publication_generation.v1"
        or int(generation.get("job_id") or 0) != int(job_id)
        or str(generation.get("owner_id") or "") != owner_id
        or int(generation.get("fencing_token") or 0) != int(fencing_token)
    ):
        raise StaleJobClaimError("provisional generation identity does not match claim")
    final_dir = private_root / "experiments" / "completed" / provisional_dir.name
    final_label = f"experiments/completed/{provisional_dir.name}"
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = _epoch(conn, current)
        row = _assert_job_claim(
            conn,
            job_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            now=current,
        )
        imported = import_run_dir(
            conn,
            private_root,
            provisional_dir,
            artifact_label_override=final_label,
        )
        # Recheck at the publication boundary. Expiry immediately removes
        # authority even when no replacement worker has claimed the row yet.
        boundary_now = _epoch(conn, now)
        row = _assert_job_claim(
            conn,
            job_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            now=boundary_now,
        )
        seq = int(row["mutation_seq"] or 0) + 1
        cur = conn.execute(
            """UPDATE queue SET status='completed', finished_at=?, run_dir_label=?,
                      last_error=NULL, claim_owner=NULL, claim_expires_at=NULL,
                      mutation_protocol='fenced.v2', mutation_seq=?
               WHERE job_id=? AND status='running' AND claim_owner=?
                 AND fencing_token=? AND claim_expires_at>?
                 AND mutation_protocol='fenced.v2' AND mutation_seq=?""",
            (
                utc_now(),
                final_label,
                seq,
                int(job_id),
                owner_id,
                int(fencing_token),
                boundary_now,
                int(row["mutation_seq"] or 0),
            ),
        )
        if cur.rowcount != 1:
            raise StaleJobClaimError(f"job {job_id} changed during publication")
        conn.execute(
            """UPDATE job_attempts SET state='completed', finished_at=?
               WHERE job_id=? AND owner_id=? AND fencing_token=?
                 AND state IN ('claimed','executing')""",
            (boundary_now, int(job_id), owner_id, int(fencing_token)),
        )
        conn.execute(
            """INSERT INTO artifact_publications(
                   job_id, fencing_token, provisional_path, final_label, state, created_at)
               VALUES(?,?,?,?, 'pending_rename', ?)
               ON CONFLICT(job_id, fencing_token) DO UPDATE SET
                   provisional_path=excluded.provisional_path,
                   final_label=excluded.final_label""",
            (
                int(job_id),
                int(fencing_token),
                str(provisional_dir),
                final_label,
                boundary_now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    recover_pending_publications(
        conn,
        private_root,
        job_id=job_id,
        fencing_token=fencing_token,
        now=now,
    )
    return final_dir, imported


def recover_pending_publications(
    conn: sqlite3.Connection,
    private_root: Path,
    *,
    job_id: int | None = None,
    fencing_token: int | None = None,
    now: float | None = None,
) -> int:
    """Finalize committed publication renames without requiring a live claim.

    Authority comes from the exact committed ``(job_id, fencing_token)``
    generation in ``artifact_publications``.  This function never re-imports a
    run and never changes the terminal queue row.
    """
    private_root = Path(private_root).resolve()
    provisional_root = (private_root / "experiments" / "provisional").resolve()
    completed_root = (private_root / "experiments" / "completed").resolve()
    where = "state='pending_rename'"
    params: list[Any] = []
    if job_id is not None:
        where += " AND job_id=?"
        params.append(int(job_id))
    if fencing_token is not None:
        where += " AND fencing_token=?"
        params.append(int(fencing_token))
    rows = conn.execute(
        f"SELECT * FROM artifact_publications WHERE {where} ORDER BY publication_id",
        params,
    ).fetchall()
    recovered = 0
    for row in rows:
        provisional_dir = Path(str(row["provisional_path"])).resolve()
        provisional_dir.relative_to(provisional_root)
        label_path = Path(str(row["final_label"]))
        if label_path.is_absolute():
            raise RuntimeError("publication final label must be relative")
        final_dir = (private_root / label_path).resolve()
        final_dir.relative_to(completed_root)
        if final_dir.name != provisional_dir.name:
            raise RuntimeError("publication paths identify different generations")
        if provisional_dir.exists() and final_dir.exists():
            raise RuntimeError("publication has both provisional and final directories")
        if provisional_dir.exists():
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            provisional_dir.replace(final_dir)
        if not final_dir.exists():
            continue
        cur = conn.execute(
            """UPDATE artifact_publications
               SET state='directory_published', published_at=?
               WHERE publication_id=? AND state='pending_rename'
                 AND job_id=? AND fencing_token=?""",
            (
                _epoch(conn, now),
                int(row["publication_id"]),
                int(row["job_id"]),
                int(row["fencing_token"]),
            ),
        )
        if cur.rowcount == 1:
            conn.commit()
            recovered += 1
        else:
            conn.rollback()
    return recovered


def mark_publication_indexes_published(
    conn: sqlite3.Connection,
    job_id: int,
    fencing_token: int,
    *,
    now: float | None = None,
) -> None:
    cur = conn.execute(
        """UPDATE artifact_publications SET state='published', published_at=?
           WHERE job_id=? AND fencing_token=?
             AND state IN ('directory_published','published')""",
        (_epoch(conn, now), int(job_id), int(fencing_token)),
    )
    if cur.rowcount != 1:
        conn.rollback()
        raise RuntimeError("publication generation is not ready for secondary indexes")
    conn.commit()


def dashboard_snapshot(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"exists": False, "runs": [], "queue": [], "totals": {}}
    uri = Path(db_path).resolve().as_posix()
    conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    runs = [
        dict(r)
        for r in conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 20")
    ]
    candidates = [
        {
            "run_id": str(r["run_id"]),
            "candidate_id": str(r["candidate_id"]),
            "symbol": str(r["symbol"]),
            "family": str(r["family"]),
            "decision": str(r["decision"]),
            "reasons": str(r["reasons"]),
            "validation_status": str(r["validation_status"]),
            "next_action": str(r["next_action"]),
        }
        for r in conn.execute(
            """
            SELECT run_id, candidate_id, symbol, family, decision, reasons,
                   validation_status, next_action
            FROM candidates
            ORDER BY
              CASE validation_status
                WHEN 'FORWARD_PAPER' THEN 4
                WHEN 'REGIME_SPECIFIC' THEN 3
                WHEN 'OBSERVE' THEN 2
                WHEN 'REJECT' THEN 1
                ELSE 0
              END DESC,
              CASE decision
                WHEN 'PROMOTE_FOR_PRESSURE_TEST' THEN 3
                WHEN 'OBSERVE' THEN 2
                WHEN 'REJECT' THEN 1
                ELSE 0
              END DESC,
              run_id DESC
            LIMIT 30
            """
        )
    ]
    queue = [
        _queue_row_for_snapshot(dict(r))
        for r in conn.execute("SELECT * FROM queue ORDER BY job_id DESC LIMIT 20")
    ]
    totals = dict(
        conn.execute(
            "SELECT COUNT(*) AS run_count, COALESCE(SUM(candidate_count), 0) AS candidate_count FROM runs"
        ).fetchone()
    )
    queue_counts = {
        str(r["status"]): int(r["n"])
        for r in conn.execute("SELECT status, COUNT(*) AS n FROM queue GROUP BY status")
    }
    validation_counts = {
        str(r["validation_status"] or "UNKNOWN"): int(r["n"])
        for r in conn.execute(
            "SELECT validation_status, COUNT(*) AS n FROM candidates GROUP BY validation_status"
        )
    }
    conn.close()
    return {
        "exists": True,
        "db_label": "strategy-lab/state/strategy_lab.sqlite",
        "runs": runs,
        "candidates": candidates,
        "queue": queue,
        "totals": totals,
        "queue_counts": queue_counts,
        "validation_counts": validation_counts,
    }


def _artifact_label(private_root: Path, run_dir: Path) -> str:
    try:
        return str(run_dir.relative_to(private_root)).replace("\\", "/")
    except ValueError:
        return run_dir.name


def _queue_row_for_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    spec = str(row.get("spec_path") or "")
    return {
        "job_id": row.get("job_id"),
        "spec_label": _spec_label(spec),
        "status": row.get("status"),
        "priority": row.get("priority"),
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "attempts": row.get("attempts"),
        "run_dir_label": row.get("run_dir_label"),
        "last_error": row.get("last_error"),
    }


def _spec_label(raw: str) -> str:
    normalized = raw.replace("\\", "/")
    marker = "configs/strategy_lab/"
    if marker in normalized:
        return marker + normalized.rsplit(marker, 1)[-1]
    return Path(raw).name if raw else ""


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _extract_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("results")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _load_candidate_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            return [dict(row) for row in csv.DictReader(f)]
    except OSError:
        return []


def _decision_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        decision = str(row.get("decision") or "UNKNOWN")
        counts[decision] = counts.get(decision, 0) + 1
    return counts


def _group_map() -> dict[str, str]:
    """symbol -> universe group (best effort; '' for all if universe unavailable)."""
    try:
        from src.research_lab.universe import load_universe

        uni = load_universe()
        return {sym: group for group, members in uni.groups.items() for sym in members}
    except Exception:
        return {}


def _norm_symbol(symbol: str) -> str:
    return str(symbol).replace("-", "_").replace("/", "_").upper()


def _row_metric(row: dict[str, Any], key: str) -> Any:
    """Read a metric from a metrics.json row (nested) or a candidates.csv row (flat)."""
    metrics = row.get("metrics")
    if isinstance(metrics, dict) and key in metrics:
        return metrics[key]
    return row.get(key)


def _data_quality(n_trades: int, min_trades: int) -> str:
    if n_trades <= 0:
        return "no_trades"
    if min_trades and n_trades < min_trades:
        return "thin"
    return "ok"


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _import_farm_results(
    conn: sqlite3.Connection,
    run_id: str,
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    """Richer per-candidate result rows (group/timeframe/backend/data_quality)."""
    from src.research_lab.gpu_runtime import GPU_SUPPORTED_FAMILIES

    runtime = (payload.get("runtime") if isinstance(payload, dict) else {}) or {}
    backend = str(
        runtime.get("effective_backend") or runtime.get("signal_backend") or ""
    )
    created_at = str((payload or {}).get("created_at") or utc_now())
    top_tf = str((payload or {}).get("timeframe") or "")
    plan_meta = (payload.get("plan_meta") if isinstance(payload, dict) else {}) or {}
    planned_group = (
        str(plan_meta.get("group") or "") if isinstance(plan_meta, dict) else ""
    )
    group_map = _group_map()
    conn.execute("DELETE FROM farm_results WHERE run_id = ?", (run_id,))
    for row in rows:
        symbol = str(row.get("symbol") or "")
        family = str(row.get("family") or "")
        n_trades = _as_int(_row_metric(row, "n_trades"))
        min_trades = _as_int(_row_metric(row, "min_trades"))
        conn.execute(
            """
            INSERT INTO farm_results(
                run_id, candidate_id, symbol, asset_group, timeframe, family, decision,
                validation_status, backend, n_trades, win_rate, avg_net_pct,
                test_avg_net_pct, profit_factor, profit_factor_state_json,
                data_file, data_quality, next_action, created_at,
                max_drawdown_pct, gpu_signal_supported
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                str(row.get("run_id") or row.get("candidate_id") or ""),
                symbol,
                planned_group or group_map.get(_norm_symbol(symbol), ""),
                str(_row_metric(row, "data_file_timeframe") or top_tf or ""),
                family,
                str(row.get("decision") or "UNKNOWN"),
                str(row.get("validation_status") or ""),
                backend,
                n_trades,
                _as_float(_row_metric(row, "win_rate")),
                _as_float(_row_metric(row, "avg_net_pct")),
                _as_float(_row_metric(row, "test_avg_net_pct")),
                _as_float(_row_metric(row, "profit_factor")),
                json.dumps(
                    _row_metric(row, "profit_factor_state") or {},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                str(_row_metric(row, "data_file_label") or ""),
                _data_quality(n_trades, min_trades),
                str(row.get("next_action") or ""),
                created_at,
                _as_float(_row_metric(row, "max_drawdown_pct")),
                1 if family in GPU_SUPPORTED_FAMILIES else 0,
            ),
        )


def _import_runtime_stats(
    conn: sqlite3.Connection, run_id: str, payload: dict[str, Any], n_results: int
) -> None:
    """Per-run backend/runtime ledger so the status report can show CPU/GPU split."""
    runtime = (payload.get("runtime") if isinstance(payload, dict) else {}) or {}
    conn.execute(
        """
        INSERT OR REPLACE INTO runtime_stats(
            run_id, requested_backend, effective_backend, signal_backend, simulation_backend,
            gpu_available, fallback_reason, accelerated_runs, elapsed_ms, timeframe, n_results, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id,
            str(
                runtime.get("requested_backend")
                or (payload or {}).get("requested_backend")
                or ""
            ),
            str(runtime.get("effective_backend") or ""),
            str(runtime.get("signal_backend") or ""),
            str(runtime.get("simulation_backend") or ""),
            1 if runtime.get("gpu_available") else 0,
            str(
                runtime.get("fallback_reason")
                or runtime.get("simulation_fallback_reason")
                or ""
            ),
            _as_int(runtime.get("accelerated_runs")),
            _as_float(runtime.get("elapsed_ms")),
            str((payload or {}).get("timeframe") or ""),
            _as_int(n_results),
            str((payload or {}).get("created_at") or utc_now()),
        ),
    )


def _reasons_text(row: dict[str, Any]) -> str:
    reasons = row.get("reasons")
    if isinstance(reasons, list):
        return "|".join(str(r) for r in reasons)
    return str(reasons or "")


def _validation_reasons_text(row: dict[str, Any]) -> str:
    reasons = row.get("validation_reasons")
    if isinstance(reasons, list):
        return "|".join(str(r) for r in reasons)
    return str(reasons or "")


def _metrics_from_csv_row(row: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "n_trades",
        "win_rate",
        "avg_net_pct",
        "total_net_pct",
        "profit_factor",
        "profit_factor_state",
        "max_drawdown_pct",
        "test_avg_net_pct",
        "best_trade_share",
    ]
    metrics = {k: row.get(k) for k in keys if k in row}
    raw_state = row.get("profit_factor_state")
    if isinstance(raw_state, str) and raw_state:
        try:
            metrics["profit_factor_state"] = json.loads(raw_state)
        except json.JSONDecodeError:
            metrics["profit_factor_state"] = {}
    return metrics


def _experiment_from_run_name(name: str) -> str:
    parts = name.split("_", 2)
    return parts[2] if len(parts) == 3 else name
