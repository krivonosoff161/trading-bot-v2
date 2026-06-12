# -*- coding: utf-8 -*-
"""SQLite state store for Strategy Research Lab.

The DB stores run indexes and queue state. Raw result artifacts remain in the
private research workspace.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def default_db_path(private_root: Path) -> Path:
    return private_root / "state" / "strategy_lab.sqlite"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
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
            last_error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_queue_status_priority
            ON queue(status, priority, created_at);
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def import_completed_runs(private_root: Path, db_path: Path | None = None) -> dict[str, int]:
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


def import_run_dir(conn: sqlite3.Connection, private_root: Path, run_dir: Path) -> int:
    run_dir = run_dir.resolve()
    run_dir.relative_to(private_root.resolve())
    payload = _load_json(run_dir / "metrics.json")
    rows = _extract_results(payload)
    if not rows:
        rows = _load_candidate_rows(run_dir / "candidates.csv")
    counts = _decision_counts(rows)
    run_id = run_dir.name
    experiment_id = str(payload.get("experiment_id") or _experiment_from_run_name(run_id))
    created_at = str(payload.get("created_at") or "")
    artifact_label = _artifact_label(private_root, run_dir)
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
                metrics_json, params_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                str(row.get("run_id") or row.get("candidate_id") or ""),
                str(row.get("symbol") or ""),
                str(row.get("family") or ""),
                str(row.get("decision") or "UNKNOWN"),
                _reasons_text(row),
                json.dumps(row.get("metrics") or _metrics_from_csv_row(row), sort_keys=True),
                json.dumps(row.get("params") or {}, sort_keys=True),
            ),
        )
    return len(rows)


def enqueue_experiment(
    conn: sqlite3.Connection,
    spec_path: Path,
    *,
    priority: int = 100,
    status: str = "queued",
) -> int:
    now = utc_now()
    cur = conn.execute(
        """
        INSERT INTO queue(spec_path, status, priority, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (str(spec_path), status, int(priority), now),
    )
    conn.commit()
    return int(cur.lastrowid)


def ensure_experiment_queued(conn: sqlite3.Connection, spec_path: Path, *, priority: int = 100) -> tuple[int, bool]:
    normalized = str(spec_path)
    row = conn.execute(
        """
        SELECT job_id FROM queue
        WHERE spec_path = ? AND status IN ('queued', 'running')
        ORDER BY job_id ASC
        LIMIT 1
        """,
        (normalized,),
    ).fetchone()
    if row is not None:
        return int(row["job_id"]), False
    return enqueue_experiment(conn, spec_path, priority=priority), True


def claim_next_job(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM queue
        WHERE status = 'queued'
        ORDER BY priority ASC, created_at ASC, job_id ASC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        """
        UPDATE queue
        SET status = 'running', started_at = ?, attempts = attempts + 1
        WHERE job_id = ? AND status = 'queued'
        """,
        (utc_now(), int(row["job_id"])),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM queue WHERE job_id = ?", (int(row["job_id"]),)).fetchone())


def complete_job(conn: sqlite3.Connection, job_id: int, run_dir_label: str) -> None:
    conn.execute(
        """
        UPDATE queue
        SET status = 'completed', finished_at = ?, run_dir_label = ?, last_error = NULL
        WHERE job_id = ?
        """,
        (utc_now(), run_dir_label, int(job_id)),
    )
    conn.commit()


def fail_job(conn: sqlite3.Connection, job_id: int, error: str) -> None:
    conn.execute(
        """
        UPDATE queue
        SET status = 'failed', finished_at = ?, last_error = ?
        WHERE job_id = ?
        """,
        (utc_now(), error[:1000], int(job_id)),
    )
    conn.commit()


def dashboard_snapshot(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"exists": False, "runs": [], "queue": [], "totals": {}}
    conn = connect(db_path)
    init_db(conn)
    runs = [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 20")]
    candidates = [
        {
            "run_id": str(r["run_id"]),
            "candidate_id": str(r["candidate_id"]),
            "symbol": str(r["symbol"]),
            "family": str(r["family"]),
            "decision": str(r["decision"]),
            "reasons": str(r["reasons"]),
        }
        for r in conn.execute(
            """
            SELECT run_id, candidate_id, symbol, family, decision, reasons
            FROM candidates
            ORDER BY
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
    queue = [_queue_row_for_snapshot(dict(r)) for r in conn.execute("SELECT * FROM queue ORDER BY job_id DESC LIMIT 20")]
    totals = dict(conn.execute("SELECT COUNT(*) AS run_count, COALESCE(SUM(candidate_count), 0) AS candidate_count FROM runs").fetchone())
    queue_counts = {
        str(r["status"]): int(r["n"])
        for r in conn.execute("SELECT status, COUNT(*) AS n FROM queue GROUP BY status")
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


def _reasons_text(row: dict[str, Any]) -> str:
    reasons = row.get("reasons")
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
        "max_drawdown_pct",
        "test_avg_net_pct",
        "best_trade_share",
    ]
    return {k: row.get(k) for k in keys if k in row}


def _experiment_from_run_name(name: str) -> str:
    parts = name.split("_", 2)
    return parts[2] if len(parts) == 3 else name
