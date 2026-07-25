"""Synthetic-only writer-coordinated immutable JSONL segments.

The module is intentionally off by default.  It accepts only a Package 08A
synthetic root capability and never discovers or imports legacy log paths.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from src.research_lab.storage_capability import (
    RESERVED,
    StorageCapabilityError,
    StorageRootCapability,
    filesystem_identity,
    is_link_or_reparse,
    load_capability,
)
from src.research_lab.storage_os_lock import StorageLockConflict, storage_root_lock

msvcrt: Any
try:  # pragma: no cover - platform import
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None
else:
    msvcrt = _msvcrt


PROTOCOL = "segmented-jsonl.v2"
CANONICALIZATION = "python-json-c14n.v1"
MAX_PAYLOAD_BYTES = 1_048_576
MAX_JSON_DEPTH = 32
MAX_OPEN_PREFIX_BYTES = 4_194_304
MAX_SEGMENT_RECORDS = 4_096
DURABILITY_LINUX = "linux-local-renameat2-dirsync.v1"
DURABILITY_WINDOWS = "windows-ntfs-movefileex-write-through.v1"

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_ID_PATTERNS = {
    "store_id": re.compile(r"segstore_[0-9a-f]{32}\Z"),
    "segment_id": re.compile(r"seg_[0-9a-f]{32}\Z"),
    "request_id": re.compile(r"req_[0-9a-f]{32}\Z"),
    "operation_id": re.compile(r"op_[0-9a-f]{32}\Z"),
    "writer_id": re.compile(r"writer_[0-9a-f]{32}\Z"),
    "root_id": re.compile(r"storageroot_[0-9a-f]{32}\Z"),
}
_ACTIONS = {"open", "append", "auto-seal", "manual-seal"}
_INTENTS = {"open_intent", "append_intent", "seal_intent"}
_SUCCESS = {
    "open_intent": "opened",
    "append_intent": "append_committed",
    "seal_intent": "sealed",
}
_EVENT_TYPES = _INTENTS | set(_SUCCESS.values()) | {"conflict"}
_CONFLICT_REASONS = {
    "intent_blob_mismatch",
    "file_identity_mismatch",
    "preimage_mismatch",
    "tail_mismatch",
    "target_exists",
    "namespace_ambiguous",
    "rename_result_ambiguous",
}


class SegmentStoreError(RuntimeError):
    """The segment operation is invalid or cannot be proved."""


class SegmentStoreConflict(SegmentStoreError):
    """Durable evidence conflicts with the requested operation."""


class SegmentStoreUnsupported(SegmentStoreError):
    """The platform/filesystem cannot support the frozen durability tier."""


@dataclass(frozen=True)
class StreamSpec:
    stream_id: str
    directory_token: str
    payload_schema: str


FIXED_STREAMS: dict[str, StreamSpec] = {
    row.stream_id: row
    for row in (
        StreamSpec("farm.cycle", "farm-cycle", "farm_journal.v1"),
        StreamSpec("farm.task_transition", "farm-task-transition", "farm_journal.v1"),
        StreamSpec("farm.error", "farm-error", "farm_journal.v1"),
        StreamSpec("scout.card", "scout-card", "scanner_journal.v4"),
        StreamSpec("scout.drop", "scout-drop", "scanner_drop.v1"),
        StreamSpec("scout.llm_budget", "scout-llm-budget", "scanner_budget.v1"),
        StreamSpec("scout.ingest", "scout-ingest", "scanner_ingest.v1"),
        StreamSpec("scout.event_audit", "scout-event-audit", "ScoutEventAudit.v1"),
        StreamSpec(
            "scout.routing_audit", "scout-routing-audit", "scanner_routing_audit.v1"
        ),
    )
}

_MANIFEST_KEYS = {
    "schema",
    "protocol",
    "canonicalization",
    "store_id",
    "root_id",
    "capability_digest",
    "canonical_root",
    "registry_sha256",
    "durability_mode",
    "filesystem_identity",
    "max_payload_bytes",
    "max_json_depth",
    "max_open_prefix_bytes",
    "max_segment_records",
    "manifest_sha256",
}
_HEADER_KEYS = {
    "schema",
    "protocol",
    "canonicalization",
    "store_id",
    "root_id",
    "capability_digest",
    "registry_sha256",
    "durability_mode",
    "stream_id",
    "payload_schema",
    "segment_id",
    "segment_seq",
    "prior_segment_sha256",
    "frame_sha256",
}
_RECORD_KEYS = {
    "schema",
    "protocol",
    "store_id",
    "root_id",
    "capability_digest",
    "registry_sha256",
    "stream_id",
    "payload_schema",
    "segment_id",
    "request_id",
    "operation_id",
    "segment_seq",
    "stream_record_seq",
    "segment_record_seq",
    "prior_frame_sha256",
    "payload_sha256",
    "payload",
    "frame_sha256",
}
_FOOTER_KEYS = {
    "schema",
    "protocol",
    "store_id",
    "root_id",
    "capability_digest",
    "registry_sha256",
    "stream_id",
    "payload_schema",
    "segment_id",
    "segment_seq",
    "record_count",
    "prefix_byte_size",
    "first_stream_record_seq",
    "final_stream_record_seq",
    "final_data_frame_sha256",
    "prefix_sha256",
    "prior_segment_sha256",
    "frame_sha256",
}
_EVENT_KEYS = {
    "schema",
    "protocol",
    "store_id",
    "root_id",
    "capability_digest",
    "registry_sha256",
    "durability_mode",
    "event_type",
    "event_id",
    "request_id",
    "operation_id",
    "operation_action",
    "writer_id",
    "stream_id",
    "payload_schema",
    "segment_id",
    "source_name",
    "target_name",
    "event_seq",
    "segment_seq",
    "file_identity",
    "pre_size",
    "post_size",
    "intent_size",
    "pre_sha256",
    "post_sha256",
    "intent_sha256",
    "prior_event_sha256",
    "reason_code",
    "event_sha256",
}
_APPEND_RECEIPT_KEYS = {
    "schema",
    "store_id",
    "stream_id",
    "request_id",
    "operation_id",
    "segment_id",
    "frame_sha256",
    "committed_event_sha256",
    "segment_seq",
    "stream_record_seq",
    "segment_record_seq",
}
_SEAL_RECEIPT_KEYS = {
    "schema",
    "store_id",
    "stream_id",
    "request_id",
    "operation_id",
    "segment_id",
    "final_name",
    "whole_file_sha256",
    "sealed_event_sha256",
    "durability_mode",
    "segment_seq",
}
_SCHEMA_SQL_SHA256 = "d11d28d06dc4ba701b202777e050381281327342e293c64554a4dadd6ea12576"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _positive_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 1


def _digest_or_none(value: object) -> bool:
    return value is None or (
        isinstance(value, str) and _HEX64.fullmatch(value) is not None
    )


def _is_strict_intended_prefix(tail: bytes, intended: bytes) -> bool:
    return 0 < len(tail) < len(intended) and intended.startswith(tail)


def _with_hash(value: dict[str, Any], key: str) -> dict[str, Any]:
    payload = dict(value)
    payload[key] = _sha(_canonical_bytes(payload))
    return payload


def _frame_bytes(value: dict[str, Any]) -> bytes:
    return _canonical_bytes(_with_hash(value, "frame_sha256")) + b"\n"


def _validate_json(value: Any, *, level: int) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if not -(2**63) <= value <= 2**63 - 1:
            raise SegmentStoreError("integer is outside signed 64-bit JSON domain")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SegmentStoreError("non-finite JSON numbers are forbidden")
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8", "strict")
        except UnicodeEncodeError as exc:
            raise SegmentStoreError("lone Unicode surrogate is forbidden") from exc
        return
    if isinstance(value, dict):
        if level > MAX_JSON_DEPTH:
            raise SegmentStoreError("JSON container depth exceeds fixed limit")
        for key, child in value.items():
            if not isinstance(key, str):
                raise SegmentStoreError("JSON object keys must be strings")
            _validate_json(key, level=level)
            _validate_json(
                child, level=level + 1 if isinstance(child, (dict, list)) else level
            )
        return
    if isinstance(value, list):
        if level > MAX_JSON_DEPTH:
            raise SegmentStoreError("JSON container depth exceeds fixed limit")
        for child in value:
            _validate_json(
                child, level=level + 1 if isinstance(child, (dict, list)) else level
            )
        return
    raise SegmentStoreError(f"unsupported JSON value type: {type(value).__name__}")


def _payload_wrapper(stream_id: str, record: object) -> tuple[dict[str, Any], bytes]:
    spec = FIXED_STREAMS.get(stream_id)
    if spec is None:
        raise SegmentStoreError("unknown fixed segment stream")
    if not isinstance(record, dict):
        raise SegmentStoreError("segment record must be a JSON object")
    wrapper = {"record": record, "schema": spec.payload_schema}
    _validate_json(wrapper, level=1)
    encoded = _canonical_bytes(wrapper)
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise SegmentStoreError("canonical payload exceeds fixed byte limit")
    return wrapper, encoded


def _require_id(kind: str, value: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERNS[kind].fullmatch(value):
        raise SegmentStoreError(f"{kind} is not canonical")
    return value


def _registry_payload() -> list[dict[str, str]]:
    return [
        {
            "directory_token": spec.directory_token,
            "payload_schema": spec.payload_schema,
            "stream_id": spec.stream_id,
        }
        for spec in sorted(FIXED_STREAMS.values(), key=lambda item: item.stream_id)
    ]


REGISTRY_SHA256 = _sha(_canonical_bytes(_registry_payload()))


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _statfs_type(path: Path) -> int:
    class StatFs(ctypes.Structure):
        _fields_ = [
            ("f_type", ctypes.c_long),
            ("f_bsize", ctypes.c_long),
            ("f_blocks", ctypes.c_ulong),
            ("f_bfree", ctypes.c_ulong),
            ("f_bavail", ctypes.c_ulong),
            ("f_files", ctypes.c_ulong),
            ("f_ffree", ctypes.c_ulong),
            ("f_fsid", ctypes.c_int * 2),
            ("f_namelen", ctypes.c_long),
            ("f_frsize", ctypes.c_long),
            ("f_flags", ctypes.c_long),
            ("f_spare", ctypes.c_long * 4),
        ]

    libc = ctypes.CDLL(None, use_errno=True)
    result = StatFs()
    if libc.statfs(os.fsencode(path), ctypes.byref(result)) != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, "statfs failed", str(path))
    return int(result.f_type) & 0xFFFFFFFF


def detect_durability_mode(root: Path) -> str:
    root = Path(root)
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if int(kernel32.GetDriveTypeW(ctypes.c_wchar_p(root.anchor))) != 3:
            raise SegmentStoreUnsupported("segment store requires a fixed local drive")
        fs_name = ctypes.create_unicode_buffer(32)
        serial = ctypes.c_ulong()
        ok = kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root.anchor),
            None,
            0,
            ctypes.byref(serial),
            None,
            None,
            fs_name,
            len(fs_name),
        )
        if not ok or fs_name.value.upper() != "NTFS":
            raise SegmentStoreUnsupported("segment store requires fixed local NTFS")
        if msvcrt is None:
            raise SegmentStoreUnsupported("Windows handle support is unavailable")
        return DURABILITY_WINDOWS
    if sys.platform != "linux":
        raise SegmentStoreUnsupported("only Linux and Windows tiers are supported")
    return _detect_linux_durability(root)


def _detect_linux_durability(root: Path) -> str:
    if _statfs_type(root) not in {0xEF53, 0x58465342, 0x9123683E}:
        raise SegmentStoreUnsupported(
            "Linux filesystem is not in the fixed local allowlist"
        )
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY")):
        raise SegmentStoreUnsupported(
            "required Linux no-follow directory primitives are absent"
        )
    libc = ctypes.CDLL(None)
    if not hasattr(libc, "renameat2"):
        raise SegmentStoreUnsupported("renameat2 is unavailable")
    return DURABILITY_LINUX


def _file_identity(fd: int, capability: StorageRootCapability) -> dict[str, int]:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or int(info.st_ino) <= 0:
        raise SegmentStoreConflict("file handle lacks stable regular-file identity")
    return {
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
        "volume_serial": int(capability.filesystem_identity["volume_serial"]),
    }


def _open_file(path: Path, *, read_only: bool = False, create: bool = False) -> int:
    if create and _path_lexists(path):
        raise FileExistsError(str(path))
    probe = _platform_path(path)
    if not create and (not _path_lexists(path) or is_link_or_reparse(probe)):
        raise SegmentStoreConflict("segment path is missing or link/reparse")
    if os.name != "nt":
        flags = (os.O_RDONLY if read_only else os.O_RDWR) | getattr(os, "O_NOFOLLOW", 0)
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        return os.open(path, flags, 0o600)
    if msvcrt is None:  # pragma: no cover
        raise SegmentStoreUnsupported("Windows handle support is unavailable")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.restype = ctypes.c_void_p
    access = 0x80000000 if read_only else 0xC0000000
    share = 0x00000001 | 0x00000002 | 0x00000004
    disposition = 1 if create else 3
    flags = 0x00200000 | (0x80000000 if not read_only else 0)
    raw = create_file(
        ctypes.c_wchar_p(_windows_extended_path(path)),
        access,
        share,
        None,
        disposition,
        flags,
        None,
    )
    if raw in (None, ctypes.c_void_p(-1).value):
        error = ctypes.get_last_error()
        if create and error in {80, 183}:
            raise FileExistsError(str(path))
        raise OSError(error, "CreateFileW failed", str(path))
    mode = (os.O_RDONLY if read_only else os.O_RDWR) | getattr(os, "O_BINARY", 0)
    try:
        return msvcrt.open_osfhandle(int(raw), mode)
    except Exception:
        kernel32.CloseHandle(ctypes.c_void_p(raw))
        raise


def _read_fd(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    parts: list[bytes] = []
    while chunk := os.read(fd, 1024 * 1024):
        parts.append(chunk)
    return b"".join(parts)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short segment write")
        view = view[written:]


def _sync_fd(fd: int) -> None:
    os.fsync(fd)


def _sync_directory(path: Path, durability_mode: str) -> None:
    if durability_mode != DURABILITY_LINUX:
        return
    fd = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _move_no_replace(source: Path, target: Path, durability_mode: str) -> None:
    if _path_lexists(target):
        raise FileExistsError(str(target))
    if durability_mode == DURABILITY_WINDOWS:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel32.MoveFileExW(
            ctypes.c_wchar_p(_windows_extended_path(source)),
            ctypes.c_wchar_p(_windows_extended_path(target)),
            0x00000008,
        ):
            raise OSError(ctypes.get_last_error(), "MoveFileExW failed", str(source))
        return
    source_dir = os.open(
        source.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    target_dir = source_dir
    try:
        if source.parent != target.parent:
            target_dir = os.open(
                target.parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        source_info = os.fstat(source_dir)
        target_info = os.fstat(target_dir)
        if (
            not stat.S_ISDIR(source_info.st_mode)
            or not stat.S_ISDIR(target_info.st_mode)
            or source_info.st_dev != target_info.st_dev
        ):
            raise SegmentStoreConflict(
                "segment move directories are unsafe or cross-volume"
            )
        libc = ctypes.CDLL(None, use_errno=True)
        if (
            libc.renameat2(
                source_dir,
                os.fsencode(source.name),
                target_dir,
                os.fsencode(target.name),
                1,
            )
            != 0
        ):
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError(str(target))
            raise OSError(error, "renameat2 failed", str(source))
    finally:
        if target_dir != source_dir:
            os.close(target_dir)
        os.close(source_dir)


def _windows_extended_path(path: Path) -> str:
    value = str(Path(path).absolute())
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _platform_path(path: Path) -> Path:
    if os.name == "nt":
        return Path(_windows_extended_path(path))
    return path


def _path_lexists(path: Path) -> bool:
    return os.path.lexists(_platform_path(path))


def _operation_id(
    store_id: str,
    request_id: str,
    action: str,
    stream_id: str,
    segment_seq: int,
) -> str:
    if action not in _ACTIONS:
        raise SegmentStoreError("unknown segment operation action")
    material = "\0".join(
        (PROTOCOL, store_id, request_id, action, stream_id, str(segment_seq))
    ).encode("utf-8")
    return "op_" + _sha(material)[:32]


@dataclass
class _PhysicalStream:
    stream_id: str
    sealed: list[dict[str, Any]]
    active: dict[str, Any] | None
    records: list[dict[str, Any]]
    blocked: bool = False


class SegmentStore:
    """One explicitly activated synthetic segmented store."""

    def __init__(
        self,
        root: Path,
        *,
        writer_id: str | None = None,
        fault_hook: Callable[[str, Path], None] | None = None,
    ) -> None:
        try:
            self.capability = load_capability(Path(root))
        except (OSError, ValueError, StorageCapabilityError) as exc:
            raise SegmentStoreConflict(
                "storage capability revalidation failed"
            ) from exc
        self.root = Path(self.capability.canonical_root)
        self.control = self.root / RESERVED
        self.lock_path = self.control / "locks" / "operation.lock"
        self.manifest_path = self.control / "segment_store.json"
        self.db_path = self.control / "segment_events.sqlite3"
        self.segments_path = self.control / "segments"
        self.staging_path = self.control / "staging"
        self.writer_id = _require_id(
            "writer_id", writer_id or "writer_" + uuid.uuid4().hex
        )
        self._fault_hook = fault_hook
        self.manifest: dict[str, Any] | None = None
        self.durability_mode: str | None = None
        if self.manifest_path.exists():
            self._load_manifest()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        try:
            with storage_root_lock(self.lock_path, wait_seconds=5.0):
                self._reload_capability()
                yield
        except StorageLockConflict as exc:
            raise SegmentStoreConflict(str(exc)) from exc

    def _fault(self, phase: str, path: Path) -> None:
        if self._fault_hook is not None:
            self._fault_hook(phase, path)

    def _reload_capability(self) -> None:
        try:
            current = load_capability(self.root)
        except (OSError, ValueError, StorageCapabilityError) as exc:
            raise SegmentStoreConflict(
                "storage capability revalidation failed"
            ) from exc
        if current != self.capability:
            raise SegmentStoreConflict("storage capability changed after construction")

    def _manifest_payload(self, store_id: str, durability_mode: str) -> dict[str, Any]:
        return {
            "schema": "SegmentStoreManifest.v2",
            "protocol": PROTOCOL,
            "canonicalization": CANONICALIZATION,
            "store_id": store_id,
            "root_id": self.capability.root_id,
            "capability_digest": self.capability.capability_digest,
            "canonical_root": self.capability.canonical_root,
            "registry_sha256": REGISTRY_SHA256,
            "durability_mode": durability_mode,
            "filesystem_identity": self.capability.filesystem_identity,
            "max_payload_bytes": MAX_PAYLOAD_BYTES,
            "max_json_depth": MAX_JSON_DEPTH,
            "max_open_prefix_bytes": MAX_OPEN_PREFIX_BYTES,
            "max_segment_records": MAX_SEGMENT_RECORDS,
        }

    def _load_manifest(self) -> None:
        if is_link_or_reparse(self.manifest_path) or not self.manifest_path.is_file():
            raise SegmentStoreConflict("segment manifest path is unsafe")
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            raise SegmentStoreConflict("segment manifest is unreadable") from exc
        if set(manifest) != _MANIFEST_KEYS:
            raise SegmentStoreConflict("segment manifest key set is invalid")
        digest = manifest.pop("manifest_sha256")
        expected_mode = detect_durability_mode(self.root)
        expected = self._manifest_payload(
            str(manifest.get("store_id", "")), expected_mode
        )
        if manifest != expected or not _HEX64.fullmatch(str(digest)):
            raise SegmentStoreConflict("segment manifest binding mismatch")
        if _sha(_canonical_bytes(manifest)) != digest:
            raise SegmentStoreConflict("segment manifest digest mismatch")
        manifest["manifest_sha256"] = digest
        _require_id("store_id", manifest["store_id"])
        _require_id("root_id", manifest["root_id"])
        self.manifest = manifest
        self.durability_mode = expected_mode

    def _metadata_expected(self) -> dict[str, Any]:
        if self.manifest is None:
            raise SegmentStoreConflict("segment store is not activated")
        return {**self.manifest}

    def activate(self) -> None:
        with self._locked():
            mode = detect_durability_mode(self.root)
            activation_artifacts = sorted(self.staging_path.glob("segment-store-*"))
            if activation_artifacts:
                raise SegmentStoreConflict(
                    "segment activation has preserved staging/ambiguity evidence"
                )
            if self.manifest_path.exists():
                self._load_manifest()
            else:
                leftovers = [self.db_path, self.segments_path]
                if any(_path_lexists(path) for path in leftovers):
                    raise SegmentStoreConflict(
                        "segment activation has unbound partial paths"
                    )
                store_id = "segstore_" + uuid.uuid4().hex
                payload = self._manifest_payload(store_id, mode)
                manifest = _with_hash(payload, "manifest_sha256")
                staging = self.staging_path / f"segment-store-{store_id}.json"
                pending = self.staging_path / (
                    f"segment-store-{store_id}.activation-pending.json"
                )
                fd = _open_file(staging, create=True)
                try:
                    _write_all(fd, _canonical_bytes(manifest))
                    _sync_fd(fd)
                finally:
                    os.close(fd)
                self._fault("after_manifest_fsync", staging)
                marker = {
                    "schema": "SegmentStoreActivationPending.v2",
                    "store_id": store_id,
                    "manifest_sha256": manifest["manifest_sha256"],
                    "source_name": staging.name,
                    "target_name": self.manifest_path.name,
                }
                marker_fd = _open_file(pending, create=True)
                try:
                    _write_all(marker_fd, _canonical_bytes(marker))
                    _sync_fd(marker_fd)
                finally:
                    os.close(marker_fd)
                _sync_directory(self.staging_path, mode)
                _move_no_replace(staging, self.manifest_path, mode)
                _sync_directory(self.control, mode)
                self.manifest = manifest
                self.durability_mode = mode
                self._load_manifest()
                pending.unlink()
                _sync_directory(self.staging_path, mode)
            self._activate_directories()
            self._activate_database()
            self._load_manifest()
            self._verify_database_readonly()

    def _activate_directories(self) -> None:
        assert self.durability_mode is not None
        if _path_lexists(self.segments_path):
            if (
                is_link_or_reparse(self.segments_path)
                or not self.segments_path.is_dir()
            ):
                raise SegmentStoreConflict("segment directory is unsafe")
        else:
            self.segments_path.mkdir()
            _sync_directory(self.control, self.durability_mode)
        expected = {spec.directory_token for spec in FIXED_STREAMS.values()}
        existing = {path.name for path in self.segments_path.iterdir()}
        if not existing.issubset(expected):
            raise SegmentStoreConflict(
                "segment directory has unexpected activation entries"
            )
        for token in sorted(expected):
            path = self.segments_path / token
            if not path.exists():
                path.mkdir()
                _sync_directory(self.segments_path, self.durability_mode)
            if is_link_or_reparse(path) or not path.is_dir():
                raise SegmentStoreConflict("fixed stream directory is unsafe")
            if filesystem_identity(path) != self.capability.filesystem_identity:
                raise SegmentStoreConflict("fixed stream directory changed filesystem")

    def _activate_database(self) -> None:
        assert self.durability_mode is not None
        if not self.db_path.exists():
            fd = _open_file(self.db_path, create=True)
            try:
                _sync_fd(fd)
            finally:
                os.close(fd)
            _sync_directory(self.control, self.durability_mode)
        fd = _open_file(self.db_path)
        try:
            identity = _file_identity(fd, self.capability)
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA journal_mode=DELETE")
                conn.execute("PRAGMA synchronous=EXTRA")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS metadata(
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL CHECK(json_valid(value))
                    );
                    CREATE TABLE IF NOT EXISTS events(
                        event_seq INTEGER PRIMARY KEY CHECK(event_seq >= 1),
                        event_id TEXT NOT NULL UNIQUE
                            CHECK(length(event_id) = 26),
                        operation_id TEXT NOT NULL
                            CHECK(length(operation_id) = 35
                                AND substr(operation_id, 1, 3) = 'op_'
                                AND substr(operation_id, 4)
                                    NOT GLOB '*[^0-9a-f]*'),
                        event_type TEXT NOT NULL CHECK(event_type IN (
                            'open_intent', 'opened', 'append_intent',
                            'append_committed', 'seal_intent', 'sealed', 'conflict'
                        )),
                        request_id TEXT NOT NULL CHECK(length(request_id) = 36
                            AND substr(request_id, 1, 4) = 'req_'
                            AND substr(request_id, 5) NOT GLOB '*[^0-9a-f]*'),
                        operation_action TEXT NOT NULL CHECK(operation_action IN (
                            'open', 'append', 'auto-seal', 'manual-seal'
                        )),
                        stream_id TEXT NOT NULL CHECK(stream_id IN (
                            'farm.cycle', 'farm.task_transition', 'farm.error',
                            'scout.card', 'scout.drop', 'scout.llm_budget',
                            'scout.ingest', 'scout.event_audit',
                            'scout.routing_audit'
                        )),
                        event_json TEXT NOT NULL CHECK(json_valid(event_json)),
                        intent_bytes BLOB,
                        UNIQUE(operation_id, event_type),
                        FOREIGN KEY(request_id) REFERENCES requests(request_id)
                            ON DELETE RESTRICT,
                        CHECK(event_id = 'event_' || printf('%020d', event_seq)),
                        CHECK(json_extract(event_json, '$.event_seq') = event_seq),
                        CHECK(json_extract(event_json, '$.event_id') = event_id),
                        CHECK(json_extract(event_json, '$.operation_id') = operation_id),
                        CHECK(json_extract(event_json, '$.event_type') = event_type),
                        CHECK(json_extract(event_json, '$.request_id') = request_id),
                        CHECK(json_extract(event_json, '$.operation_action') = operation_action),
                        CHECK(json_extract(event_json, '$.stream_id') = stream_id),
                        CHECK(
                            (event_type IN ('open_intent', 'append_intent', 'seal_intent')
                                AND intent_bytes IS NOT NULL
                                AND json_type(event_json, '$.intent_size') = 'integer'
                                AND json_extract(event_json, '$.intent_size') = length(intent_bytes)
                                AND json_type(event_json, '$.intent_sha256') = 'text')
                            OR
                            (event_type NOT IN ('open_intent', 'append_intent', 'seal_intent')
                                AND intent_bytes IS NULL
                                AND json_type(event_json, '$.intent_size') = 'null'
                                AND json_type(event_json, '$.intent_sha256') = 'null')
                        ),
                        CHECK(
                            (json_type(event_json, '$.pre_size') = 'null'
                                AND json_type(event_json, '$.pre_sha256') = 'null')
                            OR
                            (json_type(event_json, '$.pre_size') = 'integer'
                                AND json_type(event_json, '$.pre_sha256') = 'text')
                        ),
                        CHECK(
                            (json_type(event_json, '$.post_size') = 'null'
                                AND json_type(event_json, '$.post_sha256') = 'null')
                            OR
                            (json_type(event_json, '$.post_size') = 'integer'
                                AND json_type(event_json, '$.post_sha256') = 'text')
                        )
                    );
                    CREATE TABLE IF NOT EXISTS requests(
                        request_id TEXT PRIMARY KEY CHECK(length(request_id) = 36
                            AND substr(request_id, 1, 4) = 'req_'
                            AND substr(request_id, 5) NOT GLOB '*[^0-9a-f]*'),
                        external_action TEXT NOT NULL
                            CHECK(external_action IN ('append', 'manual-seal')),
                        stream_id TEXT NOT NULL CHECK(stream_id IN (
                            'farm.cycle', 'farm.task_transition', 'farm.error',
                            'scout.card', 'scout.drop', 'scout.llm_budget',
                            'scout.ingest', 'scout.event_audit',
                            'scout.routing_audit'
                        )),
                        payload_sha256 TEXT,
                        receipt_json TEXT CHECK(
                            receipt_json IS NULL OR json_valid(receipt_json)
                        ),
                        CHECK(
                            (external_action = 'append'
                                AND length(payload_sha256) = 64
                                AND payload_sha256 NOT GLOB '*[^0-9a-f]*')
                            OR (external_action = 'manual-seal'
                                AND payload_sha256 IS NULL)
                        )
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS
                        events_append_stream_record_seq
                    ON events(
                        stream_id,
                        json_extract(CAST(intent_bytes AS TEXT), '$.stream_record_seq')
                    ) WHERE event_type = 'append_intent';
                    CREATE UNIQUE INDEX IF NOT EXISTS
                        events_append_segment_record_seq
                    ON events(
                        stream_id,
                        json_extract(CAST(intent_bytes AS TEXT), '$.segment_id'),
                        json_extract(CAST(intent_bytes AS TEXT), '$.segment_record_seq')
                    ) WHERE event_type = 'append_intent';
                    CREATE TRIGGER IF NOT EXISTS events_before_insert
                    BEFORE INSERT ON events
                    BEGIN
                        SELECT CASE WHEN NEW.event_seq !=
                            COALESCE((SELECT MAX(event_seq) + 1 FROM events), 1)
                            THEN RAISE(ABORT, 'event sequence is not adjacent') END;
                        SELECT CASE WHEN NEW.event_seq > 1 AND
                            json_extract(NEW.event_json, '$.prior_event_sha256') IS NOT
                            (SELECT json_extract(event_json, '$.event_sha256')
                             FROM events WHERE event_seq = NEW.event_seq - 1)
                            THEN RAISE(ABORT, 'prior event hash is not adjacent') END;
                        SELECT CASE WHEN EXISTS(
                            SELECT 1 FROM events
                            WHERE stream_id = NEW.stream_id AND event_type = 'conflict'
                        ) THEN RAISE(ABORT, 'stream is permanently blocked') END;
                        SELECT CASE WHEN NEW.event_type IN
                            ('open_intent', 'append_intent', 'seal_intent')
                            AND NEW.event_seq > 1
                            AND (SELECT event_type FROM events
                                 WHERE event_seq = NEW.event_seq - 1) IN
                                ('open_intent', 'append_intent', 'seal_intent')
                            THEN RAISE(ABORT, 'intent barrier is pending') END;
                        SELECT CASE WHEN NEW.event_type NOT IN
                            ('open_intent', 'append_intent', 'seal_intent')
                            AND (
                                (SELECT operation_id FROM events
                                 WHERE event_seq = NEW.event_seq - 1) IS NOT NEW.operation_id
                                OR (SELECT event_type FROM events
                                    WHERE event_seq = NEW.event_seq - 1) NOT IN
                                   ('open_intent', 'append_intent', 'seal_intent')
                            ) THEN RAISE(ABORT, 'terminal is not adjacent to intent') END;
                        SELECT CASE WHEN NEW.event_type = 'opened' AND
                            (SELECT event_type FROM events
                             WHERE event_seq = NEW.event_seq - 1) != 'open_intent'
                            THEN RAISE(ABORT, 'opened transition mismatch') END;
                        SELECT CASE WHEN NEW.event_type = 'append_committed' AND
                            (SELECT event_type FROM events
                             WHERE event_seq = NEW.event_seq - 1) != 'append_intent'
                            THEN RAISE(ABORT, 'append transition mismatch') END;
                        SELECT CASE WHEN NEW.event_type = 'sealed' AND
                            (SELECT event_type FROM events
                             WHERE event_seq = NEW.event_seq - 1) != 'seal_intent'
                            THEN RAISE(ABORT, 'seal transition mismatch') END;
                        SELECT CASE WHEN NEW.event_type NOT IN
                            ('open_intent', 'append_intent', 'seal_intent')
                            AND EXISTS(
                                SELECT 1 FROM events AS prior
                                WHERE prior.event_seq = NEW.event_seq - 1
                                AND (
                                    json_extract(prior.event_json, '$.request_id') IS NOT
                                        json_extract(NEW.event_json, '$.request_id')
                                    OR json_extract(prior.event_json, '$.operation_action') IS NOT
                                        json_extract(NEW.event_json, '$.operation_action')
                                    OR json_extract(prior.event_json, '$.stream_id') IS NOT
                                        json_extract(NEW.event_json, '$.stream_id')
                                    OR json_extract(prior.event_json, '$.segment_id') IS NOT
                                        json_extract(NEW.event_json, '$.segment_id')
                                    OR json_extract(prior.event_json, '$.source_name') IS NOT
                                        json_extract(NEW.event_json, '$.source_name')
                                    OR json_extract(prior.event_json, '$.target_name') IS NOT
                                        json_extract(NEW.event_json, '$.target_name')
                                    OR json_extract(prior.event_json, '$.pre_size') IS NOT
                                        json_extract(NEW.event_json, '$.pre_size')
                                    OR json_extract(prior.event_json, '$.pre_sha256') IS NOT
                                        json_extract(NEW.event_json, '$.pre_sha256')
                                    OR json_extract(prior.event_json, '$.post_size') IS NOT
                                        json_extract(NEW.event_json, '$.post_size')
                                    OR json_extract(prior.event_json, '$.post_sha256') IS NOT
                                        json_extract(NEW.event_json, '$.post_sha256')
                                )
                            ) THEN RAISE(ABORT, 'terminal evidence differs from intent') END;
                        SELECT CASE WHEN NEW.event_type IN ('append_committed', 'sealed')
                            AND json_extract(NEW.event_json, '$.file_identity') IS NOT
                                (SELECT json_extract(event_json, '$.file_identity')
                                 FROM events WHERE event_seq = NEW.event_seq - 1)
                            THEN RAISE(ABORT, 'terminal file identity differs') END;
                        SELECT CASE WHEN NEW.event_type = 'append_intent' AND (
                            json_valid(CAST(NEW.intent_bytes AS TEXT)) != 1
                            OR json_extract(CAST(NEW.intent_bytes AS TEXT), '$.stream_record_seq') !=
                                1 + (SELECT COUNT(*) FROM events
                                     WHERE stream_id = NEW.stream_id
                                       AND event_type = 'append_intent')
                            OR json_extract(CAST(NEW.intent_bytes AS TEXT), '$.segment_record_seq') !=
                                1 + (SELECT COUNT(*) FROM events
                                     WHERE stream_id = NEW.stream_id
                                       AND event_type = 'append_intent'
                                       AND json_extract(CAST(intent_bytes AS TEXT), '$.segment_id') =
                                           json_extract(CAST(NEW.intent_bytes AS TEXT), '$.segment_id'))
                        ) THEN RAISE(ABORT, 'append record sequence mismatch') END;
                    END;
                    """
                )
                self._verify_database_schema(conn)
                existing = conn.execute("SELECT key, value FROM metadata").fetchall()
                expected = self._metadata_expected()
                if existing:
                    actual = {str(key): json.loads(value) for key, value in existing}
                    if actual != expected:
                        raise SegmentStoreConflict("segment database metadata mismatch")
                else:
                    conn.executemany(
                        "INSERT INTO metadata(key, value) VALUES (?, ?)",
                        [
                            (key, _canonical_bytes(value).decode("utf-8"))
                            for key, value in expected.items()
                        ],
                    )
                conn.commit()
            finally:
                conn.close()
            if _file_identity(fd, self.capability) != identity:
                raise SegmentStoreConflict(
                    "segment database identity changed during activation"
                )
        finally:
            os.close(fd)
        fd = _open_file(self.db_path)
        try:
            _sync_fd(fd)
        finally:
            os.close(fd)
        _sync_directory(self.control, self.durability_mode)

    def _database_identity(self, fd: int) -> dict[str, int]:
        identity = _file_identity(fd, self.capability)
        path_fd = _open_file(self.db_path, read_only=True)
        try:
            if _file_identity(path_fd, self.capability) != identity:
                raise SegmentStoreConflict(
                    "segment database held/path identity mismatch"
                )
        finally:
            os.close(path_fd)
        return identity

    def _verify_database_schema(self, conn: sqlite3.Connection) -> None:
        schema_objects = [
            {
                "type": str(row[0]),
                "name": str(row[1]),
                "table": str(row[2]),
                "sql": " ".join(str(row[3]).lower().split()),
            }
            for row in conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_autoindex_%' "
                "ORDER BY type, name"
            )
        ]
        if _sha(_canonical_bytes(schema_objects)) != _SCHEMA_SQL_SHA256:
            raise SegmentStoreConflict("segment database exact schema digest mismatch")
        expected_columns = {
            "metadata": [("key", "TEXT"), ("value", "TEXT")],
            "events": [
                ("event_seq", "INTEGER"),
                ("event_id", "TEXT"),
                ("operation_id", "TEXT"),
                ("event_type", "TEXT"),
                ("request_id", "TEXT"),
                ("operation_action", "TEXT"),
                ("stream_id", "TEXT"),
                ("event_json", "TEXT"),
                ("intent_bytes", "BLOB"),
            ],
            "requests": [
                ("request_id", "TEXT"),
                ("external_action", "TEXT"),
                ("stream_id", "TEXT"),
                ("payload_sha256", "TEXT"),
                ("receipt_json", "TEXT"),
            ],
        }
        table_rows = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        if {str(row[0]) for row in table_rows} != set(expected_columns):
            raise SegmentStoreConflict("segment database schema table set mismatch")
        required_sql = {
            "metadata": ("check(json_valid(value))",),
            "events": (
                "check(event_seq >= 1)",
                "check(event_type in",
                "check(operation_action in",
                "check(stream_id in",
                "check(json_valid(event_json))",
                "unique(operation_id, event_type)",
            ),
            "requests": (
                "check(external_action in",
                "check(stream_id in",
                "receipt_json is null or json_valid(receipt_json)",
                "payload_sha256 not glob '*[^0-9a-f]*'",
            ),
        }
        for table, sql in table_rows:
            columns = [
                (str(row[1]), str(row[2]).upper())
                for row in conn.execute(f"PRAGMA table_info({table})")
            ]
            if columns != expected_columns[str(table)]:
                raise SegmentStoreConflict("segment database schema column mismatch")
            normalized = " ".join(str(sql).lower().split())
            if any(token not in normalized for token in required_sql[str(table)]):
                raise SegmentStoreConflict(
                    "segment database schema constraint mismatch"
                )

    @contextmanager
    def _mutating_connection(self) -> Iterator[sqlite3.Connection]:
        if self.manifest is None or self.durability_mode is None:
            raise SegmentStoreConflict("segment store is not activated")
        held = _open_file(self.db_path)
        identity = self._database_identity(held)
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=EXTRA")
            conn.execute("BEGIN IMMEDIATE")
            self._verify_connection(conn, identity)
            yield conn
            self._verify_connection(conn, identity)
            self._validate_event_rows(
                conn.execute(
                    "SELECT event_json, intent_bytes FROM events ORDER BY event_seq"
                ).fetchall(),
                allow_pending=True,
            )
            conn.commit()
            if self._database_identity(held) != identity:
                raise SegmentStoreConflict(
                    "segment database identity changed after commit"
                )
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
            os.close(held)

    def _verify_connection(
        self, conn: sqlite3.Connection, identity: dict[str, int]
    ) -> None:
        if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "delete":
            raise SegmentStoreConflict("segment database journal mode mismatch")
        if int(conn.execute("PRAGMA synchronous").fetchone()[0]) != 3:
            raise SegmentStoreConflict("segment database synchronous mode mismatch")
        if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise SegmentStoreConflict("segment database foreign-key mode mismatch")
        listed = conn.execute("PRAGMA database_list").fetchall()
        main = next((row for row in listed if str(row[1]) == "main"), None)
        if main is None or Path(str(main[2])).resolve() != self.db_path.resolve():
            raise SegmentStoreConflict("segment database canonical path mismatch")
        self._verify_database_schema(conn)
        try:
            actual = {
                str(row[0]): json.loads(str(row[1]))
                for row in conn.execute("SELECT key, value FROM metadata")
            }
        except (ValueError, TypeError) as exc:
            raise SegmentStoreConflict("segment database metadata is invalid") from exc
        if actual != self._metadata_expected():
            raise SegmentStoreConflict("segment database metadata mismatch")
        event_rows = conn.execute(
            "SELECT event_json, intent_bytes FROM events ORDER BY event_seq"
        ).fetchall()
        events, _pending = self._validate_event_rows(event_rows, allow_pending=True)
        self._validate_request_rows(conn, events, event_rows)
        next_fd = _open_file(self.db_path, read_only=True)
        try:
            if self._database_identity(next_fd) != identity:
                raise SegmentStoreConflict(
                    "segment database identity changed inside transaction"
                )
        finally:
            os.close(next_fd)

    def _verify_database_readonly(self) -> None:
        if self.manifest is None:
            raise SegmentStoreConflict("segment store is not activated")
        if is_link_or_reparse(self.db_path) or not self.db_path.is_file():
            raise SegmentStoreConflict("segment database path is unsafe")
        held = _open_file(self.db_path, read_only=True)
        identity = self._database_identity(held)
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=EXTRA")
            self._verify_connection(conn, identity)
        finally:
            conn.close()
            os.close(held)

    def _event_rows(self) -> list[tuple[str, bytes | None]]:
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            return [
                (str(row[0]), row[1])
                for row in conn.execute(
                    "SELECT event_json, intent_bytes FROM events ORDER BY event_seq"
                )
            ]
        finally:
            conn.close()

    def _validate_event_semantics(
        self,
        event: dict[str, Any],
        intent_blob: bytes | None,
        pending: dict[str, Any] | None,
    ) -> None:
        event_type = str(event["event_type"])
        action = str(event["operation_action"])
        if event_type in {"open_intent", "opened"} and action != "open":
            raise SegmentStoreConflict("segment event action/evidence mismatch")
        if event_type in {"append_intent", "append_committed"} and action != "append":
            raise SegmentStoreConflict("segment event action/evidence mismatch")
        if event_type in {"seal_intent", "sealed"} and action not in {
            "auto-seal",
            "manual-seal",
        }:
            raise SegmentStoreConflict("segment event action/evidence mismatch")
        for kind in ("request_id", "writer_id", "segment_id"):
            try:
                _require_id(kind, event[kind])
            except SegmentStoreError as exc:
                raise SegmentStoreConflict(
                    "segment event identity evidence is invalid"
                ) from exc
        segment_seq = event["segment_seq"]
        if (
            isinstance(segment_seq, bool)
            or not isinstance(segment_seq, int)
            or segment_seq < 1
        ):
            raise SegmentStoreConflict("segment event sequence evidence is invalid")
        if not _HEX64.fullmatch(str(event["event_sha256"])):
            raise SegmentStoreConflict("segment event digest evidence is invalid")
        prior_hash = event["prior_event_sha256"]
        if prior_hash is not None and not _HEX64.fullmatch(str(prior_hash)):
            raise SegmentStoreConflict("segment prior-event evidence is invalid")
        identity = event["file_identity"]
        if identity is not None and (
            not isinstance(identity, dict)
            or set(identity) != {"device", "inode", "volume_serial"}
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in identity.values()
            )
        ):
            raise SegmentStoreConflict("segment file identity evidence is invalid")
        for size_key, hash_key in (
            ("pre_size", "pre_sha256"),
            ("post_size", "post_sha256"),
            ("intent_size", "intent_sha256"),
        ):
            size = event[size_key]
            digest = event[hash_key]
            if (size is None) != (digest is None):
                raise SegmentStoreConflict(
                    "segment event size/hash evidence is incomplete"
                )
            if size is not None and (
                isinstance(size, bool)
                or not isinstance(size, int)
                or size < 0
                or not _HEX64.fullmatch(str(digest))
            ):
                raise SegmentStoreConflict(
                    "segment event size/hash evidence is invalid"
                )
        source = event["source_name"]
        expected_source = f"{segment_seq:020d}.{event['segment_id']}.open.jsonl"
        if not isinstance(source, str) or source != expected_source:
            raise SegmentStoreConflict("segment source-name evidence is invalid")
        target = event["target_name"]
        if target is not None and (
            not isinstance(target, str)
            or target
            != f"{segment_seq:020d}.{event['segment_id']}.{event['post_sha256']}.sealed.jsonl"
        ):
            raise SegmentStoreConflict("segment target-name evidence is invalid")

        is_intent = event_type in _INTENTS
        if is_intent != (intent_blob is not None):
            raise SegmentStoreConflict("segment intent evidence is invalid")
        if event_type == "open_intent":
            expected = (identity is None, target is None, event["pre_size"] is None)
            if not all(expected) or event["post_size"] is None:
                raise SegmentStoreConflict("segment open evidence is invalid")
        elif event_type == "opened":
            if (
                identity is None
                or target is not None
                or event["pre_size"] is not None
                or event["post_size"] is None
            ):
                raise SegmentStoreConflict("segment opened evidence is invalid")
        elif event_type in {"append_intent", "append_committed"}:
            if (
                identity is None
                or target is not None
                or event["pre_size"] is None
                or event["post_size"] is None
            ):
                raise SegmentStoreConflict("segment append evidence is invalid")
        elif event_type in {"seal_intent", "sealed"}:
            if (
                identity is None
                or target is None
                or event["pre_size"] is None
                or event["post_size"] is None
            ):
                raise SegmentStoreConflict("segment seal evidence is invalid")

        if pending is not None and not is_intent:
            copied = (
                "request_id",
                "operation_id",
                "operation_action",
                "stream_id",
                "payload_schema",
                "segment_id",
                "source_name",
                "target_name",
                "segment_seq",
                "pre_size",
                "pre_sha256",
                "post_size",
                "post_sha256",
            )
            if any(event[key] != pending[key] for key in copied):
                raise SegmentStoreConflict(
                    "segment terminal evidence differs from intent"
                )
            if event_type == "conflict" and identity != pending["file_identity"]:
                raise SegmentStoreConflict("segment conflict identity evidence differs")
            if (
                event_type in {"append_committed", "sealed"}
                and identity != pending["file_identity"]
            ):
                raise SegmentStoreConflict(
                    "segment terminal file identity differs from intent"
                )

    def _validate_event_rows(
        self,
        rows: list[Any],
        *,
        allow_pending: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if self.manifest is None:
            raise SegmentStoreConflict("segment store manifest is unavailable")
        events: list[dict[str, Any]] = []
        prior: str | None = None
        pending: dict[str, Any] | None = None
        blocked_streams: set[str] = set()
        for expected_seq, row in enumerate(rows, 1):
            raw, intent_blob = row[0], row[1]
            try:
                event = json.loads(str(raw), object_pairs_hook=_reject_duplicate_pairs)
            except (ValueError, TypeError, UnicodeError) as exc:
                raise SegmentStoreConflict("segment event JSON is invalid") from exc
            if (
                set(event) != _EVENT_KEYS
                or event.get("schema") != "SegmentStoreEvent.v2"
            ):
                raise SegmentStoreConflict("segment event key/schema mismatch")
            if (
                not _positive_int(event.get("event_seq"))
                or event.get("event_seq") != expected_seq
                or event.get("event_id") != f"event_{expected_seq:020d}"
            ):
                raise SegmentStoreConflict("segment event sequence/id mismatch")
            if event.get("prior_event_sha256") != prior:
                raise SegmentStoreConflict("segment event prior hash mismatch")
            supplied = event.pop("event_sha256")
            computed = _sha(_canonical_bytes(event))
            event["event_sha256"] = supplied
            if supplied != computed:
                raise SegmentStoreConflict("segment event digest mismatch")
            if (
                event.get("protocol") != PROTOCOL
                or event.get("store_id") != self.manifest["store_id"]
            ):
                raise SegmentStoreConflict("segment event store binding mismatch")
            if (
                event.get("root_id") != self.capability.root_id
                or event.get("capability_digest") != self.capability.capability_digest
            ):
                raise SegmentStoreConflict("segment event root/capability mismatch")
            if (
                event.get("registry_sha256") != REGISTRY_SHA256
                or event.get("durability_mode") != self.durability_mode
            ):
                raise SegmentStoreConflict("segment event registry/durability mismatch")
            event_type = str(event.get("event_type"))
            if (
                event_type not in _EVENT_TYPES
                or event.get("operation_action") not in _ACTIONS
            ):
                raise SegmentStoreConflict("segment event type/action mismatch")
            stream_id = str(event.get("stream_id"))
            spec = FIXED_STREAMS.get(stream_id)
            if spec is None or event.get("payload_schema") != spec.payload_schema:
                raise SegmentStoreConflict("segment event stream/schema mismatch")
            expected_operation = _operation_id(
                str(event["store_id"]),
                str(event["request_id"]),
                str(event["operation_action"]),
                stream_id,
                int(event["segment_seq"]),
            )
            if event.get("operation_id") != expected_operation:
                raise SegmentStoreConflict("segment event operation id mismatch")
            if stream_id in blocked_streams:
                raise SegmentStoreConflict(
                    "segment event follows a permanent stream conflict"
                )
            self._validate_event_semantics(event, intent_blob, pending)
            if event_type in _INTENTS:
                if pending is not None:
                    raise SegmentStoreConflict("segment intents interleave globally")
                if (
                    intent_blob is None
                    or event.get("intent_size") != len(intent_blob)
                    or event.get("intent_sha256") != _sha(bytes(intent_blob))
                ):
                    raise SegmentStoreConflict("segment intent blob mismatch")
                if event.get("reason_code") is not None:
                    raise SegmentStoreConflict("intent cannot carry terminal reason")
                pending = event
            else:
                if (
                    intent_blob is not None
                    or event.get("intent_size") is not None
                    or event.get("intent_sha256") is not None
                ):
                    raise SegmentStoreConflict("non-intent event carries intent bytes")
                if pending is None or event.get("operation_id") != pending.get(
                    "operation_id"
                ):
                    raise SegmentStoreConflict(
                        "segment terminal event lacks adjacent intent"
                    )
                expected_terminal = _SUCCESS[str(pending["event_type"])]
                if event_type == "conflict":
                    if event.get("reason_code") not in _CONFLICT_REASONS:
                        raise SegmentStoreConflict("segment conflict reason is invalid")
                    blocked_streams.add(stream_id)
                elif (
                    event_type != expected_terminal
                    or event.get("reason_code") is not None
                ):
                    raise SegmentStoreConflict("segment event transition is invalid")
                pending = None
            prior = supplied
            events.append(event)
        if pending is not None and not allow_pending:
            raise SegmentStoreConflict("segment store has a pending intent")
        return events, pending

    def _derived_request_receipt(
        self,
        events: list[dict[str, Any]],
        blobs: dict[str, bytes],
        request_id: str,
        external_action: str,
        stream_id: str,
    ) -> dict[str, Any] | None:
        if external_action == "append":
            terminals = [
                event
                for event in events
                if event["request_id"] == request_id
                and event["stream_id"] == stream_id
                and event["event_type"] == "append_committed"
            ]
            if len(terminals) > 1:
                raise SegmentStoreConflict(
                    "segment request has multiple append receipts"
                )
            if not terminals:
                return None
            terminal = terminals[0]
            frames = self._decode_frames(blobs[terminal["operation_id"]])
            if len(frames) != 1 or frames[0][0].get("schema") != "SegmentRecord.v2":
                raise SegmentStoreConflict("segment request summary intent is invalid")
            return self._append_receipt(frames[0][0], terminal)
        terminals = [
            event
            for event in events
            if event["request_id"] == request_id
            and event["stream_id"] == stream_id
            and event["operation_action"] == "manual-seal"
            and event["event_type"] == "sealed"
        ]
        if len(terminals) > 1:
            raise SegmentStoreConflict("segment request has multiple seal receipts")
        if not terminals:
            return None
        terminal = terminals[0]
        intent = next(
            event
            for event in events
            if event["operation_id"] == terminal["operation_id"]
            and event["event_type"] == "seal_intent"
        )
        return self._seal_receipt(intent, terminal)

    def _validate_request_rows(
        self,
        conn: sqlite3.Connection,
        events: list[dict[str, Any]],
        event_rows: list[Any],
    ) -> None:
        blobs = {
            event["operation_id"]: bytes(row[1])
            for event, row in zip(events, event_rows, strict=True)
            if event["event_type"] in _INTENTS
        }
        rows = conn.execute(
            "SELECT request_id, external_action, stream_id, payload_sha256, receipt_json "
            "FROM requests ORDER BY request_id"
        ).fetchall()
        request_rows = {str(row[0]): row for row in rows}
        event_request_ids = {str(event["request_id"]) for event in events}
        if not event_request_ids.issubset(request_rows):
            raise SegmentStoreConflict("segment event lacks its request summary")
        for row in rows:
            request_id, action, stream_id, payload_sha256, receipt_json = row
            try:
                _require_id("request_id", str(request_id))
            except SegmentStoreError as exc:
                raise SegmentStoreConflict(
                    "segment request summary identity is invalid"
                ) from exc
            if (
                action not in {"append", "manual-seal"}
                or stream_id not in FIXED_STREAMS
            ):
                raise SegmentStoreConflict("segment request summary binding is invalid")
            related = [event for event in events if event["request_id"] == request_id]
            if related and any(event["stream_id"] != stream_id for event in related):
                raise SegmentStoreConflict("segment request summary stream differs")
            allowed_actions = (
                {"open", "append", "auto-seal"}
                if action == "append"
                else {"manual-seal"}
            )
            if related and any(
                event["operation_action"] not in allowed_actions for event in related
            ):
                raise SegmentStoreConflict("segment request summary action differs")
            if action == "append":
                if not _HEX64.fullmatch(str(payload_sha256)):
                    raise SegmentStoreConflict(
                        "segment request summary payload is invalid"
                    )
                append_intent = next(
                    (
                        event
                        for event in events
                        if event["request_id"] == request_id
                        and event["event_type"] == "append_intent"
                    ),
                    None,
                )
                if append_intent is not None:
                    frames = self._decode_frames(blobs[append_intent["operation_id"]])
                    if (
                        len(frames) != 1
                        or frames[0][0].get("payload_sha256") != payload_sha256
                    ):
                        raise SegmentStoreConflict(
                            "segment request summary payload differs"
                        )
            elif payload_sha256 is not None:
                raise SegmentStoreConflict("segment request summary payload is invalid")
            expected = self._derived_request_receipt(
                events, blobs, str(request_id), str(action), str(stream_id)
            )
            if receipt_json is None:
                continue
            try:
                receipt = json.loads(
                    str(receipt_json), object_pairs_hook=_reject_duplicate_pairs
                )
            except (ValueError, TypeError, UnicodeError) as exc:
                raise SegmentStoreConflict(
                    "segment request summary receipt is invalid"
                ) from exc
            if (
                not isinstance(receipt, dict)
                or _canonical_bytes(receipt).decode("utf-8") != receipt_json
            ):
                raise SegmentStoreConflict(
                    "segment request summary receipt is noncanonical"
                )
            keys = _APPEND_RECEIPT_KEYS if action == "append" else _SEAL_RECEIPT_KEYS
            if set(receipt) != keys or expected is None or receipt != expected:
                raise SegmentStoreConflict("segment request summary receipt differs")

    def _events(self) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        return self._validate_event_rows(self._event_rows(), allow_pending=True)

    def _publish_event(
        self,
        *,
        event_type: str,
        request_id: str,
        operation_action: str,
        stream_id: str,
        segment_id: str,
        segment_seq: int,
        source_name: str,
        target_name: str | None,
        file_identity: dict[str, int] | None,
        pre_size: int | None,
        pre_sha256: str | None,
        post_size: int | None,
        post_sha256: str | None,
        intent_bytes: bytes | None = None,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        if self.manifest is None or self.durability_mode is None:
            raise SegmentStoreConflict("segment store is not activated")
        if event_type not in _EVENT_TYPES or operation_action not in _ACTIONS:
            raise SegmentStoreError("invalid event type or operation action")
        spec = FIXED_STREAMS.get(stream_id)
        if spec is None:
            raise SegmentStoreError("unknown fixed segment stream")
        _require_id("request_id", request_id)
        _require_id("segment_id", segment_id)
        operation_id = _operation_id(
            self.manifest["store_id"],
            request_id,
            operation_action,
            stream_id,
            segment_seq,
        )
        if Path(source_name).name != source_name or (
            target_name is not None and Path(target_name).name != target_name
        ):
            raise SegmentStoreError("event file name is not canonical")
        with self._mutating_connection() as conn:
            rows = conn.execute(
                "SELECT event_json, intent_bytes FROM events ORDER BY event_seq"
            ).fetchall()
            events, pending = self._validate_event_rows(rows, allow_pending=True)
            if event_type in _INTENTS and pending is not None:
                raise SegmentStoreConflict("another segment intent is globally pending")
            if event_type not in _INTENTS:
                if pending is None or pending["operation_id"] != operation_id:
                    raise SegmentStoreConflict(
                        "terminal event does not match pending intent"
                    )
            seq = len(events) + 1
            prior_hash = events[-1]["event_sha256"] if events else None
            payload: dict[str, Any] = {
                "schema": "SegmentStoreEvent.v2",
                "protocol": PROTOCOL,
                "store_id": self.manifest["store_id"],
                "root_id": self.capability.root_id,
                "capability_digest": self.capability.capability_digest,
                "registry_sha256": REGISTRY_SHA256,
                "durability_mode": self.durability_mode,
                "event_type": event_type,
                "event_id": f"event_{seq:020d}",
                "request_id": request_id,
                "operation_id": operation_id,
                "operation_action": operation_action,
                "writer_id": self.writer_id,
                "stream_id": stream_id,
                "payload_schema": spec.payload_schema,
                "segment_id": segment_id,
                "source_name": source_name,
                "target_name": target_name,
                "event_seq": seq,
                "segment_seq": int(segment_seq),
                "file_identity": file_identity,
                "pre_size": pre_size,
                "post_size": post_size,
                "intent_size": len(intent_bytes) if intent_bytes is not None else None,
                "pre_sha256": pre_sha256,
                "post_sha256": post_sha256,
                "intent_sha256": _sha(intent_bytes)
                if intent_bytes is not None
                else None,
                "prior_event_sha256": prior_hash,
                "reason_code": reason_code,
            }
            event = _with_hash(payload, "event_sha256")
            conn.execute(
                """
                INSERT INTO events(
                    event_seq, event_id, operation_id, event_type, request_id,
                    operation_action, stream_id, event_json, intent_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seq,
                    event["event_id"],
                    operation_id,
                    event_type,
                    request_id,
                    operation_action,
                    stream_id,
                    _canonical_bytes(event).decode("utf-8"),
                    intent_bytes,
                ),
            )
        return event

    def _publish_terminal_from_intent(
        self,
        intent: dict[str, Any],
        *,
        event_type: str,
        file_identity: dict[str, int] | None = None,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        return self._publish_event(
            event_type=event_type,
            request_id=intent["request_id"],
            operation_action=intent["operation_action"],
            stream_id=intent["stream_id"],
            segment_id=intent["segment_id"],
            segment_seq=intent["segment_seq"],
            source_name=intent["source_name"],
            target_name=intent["target_name"],
            file_identity=file_identity
            if file_identity is not None
            else intent["file_identity"],
            pre_size=intent["pre_size"],
            pre_sha256=intent["pre_sha256"],
            post_size=intent["post_size"],
            post_sha256=intent["post_sha256"],
            reason_code=reason_code,
        )

    def _ensure_request(
        self,
        request_id: str,
        *,
        external_action: str,
        stream_id: str,
        payload_sha256: str | None,
    ) -> dict[str, Any] | None:
        _require_id("request_id", request_id)
        if (
            external_action not in {"append", "manual-seal"}
            or stream_id not in FIXED_STREAMS
        ):
            raise SegmentStoreError("request action/stream is invalid")
        with self._mutating_connection() as conn:
            event_rows = conn.execute(
                "SELECT event_json, intent_bytes FROM events ORDER BY event_seq"
            ).fetchall()
            events, pending = self._validate_event_rows(event_rows, allow_pending=True)
            if pending is not None:
                raise SegmentStoreConflict(
                    "segment intent must recover before a request"
                )
            row = conn.execute(
                "SELECT external_action, stream_id, payload_sha256, receipt_json "
                "FROM requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO requests(request_id, external_action, stream_id, payload_sha256, receipt_json) "
                    "VALUES (?, ?, ?, ?, NULL)",
                    (request_id, external_action, stream_id, payload_sha256),
                )
                return None
            if (
                str(row[0]) != external_action
                or str(row[1]) != stream_id
                or row[2] != payload_sha256
            ):
                raise SegmentStoreConflict("operation_reuse_mismatch")
            if row[3] is not None:
                return json.loads(str(row[3]))
            blobs = {
                event["operation_id"]: bytes(event_row[1])
                for event, event_row in zip(events, event_rows, strict=True)
                if event["event_type"] in _INTENTS
            }
            receipt = self._derived_request_receipt(
                events, blobs, request_id, external_action, stream_id
            )
            if receipt is not None:
                conn.execute(
                    "UPDATE requests SET receipt_json=? WHERE request_id=?",
                    (_canonical_bytes(receipt).decode("utf-8"), request_id),
                )
            return receipt

    def _set_receipt(self, request_id: str, receipt: dict[str, Any]) -> None:
        with self._mutating_connection() as conn:
            row = conn.execute(
                "SELECT receipt_json FROM requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise SegmentStoreConflict("segment request row is missing")
            encoded = _canonical_bytes(receipt).decode("utf-8")
            if row[0] is not None and str(row[0]) != encoded:
                raise SegmentStoreConflict("segment request receipt mismatch")
            conn.execute(
                "UPDATE requests SET receipt_json=? WHERE request_id=?",
                (encoded, request_id),
            )

    def _request_receipt(self, request_id: str) -> dict[str, Any] | None:
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            row = conn.execute(
                "SELECT receipt_json FROM requests WHERE request_id=?", (request_id,)
            ).fetchone()
            return json.loads(str(row[0])) if row and row[0] is not None else None
        finally:
            conn.close()

    def _intent_blobs(self) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for raw, blob in self._event_rows():
            event = json.loads(raw)
            if event["event_type"] in _INTENTS:
                if blob is None:
                    raise SegmentStoreConflict(
                        "segment intent is missing its durable payload"
                    )
                result[event["operation_id"]] = bytes(blob)
        return result

    def _decode_frames(self, data: bytes) -> list[tuple[dict[str, Any], bytes]]:
        if not data or data.startswith(b"\xef\xbb\xbf") or not data.endswith(b"\n"):
            raise SegmentStoreConflict("segment framing is incomplete or has a BOM")
        lines = data.splitlines(keepends=True)
        frames: list[tuple[dict[str, Any], bytes]] = []
        for line in lines:
            if not line.endswith(b"\n") or line in {b"\n", b"\r\n"} or b"\r" in line:
                raise SegmentStoreConflict("segment line framing is invalid")
            try:
                frame = json.loads(
                    line[:-1].decode("utf-8", "strict"),
                    object_pairs_hook=_reject_duplicate_pairs,
                )
            except (UnicodeError, ValueError, TypeError) as exc:
                raise SegmentStoreConflict("segment frame JSON is invalid") from exc
            try:
                canonical = _canonical_bytes(frame) if isinstance(frame, dict) else None
            except (TypeError, ValueError, UnicodeError) as exc:
                raise SegmentStoreConflict(
                    "segment frame JSON domain is invalid"
                ) from exc
            if canonical is None or canonical + b"\n" != line:
                raise SegmentStoreConflict("segment frame is not canonical")
            supplied = frame.get("frame_sha256")
            unhashed = dict(frame)
            unhashed.pop("frame_sha256", None)
            if supplied != _sha(_canonical_bytes(unhashed)):
                raise SegmentStoreConflict("segment frame digest mismatch")
            frames.append((frame, line))
        return frames

    def _common_frame_check(
        self,
        frame: dict[str, Any],
        *,
        spec: StreamSpec,
        segment_id: str,
        segment_seq: int,
    ) -> None:
        assert self.manifest is not None
        expected = {
            "protocol": PROTOCOL,
            "store_id": self.manifest["store_id"],
            "root_id": self.capability.root_id,
            "capability_digest": self.capability.capability_digest,
            "registry_sha256": REGISTRY_SHA256,
            "stream_id": spec.stream_id,
            "payload_schema": spec.payload_schema,
            "segment_id": segment_id,
            "segment_seq": segment_seq,
        }
        if any(frame.get(key) != value for key, value in expected.items()):
            raise SegmentStoreConflict("segment frame binding mismatch")

    def _parse_segment(
        self,
        path: Path,
        *,
        spec: StreamSpec,
        segment_id: str,
        segment_seq: int,
        prior_segment_sha256: str | None,
        sealed: bool,
        append_intents: dict[str, bytes],
    ) -> dict[str, Any]:
        if self.manifest is None:
            raise SegmentStoreConflict("segment store manifest is unavailable")
        probe = _platform_path(path)
        if is_link_or_reparse(probe) or not probe.is_file():
            raise SegmentStoreConflict("declared segment path is unsafe")
        fd = _open_file(path, read_only=True)
        try:
            identity = _file_identity(fd, self.capability)
            data = _read_fd(fd)
        finally:
            os.close(fd)
        frames = self._decode_frames(data)
        header, header_bytes = frames[0]
        if set(header) != _HEADER_KEYS or header.get("schema") != "SegmentHeader.v2":
            raise SegmentStoreConflict("segment header key/schema mismatch")
        if (
            not _positive_int(header.get("segment_seq"))
            or not _digest_or_none(header.get("prior_segment_sha256"))
            or not isinstance(header.get("frame_sha256"), str)
            or _HEX64.fullmatch(header["frame_sha256"]) is None
        ):
            raise SegmentStoreConflict("segment header type evidence is invalid")
        self._common_frame_check(
            header, spec=spec, segment_id=segment_id, segment_seq=segment_seq
        )
        if (
            header.get("canonicalization") != CANONICALIZATION
            or header.get("durability_mode") != self.durability_mode
        ):
            raise SegmentStoreConflict(
                "segment header canonicalization/durability mismatch"
            )
        if header.get("prior_segment_sha256") != prior_segment_sha256:
            raise SegmentStoreConflict("segment prior-segment chain mismatch")
        body = frames[1:]
        footer: dict[str, Any] | None = None
        footer_bytes = b""
        if sealed:
            if not body:
                raise SegmentStoreConflict("sealed segment lacks footer")
            footer, footer_bytes = body[-1]
            body = body[:-1]
        elif body and body[-1][0].get("schema") == "SegmentFooter.v2":
            raise SegmentStoreConflict("active segment has a footer")
        records: list[dict[str, Any]] = []
        previous_frame = header["frame_sha256"]
        expected_segment_record = 1
        for record, record_bytes in body:
            if (
                set(record) != _RECORD_KEYS
                or record.get("schema") != "SegmentRecord.v2"
            ):
                raise SegmentStoreConflict("segment record key/schema mismatch")
            if (
                not _positive_int(record.get("segment_seq"))
                or not _positive_int(record.get("stream_record_seq"))
                or not _positive_int(record.get("segment_record_seq"))
                or any(
                    not isinstance(record.get(key), str)
                    or _HEX64.fullmatch(record[key]) is None
                    for key in (
                        "prior_frame_sha256",
                        "payload_sha256",
                        "frame_sha256",
                    )
                )
            ):
                raise SegmentStoreConflict("segment record type evidence is invalid")
            self._common_frame_check(
                record, spec=spec, segment_id=segment_id, segment_seq=segment_seq
            )
            if record.get("segment_record_seq") != expected_segment_record:
                raise SegmentStoreConflict("segment record sequence mismatch")
            if record.get("prior_frame_sha256") != previous_frame:
                raise SegmentStoreConflict("segment record hash chain mismatch")
            payload = record.get("payload")
            if not isinstance(payload, dict) or set(payload) != {"record", "schema"}:
                raise SegmentStoreConflict("segment payload wrapper mismatch")
            if payload.get("schema") != spec.payload_schema or not isinstance(
                payload.get("record"), dict
            ):
                raise SegmentStoreConflict("segment payload schema mismatch")
            try:
                _validate_json(payload, level=1)
                payload_bytes = _canonical_bytes(payload)
            except (SegmentStoreError, TypeError, ValueError, UnicodeError) as exc:
                raise SegmentStoreConflict(
                    "segment payload JSON domain is invalid"
                ) from exc
            if len(payload_bytes) > MAX_PAYLOAD_BYTES:
                raise SegmentStoreConflict(
                    "segment payload exceeds the fixed byte limit"
                )
            if record.get("payload_sha256") != _sha(payload_bytes):
                raise SegmentStoreConflict("segment payload digest mismatch")
            _require_id("request_id", record["request_id"])
            expected_op = _operation_id(
                self.manifest["store_id"],
                record["request_id"],
                "append",
                spec.stream_id,
                segment_seq,
            )
            if record.get("operation_id") != expected_op:
                raise SegmentStoreConflict("segment record operation mismatch")
            if append_intents.get(expected_op) != record_bytes:
                raise SegmentStoreConflict("segment record differs from durable intent")
            records.append(record)
            previous_frame = record["frame_sha256"]
            expected_segment_record += 1
        if sealed:
            assert footer is not None
            if (
                set(footer) != _FOOTER_KEYS
                or footer.get("schema") != "SegmentFooter.v2"
            ):
                raise SegmentStoreConflict("segment footer key/schema mismatch")
            if (
                not _positive_int(footer.get("segment_seq"))
                or not _positive_int(footer.get("record_count"))
                or not _positive_int(footer.get("prefix_byte_size"))
                or not _positive_int(footer.get("first_stream_record_seq"))
                or not _positive_int(footer.get("final_stream_record_seq"))
                or not _digest_or_none(footer.get("prior_segment_sha256"))
                or any(
                    not isinstance(footer.get(key), str)
                    or _HEX64.fullmatch(footer[key]) is None
                    for key in (
                        "final_data_frame_sha256",
                        "prefix_sha256",
                        "frame_sha256",
                    )
                )
            ):
                raise SegmentStoreConflict("segment footer type evidence is invalid")
            self._common_frame_check(
                footer, spec=spec, segment_id=segment_id, segment_seq=segment_seq
            )
            prefix = data[: -len(footer_bytes)]
            if (
                not records
                or len(records) > MAX_SEGMENT_RECORDS
                or len(prefix) > MAX_OPEN_PREFIX_BYTES
                or footer.get("record_count") != len(records)
                or footer.get("prefix_byte_size") != len(prefix)
                or footer.get("prefix_sha256") != _sha(prefix)
                or footer.get("first_stream_record_seq")
                != records[0]["stream_record_seq"]
                or footer.get("final_stream_record_seq")
                != records[-1]["stream_record_seq"]
                or footer.get("final_data_frame_sha256") != records[-1]["frame_sha256"]
                or footer.get("prior_segment_sha256") != prior_segment_sha256
            ):
                raise SegmentStoreConflict("segment footer evidence mismatch")
        elif len(records) > MAX_SEGMENT_RECORDS or len(data) > MAX_OPEN_PREFIX_BYTES:
            raise SegmentStoreConflict("active segment exceeds fixed protocol limits")
        return {
            "path": path,
            "identity": identity,
            "bytes": data,
            "sha256": _sha(data),
            "header": header,
            "header_bytes": header_bytes,
            "records": records,
            "footer": footer,
        }

    def _physical_streams(
        self,
        events: list[dict[str, Any]],
        pending: dict[str, Any] | None,
    ) -> dict[str, _PhysicalStream]:
        if pending is not None:
            raise SegmentStoreConflict("segment store has a pending intent")
        intent_blobs = self._intent_blobs()
        opened: dict[str, dict[str, Any] | None] = {
            stream: None for stream in FIXED_STREAMS
        }
        sealed: dict[str, list[dict[str, Any]]] = {
            stream: [] for stream in FIXED_STREAMS
        }
        blocked: set[str] = set()
        last_post: dict[tuple[str, str], dict[str, Any]] = {}
        append_success: dict[str, dict[str, Any]] = {}
        for event in events:
            stream = event["stream_id"]
            event_type = event["event_type"]
            if event_type == "opened":
                if opened[stream] is not None:
                    raise SegmentStoreConflict("stream opened a second active segment")
                opened[stream] = {
                    "source_name": event["source_name"],
                    "segment_id": event["segment_id"],
                    "segment_seq": event["segment_seq"],
                    "open_operation_id": event["operation_id"],
                    "identity": event["file_identity"],
                }
                last_post[(stream, event["segment_id"])] = event
            elif event_type == "append_committed":
                append_success[event["operation_id"]] = event
                last_post[(stream, event["segment_id"])] = event
            elif event_type == "sealed":
                active = opened[stream]
                if (
                    active is None
                    or active["source_name"] != event["source_name"]
                    or active["segment_id"] != event["segment_id"]
                ):
                    raise SegmentStoreConflict(
                        "sealed event does not match active segment"
                    )
                sealed[stream].append(
                    {
                        **active,
                        "target_name": event["target_name"],
                        "whole_sha256": event["post_sha256"],
                        "seal_operation_id": event["operation_id"],
                        "identity": event["file_identity"],
                    }
                )
                opened[stream] = None
                last_post[(stream, event["segment_id"])] = event
            elif event_type == "conflict":
                blocked.add(stream)
        views: dict[str, _PhysicalStream] = {}
        used_append_operations: set[str] = set()
        for stream_id, spec in FIXED_STREAMS.items():
            directory = self.segments_path / spec.directory_token
            if is_link_or_reparse(directory) or not directory.is_dir():
                raise SegmentStoreConflict("fixed stream directory is unsafe")
            if stream_id in blocked:
                views[stream_id] = _PhysicalStream(
                    stream_id, sealed[stream_id], opened[stream_id], [], True
                )
                continue
            expected_names = {item["target_name"] for item in sealed[stream_id]}
            open_segment = opened[stream_id]
            if open_segment is not None:
                expected_names.add(open_segment["source_name"])
            actual_names = {path.name for path in directory.iterdir()}
            if actual_names != expected_names:
                raise SegmentStoreConflict(
                    "stream directory has undeclared or missing files"
                )
            parsed_sealed: list[dict[str, Any]] = []
            all_records: list[dict[str, Any]] = []
            prior_digest: str | None = None
            expected_segment_seq = 1
            expected_stream_record_seq = 1
            for declaration in sealed[stream_id]:
                if declaration["segment_seq"] != expected_segment_seq:
                    raise SegmentStoreConflict("sealed segment sequence has a gap")
                path = directory / declaration["target_name"]
                parsed = self._parse_segment(
                    path,
                    spec=spec,
                    segment_id=declaration["segment_id"],
                    segment_seq=declaration["segment_seq"],
                    prior_segment_sha256=prior_digest,
                    sealed=True,
                    append_intents=intent_blobs,
                )
                expected_name = (
                    f"{declaration['segment_seq']:020d}.{declaration['segment_id']}."
                    f"{parsed['sha256']}.sealed.jsonl"
                )
                if (
                    path.name != expected_name
                    or parsed["sha256"] != declaration["whole_sha256"]
                ):
                    raise SegmentStoreConflict("sealed segment name/digest mismatch")
                if parsed["identity"] != declaration["identity"]:
                    raise SegmentStoreConflict("sealed segment identity mismatch")
                if (
                    parsed["header_bytes"]
                    != intent_blobs[declaration["open_operation_id"]]
                ):
                    raise SegmentStoreConflict(
                        "segment header differs from open intent"
                    )
                footer_line = parsed["bytes"].splitlines(keepends=True)[-1]
                if footer_line != intent_blobs[declaration["seal_operation_id"]]:
                    raise SegmentStoreConflict(
                        "segment footer differs from seal intent"
                    )
                for record in parsed["records"]:
                    if record["stream_record_seq"] != expected_stream_record_seq:
                        raise SegmentStoreConflict("stream record sequence has a gap")
                    expected_stream_record_seq += 1
                    used_append_operations.add(record["operation_id"])
                all_records.extend(parsed["records"])
                parsed_sealed.append(parsed)
                prior_digest = parsed["sha256"]
                expected_segment_seq += 1
            parsed_active: dict[str, Any] | None = None
            active = opened[stream_id]
            if active is not None:
                if active["segment_seq"] != expected_segment_seq:
                    raise SegmentStoreConflict("active segment sequence has a gap")
                expected_active_name = (
                    f"{active['segment_seq']:020d}.{active['segment_id']}.open.jsonl"
                )
                if active["source_name"] != expected_active_name:
                    raise SegmentStoreConflict("active segment name mismatch")
                parsed_active = self._parse_segment(
                    directory / active["source_name"],
                    spec=spec,
                    segment_id=active["segment_id"],
                    segment_seq=active["segment_seq"],
                    prior_segment_sha256=prior_digest,
                    sealed=False,
                    append_intents=intent_blobs,
                )
                if parsed_active["identity"] != active["identity"]:
                    raise SegmentStoreConflict("active segment identity mismatch")
                if (
                    parsed_active["header_bytes"]
                    != intent_blobs[active["open_operation_id"]]
                ):
                    raise SegmentStoreConflict("active header differs from open intent")
                terminal = last_post[(stream_id, active["segment_id"])]
                if (
                    terminal["post_size"] != len(parsed_active["bytes"])
                    or terminal["post_sha256"] != parsed_active["sha256"]
                ):
                    raise SegmentStoreConflict(
                        "active segment differs from last committed event"
                    )
                for record in parsed_active["records"]:
                    if record["stream_record_seq"] != expected_stream_record_seq:
                        raise SegmentStoreConflict("stream record sequence has a gap")
                    expected_stream_record_seq += 1
                    used_append_operations.add(record["operation_id"])
                all_records.extend(parsed_active["records"])
            views[stream_id] = _PhysicalStream(
                stream_id=stream_id,
                sealed=parsed_sealed,
                active=parsed_active,
                records=all_records,
            )
        committed_unblocked = {
            operation_id
            for operation_id, event in append_success.items()
            if event["stream_id"] not in blocked
        }
        if used_append_operations != committed_unblocked:
            raise SegmentStoreConflict(
                "committed append events and physical frames disagree"
            )
        return views

    def _audit_locked(self) -> dict[str, Any]:
        self._load_manifest()
        self._verify_database_readonly()
        events, pending = self._events()
        if pending is not None:
            return {
                "activated": True,
                "status": "incomplete_blocked",
                "events": len(events),
                "pending_operation_id": pending["operation_id"],
            }
        views = self._physical_streams(events, pending)
        blocked = sorted(stream for stream, view in views.items() if view.blocked)
        return {
            "activated": True,
            "status": "conflict" if blocked else "ok",
            "events": len(events),
            "blocked_streams": blocked,
            "streams": {
                stream: {
                    "records": len(view.records),
                    "sealed_segments": len(view.sealed),
                    "active": view.active is not None,
                    "blocked": view.blocked,
                }
                for stream, view in views.items()
            },
        }

    def audit(self) -> dict[str, Any]:
        with self._locked():
            return self._audit_locked()

    @classmethod
    def audit_readonly(cls, root: Path) -> dict[str, Any]:
        try:
            capability = load_capability(Path(root))
        except (OSError, ValueError, StorageCapabilityError) as exc:
            raise SegmentStoreConflict(
                "storage capability revalidation failed"
            ) from exc
        manifest = Path(capability.canonical_root) / RESERVED / "segment_store.json"
        if not _path_lexists(manifest):
            return {"activated": False, "status": "absent", "events": 0}
        return cls(Path(capability.canonical_root)).audit()

    def read_records(
        self, stream_id: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        if stream_id not in FIXED_STREAMS:
            raise SegmentStoreError("unknown fixed segment stream")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0
        ):
            raise SegmentStoreError("read limit must be a positive integer")
        with self._locked():
            self._load_manifest()
            self._verify_database_readonly()
            events, pending = self._events()
            views = self._physical_streams(events, pending)
            view = views[stream_id]
            if view.blocked:
                raise SegmentStoreConflict(
                    "segment stream is blocked by terminal conflict"
                )
            result = [
                json.loads(_canonical_bytes(row["payload"]["record"]))
                for row in view.records
            ]
            return result[-limit:] if limit is not None else result

    def _views_locked(self) -> tuple[list[dict[str, Any]], dict[str, _PhysicalStream]]:
        self._load_manifest()
        self._verify_database_readonly()
        events, pending = self._events()
        return events, self._physical_streams(events, pending)

    def _open_segment_locked(self, stream_id: str, request_id: str) -> None:
        assert self.manifest is not None and self.durability_mode is not None
        _events, views = self._views_locked()
        view = views[stream_id]
        if view.blocked:
            raise SegmentStoreConflict("segment stream is blocked by terminal conflict")
        if view.active is not None:
            return
        segment_seq = len(view.sealed) + 1
        prior_digest = view.sealed[-1]["sha256"] if view.sealed else None
        segment_id = "seg_" + uuid.uuid4().hex
        spec = FIXED_STREAMS[stream_id]
        header = {
            "schema": "SegmentHeader.v2",
            "protocol": PROTOCOL,
            "canonicalization": CANONICALIZATION,
            "store_id": self.manifest["store_id"],
            "root_id": self.capability.root_id,
            "capability_digest": self.capability.capability_digest,
            "registry_sha256": REGISTRY_SHA256,
            "durability_mode": self.durability_mode,
            "stream_id": stream_id,
            "payload_schema": spec.payload_schema,
            "segment_id": segment_id,
            "segment_seq": segment_seq,
            "prior_segment_sha256": prior_digest,
        }
        intended = _frame_bytes(header)
        source_name = f"{segment_seq:020d}.{segment_id}.open.jsonl"
        intent = self._publish_event(
            event_type="open_intent",
            request_id=request_id,
            operation_action="open",
            stream_id=stream_id,
            segment_id=segment_id,
            segment_seq=segment_seq,
            source_name=source_name,
            target_name=None,
            file_identity=None,
            pre_size=None,
            pre_sha256=None,
            post_size=len(intended),
            post_sha256=_sha(intended),
            intent_bytes=intended,
        )
        path = self.segments_path / spec.directory_token / source_name
        self._fault("after_open_intent", path)
        fd = _open_file(path, create=True)
        try:
            _write_all(fd, intended)
            _sync_fd(fd)
            identity = _file_identity(fd, self.capability)
            if _read_fd(fd) != intended:
                raise SegmentStoreConflict("opened segment header verification failed")
        finally:
            os.close(fd)
        _sync_directory(path.parent, self.durability_mode)
        self._fault("after_open_fsync", path)
        self._publish_terminal_from_intent(
            intent, event_type="opened", file_identity=identity
        )

    def _append_receipt(
        self,
        record: dict[str, Any],
        committed_event: dict[str, Any],
    ) -> dict[str, Any]:
        assert self.manifest is not None
        return {
            "schema": "AppendReceipt.v2",
            "store_id": self.manifest["store_id"],
            "stream_id": record["stream_id"],
            "request_id": record["request_id"],
            "operation_id": record["operation_id"],
            "segment_id": record["segment_id"],
            "frame_sha256": record["frame_sha256"],
            "committed_event_sha256": committed_event["event_sha256"],
            "segment_seq": record["segment_seq"],
            "stream_record_seq": record["stream_record_seq"],
            "segment_record_seq": record["segment_record_seq"],
        }

    def _seal_receipt(
        self,
        intent: dict[str, Any],
        sealed_event: dict[str, Any],
    ) -> dict[str, Any]:
        assert self.manifest is not None and self.durability_mode is not None
        return {
            "schema": "SealReceipt.v2",
            "store_id": self.manifest["store_id"],
            "stream_id": intent["stream_id"],
            "request_id": intent["request_id"],
            "operation_id": intent["operation_id"],
            "segment_id": intent["segment_id"],
            "final_name": intent["target_name"],
            "whole_file_sha256": intent["post_sha256"],
            "sealed_event_sha256": sealed_event["event_sha256"],
            "durability_mode": self.durability_mode,
            "segment_seq": intent["segment_seq"],
        }

    def _append_frame_locked(
        self,
        stream_id: str,
        wrapper: dict[str, Any],
        payload_bytes: bytes,
        request_id: str,
    ) -> dict[str, Any]:
        assert self.manifest is not None
        _events, views = self._views_locked()
        view = views[stream_id]
        if view.blocked or view.active is None:
            raise SegmentStoreConflict(
                "segment stream is blocked or lacks active segment"
            )
        active = view.active
        segment_seq = int(active["header"]["segment_seq"])
        segment_id = str(active["header"]["segment_id"])
        operation_id = _operation_id(
            self.manifest["store_id"], request_id, "append", stream_id, segment_seq
        )
        previous_hash = (
            active["records"][-1]["frame_sha256"]
            if active["records"]
            else active["header"]["frame_sha256"]
        )
        record_value = {
            "schema": "SegmentRecord.v2",
            "protocol": PROTOCOL,
            "store_id": self.manifest["store_id"],
            "root_id": self.capability.root_id,
            "capability_digest": self.capability.capability_digest,
            "registry_sha256": REGISTRY_SHA256,
            "stream_id": stream_id,
            "payload_schema": FIXED_STREAMS[stream_id].payload_schema,
            "segment_id": segment_id,
            "request_id": request_id,
            "operation_id": operation_id,
            "segment_seq": segment_seq,
            "stream_record_seq": len(view.records) + 1,
            "segment_record_seq": len(active["records"]) + 1,
            "prior_frame_sha256": previous_hash,
            "payload_sha256": _sha(payload_bytes),
            "payload": wrapper,
        }
        intended = _frame_bytes(record_value)
        if (
            len(active["records"]) >= MAX_SEGMENT_RECORDS
            or len(active["bytes"]) + len(intended) > MAX_OPEN_PREFIX_BYTES
        ):
            self._seal_segment_locked(stream_id, request_id, action="auto-seal")
            self._open_segment_locked(stream_id, request_id)
            return self._append_frame_locked(
                stream_id, wrapper, payload_bytes, request_id
            )
        path = Path(active["path"])
        pre = bytes(active["bytes"])
        post = pre + intended
        intent = self._publish_event(
            event_type="append_intent",
            request_id=request_id,
            operation_action="append",
            stream_id=stream_id,
            segment_id=segment_id,
            segment_seq=segment_seq,
            source_name=path.name,
            target_name=None,
            file_identity=active["identity"],
            pre_size=len(pre),
            pre_sha256=_sha(pre),
            post_size=len(post),
            post_sha256=_sha(post),
            intent_bytes=intended,
        )
        self._fault("after_append_intent", path)
        fd = _open_file(path)
        try:
            if (
                _file_identity(fd, self.capability) != active["identity"]
                or _read_fd(fd) != pre
            ):
                self._publish_terminal_from_intent(
                    intent, event_type="conflict", reason_code="preimage_mismatch"
                )
                raise SegmentStoreConflict("append preimage mismatch")
            os.lseek(fd, 0, os.SEEK_END)
            _write_all(fd, intended)
            self._fault("after_append_bytes", path)
            _sync_fd(fd)
            self._fault("after_append_fsync", path)
            if _read_fd(fd) != post:
                raise SegmentStoreConflict("append postimage verification failed")
            identity = _file_identity(fd, self.capability)
        finally:
            os.close(fd)
        committed = self._publish_terminal_from_intent(
            intent, event_type="append_committed", file_identity=identity
        )
        record = _with_hash(record_value, "frame_sha256")
        receipt = self._append_receipt(record, committed)
        self._set_receipt(request_id, receipt)
        return receipt

    def append(
        self,
        stream_id: str,
        record: object,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        wrapper, payload_bytes = _payload_wrapper(stream_id, record)
        _require_id("request_id", request_id)
        with self._locked():
            self._load_manifest()
            self._recover_global_locked()
            existing = self._ensure_request(
                request_id,
                external_action="append",
                stream_id=stream_id,
                payload_sha256=_sha(payload_bytes),
            )
            if existing is not None:
                return existing
            self._open_segment_locked(stream_id, request_id)
            return self._append_frame_locked(
                stream_id, wrapper, payload_bytes, request_id
            )

    def _seal_segment_locked(
        self,
        stream_id: str,
        request_id: str,
        *,
        action: str,
    ) -> dict[str, Any]:
        assert self.manifest is not None and self.durability_mode is not None
        if action not in {"auto-seal", "manual-seal"}:
            raise SegmentStoreError("invalid seal action")
        _events, views = self._views_locked()
        view = views[stream_id]
        if view.blocked or view.active is None:
            raise SegmentStoreConflict(
                "segment stream is blocked or has no active segment"
            )
        active = view.active
        records = active["records"]
        if not records:
            raise SegmentStoreConflict("empty active segment cannot be sealed")
        header = active["header"]
        prefix = bytes(active["bytes"])
        footer_value = {
            "schema": "SegmentFooter.v2",
            "protocol": PROTOCOL,
            "store_id": self.manifest["store_id"],
            "root_id": self.capability.root_id,
            "capability_digest": self.capability.capability_digest,
            "registry_sha256": REGISTRY_SHA256,
            "stream_id": stream_id,
            "payload_schema": FIXED_STREAMS[stream_id].payload_schema,
            "segment_id": header["segment_id"],
            "segment_seq": header["segment_seq"],
            "record_count": len(records),
            "prefix_byte_size": len(prefix),
            "first_stream_record_seq": records[0]["stream_record_seq"],
            "final_stream_record_seq": records[-1]["stream_record_seq"],
            "final_data_frame_sha256": records[-1]["frame_sha256"],
            "prefix_sha256": _sha(prefix),
            "prior_segment_sha256": header["prior_segment_sha256"],
        }
        footer = _frame_bytes(footer_value)
        whole = prefix + footer
        digest = _sha(whole)
        source = Path(active["path"])
        target_name = (
            f"{header['segment_seq']:020d}.{header['segment_id']}.{digest}.sealed.jsonl"
        )
        target = source.parent / target_name
        intent = self._publish_event(
            event_type="seal_intent",
            request_id=request_id,
            operation_action=action,
            stream_id=stream_id,
            segment_id=header["segment_id"],
            segment_seq=header["segment_seq"],
            source_name=source.name,
            target_name=target_name,
            file_identity=active["identity"],
            pre_size=len(prefix),
            pre_sha256=_sha(prefix),
            post_size=len(whole),
            post_sha256=digest,
            intent_bytes=footer,
        )
        self._fault("after_seal_intent", source)
        fd = _open_file(source)
        try:
            if (
                _file_identity(fd, self.capability) != active["identity"]
                or _read_fd(fd) != prefix
            ):
                self._publish_terminal_from_intent(
                    intent, event_type="conflict", reason_code="preimage_mismatch"
                )
                raise SegmentStoreConflict("seal preimage mismatch")
            os.lseek(fd, 0, os.SEEK_END)
            _write_all(fd, footer)
            self._fault("after_footer_bytes", source)
            _sync_fd(fd)
            self._fault("after_footer_fsync", source)
            if _read_fd(fd) != whole:
                raise SegmentStoreConflict("seal post-footer verification failed")
            expected_identity = _file_identity(fd, self.capability)
            self._fault("before_seal_rename", source)
            try:
                _move_no_replace(source, target, self.durability_mode)
            except FileExistsError:
                self._publish_terminal_from_intent(
                    intent, event_type="conflict", reason_code="target_exists"
                )
                raise SegmentStoreConflict("seal target exists")
            self._fault("after_seal_rename", target)
            target_fd = _open_file(target, read_only=True)
            try:
                target_identity = _file_identity(target_fd, self.capability)
                target_bytes = _read_fd(target_fd)
            finally:
                os.close(target_fd)
            if target_identity != expected_identity:
                self._publish_terminal_from_intent(
                    intent, event_type="conflict", reason_code="file_identity_mismatch"
                )
                raise SegmentStoreConflict("file_identity_mismatch")
            if target_bytes != whole:
                self._publish_terminal_from_intent(
                    intent, event_type="conflict", reason_code="preimage_mismatch"
                )
                raise SegmentStoreConflict("sealed target bytes mismatch")
        finally:
            os.close(fd)
        _sync_directory(source.parent, self.durability_mode)
        sealed_event = self._publish_terminal_from_intent(
            intent, event_type="sealed", file_identity=target_identity
        )
        receipt = self._seal_receipt(intent, sealed_event)
        if action == "manual-seal":
            self._set_receipt(request_id, receipt)
        return receipt

    def seal(self, stream_id: str, *, request_id: str) -> dict[str, Any]:
        if stream_id not in FIXED_STREAMS:
            raise SegmentStoreError("unknown fixed segment stream")
        _require_id("request_id", request_id)
        with self._locked():
            self._load_manifest()
            self._recover_global_locked()
            existing = self._ensure_request(
                request_id,
                external_action="manual-seal",
                stream_id=stream_id,
                payload_sha256=None,
            )
            if existing is not None:
                return existing
            return self._seal_segment_locked(
                stream_id, request_id, action="manual-seal"
            )

    def _pending_with_blob(self) -> tuple[dict[str, Any] | None, bytes | None]:
        rows = self._event_rows()
        events, pending = self._validate_event_rows(rows, allow_pending=True)
        if pending is None:
            return None, None
        raw, blob = rows[-1]
        if json.loads(raw)["operation_id"] != pending["operation_id"] or blob is None:
            raise SegmentStoreConflict("pending segment intent blob is unavailable")
        return pending, bytes(blob)

    def _request_external_action(self, request_id: str) -> str | None:
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            row = conn.execute(
                "SELECT external_action FROM requests WHERE request_id=?", (request_id,)
            ).fetchone()
            return str(row[0]) if row else None
        finally:
            conn.close()

    def _conflict_pending(self, intent: dict[str, Any], reason: str) -> None:
        self._publish_terminal_from_intent(
            intent, event_type="conflict", reason_code=reason
        )
        raise SegmentStoreConflict(reason)

    def _recover_open_locked(self, intent: dict[str, Any], intended: bytes) -> None:
        assert self.durability_mode is not None
        path = (
            self.segments_path
            / FIXED_STREAMS[intent["stream_id"]].directory_token
            / intent["source_name"]
        )
        if not _path_lexists(path):
            fd = _open_file(path, create=True)
            try:
                _write_all(fd, intended)
                _sync_fd(fd)
                if _read_fd(fd) != intended:
                    self._conflict_pending(intent, "preimage_mismatch")
                identity = _file_identity(fd, self.capability)
            finally:
                os.close(fd)
            _sync_directory(path.parent, self.durability_mode)
        else:
            fd = _open_file(path)
            try:
                data = _read_fd(fd)
                if (
                    len(data) != intent["post_size"]
                    or _sha(data) != intent["post_sha256"]
                    or data != intended
                ):
                    self._conflict_pending(intent, "preimage_mismatch")
                _sync_fd(fd)
                identity = _file_identity(fd, self.capability)
            finally:
                os.close(fd)
            _sync_directory(path.parent, self.durability_mode)
        self._publish_terminal_from_intent(
            intent, event_type="opened", file_identity=identity
        )

    def _recover_append_locked(self, intent: dict[str, Any], intended: bytes) -> None:
        path = (
            self.segments_path
            / FIXED_STREAMS[intent["stream_id"]].directory_token
            / intent["source_name"]
        )
        if not _path_lexists(path):
            self._conflict_pending(intent, "namespace_ambiguous")
        fd = _open_file(path)
        try:
            identity = _file_identity(fd, self.capability)
            if identity != intent["file_identity"]:
                self._conflict_pending(intent, "file_identity_mismatch")
            data = _read_fd(fd)
            pre_size = int(intent["pre_size"])
            post_size = int(intent["post_size"])
            if len(data) < pre_size or _sha(data[:pre_size]) != intent["pre_sha256"]:
                self._conflict_pending(intent, "tail_mismatch")
            tail = data[pre_size:]
            if len(data) == pre_size:
                os.lseek(fd, 0, os.SEEK_END)
                _write_all(fd, intended)
            elif (
                len(data) == post_size
                and _sha(data) == intent["post_sha256"]
                and tail == intended
            ):
                pass
            elif _is_strict_intended_prefix(tail, intended):
                os.ftruncate(fd, pre_size)
                _sync_fd(fd)
                os.lseek(fd, 0, os.SEEK_END)
                _write_all(fd, intended)
            else:
                self._conflict_pending(intent, "tail_mismatch")
            _sync_fd(fd)
            final = _read_fd(fd)
            if len(final) != post_size or _sha(final) != intent["post_sha256"]:
                self._conflict_pending(intent, "tail_mismatch")
        finally:
            os.close(fd)
        committed = self._publish_terminal_from_intent(
            intent, event_type="append_committed", file_identity=identity
        )
        record = self._decode_frames(intended)[0][0]
        receipt = self._append_receipt(record, committed)
        if self._request_external_action(intent["request_id"]) == "append":
            self._set_receipt(intent["request_id"], receipt)

    def _finish_seal_move(
        self,
        intent: dict[str, Any],
        source: Path,
        target: Path,
        expected_identity: dict[str, int],
    ) -> dict[str, int]:
        assert self.durability_mode is not None
        try:
            _move_no_replace(source, target, self.durability_mode)
        except FileExistsError:
            self._conflict_pending(intent, "target_exists")
        target_fd = _open_file(target, read_only=True)
        try:
            target_identity = _file_identity(target_fd, self.capability)
            target_data = _read_fd(target_fd)
        finally:
            os.close(target_fd)
        if target_identity != expected_identity:
            self._conflict_pending(intent, "file_identity_mismatch")
        if (
            len(target_data) != intent["post_size"]
            or _sha(target_data) != intent["post_sha256"]
        ):
            self._conflict_pending(intent, "preimage_mismatch")
        _sync_directory(target.parent, self.durability_mode)
        return target_identity

    def _recover_seal_locked(self, intent: dict[str, Any], intended: bytes) -> None:
        assert self.durability_mode is not None
        directory = (
            self.segments_path / FIXED_STREAMS[intent["stream_id"]].directory_token
        )
        source = directory / intent["source_name"]
        target = directory / intent["target_name"]
        source_exists = _path_lexists(source)
        target_exists = _path_lexists(target)
        if source_exists and target_exists:
            self._conflict_pending(intent, "namespace_ambiguous")
        if not source_exists and not target_exists:
            self._conflict_pending(intent, "namespace_ambiguous")
        if target_exists:
            if self.durability_mode == DURABILITY_WINDOWS:
                self._conflict_pending(intent, "rename_result_ambiguous")
            fd = _open_file(target, read_only=True)
            try:
                identity = _file_identity(fd, self.capability)
                data = _read_fd(fd)
            finally:
                os.close(fd)
            if (
                identity != intent["file_identity"]
                or len(data) != intent["post_size"]
                or _sha(data) != intent["post_sha256"]
            ):
                self._conflict_pending(intent, "preimage_mismatch")
            _sync_directory(directory, self.durability_mode)
            target_identity = identity
        else:
            fd = _open_file(source)
            try:
                identity = _file_identity(fd, self.capability)
                if identity != intent["file_identity"]:
                    self._conflict_pending(intent, "file_identity_mismatch")
                data = _read_fd(fd)
                pre_size = int(intent["pre_size"])
                if (
                    len(data) < pre_size
                    or _sha(data[:pre_size]) != intent["pre_sha256"]
                ):
                    self._conflict_pending(intent, "tail_mismatch")
                tail = data[pre_size:]
                if len(data) == pre_size:
                    os.lseek(fd, 0, os.SEEK_END)
                    _write_all(fd, intended)
                elif (
                    len(data) == intent["post_size"]
                    and _sha(data) == intent["post_sha256"]
                    and tail == intended
                ):
                    pass
                elif _is_strict_intended_prefix(tail, intended):
                    os.ftruncate(fd, pre_size)
                    _sync_fd(fd)
                    os.lseek(fd, 0, os.SEEK_END)
                    _write_all(fd, intended)
                else:
                    self._conflict_pending(intent, "tail_mismatch")
                _sync_fd(fd)
                if _sha(_read_fd(fd)) != intent["post_sha256"]:
                    self._conflict_pending(intent, "tail_mismatch")
                expected_identity = _file_identity(fd, self.capability)
            finally:
                os.close(fd)
            target_identity = self._finish_seal_move(
                intent, source, target, expected_identity
            )
        sealed_event = self._publish_terminal_from_intent(
            intent, event_type="sealed", file_identity=target_identity
        )
        if self._request_external_action(intent["request_id"]) == "manual-seal":
            self._set_receipt(
                intent["request_id"], self._seal_receipt(intent, sealed_event)
            )

    def _recover_global_locked(self) -> None:
        self._verify_database_readonly()
        intent, blob = self._pending_with_blob()
        if intent is None:
            return
        assert blob is not None
        if len(blob) != intent["intent_size"] or _sha(blob) != intent["intent_sha256"]:
            self._conflict_pending(intent, "intent_blob_mismatch")
        if intent["event_type"] == "open_intent":
            self._recover_open_locked(intent, blob)
        elif intent["event_type"] == "append_intent":
            self._recover_append_locked(intent, blob)
        elif intent["event_type"] == "seal_intent":
            self._recover_seal_locked(intent, blob)
        else:  # pragma: no cover - event validator prevents this
            raise SegmentStoreConflict("unknown pending segment intent")

    def recover(self) -> dict[str, Any]:
        with self._locked():
            self._load_manifest()
            self._recover_global_locked()
            return self._audit_locked()
