"""Fail-closed, read-only event-spec reachability reporting."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any


_OUTBOX_COLUMNS = {
    "materialization_id", "task_id", "task_fencing_token", "spec_path",
    "spec_digest", "spec_json", "priority", "state", "queue_job_id",
    "created_at", "updated_at",
}
_QUEUE_COLUMNS = {
    "job_id", "spec_path", "status", "materialization_id", "materialization_digest",
}
_MATERIALIZATION_COLUMNS = {
    "materialization_id", "job_id", "spec_path", "spec_digest",
}


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _spec_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identity(info: os.stat_result) -> dict[str, int]:
    return {
        "device": int(info.st_dev), "inode": int(info.st_ino),
        "size": int(info.st_size), "mtime_ns": int(info.st_mtime_ns),
    }


def _is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    attrs = int(getattr(info, "st_file_attributes", 0))
    return stat.S_ISLNK(info.st_mode) or bool(attrs & 0x400)


def _safe_database_path(private_root: Path, name: str) -> Path:
    root = Path(os.path.abspath(private_root))
    if not root.is_absolute() or not root.is_dir() or _is_link_or_reparse(root):
        raise ValueError("private root is unsafe")
    state = root / "state"
    if not state.is_dir() or _is_link_or_reparse(state):
        raise ValueError("private state directory is unsafe")
    path = state / name
    if not os.path.lexists(path):
        raise FileNotFoundError(path)
    if _is_link_or_reparse(path):
        raise ValueError("reference database is a link or reparse point")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("reference database is not a regular file")
    if os.path.normcase(str(path.resolve(strict=True))) != os.path.normcase(str(path)):
        raise ValueError("reference database canonical path changed")
    return path


def _blocked_source(path: Path, reason: str, detail: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "complete": False, "reason": reason}
    if detail:
        result["detail"] = detail
    return result


def _read_database(
    private_root: Path,
    name: str,
    required: dict[str, set[str]],
) -> dict[str, Any]:
    display_path = Path(private_root) / "state" / name
    try:
        path = _safe_database_path(private_root, name)
    except FileNotFoundError:
        return _blocked_source(display_path, "missing")
    except (OSError, ValueError) as exc:
        return _blocked_source(display_path, "unsafe_path", str(exc))
    try:
        before = path.stat(follow_symlinks=False)
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        try:
            integrity = str(conn.execute("PRAGMA quick_check").fetchone()[0])
            if integrity != "ok":
                return _blocked_source(path, "corrupt", integrity)
            version_before = int(conn.execute("PRAGMA data_version").fetchone()[0])
            schema_rows = conn.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            schema = {str(row["name"]): str(row["sql"] or "") for row in schema_rows}
            missing_tables = sorted(set(required) - set(schema))
            missing_columns: dict[str, list[str]] = {}
            for table, columns in required.items():
                if table not in schema:
                    continue
                actual = {
                    str(row["name"])
                    for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                }
                missing = sorted(columns - actual)
                if missing:
                    missing_columns[table] = missing
            if missing_tables or missing_columns:
                result = _blocked_source(path, "unexpected_schema")
                result["missing_tables"] = missing_tables
                result["missing_columns"] = missing_columns
                return result
            tables: dict[str, list[dict[str, Any]]] = {}
            for table in required:
                rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
                tables[table] = [{key: row[key] for key in row.keys()} for row in rows]
            version_after = int(conn.execute("PRAGMA data_version").fetchone()[0])
        finally:
            conn.close()
        after = path.stat(follow_symlinks=False)
        changed = version_before != version_after or _identity(before) != _identity(after)
        return {
            "path": str(path), "complete": not changed,
            "reason": "changed_during_snapshot" if changed else "complete",
            "file_identity": _identity(after), "schema_digest": _digest(schema),
            "data_version_before": version_before, "data_version_after": version_after,
            "tables": tables, "row_set_digest": _digest(tables),
        }
    except (OSError, sqlite3.Error, TypeError, ValueError, IndexError) as exc:
        return _blocked_source(path, "unreadable_or_corrupt", str(exc))


def _normalized_path(value: Any) -> str:
    raw = str(value or "")
    if not raw or "\x00" in raw:
        raise ValueError("empty or invalid spec path")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("spec path is not absolute")
    return os.path.normcase(str(path.resolve(strict=False)))


def _cross_validate(task_db: dict[str, Any], queue_db: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    outbox = task_db["tables"]["materialization_outbox"]
    queue = queue_db["tables"]["queue"]
    bindings = queue_db["tables"]["queue_materializations"]
    queue_by_job = {int(row["job_id"]): row for row in queue}
    binding_by_mid = {str(row["materialization_id"]): row for row in bindings}
    if len(queue_by_job) != len(queue):
        errors.append("duplicate queue job id")
    if len(binding_by_mid) != len(bindings):
        errors.append("duplicate materialization binding")
    path_digests: dict[str, set[str]] = {}

    def record(row: dict[str, Any], digest_key: str) -> tuple[str, str]:
        path = _normalized_path(row.get("spec_path"))
        digest = str(row.get(digest_key) or "")
        if not digest.startswith("sha256:"):
            raise ValueError("missing or invalid spec digest")
        path_digests.setdefault(path, set()).add(digest)
        return path, digest

    try:
        for row in outbox:
            path, digest = record(row, "spec_digest")
            if _spec_digest(str(row["spec_json"])) != digest:
                errors.append(f"outbox digest mismatch:{row['materialization_id']}")
            queue_job_id = row.get("queue_job_id")
            if queue_job_id is None:
                continue
            queued = queue_by_job.get(int(queue_job_id))
            binding = binding_by_mid.get(str(row["materialization_id"]))
            if queued is None or binding is None:
                errors.append(f"outbox binding missing:{row['materialization_id']}")
                continue
            qpath, qdigest = record(queued, "materialization_digest")
            bpath, bdigest = record(binding, "spec_digest")
            if (
                str(queued.get("materialization_id") or "") != str(row["materialization_id"])
                or int(binding["job_id"]) != int(queue_job_id)
                or path != qpath or path != bpath or digest != qdigest or digest != bdigest
            ):
                errors.append(f"outbox cross-table mismatch:{row['materialization_id']}")
        for row in queue:
            mid = str(row.get("materialization_id") or "")
            if not mid:
                continue
            path, digest = record(row, "materialization_digest")
            binding = binding_by_mid.get(mid)
            if binding is None:
                errors.append(f"queue binding missing:{mid}")
                continue
            bpath, bdigest = record(binding, "spec_digest")
            if int(binding["job_id"]) != int(row["job_id"]) or path != bpath or digest != bdigest:
                errors.append(f"queue cross-table mismatch:{mid}")
        for row in bindings:
            mid = str(row["materialization_id"])
            path, digest = record(row, "spec_digest")
            queued = queue_by_job.get(int(row["job_id"]))
            if queued is None:
                errors.append(f"orphan materialization binding:{mid}")
                continue
            qpath, qdigest = record(queued, "materialization_digest")
            if str(queued.get("materialization_id") or "") != mid or path != qpath or digest != qdigest:
                errors.append(f"materialization cross-table mismatch:{mid}")
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"invalid reference row:{exc}")
    for path, digests in path_digests.items():
        if len(digests) > 1:
            errors.append(f"conflicting digests for path:{path}")
    return sorted(set(errors))


def event_spec_reachability_snapshot(private_root: Path) -> dict[str, Any]:
    root = Path(private_root)
    task_db = _read_database(
        root, "farm_tasks.sqlite", {"materialization_outbox": _OUTBOX_COLUMNS}
    )
    queue_db = _read_database(
        root, "strategy_lab.sqlite",
        {"queue": _QUEUE_COLUMNS, "queue_materializations": _MATERIALIZATION_COLUMNS},
    )
    sources_complete = bool(task_db.get("complete") and queue_db.get("complete"))
    integrity_errors = _cross_validate(task_db, queue_db) if sources_complete else []
    complete = sources_complete and not integrity_errors
    paths: set[str] = set()
    digests: set[str] = set()
    if complete:
        rows = [
            *task_db["tables"]["materialization_outbox"],
            *queue_db["tables"]["queue"],
            *queue_db["tables"]["queue_materializations"],
        ]
        for row in rows:
            if row.get("spec_path"):
                paths.add(_normalized_path(row["spec_path"]))
            for key in ("spec_digest", "materialization_digest"):
                if row.get(key):
                    digests.add(str(row[key]))
    payload = {
        "schema": "StorageReachabilitySnapshot.v2", "class": "event_specs",
        "complete": complete, "status": "report_only" if complete else "incomplete_blocked",
        "mutation_authority": False,
        "new_reference_race": "uncoordinated_producers_block_apply",
        "sources": [task_db, queue_db], "integrity_errors": integrity_errors,
        "protected_paths": sorted(paths), "protected_digests": sorted(digests),
    }
    return {**payload, "snapshot_digest": _digest(payload)}
