"""Durable process ownership leases with monotonically increasing fences.

The store is deliberately small and independent from the research databases.  A
PID is never ownership by itself: the persisted process identity, owner id,
unexpired lease, and fencing token must all still match before a mutation is
authoritative.
"""
from __future__ import annotations

import sqlite3
import hashlib
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable


class OwnershipConflictError(RuntimeError):
    """The resource has another live owner or its durable state is unsafe."""


class StaleProcessLeaseError(RuntimeError):
    """A lease no longer grants mutation authority."""


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    started_at: float
    executable: str
    command_digest: str


@dataclass(frozen=True)
class ProcessLease:
    resource_id: str
    role_id: str
    owner_id: str
    identity: ProcessIdentity
    fencing_token: int
    lease_expires_at: float

    def replace(self, **changes: object) -> "ProcessLease":
        return replace(self, **changes)


IdentityProbe = Callable[[int], ProcessIdentity | None]


def _command_digest(argv: list[str]) -> str:
    payload = "\0".join(str(part) for part in argv).encode("utf-8", errors="surrogatepass")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def probe_process_identity(pid: int) -> ProcessIdentity | None:
    """Read an exact process tuple; permission/probe failures fail closed."""
    try:
        import psutil  # type: ignore[import-untyped]
    except Exception as exc:
        raise OwnershipConflictError("process identity support unavailable") from exc
    try:
        process = psutil.Process(int(pid))
        return ProcessIdentity(
            pid=int(pid),
            started_at=float(process.create_time()),
            executable=str(process.exe()),
            command_digest=_command_digest(process.cmdline()),
        )
    except (psutil.NoSuchProcess, ProcessLookupError):
        return None
    except Exception as exc:
        raise OwnershipConflictError(f"process identity probe failed for pid {pid}") from exc


def current_process_identity() -> ProcessIdentity:
    identity = probe_process_identity(os.getpid())
    if identity is None:
        raise OwnershipConflictError("current process identity disappeared")
    return identity


class OwnershipStore:
    """SQLite-backed owner registry shared by independent launch surfaces."""

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        identity_probe: IdentityProbe,
    ) -> None:
        self._clock = clock
        self._identity_probe = identity_probe
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_connection = sqlite3.connect(str(path), timeout=30)
        self.raw_connection.row_factory = sqlite3.Row
        self.raw_connection.execute("PRAGMA busy_timeout = 30000")
        self.raw_connection.execute("PRAGMA journal_mode = WAL")
        self.raw_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ownership_resources (
                resource_id TEXT PRIMARY KEY,
                role_id TEXT NOT NULL,
                owner_id TEXT,
                pid INTEGER,
                started_at REAL,
                executable TEXT,
                command_digest TEXT,
                lease_expires_at REAL,
                next_fence INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )
            """
        )
        self.raw_connection.commit()

    def close(self) -> None:
        self.raw_connection.close()

    @staticmethod
    def _identity_from_row(row: sqlite3.Row) -> ProcessIdentity:
        try:
            identity = ProcessIdentity(
                pid=int(row["pid"]),
                started_at=float(row["started_at"]),
                executable=str(row["executable"]),
                command_digest=str(row["command_digest"]),
            )
        except (TypeError, ValueError) as exc:
            raise OwnershipConflictError("corrupt ownership identity") from exc
        if (
            identity.pid <= 0
            or identity.started_at <= 0
            or not identity.executable
            or not identity.command_digest
        ):
            raise OwnershipConflictError("corrupt ownership identity")
        return identity

    @staticmethod
    def _same_identity(left: ProcessIdentity | None, right: ProcessIdentity) -> bool:
        return bool(left is not None and left == right)

    def acquire(
        self,
        *,
        resource_id: str,
        role_id: str,
        owner_id: str,
        identity: ProcessIdentity,
        lease_seconds: float,
    ) -> ProcessLease:
        if not resource_id or not role_id or not owner_id or lease_seconds <= 0:
            raise ValueError("resource, role, owner, and positive lease are required")
        if identity.pid <= 0 or identity.started_at <= 0 or not identity.executable or not identity.command_digest:
            raise ValueError("complete process identity is required")
        now = float(self._clock())
        conn = self.raw_connection
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM ownership_resources WHERE resource_id=?", (resource_id,)
            ).fetchone()
            if row is None:
                current_fence = 0
                conn.execute(
                    "INSERT INTO ownership_resources(resource_id, role_id, next_fence, updated_at) "
                    "VALUES(?,?,0,?)",
                    (resource_id, role_id, now),
                )
            else:
                current_fence = int(row["next_fence"])
                if (
                    current_fence < 0
                    or not str(row["role_id"])
                    or str(row["role_id"]) != role_id
                ):
                    raise OwnershipConflictError("corrupt ownership fence")
                if row["owner_id"] is not None:
                    persisted = self._identity_from_row(row)
                    expires = row["lease_expires_at"]
                    if expires is None:
                        raise OwnershipConflictError("corrupt ownership lease")
                    live = self._identity_probe(persisted.pid)
                    if float(expires) <= now and self._same_identity(live, persisted):
                        raise OwnershipConflictError("expired_alive_conflict")
                    if float(expires) <= now and live is not None:
                        raise OwnershipConflictError("identity_mismatch")
                    if float(expires) > now:
                        raise OwnershipConflictError("resource already owned")
                elif any(
                    row[name] is not None
                    for name in (
                        "pid", "started_at", "executable",
                        "command_digest", "lease_expires_at",
                    )
                ):
                    raise OwnershipConflictError("corrupt released ownership state")
            fence = current_fence + 1
            expires_at = now + float(lease_seconds)
            conn.execute(
                """
                UPDATE ownership_resources
                SET role_id=?, owner_id=?, pid=?, started_at=?, executable=?,
                    command_digest=?, lease_expires_at=?, next_fence=?, updated_at=?
                WHERE resource_id=?
                """,
                (
                    role_id, owner_id, identity.pid, identity.started_at,
                    identity.executable, identity.command_digest, expires_at,
                    fence, now, resource_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return ProcessLease(resource_id, role_id, owner_id, identity, fence, expires_at)

    def _assert_authoritative(self, lease: ProcessLease, *, now: float | None = None) -> sqlite3.Row:
        current = float(self._clock()) if now is None else float(now)
        row = self.raw_connection.execute(
            "SELECT * FROM ownership_resources WHERE resource_id=?", (lease.resource_id,)
        ).fetchone()
        if row is None or row["owner_id"] is None:
            raise StaleProcessLeaseError("lease is no longer owned")
        persisted = self._identity_from_row(row)
        if (
            str(row["role_id"]) != lease.role_id
            or str(row["owner_id"]) != lease.owner_id
            or int(row["next_fence"]) != lease.fencing_token
            or persisted != lease.identity
            or float(row["lease_expires_at"] or 0) <= current
        ):
            raise StaleProcessLeaseError("stale process lease")
        try:
            live = self._identity_probe(persisted.pid)
        except OwnershipConflictError as exc:
            raise StaleProcessLeaseError("process identity cannot be revalidated") from exc
        if not self._same_identity(live, persisted):
            raise StaleProcessLeaseError("process identity no longer matches lease")
        return row

    def is_authoritative(self, lease: ProcessLease) -> bool:
        try:
            self._assert_authoritative(lease)
        except (OwnershipConflictError, StaleProcessLeaseError):
            return False
        return True

    def status(self, resource_id: str) -> dict[str, object]:
        """Return liveness and ownership separately for operator projections."""
        row = self.raw_connection.execute(
            "SELECT * FROM ownership_resources WHERE resource_id=?", (resource_id,)
        ).fetchone()
        if row is None or row["owner_id"] is None:
            return {
                "resource_id": resource_id,
                "state": "released",
                "alive": False,
                "exclusive_owner": False,
            }
        try:
            persisted = self._identity_from_row(row)
            live = self._identity_probe(persisted.pid)
        except OwnershipConflictError:
            return {
                "resource_id": resource_id,
                "state": "identity_mismatch",
                "alive": None,
                "exclusive_owner": False,
            }
        same = self._same_identity(live, persisted)
        expired = float(row["lease_expires_at"] or 0) <= float(self._clock())
        state = (
            "identity_mismatch" if live is not None and not same
            else "lease_expired" if expired
            else "exclusive_owner" if same
            else "display_only"
        )
        return {
            "resource_id": resource_id,
            "state": state,
            "alive": live is not None,
            "exclusive_owner": bool(same and not expired),
            "owner_id": str(row["owner_id"]),
            "fencing_token": int(row["next_fence"]),
            "lease_expires_at": float(row["lease_expires_at"]),
        }

    def renew(self, lease: ProcessLease, *, lease_seconds: float = 30.0) -> ProcessLease:
        if lease_seconds <= 0:
            raise ValueError("positive lease is required")
        now = float(self._clock())
        conn = self.raw_connection
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_authoritative(lease, now=now)
            expires_at = now + float(lease_seconds)
            conn.execute(
                "UPDATE ownership_resources SET lease_expires_at=?, updated_at=? "
                "WHERE resource_id=? AND owner_id=? AND next_fence=?",
                (expires_at, now, lease.resource_id, lease.owner_id, lease.fencing_token),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return replace(lease, lease_expires_at=expires_at)

    def release(self, lease: ProcessLease) -> None:
        now = float(self._clock())
        conn = self.raw_connection
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_authoritative(lease, now=now)
            conn.execute(
                """
                UPDATE ownership_resources
                SET owner_id=NULL, pid=NULL, started_at=NULL, executable=NULL,
                    command_digest=NULL, lease_expires_at=NULL, updated_at=?
                WHERE resource_id=? AND owner_id=? AND next_fence=?
                """,
                (now, lease.resource_id, lease.owner_id, lease.fencing_token),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def acknowledge_stop_intent(self, lease: ProcessLease, stop_path: Path) -> None:
        conn = self.raw_connection
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_authoritative(lease)
            stop_path.unlink(missing_ok=True)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
