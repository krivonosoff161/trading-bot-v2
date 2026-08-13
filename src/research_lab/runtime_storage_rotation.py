"""Owner-activated, writer-coordinated rotation for private runtime streams.

The legacy storage policy only reports growth.  This module adds an exact-root,
off-by-default capability.  Writers seal active append-only files with an atomic
rename while holding one OS lock; sealed files are removed only after the
existing content-addressed archive catalog has copied and restore-verified them.

No environment discovery, process control, database mutation, network access or
execution authority lives here.  A missing/invalid capability keeps legacy
append behaviour, while a present but invalid capability fails closed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
import uuid
from typing import Any, Callable, Iterable, Mapping, Sequence, TextIO

from src.research_lab.archive_catalog import ArchiveCatalog, ArchiveCatalogError
from src.research_lab.private_archive_capability import (
    RETENTION_RECLAMATION_ROLE,
    load_private_archive_capability,
)
from src.research_lab.storage_capability import (
    canonical_json,
    content_digest,
    filesystem_identity,
    is_link_or_reparse,
)
from src.research_lab.storage_os_lock import StorageLockConflict, storage_root_lock


_WINDOWS_REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


def _replace_with_bounded_retry(source: Path, target: Path) -> None:
    """Preserve atomic replacement across bounded Windows sharing transients."""
    for attempt in range(len(_WINDOWS_REPLACE_RETRY_DELAYS) + 1):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            transient = isinstance(exc, PermissionError) or getattr(
                exc, "winerror", None
            ) in {5, 32, 33}
            if not transient or attempt == len(_WINDOWS_REPLACE_RETRY_DELAYS):
                raise
            time.sleep(_WINDOWS_REPLACE_RETRY_DELAYS[attempt])


def _snapshot_lock_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".lock")


def _ensure_snapshot_lock_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    try:
        os.write(descriptor, b"0")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


SCHEMA = "RuntimeStorageCapability.v1"
AUTHORITY_SCHEMA = "RuntimeStorageOwnerAuthority.v1"
POLICY_ID = "runtime_storage_rotation.v1"
CONTROL_DIR = ".runtime-storage-v1"
_AUTHORITY_ID = re.compile(r"owner_[a-f0-9]{32}")
_SHA = re.compile(r"[a-f0-9]{40}")
_STREAM = re.compile(r"[a-z][a-z0-9_.]{2,95}")


class RuntimeStorageError(RuntimeError):
    """The runtime-storage capability or a coordinated mutation is unsafe."""


def _snapshot_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_bounded_rebuildable_snapshot_locked(
    path: Path,
    payload: Mapping[str, Any],
    *,
    max_bytes: int,
) -> dict[str, Any]:
    target = Path(path)
    budget = int(max_bytes)
    if budget <= 0:
        raise RuntimeStorageError("rebuildable snapshot budget must be positive")
    encoded = _snapshot_bytes(payload)
    size = len(encoded)
    if size > budget:
        raise RuntimeStorageError("rebuildable snapshot exceeds its bounded budget")
    digest = hashlib.sha256(encoded).hexdigest()
    digest_path = target.with_suffix(target.suffix + ".sha256")
    if target.exists() and is_link_or_reparse(target):
        raise RuntimeStorageError("rebuildable snapshot target is unsafe")
    if digest_path.exists() and is_link_or_reparse(digest_path):
        raise RuntimeStorageError("rebuildable snapshot digest target is unsafe")
    try:
        stat = target.stat()
        target_bytes = target.read_bytes()
        target_digest = hashlib.sha256(target_bytes).hexdigest()
        recorded_digest, recorded_size, recorded_mtime = digest_path.read_text(
            encoding="ascii"
        ).strip().split()
        unchanged = bool(
            target_bytes == encoded
            and stat.st_size == size
            and target_digest == digest
            and recorded_digest == digest
            and int(recorded_size) == size
            and int(recorded_mtime) == stat.st_mtime_ns
        )
    except (OSError, ValueError):
        unchanged = False
    if unchanged:
        return {"changed": False, "bytes": size, "sha256": digest}

    temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    sidecar_temporary = digest_path.with_name(
        f".{digest_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        if not target.exists() or target.read_bytes() != encoded:
            with temporary.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            _replace_with_bounded_retry(temporary, target)
        committed = target.read_bytes()
        committed_digest = hashlib.sha256(committed).hexdigest()
        if committed_digest != digest or committed != encoded:
            raise RuntimeStorageError("rebuildable snapshot target digest mismatch")
        expected_sidecar = f"{committed_digest} {len(committed)} {target.stat().st_mtime_ns}\n"
        with sidecar_temporary.open("x", encoding="ascii", newline="\n") as stream:
            stream.write(expected_sidecar)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_bounded_retry(sidecar_temporary, digest_path)
        if digest_path.read_text(encoding="ascii") != expected_sidecar:
            raise RuntimeStorageError("rebuildable snapshot sidecar mismatch")
    finally:
        temporary.unlink(missing_ok=True)
        sidecar_temporary.unlink(missing_ok=True)
    return {"changed": True, "bytes": size, "sha256": digest}


def write_bounded_rebuildable_snapshot(
    path: Path,
    payload: Mapping[str, Any],
    *,
    max_bytes: int,
) -> dict[str, Any]:
    """Publish one byte-bounded snapshot and digest under a target OS lock.

    The target bytes are re-hashed while the lock is held.  Cooperative writers
    therefore cannot accept a stale size/mtime sidecar or interleave payload and
    sidecar replacement.  Interrupted temporary files are removed before return.
    """

    target = Path(path)
    lock_path = _snapshot_lock_path(target)
    _ensure_snapshot_lock_file(lock_path)
    try:
        with storage_root_lock(lock_path, wait_seconds=5.0):
            return _write_bounded_rebuildable_snapshot_locked(
                target, payload, max_bytes=max_bytes
            )
    except StorageLockConflict as exc:
        raise RuntimeStorageError("rebuildable snapshot lock is unavailable") from exc


def update_bounded_rebuildable_snapshot(
    path: Path,
    update: Callable[[Mapping[str, Any] | None], Mapping[str, Any]],
    *,
    max_bytes: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Atomically read, update and publish one rebuildable JSON snapshot."""

    target = Path(path)
    lock_path = _snapshot_lock_path(target)
    _ensure_snapshot_lock_file(lock_path)
    try:
        with storage_root_lock(lock_path, wait_seconds=5.0):
            current: Mapping[str, Any] | None = None
            if target.exists():
                if is_link_or_reparse(target):
                    raise RuntimeStorageError("rebuildable snapshot target is unsafe")
                try:
                    loaded = json.loads(target.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    loaded = None
                if isinstance(loaded, Mapping):
                    current = loaded
            payload = dict(update(current))
            result = _write_bounded_rebuildable_snapshot_locked(
                target, payload, max_bytes=max_bytes
            )
            return payload, result
    except StorageLockConflict as exc:
        raise RuntimeStorageError("rebuildable snapshot lock is unavailable") from exc


@dataclass(frozen=True)
class RuntimeStreamPolicy:
    stream_id: str
    relative_path: str
    kind: str
    contour: str
    payload_schema: str
    sensitivity: str
    max_active_bytes: int
    tail_records: int = 1_000


DEFAULT_STREAMS: tuple[RuntimeStreamPolicy, ...] = (
    RuntimeStreamPolicy("farm.cycle", "logs/farm/cycle_log.jsonl", "farm_journal", "farm_and_runtime", "farm_journal.v1", "private_payload", 4 * 1024 * 1024),
    RuntimeStreamPolicy("farm.transitions", "logs/farm/task_transitions.jsonl", "farm_journal", "farm_and_runtime", "farm_journal.v1", "private_payload", 8 * 1024 * 1024),
    RuntimeStreamPolicy("farm.errors", "logs/farm/errors.jsonl", "farm_journal", "incidents_and_causality", "farm_journal.v1", "private_payload", 4 * 1024 * 1024),
    RuntimeStreamPolicy("farm.stdout", "logs/farm_full_cycle_loop.log", "runtime_stdout", "farm_and_runtime", "RuntimeStdoutLine.v1", "private_payload", 16 * 1024 * 1024),
    RuntimeStreamPolicy("telegram.stdout", "logs/paper_telegram_sender_loop.log", "runtime_stdout", "telegram_and_delivery", "RuntimeStdoutLine.v1", "private_payload", 8 * 1024 * 1024),
    RuntimeStreamPolicy("lineage.scanner", "state/lineage/scanner_events.jsonl", "lineage", "data_and_lineage", "ScannerEvent.v1", "private_payload", 8 * 1024 * 1024),
    RuntimeStreamPolicy("lineage.links", "state/lineage/cycle_links.jsonl", "lineage", "data_and_lineage", "LineageLink.v1", "private_payload", 16 * 1024 * 1024),
    RuntimeStreamPolicy("llm.invocations", "state/llm_advice/invocations.jsonl", "llm_invocation", "models_and_llm", "LLMInvocation.v1", "private_metadata", 8 * 1024 * 1024),
    RuntimeStreamPolicy("derived.calculator_sweeps", "state/derived/calculator_sweep_proposals.jsonl", "derived_artifact", "research_and_strategies", "DerivedRuntimeArtifact.v1", "private_payload", 8 * 1024 * 1024),
    RuntimeStreamPolicy("derived.paper_signals", "state/derived/paper_signals.jsonl", "derived_artifact", "paper_lifecycle", "DerivedRuntimeArtifact.v1", "private_payload", 8 * 1024 * 1024),
    RuntimeStreamPolicy("derived.paper_training", "state/derived/paper_signal_training.jsonl", "derived_artifact", "paper_lifecycle", "DerivedRuntimeArtifact.v1", "private_payload", 16 * 1024 * 1024),
    RuntimeStreamPolicy("derived.product_training", "state/derived/product_signal_training.jsonl", "derived_artifact", "paper_lifecycle", "DerivedRuntimeArtifact.v1", "private_payload", 16 * 1024 * 1024),
    RuntimeStreamPolicy("derived.paper_instructions", "state/derived/main_paper_instructions.jsonl", "derived_artifact", "paper_lifecycle", "DerivedRuntimeArtifact.v1", "private_payload", 8 * 1024 * 1024),
    RuntimeStreamPolicy("derived.paper_consumed", "state/derived/main_paper_consumed.jsonl", "derived_artifact", "paper_lifecycle", "DerivedRuntimeArtifact.v1", "private_payload", 8 * 1024 * 1024),
    RuntimeStreamPolicy("derived.paper_queue", "state/derived/main_paper_runtime_queue.jsonl", "derived_artifact", "paper_lifecycle", "DerivedRuntimeArtifact.v1", "private_payload", 8 * 1024 * 1024),
    RuntimeStreamPolicy("derived.paper_observations", "state/derived/main_paper_runtime_observation.jsonl", "derived_artifact", "paper_lifecycle", "DerivedRuntimeArtifact.v1", "private_payload", 16 * 1024 * 1024),
    RuntimeStreamPolicy("derived.paper_trades", "state/derived/main_paper_trades.jsonl", "derived_artifact", "paper_lifecycle", "DerivedRuntimeArtifact.v1", "private_payload", 16 * 1024 * 1024),
    RuntimeStreamPolicy("derived.telegram_preview", "state/derived/paper_telegram_preview.jsonl", "derived_artifact", "telegram_and_delivery", "DerivedRuntimeArtifact.v1", "private_payload", 8 * 1024 * 1024),
    RuntimeStreamPolicy("derived.telegram_delivery", "state/derived/paper_telegram_delivery.jsonl", "derived_artifact", "telegram_and_delivery", "DerivedRuntimeArtifact.v1", "private_payload", 8 * 1024 * 1024),
)


@dataclass(frozen=True)
class RuntimeStorageAuthority:
    schema: str
    project_id: str
    action: str
    source_root: str
    archive_root: str
    source_revision: str
    issued_at: float
    expires_at: float
    turn_id: str
    authority_id: str
    source_budget_bytes: int
    archive_budget_bytes: int
    minimum_source_free_bytes: int
    minimum_archive_free_bytes: int
    synthetic: bool = False

    @property
    def digest(self) -> str:
        return content_digest(asdict(self))


@dataclass(frozen=True)
class RuntimeStorageCapability:
    schema: str
    policy_id: str
    project_id: str
    source_root: str
    archive_root: str
    source_revision: str
    source_filesystem_identity: dict[str, int]
    archive_capability_digest: str
    authority_id: str
    authority_digest: str
    source_budget_bytes: int
    archive_budget_bytes: int
    minimum_source_free_bytes: int
    minimum_archive_free_bytes: int
    synthetic: bool
    streams: tuple[RuntimeStreamPolicy, ...]
    capability_digest: str


def _absolute_directory(path: Path, *, label: str) -> Path:
    lexical = Path(os.path.abspath(path))
    if not lexical.is_absolute() or not lexical.is_dir() or is_link_or_reparse(lexical):
        raise RuntimeStorageError(f"{label} must be an existing safe absolute directory")
    resolved = lexical.resolve(strict=True)
    if os.path.normcase(str(lexical)) != os.path.normcase(str(resolved)):
        raise RuntimeStorageError(f"{label} changed through path resolution")
    return resolved


def _control(root: Path) -> Path:
    return root / CONTROL_DIR


def _capability_path(root: Path) -> Path:
    return _control(root) / "capability.json"


def _lock_path(root: Path) -> Path:
    return _control(root) / "rotation.lock"


def _status_path(root: Path) -> Path:
    return _control(root) / "status.json"


def _pending_root(root: Path) -> Path:
    return _control(root) / "pending"


def _tail_path(root: Path, policy: RuntimeStreamPolicy) -> Path:
    return _control(root) / "tails" / f"{policy.stream_id}.jsonl"


def _index_path(root: Path) -> Path:
    return _control(root) / "semantic_index.sqlite3"


def _atomic_replace_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    payload = (canonical_json(dict(value)) + "\n").encode("utf-8")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    _replace_with_bounded_retry(temporary, path)


def _policy_payload(policy: RuntimeStreamPolicy) -> dict[str, Any]:
    return asdict(policy)


def activate_runtime_storage(
    source_root: Path,
    *,
    archive_root: Path,
    authority: RuntimeStorageAuthority,
    now: float,
    streams: Sequence[RuntimeStreamPolicy] = DEFAULT_STREAMS,
) -> RuntimeStorageCapability:
    """Activate rotation after a separately-created archive capability is proven."""
    source = _absolute_directory(source_root, label="source root")
    archive = _absolute_directory(archive_root, label="archive root")
    if authority.schema != AUTHORITY_SCHEMA or authority.project_id != "trading-bot-v2":
        raise RuntimeStorageError("runtime storage authority identity mismatch")
    if authority.action != "activate_runtime_storage_rotation":
        raise RuntimeStorageError("runtime storage authority action mismatch")
    if not authority.issued_at <= now < authority.expires_at or not authority.turn_id:
        raise RuntimeStorageError("runtime storage authority is stale")
    if not _AUTHORITY_ID.fullmatch(authority.authority_id):
        raise RuntimeStorageError("runtime storage authority id is invalid")
    if not _SHA.fullmatch(authority.source_revision):
        raise RuntimeStorageError("runtime storage revision is invalid")
    if os.path.normcase(authority.source_root) != os.path.normcase(str(source)) or os.path.normcase(authority.archive_root) != os.path.normcase(str(archive)):
        raise RuntimeStorageError("runtime storage authority root mismatch")
    if (
        authority.source_budget_bytes <= 0
        or authority.archive_budget_bytes <= 0
        or authority.minimum_source_free_bytes < 0
        or authority.minimum_archive_free_bytes < 0
    ):
        raise RuntimeStorageError("runtime storage budget is invalid")
    archive_capability = load_private_archive_capability(archive)
    required_kinds = {item.kind for item in streams}
    if os.path.normcase(archive_capability.source_root) != os.path.normcase(str(source)) or not required_kinds.issubset(set(archive_capability.allowed_kinds)):
        raise RuntimeStorageError("archive capability does not cover runtime streams")
    if not authority.synthetic and archive_capability.synthetic:
        raise RuntimeStorageError("synthetic archive capability cannot serve production")
    if archive_capability.storage_role != RETENTION_RECLAMATION_ROLE:
        raise RuntimeStorageError(
            "runtime storage requires a retention-reclamation archive capability"
        )
    if not streams or len({item.stream_id for item in streams}) != len(streams):
        raise RuntimeStorageError("runtime stream registry is empty or duplicated")
    for item in streams:
        if not _STREAM.fullmatch(item.stream_id) or item.max_active_bytes <= 0 or item.tail_records <= 0:
            raise RuntimeStorageError("runtime stream policy is invalid")
        relative = Path(item.relative_path)
        if relative.is_absolute() or ".." in relative.parts or "\\" in item.relative_path:
            raise RuntimeStorageError("runtime stream path is unsafe")
    control = _control(source)
    if control.exists():
        existing = load_runtime_storage_capability(source)
        if (
            existing.archive_root == str(archive)
            and existing.source_revision == authority.source_revision
            and existing.authority_id == authority.authority_id
            and existing.authority_digest == authority.digest
            and existing.source_budget_bytes == authority.source_budget_bytes
            and existing.archive_budget_bytes == authority.archive_budget_bytes
            and existing.minimum_source_free_bytes == authority.minimum_source_free_bytes
            and existing.minimum_archive_free_bytes == authority.minimum_archive_free_bytes
            and existing.synthetic == authority.synthetic
            and existing.streams == tuple(streams)
        ):
            return existing
        raise RuntimeStorageError("runtime storage capability already differs")
    control.mkdir()
    (_pending_root(source)).mkdir()
    (control / "tails").mkdir()
    _lock_path(source).write_bytes(b"0")
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "policy_id": POLICY_ID,
        "project_id": "trading-bot-v2",
        "source_root": str(source),
        "archive_root": str(archive),
        "source_revision": authority.source_revision,
        "source_filesystem_identity": filesystem_identity(source),
        "archive_capability_digest": archive_capability.capability_digest,
        "authority_id": authority.authority_id,
        "authority_digest": authority.digest,
        "source_budget_bytes": authority.source_budget_bytes,
        "archive_budget_bytes": authority.archive_budget_bytes,
        "minimum_source_free_bytes": authority.minimum_source_free_bytes,
        "minimum_archive_free_bytes": authority.minimum_archive_free_bytes,
        "synthetic": authority.synthetic,
        "streams": [_policy_payload(item) for item in streams],
    }
    payload["capability_digest"] = content_digest(payload)
    _atomic_replace_json(_capability_path(source), payload)
    _initialize_index(source)
    capability = load_runtime_storage_capability(source)
    rebuild_runtime_semantic_index(capability)
    seal_oversized_active_streams(capability)
    maintenance = archive_pending_segments(capability)
    if maintenance["state"] != "ready":
        raise RuntimeStorageError("initial runtime storage archival failed")
    _write_status(capability, state="ready", problems=[])
    return capability


def load_runtime_storage_capability(source_root: Path) -> RuntimeStorageCapability:
    source = _absolute_directory(source_root, label="source root")
    path = _capability_path(source)
    if not path.exists():
        raise RuntimeStorageError("runtime storage capability is absent")
    if not path.is_file() or is_link_or_reparse(path):
        raise RuntimeStorageError("runtime storage capability path is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeStorageError("runtime storage capability is unreadable") from exc
    expected_keys = {
        "schema",
        "policy_id",
        "project_id",
        "source_root",
        "archive_root",
        "source_revision",
        "source_filesystem_identity",
        "archive_capability_digest",
        "authority_id",
        "authority_digest",
        "source_budget_bytes",
        "archive_budget_bytes",
        "minimum_source_free_bytes",
        "minimum_archive_free_bytes",
        "synthetic",
        "streams",
        "capability_digest",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RuntimeStorageError("runtime storage capability shape is invalid")
    digest = str(value.pop("capability_digest", ""))
    if digest != content_digest(value):
        raise RuntimeStorageError("runtime storage capability digest mismatch")
    if value.get("schema") != SCHEMA or value.get("policy_id") != POLICY_ID or value.get("project_id") != "trading-bot-v2" or value.get("source_root") != str(source):
        raise RuntimeStorageError("runtime storage capability binding mismatch")
    if value.get("source_filesystem_identity") != filesystem_identity(source):
        raise RuntimeStorageError("runtime source filesystem identity drift")
    try:
        streams = tuple(RuntimeStreamPolicy(**item) for item in value.pop("streams"))
    except (TypeError, ValueError, KeyError) as exc:
        raise RuntimeStorageError("runtime stream registry is invalid") from exc
    archive = load_private_archive_capability(Path(str(value.get("archive_root"))))
    if archive.capability_digest != value.get("archive_capability_digest") or os.path.normcase(archive.source_root) != os.path.normcase(str(source)):
        raise RuntimeStorageError("runtime archive binding drift")
    if archive.storage_role != RETENTION_RECLAMATION_ROLE:
        raise RuntimeStorageError(
            "runtime archive role is not retention-reclamation"
        )
    capability = RuntimeStorageCapability(**value, streams=streams, capability_digest=digest)
    if (
        not _SHA.fullmatch(capability.source_revision)
        or not _AUTHORITY_ID.fullmatch(capability.authority_id)
        or capability.source_budget_bytes <= 0
        or capability.archive_budget_bytes <= 0
        or capability.minimum_source_free_bytes < 0
        or capability.minimum_archive_free_bytes < 0
        or not streams
        or len({item.stream_id for item in streams}) != len(streams)
    ):
        raise RuntimeStorageError("runtime storage capability values are invalid")
    for item in streams:
        relative = Path(item.relative_path)
        if (
            not _STREAM.fullmatch(item.stream_id)
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in item.relative_path
            or item.max_active_bytes <= 0
            or item.tail_records <= 0
            or item.kind not in archive.allowed_kinds
            or item.sensitivity not in archive.allowed_sensitivity
        ):
            raise RuntimeStorageError("runtime stream policy is invalid")
    return capability


def maybe_runtime_storage_capability(path: Path) -> RuntimeStorageCapability | None:
    """Resolve only an ancestor capability; never consult environment/default paths."""
    lexical = Path(os.path.abspath(path))
    for parent in (lexical.parent, *lexical.parents):
        if (parent / CONTROL_DIR).exists():
            capability = load_runtime_storage_capability(parent)
            try:
                lexical.relative_to(Path(capability.source_root))
            except ValueError as exc:
                raise RuntimeStorageError("runtime stream escapes bound source root") from exc
            return capability
    return None


def _policy_for(capability: RuntimeStorageCapability, path: Path) -> RuntimeStreamPolicy | None:
    source = Path(capability.source_root)
    lexical = Path(os.path.abspath(path))
    try:
        relative = lexical.relative_to(source).as_posix()
    except ValueError as exc:
        raise RuntimeStorageError("runtime stream is outside source root") from exc
    for policy in capability.streams:
        if policy.relative_path == relative:
            return policy
    return None


def _initialize_index(root: Path) -> None:
    connection = sqlite3.connect(_index_path(root))
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS semantic_keys(
              stream_id TEXT NOT NULL,
              key_type TEXT NOT NULL,
              key_value TEXT NOT NULL,
              status TEXT NOT NULL,
              role_id TEXT NOT NULL,
              provider TEXT NOT NULL,
              category TEXT NOT NULL,
              observed_at TEXT NOT NULL,
              event_digest TEXT NOT NULL,
              PRIMARY KEY(stream_id,key_type,key_value,event_digest)
            );
            CREATE INDEX IF NOT EXISTS idx_semantic_lookup
              ON semantic_keys(stream_id,key_type,key_value);
            CREATE INDEX IF NOT EXISTS idx_semantic_recent
              ON semantic_keys(stream_id,role_id,provider,observed_at);
            CREATE TABLE IF NOT EXISTS llm_latest(
              logical_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              role_id TEXT NOT NULL,
              total_tokens INTEGER NOT NULL,
              cost_rub REAL NOT NULL,
              event_phase TEXT NOT NULL
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def _semantic_values(policy: RuntimeStreamPolicy, row: Mapping[str, Any]) -> list[tuple[str, str, str, str, str, str, str, str]]:
    observed = str(row.get("completed_at") or row.get("linked_at") or row.get("timestamp") or row.get("created_at") or "")
    row_digest = hashlib.sha256(canonical_json(dict(row)).encode("utf-8")).hexdigest()
    values: list[tuple[str, str, str, str, str, str, str, str]] = []
    for key in ("invocation_id", "lineage_link_id", "scanner_event_id"):
        value = str(row.get(key) or "")
        if value:
            values.append((key, value, str(row.get("status") or ""), str(row.get("role_id") or ""), str(row.get("provider") or ""), str(row.get("source") or ""), observed, row_digest))
    return values


def _index_rows(root: Path, policy: RuntimeStreamPolicy, rows: Iterable[Mapping[str, Any]]) -> None:
    values = [entry for row in rows for entry in _semantic_values(policy, row)]
    if not values:
        return
    connection = sqlite3.connect(_index_path(root))
    try:
        with connection:
            connection.executemany(
                "INSERT OR IGNORE INTO semantic_keys VALUES(?,?,?,?,?,?,?,?,?)",
                [(policy.stream_id, *entry) for entry in values],
            )
            if policy.stream_id == "llm.invocations":
                for row in rows:
                    logical_id = str(row.get("correlation_id") or row.get("invocation_id") or "")
                    if not logical_id:
                        continue
                    event_phase = str(row.get("event_phase") or "completed")
                    current = connection.execute(
                        "SELECT event_phase FROM llm_latest WHERE logical_id=?",
                        (logical_id,),
                    ).fetchone()
                    if current is not None and str(current[0]) == "completed" and event_phase != "completed":
                        continue
                    connection.execute(
                        """INSERT OR REPLACE INTO llm_latest
                           VALUES(?,?,?,?,?,?)""",
                        (
                            logical_id,
                            str(row.get("status") or "unknown"),
                            str(row.get("role_id") or "unknown"),
                            int(row.get("total_tokens") or 0),
                            float(row.get("cost_rub") or 0.0),
                            event_phase,
                        ),
                    )
    finally:
        connection.close()


def _decode_json_lines(lines: Iterable[bytes]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def rebuild_runtime_semantic_index(capability: RuntimeStorageCapability) -> int:
    root = Path(capability.source_root)
    _initialize_index(root)
    count = 0
    with storage_root_lock(_lock_path(root), wait_seconds=5.0):
        connection = sqlite3.connect(_index_path(root))
        try:
            with connection:
                connection.execute("DELETE FROM semantic_keys")
                connection.execute("DELETE FROM llm_latest")
        finally:
            connection.close()
        for policy in capability.streams:
            paths = sorted((_pending_root(root) / policy.stream_id).glob("*.sealed"))
            active = root / Path(policy.relative_path)
            if active.is_file():
                paths.append(active)
            for path in paths:
                raw_lines = path.read_bytes().splitlines(keepends=True)
                rows = _decode_json_lines(raw_lines)
                _index_rows(root, policy, rows)
                _append_tail(root, policy, raw_lines)
                count += len(rows)
    return count


def seal_oversized_active_streams(capability: RuntimeStorageCapability) -> int:
    """Seal pre-cutover or recovered oversized active files without truncation."""
    root = Path(capability.source_root)
    sealed_count = 0
    with storage_root_lock(_lock_path(root), wait_seconds=5.0):
        for policy in capability.streams:
            active = root / Path(policy.relative_path)
            if not active.exists() or active.stat().st_size < policy.max_active_bytes:
                continue
            if not active.is_file() or is_link_or_reparse(active):
                raise RuntimeStorageError("runtime stream path is unsafe")
            pending_dir = _pending_root(root) / policy.stream_id
            pending_dir.mkdir(parents=True, exist_ok=True)
            _replace_with_bounded_retry(
                active, pending_dir / f"{uuid.uuid4().hex}.sealed"
            )
            sealed_count += 1
    return sealed_count


def semantic_key_exists(source_root: Path, *, stream_id: str, key_type: str, key_value: str, status: str = "") -> bool:
    capability = load_runtime_storage_capability(source_root)
    connection = sqlite3.connect(_index_path(Path(capability.source_root)))
    try:
        query = "SELECT 1 FROM semantic_keys WHERE stream_id=? AND key_type=? AND key_value=?"
        params: list[str] = [stream_id, key_type, key_value]
        if status:
            query += " AND status=?"
            params.append(status)
        return connection.execute(query + " LIMIT 1", params).fetchone() is not None
    finally:
        connection.close()


def recent_semantic_statuses(source_root: Path, *, stream_id: str, role_id: str, provider: str, limit: int) -> list[str]:
    capability = load_runtime_storage_capability(source_root)
    connection = sqlite3.connect(_index_path(Path(capability.source_root)))
    try:
        rows = connection.execute(
            "SELECT status FROM semantic_keys WHERE stream_id=? AND role_id=? AND provider=? ORDER BY rowid DESC LIMIT ?",
            (stream_id, role_id, provider, max(1, int(limit))),
        ).fetchall()
        return [str(row[0]) for row in reversed(rows)]
    finally:
        connection.close()


def semantic_status_count(source_root: Path, *, stream_id: str, key_type: str, key_value: str, statuses: Iterable[str]) -> int:
    wanted = tuple(statuses)
    if not wanted:
        return 0
    capability = load_runtime_storage_capability(source_root)
    placeholders = ",".join("?" for _ in wanted)
    connection = sqlite3.connect(_index_path(Path(capability.source_root)))
    try:
        row = connection.execute(
            f"SELECT COUNT(*) FROM semantic_keys WHERE stream_id=? AND key_type=? AND key_value=? AND status IN ({placeholders})",  # noqa: S608 - placeholders only
            (stream_id, key_type, key_value, *wanted),
        ).fetchone()
        return int(row[0])
    finally:
        connection.close()


def semantic_key_values_for_path(path: Path, *, key_type: str) -> set[str]:
    capability = maybe_runtime_storage_capability(path)
    if capability is None:
        return set()
    policy = _policy_for(capability, path)
    if policy is None:
        return set()
    connection = sqlite3.connect(_index_path(Path(capability.source_root)))
    try:
        rows = connection.execute(
            "SELECT DISTINCT key_value FROM semantic_keys WHERE stream_id=? AND key_type=?",
            (policy.stream_id, key_type),
        ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        connection.close()


def semantic_counts_for_path(path: Path) -> dict[str, Any] | None:
    capability = maybe_runtime_storage_capability(path)
    if capability is None:
        return None
    policy = _policy_for(capability, path)
    if policy is None or policy.stream_id not in {"lineage.scanner", "lineage.links"}:
        return None
    key_type = "scanner_event_id" if policy.stream_id == "lineage.scanner" else "lineage_link_id"
    connection = sqlite3.connect(_index_path(Path(capability.source_root)))
    try:
        total = int(
            connection.execute(
                "SELECT COUNT(DISTINCT key_value) FROM semantic_keys WHERE stream_id=? AND key_type=?",
                (policy.stream_id, key_type),
            ).fetchone()[0]
        )
        by_key = {
            str(value): int(count)
            for value, count in connection.execute(
                "SELECT category,COUNT(DISTINCT key_value) FROM semantic_keys WHERE stream_id=? AND key_type=? AND category<>'' GROUP BY category",
                (policy.stream_id, key_type),
            ).fetchall()
        }
        return {"exists": total > 0, "rows": total, "by_key": by_key}
    finally:
        connection.close()


def llm_invocation_summary(source_root: Path) -> dict[str, Any]:
    capability = load_runtime_storage_capability(source_root)
    connection = sqlite3.connect(_index_path(Path(capability.source_root)))
    try:
        rows = connection.execute(
            "SELECT status,role_id,total_tokens,cost_rub FROM llm_latest"
        ).fetchall()
        invocation_events = int(
            connection.execute(
                "SELECT COUNT(DISTINCT event_digest) FROM semantic_keys WHERE stream_id='llm.invocations'"
            ).fetchone()[0]
        )
    finally:
        connection.close()
    by_status: dict[str, int] = {}
    by_role: dict[str, int] = {}
    total_tokens = 0
    total_cost = 0.0
    for status, role, tokens, cost in rows:
        by_status[str(status)] = by_status.get(str(status), 0) + 1
        by_role[str(role)] = by_role.get(str(role), 0) + 1
        total_tokens += int(tokens)
        total_cost += float(cost)
    return {
        "schema": "LLMInvocationSummary.v1",
        "invocations": len(rows),
        "invocation_events": invocation_events,
        "by_status": dict(sorted(by_status.items())),
        "by_role": dict(sorted(by_role.items())),
        "total_tokens": total_tokens,
        "total_cost_rub": round(total_cost, 4),
        "paper_only": True,
        "execution_allowed": False,
        "secrets_exposed": False,
    }


def _append_tail(root: Path, policy: RuntimeStreamPolicy, lines: Sequence[bytes]) -> None:
    if policy.payload_schema == "RuntimeStdoutLine.v1":
        return
    target = _tail_path(root, policy)
    existing = target.read_bytes().splitlines(keepends=True) if target.exists() else []
    combined = (existing + list(lines))[-policy.tail_records :]
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.writelines(combined)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_bounded_retry(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def append_runtime_lines(path: Path, lines: Sequence[bytes]) -> Path:
    """Append completed lines and atomically seal at the configured byte cap."""
    if not lines:
        return path
    capability = maybe_runtime_storage_capability(path)
    if capability is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as stream:
            stream.writelines(lines)
        return path
    policy = _policy_for(capability, path)
    if policy is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as stream:
            stream.writelines(lines)
        return path
    root = Path(capability.source_root)
    sealed: Path | None = None
    try:
        with storage_root_lock(_lock_path(root), wait_seconds=5.0):
            if path.exists() and (not path.is_file() or is_link_or_reparse(path)):
                raise RuntimeStorageError("runtime stream path is unsafe")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as stream:
                stream.writelines(lines)
                stream.flush()
                os.fsync(stream.fileno())
            rows = _decode_json_lines(lines)
            _index_rows(root, policy, rows)
            _append_tail(root, policy, lines)
            if path.stat().st_size >= policy.max_active_bytes:
                pending_dir = _pending_root(root) / policy.stream_id
                pending_dir.mkdir(parents=True, exist_ok=True)
                sealed = pending_dir / f"{uuid.uuid4().hex}.sealed"
                _replace_with_bounded_retry(path, sealed)
    except StorageLockConflict as exc:
        raise RuntimeStorageError("runtime storage lock is unavailable") from exc
    if sealed is not None:
        result = archive_pending_segments(capability, stream_ids=(policy.stream_id,))
        if result["state"] != "ready":
            raise RuntimeStorageError("runtime storage archival failed closed")
    return path


def append_runtime_jsonl(path: Path, row: Mapping[str, Any]) -> Path:
    payload = (json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    return append_runtime_lines(path, (payload,))


class _RuntimeTee:
    def __init__(self, original: TextIO, path: Path) -> None:
        self._original = original
        self._path = path
        self._pending = ""

    @property
    def encoding(self) -> str:
        return str(getattr(self._original, "encoding", None) or "utf-8")

    def write(self, value: str) -> int:
        written = self._original.write(value)
        self._pending += value
        lines = self._pending.splitlines(keepends=True)
        complete: list[str] = []
        while lines and lines[0].endswith(("\n", "\r")):
            complete.append(lines.pop(0))
        self._pending = "".join(lines)
        if complete:
            append_runtime_lines(
                self._path,
                tuple(line.encode("utf-8", errors="replace") for line in complete),
            )
        return written

    def flush(self) -> None:
        self._original.flush()
        if self._pending:
            append_runtime_lines(
                self._path,
                (self._pending.encode("utf-8", errors="replace") + b"\n",),
            )
            self._pending = ""

    def isatty(self) -> bool:
        return bool(self._original.isatty())

    def fileno(self) -> int:
        return self._original.fileno()


def install_runtime_stdout_tee(private_root: Path, *, stream_id: str) -> None:
    """Install an in-process tee; it owns no thread/process and needs no cleanup."""
    capability = maybe_runtime_storage_capability(Path(private_root) / "sentinel")
    policies = DEFAULT_STREAMS if capability is None else capability.streams
    matches = [item for item in policies if item.stream_id == stream_id]
    if len(matches) != 1 or matches[0].kind != "runtime_stdout":
        raise RuntimeStorageError("runtime stdout stream is absent or ambiguous")
    target = Path(private_root) / Path(matches[0].relative_path)
    sys.stdout = _RuntimeTee(sys.stdout, target)  # type: ignore[assignment]
    sys.stderr = _RuntimeTee(sys.stderr, target)  # type: ignore[assignment]


def read_runtime_tail(path: Path, *, limit: int) -> list[dict[str, Any]]:
    capability = maybe_runtime_storage_capability(path)
    policy = _policy_for(capability, path) if capability is not None else None
    source = _tail_path(Path(capability.source_root), policy) if capability and policy else path
    if not source.exists():
        return []
    rows = _decode_json_lines(source.read_bytes().splitlines())
    return [dict(row) for row in rows[-max(0, int(limit)) :]]


def archive_pending_segments(capability: RuntimeStorageCapability, *, stream_ids: Iterable[str] = ()) -> dict[str, Any]:
    root = Path(capability.source_root)
    selected = set(stream_ids)
    catalog = ArchiveCatalog(Path(capability.archive_root))
    archived = 0
    retained = 0
    problems: list[str] = []
    for policy in capability.streams:
        if selected and policy.stream_id not in selected:
            continue
        pending_dir = _pending_root(root) / policy.stream_id
        # A process crash may leave a claimed segment. A live archiver should
        # finish quickly because segments are capped; only old claims are
        # recovered, so concurrent maintainers never steal each other's work.
        for interrupted in sorted(pending_dir.glob("*.archiving")):
            try:
                if max(0.0, dt.datetime.now().timestamp() - interrupted.stat().st_mtime) < 300.0:
                    continue
                recovered = interrupted.with_suffix(".sealed")
                with storage_root_lock(_lock_path(root), wait_seconds=5.0):
                    if interrupted.exists() and not recovered.exists():
                        _replace_with_bounded_retry(interrupted, recovered)
            except (OSError, StorageLockConflict):
                problems.append(f"claim_recovery_failed:{policy.stream_id}")
        for sealed in sorted(pending_dir.glob("*.sealed")):
            claimed = sealed.with_suffix(".archiving")
            try:
                with storage_root_lock(_lock_path(root), wait_seconds=5.0):
                    if not sealed.exists():
                        continue
                    if claimed.exists():
                        continue
                    _replace_with_bounded_retry(sealed, claimed)
                stat = claimed.stat()
                observed = dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).isoformat()
                manifest = catalog.register_jsonl(
                    claimed,
                    stream_id=policy.stream_id,
                    kind=policy.kind,
                    contour=policy.contour,
                    payload_schema=policy.payload_schema,
                    source_revision=capability.source_revision,
                    sensitivity=policy.sensitivity,
                    first_observed_at=observed,
                    last_observed_at=observed,
                    created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    restore_verified=True,
                )
                if not manifest.restore_verified or not manifest.copy_verified:
                    raise RuntimeStorageError("archive verification was not recorded")
                with storage_root_lock(_lock_path(root), wait_seconds=5.0):
                    if hashlib.sha256(claimed.read_bytes()).hexdigest() != manifest.source_sha256:
                        raise RuntimeStorageError("claimed segment changed before release")
                    claimed.unlink()
                archived += 1
            except (OSError, ArchiveCatalogError, RuntimeStorageError, StorageLockConflict):
                retained += 1
                problems.append(f"archive_failed:{policy.stream_id}")
                try:
                    with storage_root_lock(_lock_path(root), wait_seconds=5.0):
                        if claimed.exists() and not sealed.exists():
                            _replace_with_bounded_retry(claimed, sealed)
                except (OSError, StorageLockConflict):
                    problems.append(f"claim_release_failed:{policy.stream_id}")
    budget = storage_budget_status(capability)
    if budget["state"] != "ready":
        problems.extend(str(item) for item in budget["problems"])
    state = "degraded" if problems else "ready"
    _write_status(capability, state=state, problems=sorted(set(problems)))
    return {"schema": "RuntimeStorageMaintenance.v1", "state": state, "archived": archived, "retained": retained, "problems": sorted(set(problems))}


def storage_budget_status(capability: RuntimeStorageCapability) -> dict[str, Any]:
    root = Path(capability.source_root)
    archive = Path(capability.archive_root)
    controlled: list[Path] = []
    for policy in capability.streams:
        active = root / Path(policy.relative_path)
        if active.is_file():
            controlled.append(active)
        controlled.extend((_pending_root(root) / policy.stream_id).glob("*.sealed"))
    controlled.extend((_control(root) / "tails").glob("*.jsonl"))
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{_index_path(root)}{suffix}")
        if candidate.is_file():
            controlled.append(candidate)
    for candidate in (_capability_path(root), _status_path(root)):
        if candidate.is_file():
            controlled.append(candidate)
    source_bytes = sum(path.stat().st_size for path in controlled if path.is_file())
    archive_files = [
        path
        for path in (archive / "objects").rglob("*")
        if path.is_file() and not is_link_or_reparse(path)
    ]
    archive_bytes = sum(path.stat().st_size for path in archive_files)
    source_free_bytes = int(shutil.disk_usage(root).free)
    archive_free_bytes = int(shutil.disk_usage(archive).free)
    archive_capability = load_private_archive_capability(archive)
    if archive_capability.storage_role != RETENTION_RECLAMATION_ROLE:
        raise RuntimeStorageError("runtime archive role is not retention-reclamation")
    state = "ready"
    problems: list[str] = []
    if source_bytes > capability.source_budget_bytes:
        state = "failed"
        problems.append("source_budget_exceeded")
    if archive_bytes > capability.archive_budget_bytes:
        state = "failed"
        problems.append("archive_budget_exceeded")
    if source_free_bytes < capability.minimum_source_free_bytes:
        state = "failed"
        problems.append("minimum_source_free_space_lost")
    if archive_free_bytes < capability.minimum_archive_free_bytes:
        state = "failed"
        problems.append("minimum_archive_free_space_lost")
    return {
        "schema": "RuntimeStorageBudget.v1",
        "state": state,
        "controlled_source_bytes": source_bytes,
        "source_budget_bytes": capability.source_budget_bytes,
        "archive_object_bytes": archive_bytes,
        "archive_budget_bytes": capability.archive_budget_bytes,
        "source_free_bytes": source_free_bytes,
        "minimum_source_free_bytes": capability.minimum_source_free_bytes,
        "archive_free_bytes": archive_free_bytes,
        "minimum_archive_free_bytes": capability.minimum_archive_free_bytes,
        "archive_storage_role": archive_capability.storage_role,
        "disaster_recovery_claim": False,
        "problems": problems,
    }


def _write_status(capability: RuntimeStorageCapability, *, state: str, problems: Sequence[str]) -> None:
    budget = storage_budget_status(capability)
    if budget["state"] == "failed":
        state = "failed"
        problems = tuple(sorted(set(problems) | set(budget["problems"])))
    _atomic_replace_json(
        _status_path(Path(capability.source_root)),
        {
            "schema": "RuntimeStorageStatus.v1",
            "state": state,
            "problems": list(problems),
            "controlled_source_bytes": budget["controlled_source_bytes"],
            "source_budget_bytes": budget["source_budget_bytes"],
            "archive_object_bytes": budget["archive_object_bytes"],
            "archive_budget_bytes": budget["archive_budget_bytes"],
            "source_free_bytes": budget["source_free_bytes"],
            "minimum_source_free_bytes": budget["minimum_source_free_bytes"],
            "archive_free_bytes": budget["archive_free_bytes"],
            "minimum_archive_free_bytes": budget["minimum_archive_free_bytes"],
            "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "capability_digest": capability.capability_digest,
        },
    )
