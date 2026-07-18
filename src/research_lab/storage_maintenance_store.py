"""Durable, synthetic-only quarantine/restore storage maintenance.

Every filesystem mutation is fenced by one non-reentrant OS lock, a freshly
validated root capability, and the exact live SQLite writer lease.  The event
journal is append-only and cryptographically bound to the activated root.
"""

from __future__ import annotations

import hashlib
import ctypes
import json
import os
import sqlite3
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from src.research_lab.ownership import ProcessIdentity
from src.research_lab.storage_capability import (
    RESERVED,
    StorageCapabilityError,
    StorageRootCapability,
    canonical_json,
    ensure_safe_parent,
    filesystem_identity,
    is_link_or_reparse,
    load_capability,
    parse_positive_budget,
    safe_relative_file,
    validate_relative_path_text,
)

try:
    import msvcrt
except ImportError:  # pragma: no cover - platform branch
    msvcrt = None
try:
    import fcntl
except ImportError:  # pragma: no cover - platform branch
    fcntl = None


class StorageMaintenanceConflict(RuntimeError):
    """The requested mutation cannot be proved safe and authoritative."""


@dataclass(frozen=True)
class MaintenanceLease:
    owner_id: str
    identity: ProcessIdentity
    fence: int
    mutation_seq: int
    expires_at: float


@dataclass(frozen=True)
class FileSnapshot:
    relative_path: str
    identity: dict[str, int]
    content_digest: str

    def payload(self, capability: StorageRootCapability) -> dict[str, Any]:
        return {
            "schema": "StorageFileInventory.v2",
            "root_id": capability.root_id,
            "capability_digest": capability.capability_digest,
            "canonical_root": capability.canonical_root,
            "filesystem_identity": capability.filesystem_identity,
            "relative_path": self.relative_path,
            "identity": self.identity,
            "content_digest": self.content_digest,
        }


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _stat_identity(info: os.stat_result) -> dict[str, int]:
    return {
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
    }


def _path_identity(path: Path) -> dict[str, int]:
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or is_link_or_reparse(path):
        raise StorageMaintenanceConflict("storage item is not a no-follow regular file")
    return _stat_identity(info)


def _fd_digest(fd: int) -> str:
    hasher = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while block := os.read(fd, 1024 * 1024):
        hasher.update(block)
    os.lseek(fd, 0, os.SEEK_SET)
    return "sha256:" + hasher.hexdigest()


def _path_digest(path: Path) -> str:
    fd = _open_nofollow_read(path)
    try:
        return _fd_digest(fd)
    finally:
        os.close(fd)


def _open_nofollow_read(path: Path) -> int:
    """Open a no-follow read handle that still permits atomic rename on Windows."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if os.name != "nt":
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return os.open(path, flags)
    if msvcrt is None:  # pragma: no cover - guarded platform branch
        raise StorageMaintenanceConflict("Windows file handle support is unavailable")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        ctypes.c_wchar_p(str(path)),
        0x80000000,  # GENERIC_READ
        0x00000001 | 0x00000002 | 0x00000004,  # SHARE_READ|WRITE|DELETE
        None,
        3,  # OPEN_EXISTING
        0x00200000 | 0x08000000,  # OPEN_REPARSE_POINT|SEQUENTIAL_SCAN
        None,
    )
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError(ctypes.get_last_error(), "CreateFileW failed", str(path))
    try:
        return msvcrt.open_osfhandle(int(handle), flags)
    except Exception:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        raise


_LOCAL_LOCK = threading.Lock()


def _open_lock_nofollow(path: Path) -> tuple[Any, dict[str, int]]:
    before = path.lstat()
    if is_link_or_reparse(path) or not stat.S_ISREG(before.st_mode):
        raise StorageMaintenanceConflict("storage operation lock path is unsafe")
    if os.name != "nt":
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
    else:
        if msvcrt is None:  # pragma: no cover - guarded platform branch
            raise StorageMaintenanceConflict("Windows lock handle support is unavailable")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.restype = ctypes.c_void_p
        raw_handle = create_file(
            ctypes.c_wchar_p(str(path)), 0xC0000000,
            0x00000001 | 0x00000002, None, 3, 0x00200000, None,
        )
        if raw_handle in (None, ctypes.c_void_p(-1).value):
            raise OSError(ctypes.get_last_error(), "CreateFileW failed", str(path))
        try:
            fd = msvcrt.open_osfhandle(
                int(raw_handle), os.O_RDWR | getattr(os, "O_BINARY", 0)
            )
        except Exception:
            kernel32.CloseHandle(ctypes.c_void_p(raw_handle))
            raise
    handle = os.fdopen(fd, "r+b", closefd=True)
    opened = os.fstat(handle.fileno())
    if _stat_identity(opened) != _stat_identity(before) or is_link_or_reparse(path):
        handle.close()
        raise StorageMaintenanceConflict("storage operation lock identity changed")
    return handle, _stat_identity(opened)


@contextmanager
def _os_lock(path: Path) -> Iterator[None]:
    if not _LOCAL_LOCK.acquire(blocking=False):
        raise StorageMaintenanceConflict("storage operation lock is already held")
    handle = None
    locked = False
    try:
        handle, lock_identity = _open_lock_nofollow(path)
        try:
            if os.name == "nt":
                if msvcrt is None:
                    raise StorageMaintenanceConflict("Windows OS locking is unavailable")
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                if fcntl is None:
                    raise StorageMaintenanceConflict("POSIX OS locking is unavailable")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            if _path_identity(path) != lock_identity:
                raise StorageMaintenanceConflict("storage operation lock identity changed")
        except (OSError, BlockingIOError) as exc:
            raise StorageMaintenanceConflict("cannot acquire storage operation lock") from exc
        yield
    finally:
        if handle is not None:
            try:
                if locked and os.name == "nt" and msvcrt is not None:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                elif locked and fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        _LOCAL_LOCK.release()


class StorageMaintenanceStore:
    _EVENT_TYPES = {"planned", "claimed", "quarantined", "restored", "conflict", "failed"}
    _TRANSITIONS = {
        None: {"planned"},
        "planned": {"claimed", "conflict", "failed"},
        "claimed": {"quarantined", "conflict", "failed"},
        "quarantined": {"restored", "conflict", "failed"},
        "restored": set(),
        "conflict": set(),
        "failed": set(),
    }

    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.capability = load_capability(root)
        self.root = Path(self.capability.canonical_root)
        self.control = self.root / RESERVED
        self.path = self.control / "operations.sqlite3"
        self.lock_path = self.control / "locks" / "operation.lock"
        self._clock = clock

    def _reload_capability(self) -> StorageRootCapability:
        try:
            current = load_capability(self.root)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise StorageMaintenanceConflict("storage capability revalidation failed") from exc
        if current != self.capability:
            raise StorageMaintenanceConflict("storage capability changed after construction")
        return current

    def _meta_payload(self) -> dict[str, str]:
        return {
            "schema": "StorageMaintenanceJournal.v2",
            "root_id": self.capability.root_id,
            "capability_digest": self.capability.capability_digest,
            "canonical_root": self.capability.canonical_root,
            "filesystem_identity_json": canonical_json(self.capability.filesystem_identity),
        }

    def activate(self) -> None:
        with _os_lock(self.lock_path):
            self._reload_capability()
            if os.path.lexists(self.path):
                if is_link_or_reparse(self.path) or not self.path.is_file():
                    raise StorageMaintenanceConflict("operation journal path is unsafe")
                conn = self._connect_unverified()
                try:
                    self._verify_store_binding(conn)
                finally:
                    conn.close()
                return
            conn = sqlite3.connect(self.path)
            try:
                conn.executescript(
                    """
                    PRAGMA foreign_keys=ON;
                    CREATE TABLE store_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE lease(
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                        owner_id TEXT,pid INTEGER,started_at REAL,executable TEXT,
                        command_digest TEXT,expires_at REAL,fence INTEGER NOT NULL,
                        mutation_seq INTEGER NOT NULL
                    );
                    INSERT INTO lease VALUES(1,NULL,NULL,NULL,NULL,NULL,NULL,0,0);
                    CREATE TABLE item_events(
                        event_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,
                        item_id TEXT NOT NULL,event_seq INTEGER NOT NULL,
                        prior_event_hash TEXT NOT NULL,event_hash TEXT NOT NULL UNIQUE,
                        event_type TEXT NOT NULL,relative_path TEXT NOT NULL,
                        quarantine_path TEXT NOT NULL,content_digest TEXT NOT NULL,
                        inventory_digest TEXT NOT NULL,inventory_json TEXT NOT NULL,
                        capability_digest TEXT NOT NULL,root_id TEXT NOT NULL,
                        canonical_root TEXT NOT NULL,filesystem_identity_json TEXT NOT NULL,
                        detail_json TEXT NOT NULL,owner_id TEXT NOT NULL,
                        writer_fence INTEGER NOT NULL,created_at REAL NOT NULL,
                        UNIQUE(item_id,event_seq)
                    );
                    CREATE TRIGGER immutable_item_events_update BEFORE UPDATE ON item_events
                      BEGIN SELECT RAISE(ABORT,'immutable storage event'); END;
                    CREATE TRIGGER immutable_item_events_delete BEFORE DELETE ON item_events
                      BEGIN SELECT RAISE(ABORT,'immutable storage event'); END;
                    """
                )
                conn.executemany(
                    "INSERT INTO store_meta(key,value) VALUES(?,?)",
                    self._meta_payload().items(),
                )
                conn.commit()
                self._verify_store_binding(conn)
            finally:
                conn.close()

    def _connect_unverified(self, *, readonly: bool = False) -> sqlite3.Connection:
        if not self.path.exists():
            raise StorageMaintenanceConflict("storage operation journal is not activated")
        if is_link_or_reparse(self.path) or not self.path.is_file():
            raise StorageMaintenanceConflict("operation journal path is unsafe")
        before = _path_identity(self.path)
        if readonly:
            conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=5.0)
        else:
            conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        if is_link_or_reparse(self.path) or _path_identity(self.path) != before:
            conn.close()
            raise StorageMaintenanceConflict("operation journal identity changed during open")
        return conn

    def _verify_store_binding(self, conn: sqlite3.Connection) -> None:
        try:
            actual = {str(row[0]): str(row[1]) for row in conn.execute("SELECT key,value FROM store_meta")}
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(item_events)")}
        except sqlite3.Error as exc:
            raise StorageMaintenanceConflict("operation journal schema is invalid") from exc
        required = {
            "event_id", "operation_id", "item_id", "event_seq", "prior_event_hash",
            "event_hash", "event_type", "relative_path", "quarantine_path",
            "content_digest", "inventory_digest", "inventory_json", "capability_digest",
            "root_id", "canonical_root", "filesystem_identity_json", "detail_json",
            "owner_id", "writer_fence", "created_at",
        }
        if actual != self._meta_payload() or not required.issubset(columns):
            raise StorageMaintenanceConflict("operation journal root/capability binding mismatch")

    def _authorize_conn(self, conn: sqlite3.Connection, lease: MaintenanceLease) -> None:
        self._reload_capability()
        self._verify_store_binding(conn)
        row = conn.execute("SELECT * FROM lease WHERE singleton=1").fetchone()
        if row is None:
            raise StorageMaintenanceConflict("storage lease row is missing")
        expected = asdict(lease.identity)
        actual_identity = {
            "pid": row["pid"],
            "started_at": row["started_at"],
            "executable": row["executable"],
            "command_digest": row["command_digest"],
        }
        if (
            row["owner_id"] != lease.owner_id
            or actual_identity != expected
            or int(row["fence"]) != lease.fence
            or int(row["mutation_seq"]) != lease.mutation_seq
            or float(row["expires_at"] or 0) != lease.expires_at
            or float(row["expires_at"] or 0) <= self._clock()
            or lease.expires_at <= self._clock()
        ):
            raise StorageMaintenanceConflict("stale or expired storage writer lease")

    def acquire_writer(
        self,
        *,
        owner_id: str,
        identity: ProcessIdentity,
        lease_seconds: float = 30.0,
    ) -> MaintenanceLease:
        if not owner_id or lease_seconds <= 0:
            raise StorageMaintenanceConflict("owner id and positive lease are required")
        with _os_lock(self.lock_path):
            self._reload_capability()
            conn = self._connect_unverified()
            try:
                self._verify_store_binding(conn)
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM lease WHERE singleton=1").fetchone()
                now = self._clock()
                assert row is not None
                same_owner = row["owner_id"] == owner_id and all(
                    row[key] == value for key, value in asdict(identity).items()
                )
                live = row["owner_id"] is not None and float(row["expires_at"] or 0) > now
                if live and not same_owner:
                    raise StorageMaintenanceConflict("storage writer lease is held by another owner")
                fence = int(row["fence"]) if same_owner else int(row["fence"]) + 1
                mutation_seq = int(row["mutation_seq"]) + 1
                expires = now + float(lease_seconds)
                conn.execute(
                    """UPDATE lease SET owner_id=?,pid=?,started_at=?,executable=?,
                       command_digest=?,expires_at=?,fence=?,mutation_seq=?
                       WHERE singleton=1""",
                    (owner_id, identity.pid, identity.started_at, identity.executable,
                     identity.command_digest, expires, fence, mutation_seq),
                )
                conn.commit()
                return MaintenanceLease(owner_id, identity, fence, mutation_seq, expires)
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @staticmethod
    def _validate_operation_id(value: str) -> None:
        if len(value) != 32 or any(ch not in "0123456789abcdef" for ch in value):
            raise StorageMaintenanceConflict("operation id is not canonical")

    @staticmethod
    def _validate_item_id(value: str) -> None:
        prefix = "storageitem_"
        suffix = value.removeprefix(prefix)
        if not value.startswith(prefix) or len(suffix) != 64 or any(
            ch not in "0123456789abcdef" for ch in suffix
        ):
            raise StorageMaintenanceConflict("item id is not canonical")

    def _ensure_control_parent(self, path: Path) -> None:
        try:
            relative = path.parent.relative_to(self.control)
        except ValueError as exc:
            raise StorageMaintenanceConflict("internal destination escaped control root") from exc
        current = self.control
        for part in relative.parts:
            if part in {"", ".", ".."}:
                raise StorageMaintenanceConflict("internal destination is not canonical")
            current /= part
            if os.path.lexists(current):
                if is_link_or_reparse(current) or not current.is_dir():
                    raise StorageMaintenanceConflict("internal destination ancestor is unsafe")
            else:
                current.mkdir()
            if filesystem_identity(current) != self.capability.filesystem_identity:
                raise StorageMaintenanceConflict("internal destination changed filesystem")

    def _validated_paths(self, operation_id: str, relative_path: str) -> tuple[Path, str, Path]:
        self._validate_operation_id(operation_id)
        relative = validate_relative_path_text(self.capability, relative_path).as_posix()
        quarantine_relative = f"quarantine/{operation_id}/{relative}"
        source = self.root / Path(*relative.split("/"))
        destination = self.control / Path(*quarantine_relative.split("/"))
        return source, quarantine_relative, destination

    @contextmanager
    def _snapshot_open(self, path: Path) -> Iterator[tuple[int, FileSnapshot]]:
        relative = safe_relative_file(self.capability, path)
        fd = _open_nofollow_read(path)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise StorageMaintenanceConflict("candidate is not a regular file")
            snapshot = FileSnapshot(relative, _stat_identity(info), _fd_digest(fd))
            if _path_identity(path) != snapshot.identity:
                raise StorageMaintenanceConflict("candidate identity changed during no-follow open")
            yield fd, snapshot
        finally:
            os.close(fd)

    def _enumerate_candidates_locked(self) -> list[Path]:
        cache = self.root / self.capability.allowed_subtree
        if not os.path.lexists(cache):
            return []
        if is_link_or_reparse(cache) or not cache.is_dir():
            raise StorageMaintenanceConflict("cache subtree is unsafe")
        candidates: list[Path] = []
        for directory, names, files in os.walk(cache, topdown=True, followlinks=False):
            base = Path(directory)
            for name in list(names):
                child = base / name
                if is_link_or_reparse(child) or not child.is_dir():
                    raise StorageMaintenanceConflict("cache contains a reparse directory")
            for name in files:
                path = base / name
                safe_relative_file(self.capability, path)
                candidates.append(path)
        return sorted(candidates, key=lambda path: (_path_identity(path)["mtime_ns"], str(path)))

    def _inventory_payload(self, snapshot: FileSnapshot) -> dict[str, Any]:
        return snapshot.payload(self.capability)

    def _event_payload(
        self,
        *,
        operation_id: str,
        item_id: str,
        event_seq: int,
        prior_event_hash: str,
        event_type: str,
        relative_path: str,
        quarantine_path: str,
        inventory: dict[str, Any],
        detail: dict[str, Any],
        owner_id: str,
        writer_fence: int,
    ) -> dict[str, Any]:
        return {
            "operation_id": operation_id,
            "item_id": item_id,
            "event_seq": event_seq,
            "prior_event_hash": prior_event_hash,
            "event_type": event_type,
            "relative_path": relative_path,
            "quarantine_path": quarantine_path,
            "content_digest": inventory["content_digest"],
            "inventory_digest": _digest(inventory),
            "inventory": inventory,
            "capability_digest": self.capability.capability_digest,
            "root_id": self.capability.root_id,
            "canonical_root": self.capability.canonical_root,
            "filesystem_identity": self.capability.filesystem_identity,
            "detail": detail,
            "owner_id": owner_id,
            "writer_fence": writer_fence,
        }

    def _append_event_locked(
        self,
        conn: sqlite3.Connection,
        lease: MaintenanceLease,
        *,
        operation_id: str,
        item_id: str,
        event_type: str,
        relative_path: str,
        quarantine_path: str,
        inventory: dict[str, Any],
        detail: dict[str, Any] | None = None,
    ) -> sqlite3.Row:
        conn.execute("BEGIN IMMEDIATE")
        self._authorize_conn(conn, lease)
        if event_type not in self._EVENT_TYPES:
            raise StorageMaintenanceConflict("unknown storage event type")
        self._validate_item_id(item_id)
        _source, expected_quarantine, _destination = self._validated_paths(operation_id, relative_path)
        if quarantine_path != expected_quarantine:
            raise StorageMaintenanceConflict("quarantine path is not canonical")
        if inventory != self._validated_inventory(inventory, relative_path):
            raise StorageMaintenanceConflict("inventory is not canonically bound")
        detail = detail or {}
        last = conn.execute(
            "SELECT * FROM item_events WHERE item_id=? ORDER BY event_seq DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        prior_type = None if last is None else str(last["event_type"])
        next_seq = 1 if last is None else int(last["event_seq"]) + 1
        prior_hash = "" if last is None else str(last["event_hash"])
        payload = self._event_payload(
            operation_id=operation_id, item_id=item_id, event_seq=next_seq,
            prior_event_hash=prior_hash, event_type=event_type,
            relative_path=relative_path, quarantine_path=quarantine_path,
            inventory=inventory, detail=detail, owner_id=lease.owner_id,
            writer_fence=lease.fence,
        )
        if last is not None:
            previous_payload = self._payload_from_row(last)
            comparable_previous = {key: value for key, value in previous_payload.items() if key not in {"event_seq", "prior_event_hash"}}
            comparable_new = {key: value for key, value in payload.items() if key not in {"event_seq", "prior_event_hash"}}
            if comparable_previous == comparable_new:
                conn.commit()
                return last
        restore_pending = (
            prior_type == "quarantined"
            and event_type == "quarantined"
            and detail == {"action": "restore_pending"}
        )
        if not restore_pending and event_type not in self._TRANSITIONS.get(prior_type, set()):
            raise StorageMaintenanceConflict(f"illegal storage event transition {prior_type!r}->{event_type!r}")
        event_hash = _digest(payload)
        event_id = "storageevent_" + event_hash.removeprefix("sha256:")
        conn.execute(
            """INSERT INTO item_events(
               event_id,operation_id,item_id,event_seq,prior_event_hash,event_hash,event_type,
               relative_path,quarantine_path,content_digest,inventory_digest,inventory_json,
               capability_digest,root_id,canonical_root,filesystem_identity_json,detail_json,
               owner_id,writer_fence,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, operation_id, item_id, next_seq, prior_hash, event_hash, event_type,
             relative_path, quarantine_path, inventory["content_digest"], _digest(inventory),
             canonical_json(inventory), self.capability.capability_digest,
             self.capability.root_id, self.capability.canonical_root,
             canonical_json(self.capability.filesystem_identity), canonical_json(detail),
             lease.owner_id, lease.fence, self._clock()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM item_events WHERE event_id=?", (event_id,)).fetchone()
        assert row is not None
        return row

    def _validated_inventory(self, inventory: dict[str, Any], relative_path: str) -> dict[str, Any]:
        expected_keys = {
            "schema", "root_id", "capability_digest", "canonical_root",
            "filesystem_identity", "relative_path", "identity", "content_digest",
        }
        if set(inventory) != expected_keys:
            raise StorageMaintenanceConflict("inventory fields are incomplete")
        if (
            inventory["schema"] != "StorageFileInventory.v2"
            or inventory["root_id"] != self.capability.root_id
            or inventory["capability_digest"] != self.capability.capability_digest
            or inventory["canonical_root"] != self.capability.canonical_root
            or inventory["filesystem_identity"] != self.capability.filesystem_identity
            or inventory["relative_path"] != relative_path
            or not str(inventory["content_digest"]).startswith("sha256:")
        ):
            raise StorageMaintenanceConflict("inventory root or content binding mismatch")
        identity = inventory["identity"]
        if set(identity) != {"device", "inode", "size", "mtime_ns"} or any(
            isinstance(value, bool) or not isinstance(value, int) for value in identity.values()
        ):
            raise StorageMaintenanceConflict("inventory identity is invalid")
        return inventory

    def _payload_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            inventory = json.loads(str(row["inventory_json"]))
            detail = json.loads(str(row["detail_json"]))
            filesystem = json.loads(str(row["filesystem_identity_json"]))
        except (json.JSONDecodeError, TypeError) as exc:
            raise StorageMaintenanceConflict("storage event JSON is invalid") from exc
        return {
            "operation_id": row["operation_id"], "item_id": row["item_id"],
            "event_seq": row["event_seq"], "prior_event_hash": row["prior_event_hash"],
            "event_type": row["event_type"], "relative_path": row["relative_path"],
            "quarantine_path": row["quarantine_path"], "content_digest": row["content_digest"],
            "inventory_digest": row["inventory_digest"], "inventory": inventory,
            "capability_digest": row["capability_digest"], "root_id": row["root_id"],
            "canonical_root": row["canonical_root"], "filesystem_identity": filesystem,
            "detail": detail, "owner_id": row["owner_id"], "writer_fence": row["writer_fence"],
        }

    def _verify_file_matches(self, path: Path, inventory: dict[str, Any]) -> bool:
        try:
            return (
                _path_identity(path) == inventory["identity"]
                and _path_digest(path) == inventory["content_digest"]
                and filesystem_identity(path) == self.capability.filesystem_identity
            )
        except (OSError, StorageCapabilityError, StorageMaintenanceConflict):
            return False

    def _authorize_before_move(self, conn: sqlite3.Connection, lease: MaintenanceLease, *paths: Path) -> None:
        self._authorize_conn(conn, lease)
        for path in paths:
            existing = path if os.path.lexists(path) else path.parent
            if is_link_or_reparse(existing):
                raise StorageMaintenanceConflict("mutation path contains a link or reparse point")
            if filesystem_identity(existing) != self.capability.filesystem_identity:
                raise StorageMaintenanceConflict("mutation path changed filesystem")

    def quarantine_to_budget(
        self,
        lease: MaintenanceLease,
        *,
        max_mb: float,
        after_validate: Callable[[Path], None] | None = None,
        fail_after_items: int | None = None,
        fail_phase: str | None = None,
    ) -> dict[str, Any]:
        budget_bytes = int(parse_positive_budget(max_mb) * 1024 * 1024)
        operation_id = uuid.uuid4().hex
        results: list[dict[str, Any]] = []
        with _os_lock(self.lock_path):
            self._reload_capability()
            conn = self._connect_unverified()
            try:
                self._authorize_conn(conn, lease)
                candidates = self._enumerate_candidates_locked()
                total = sum(_path_identity(path)["size"] for path in candidates)
                for index, source in enumerate(candidates):
                    if total <= budget_bytes:
                        break
                    with self._snapshot_open(source) as (fd, snapshot):
                        relative = snapshot.relative_path
                        inventory = self._inventory_payload(snapshot)
                        inventory_digest = _digest(inventory)
                        item_id = "storageitem_" + _digest(
                            {"operation_id": operation_id, "relative_path": relative,
                             "inventory_digest": inventory_digest}
                        ).removeprefix("sha256:")
                        _source, quarantine_relative, destination = self._validated_paths(
                            operation_id, relative
                        )
                        self._append_event_locked(
                            conn, lease, operation_id=operation_id, item_id=item_id,
                            event_type="planned", relative_path=relative,
                            quarantine_path=quarantine_relative, inventory=inventory,
                        )
                        if after_validate is not None:
                            after_validate(source)
                        self._authorize_conn(conn, lease)
                        if _stat_identity(os.fstat(fd)) != snapshot.identity or _fd_digest(fd) != snapshot.content_digest:
                            self._append_event_locked(
                                conn, lease, operation_id=operation_id, item_id=item_id,
                                event_type="conflict", relative_path=relative,
                                quarantine_path=quarantine_relative, inventory=inventory,
                                detail={"reason": "open_file_changed_before_claim"},
                            )
                            results.append({"item_id": item_id, "relative_path": relative, "state": "conflict"})
                            continue
                        if _path_identity(source) != snapshot.identity:
                            self._append_event_locked(
                                conn, lease, operation_id=operation_id, item_id=item_id,
                                event_type="conflict", relative_path=relative,
                                quarantine_path=quarantine_relative, inventory=inventory,
                                detail={"reason": "path_identity_changed_before_claim"},
                            )
                            results.append({"item_id": item_id, "relative_path": relative, "state": "conflict"})
                            continue
                        staging = self.control / "staging" / operation_id / f"{item_id}.claimed"
                        self._ensure_control_parent(staging)
                        self._authorize_before_move(conn, lease, source, staging)
                        os.replace(source, staging)
                        if fail_phase == "after_claim_move":
                            raise StorageMaintenanceConflict("synthetic crash after claim move")
                        if not self._verify_file_matches(staging, inventory):
                            self._append_event_locked(
                                conn, lease, operation_id=operation_id, item_id=item_id,
                                event_type="conflict", relative_path=relative,
                                quarantine_path=quarantine_relative, inventory=inventory,
                                detail={"reason": "claimed_identity_mismatch"},
                            )
                            results.append({"item_id": item_id, "relative_path": relative, "state": "conflict"})
                            continue
                        self._append_event_locked(
                            conn, lease, operation_id=operation_id, item_id=item_id,
                            event_type="claimed", relative_path=relative,
                            quarantine_path=quarantine_relative, inventory=inventory,
                        )
                        if fail_phase == "after_claim_event":
                            raise StorageMaintenanceConflict("synthetic crash after claim event")
                        self._ensure_control_parent(destination)
                        if os.path.lexists(destination):
                            raise StorageMaintenanceConflict("quarantine destination already exists")
                        self._authorize_before_move(conn, lease, staging, destination)
                        os.replace(staging, destination)
                        if fail_phase == "after_quarantine_move":
                            raise StorageMaintenanceConflict("synthetic crash after quarantine move")
                        if not self._verify_file_matches(destination, inventory):
                            raise StorageMaintenanceConflict("quarantine verification failed")
                        self._append_event_locked(
                            conn, lease, operation_id=operation_id, item_id=item_id,
                            event_type="quarantined", relative_path=relative,
                            quarantine_path=quarantine_relative, inventory=inventory,
                        )
                        total -= int(snapshot.identity["size"])
                        results.append({"item_id": item_id, "relative_path": relative, "state": "quarantined"})
                        if fail_after_items is not None and index + 1 >= fail_after_items:
                            raise StorageMaintenanceConflict("synthetic failure after item mutation")
            finally:
                conn.close()
        return {"operation_id": operation_id, "items": results,
                "active_after_bytes": total, "physical_bytes_reclaimed": 0}

    def _latest_rows(self, conn: sqlite3.Connection, states: tuple[str, ...]) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in states)
        return conn.execute(
            f"""SELECT e.* FROM item_events e
                JOIN (SELECT item_id,MAX(event_seq) seq FROM item_events GROUP BY item_id) x
                  ON x.item_id=e.item_id AND x.seq=e.event_seq
                WHERE e.event_type IN ({placeholders}) ORDER BY e.rowid""",
            states,
        ).fetchall()

    def _validated_row(self, row: sqlite3.Row) -> tuple[dict[str, Any], Path, Path, Path]:
        payload = self._payload_from_row(row)
        self._validate_item_id(str(row["item_id"]))
        inventory = self._validated_inventory(payload["inventory"], str(row["relative_path"]))
        if _digest(inventory) != row["inventory_digest"] or inventory["content_digest"] != row["content_digest"]:
            raise StorageMaintenanceConflict("event inventory digest mismatch")
        source, expected_quarantine, destination = self._validated_paths(
            str(row["operation_id"]), str(row["relative_path"])
        )
        if row["quarantine_path"] != expected_quarantine:
            raise StorageMaintenanceConflict("event quarantine path is invalid")
        staging = self.control / "staging" / str(row["operation_id"]) / f"{row['item_id']}.claimed"
        return inventory, source, staging, destination

    def recover(self, lease: MaintenanceLease) -> dict[str, int]:
        recovered = conflicts = failed = 0
        with _os_lock(self.lock_path):
            self._reload_capability()
            conn = self._connect_unverified()
            try:
                self._authorize_conn(conn, lease)
                self._audit_conn(conn)
                for row in self._latest_rows(conn, ("planned", "claimed", "quarantined")):
                    inventory, source, staging, destination = self._validated_row(row)
                    row_detail = json.loads(str(row["detail_json"]))
                    kwargs = {
                        "operation_id": str(row["operation_id"]), "item_id": str(row["item_id"]),
                        "relative_path": str(row["relative_path"]),
                        "quarantine_path": str(row["quarantine_path"]), "inventory": inventory,
                    }
                    if row["event_type"] == "quarantined":
                        if row_detail != {"action": "restore_pending"}:
                            continue
                        source_exists = os.path.lexists(source)
                        quarantine_exists = os.path.lexists(destination)
                        if source_exists and quarantine_exists:
                            self._append_event_locked(
                                conn, lease, event_type="conflict",
                                detail={"reason": "restore_has_two_authoritative_paths"},
                                **kwargs,
                            )
                            conflicts += 1
                            continue
                        if source_exists:
                            try:
                                safe_relative_file(self.capability, source)
                            except StorageCapabilityError:
                                source_matches = False
                            else:
                                source_matches = self._verify_file_matches(source, inventory)
                            if source_matches:
                                self._append_event_locked(
                                    conn, lease, event_type="restored",
                                    detail={"recovered": True, "action": "restore"},
                                    **kwargs,
                                )
                                recovered += 1
                            else:
                                self._append_event_locked(
                                    conn, lease, event_type="conflict",
                                    detail={"reason": "restored_identity_mismatch"},
                                    **kwargs,
                                )
                                conflicts += 1
                            continue
                        if quarantine_exists:
                            self._ensure_control_parent(destination)
                            if not self._verify_file_matches(destination, inventory):
                                self._append_event_locked(
                                    conn, lease, event_type="conflict",
                                    detail={"reason": "restore_source_identity_mismatch"},
                                    **kwargs,
                                )
                                conflicts += 1
                                continue
                            ensure_safe_parent(self.capability, str(row["relative_path"]))
                            self._authorize_before_move(conn, lease, destination, source)
                            os.replace(destination, source)
                            if not self._verify_file_matches(source, inventory):
                                raise StorageMaintenanceConflict(
                                    "recovered restore verification failed"
                                )
                            self._append_event_locked(
                                conn, lease, event_type="restored",
                                detail={"recovered": True, "action": "restore"},
                                **kwargs,
                            )
                            recovered += 1
                            continue
                        self._append_event_locked(
                            conn, lease, event_type="failed",
                            detail={"reason": "restore_bytes_missing"}, **kwargs,
                        )
                        failed += 1
                        continue
                    if os.path.lexists(destination):
                        if not self._verify_file_matches(destination, inventory):
                            self._append_event_locked(conn, lease, event_type="conflict", detail={"reason": "quarantine_identity_mismatch"}, **kwargs)
                            conflicts += 1
                            continue
                        if row["event_type"] == "planned":
                            self._append_event_locked(conn, lease, event_type="claimed", detail={"recovered": True}, **kwargs)
                        self._append_event_locked(conn, lease, event_type="quarantined", detail={"recovered": True}, **kwargs)
                        recovered += 1
                    elif os.path.lexists(staging):
                        if not self._verify_file_matches(staging, inventory):
                            self._append_event_locked(conn, lease, event_type="conflict", detail={"reason": "claimed_identity_mismatch"}, **kwargs)
                            conflicts += 1
                            continue
                        if row["event_type"] == "planned":
                            self._append_event_locked(conn, lease, event_type="claimed", detail={"recovered": True}, **kwargs)
                        self._ensure_control_parent(destination)
                        self._authorize_before_move(conn, lease, staging, destination)
                        os.replace(staging, destination)
                        if not self._verify_file_matches(destination, inventory):
                            raise StorageMaintenanceConflict("recovered quarantine verification failed")
                        self._append_event_locked(conn, lease, event_type="quarantined", detail={"recovered": True}, **kwargs)
                        recovered += 1
                    elif os.path.lexists(source):
                        self._append_event_locked(conn, lease, event_type="failed", detail={"reason": "claim_not_started"}, **kwargs)
                        failed += 1
                    else:
                        self._append_event_locked(conn, lease, event_type="failed", detail={"reason": "no_authoritative_bytes"}, **kwargs)
                        failed += 1
            finally:
                conn.close()
        return {"recovered": recovered, "conflicts": conflicts, "failed": failed}

    def restore(
        self,
        lease: MaintenanceLease,
        *,
        item_id: str,
        fail_phase: str | None = None,
    ) -> dict[str, Any]:
        with _os_lock(self.lock_path):
            self._reload_capability()
            conn = self._connect_unverified()
            try:
                self._authorize_conn(conn, lease)
                self._audit_conn(conn)
                row = conn.execute(
                    "SELECT * FROM item_events WHERE item_id=? ORDER BY event_seq DESC LIMIT 1",
                    (item_id,),
                ).fetchone()
                if row is None or row["event_type"] != "quarantined":
                    raise StorageMaintenanceConflict("item is not in quarantined state")
                inventory, source, _staging, quarantined = self._validated_row(row)
                if os.path.lexists(source):
                    raise StorageMaintenanceConflict("restore destination is occupied")
                if not self._verify_file_matches(quarantined, inventory):
                    raise StorageMaintenanceConflict("quarantine bytes do not match the item identity")
                ensure_safe_parent(self.capability, str(row["relative_path"]))
                self._append_event_locked(
                    conn, lease, operation_id=str(row["operation_id"]), item_id=item_id,
                    event_type="quarantined", relative_path=str(row["relative_path"]),
                    quarantine_path=str(row["quarantine_path"]), inventory=inventory,
                    detail={"action": "restore_pending"},
                )
                if fail_phase == "after_restore_intent":
                    raise StorageMaintenanceConflict("synthetic crash after restore intent")
                self._authorize_before_move(conn, lease, quarantined, source)
                os.replace(quarantined, source)
                if fail_phase == "after_restore_move":
                    raise StorageMaintenanceConflict("synthetic crash after restore move")
                if not self._verify_file_matches(source, inventory):
                    raise StorageMaintenanceConflict("restored bytes failed identity verification")
                self._append_event_locked(
                    conn, lease, operation_id=str(row["operation_id"]), item_id=item_id,
                    event_type="restored", relative_path=str(row["relative_path"]),
                    quarantine_path=str(row["quarantine_path"]), inventory=inventory,
                )
                return {"item_id": item_id, "state": "restored",
                        "relative_path": row["relative_path"]}
            finally:
                conn.close()

    def _audit_conn(self, conn: sqlite3.Connection) -> dict[str, Any]:
        self._verify_store_binding(conn)
        rows = conn.execute("SELECT * FROM item_events ORDER BY rowid").fetchall()
        prior_hash: dict[str, str] = {}
        prior_seq: dict[str, int] = {}
        prior_state: dict[str, str | None] = {}
        states: dict[str, str] = {}
        bindings: dict[str, tuple[Any, ...]] = {}
        for row in rows:
            item_id = str(row["item_id"])
            seq = int(row["event_seq"])
            event_type = str(row["event_type"])
            if seq != prior_seq.get(item_id, 0) + 1:
                raise StorageMaintenanceConflict("storage event sequence is not contiguous")
            if str(row["prior_event_hash"]) != prior_hash.get(item_id, ""):
                raise StorageMaintenanceConflict("storage event prior hash mismatch")
            detail = json.loads(str(row["detail_json"]))
            restore_pending = (
                prior_state.get(item_id) == "quarantined"
                and event_type == "quarantined"
                and detail == {"action": "restore_pending"}
            )
            if not restore_pending and event_type not in self._TRANSITIONS.get(
                prior_state.get(item_id), set()
            ):
                raise StorageMaintenanceConflict("storage event transition is illegal")
            inventory, _source, _staging, _destination = self._validated_row(row)
            binding = (
                row["operation_id"], row["relative_path"], row["quarantine_path"],
                row["content_digest"], row["inventory_digest"], row["root_id"],
                row["capability_digest"],
            )
            if item_id in bindings and bindings[item_id] != binding:
                raise StorageMaintenanceConflict("storage item binding changed across events")
            bindings[item_id] = binding
            if row["capability_digest"] != self.capability.capability_digest or row["root_id"] != self.capability.root_id:
                raise StorageMaintenanceConflict("storage event capability binding mismatch")
            if row["canonical_root"] != self.capability.canonical_root or json.loads(row["filesystem_identity_json"]) != self.capability.filesystem_identity:
                raise StorageMaintenanceConflict("storage event root binding mismatch")
            if _digest(inventory) != row["inventory_digest"]:
                raise StorageMaintenanceConflict("storage event inventory hash mismatch")
            payload = self._payload_from_row(row)
            event_hash = _digest(payload)
            if event_hash != row["event_hash"]:
                raise StorageMaintenanceConflict("storage event digest mismatch")
            if row["event_id"] != "storageevent_" + event_hash.removeprefix("sha256:"):
                raise StorageMaintenanceConflict("storage event id mismatch")
            prior_hash[item_id] = event_hash
            prior_seq[item_id] = seq
            prior_state[item_id] = event_type
            states[item_id] = event_type
        counts = {state: sum(value == state for value in states.values()) for state in sorted(self._EVENT_TYPES)}
        return {"activated": True, "events": len(rows), "items": len(states), "state_counts": counts}

    @classmethod
    def audit_readonly(cls, root: Path) -> dict[str, Any]:
        capability = load_capability(root)
        path = Path(capability.canonical_root) / RESERVED / "operations.sqlite3"
        if not os.path.lexists(path):
            return {"activated": False, "events": 0}
        if is_link_or_reparse(path) or not path.is_file():
            raise StorageMaintenanceConflict("operation journal path is unsafe")
        store = cls(root)
        conn = store._connect_unverified(readonly=True)
        try:
            return store._audit_conn(conn)
        finally:
            conn.close()
