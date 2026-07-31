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
from typing import Any, Callable, Mapping, Sequence


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

    def replace(self, *, owner_id: str) -> "ProcessLease":
        return replace(self, owner_id=owner_id)


IdentityProbe = Callable[[int], ProcessIdentity | None]


@dataclass(frozen=True)
class CanonicalAuthorityAssessment:
    """Fail-closed process-authority view for canonical farm monitoring.

    Resource leases are not process cardinality.  The canonical farm may hold
    the short-lived ``strategy_lab_worker`` lease while it drains a compute
    job, but only when both leases resolve to the same exact live process
    identity.  Unexpected resources remain competing writer authority.
    """

    green: bool
    distinct_process_authorities: int
    canonical_owner_id: str | None
    canonical_fence: int | None
    process_identity: ProcessIdentity | None
    resources: tuple[str, ...]
    errors: tuple[str, ...]


_CANONICAL_FARM_AUTHORITIES = {
    "canonical_farm": "farm",
    "strategy_lab_worker": "compute_worker",
}


def assess_canonical_farm_authority(
    rows: Sequence[Mapping[str, Any]],
    *,
    identity_probe: IdentityProbe,
    now: float | None = None,
    prior_canonical_owner_id: str | None = None,
    prior_fences: Mapping[str, int] | None = None,
) -> CanonicalAuthorityAssessment:
    """Classify active owner rows by exact process and writer authority.

    Callers pass the current ownership rows rather than counting them.  One
    canonical farm row is required.  A nested compute-worker row is allowed
    only for the same PID, start time, executable and command digest.  A
    different process identity, unexpected resource/role, expired row, live
    identity mismatch, canonical generation change, or fence regression is a
    hard failure.
    """

    current = float(time.time() if now is None else now)
    previous_fences = dict(prior_fences or {})
    errors: list[str] = []
    resources: list[str] = []
    parsed: list[tuple[Mapping[str, Any], ProcessIdentity, str, str, str, int]] = []
    seen_resources: set[str] = set()

    for row in rows:
        try:
            resource_id_value = row["resource_id"]
            role_id_value = row["role_id"]
            owner_id_value = row["owner_id"]
            executable_value = row["executable"]
            command_digest_value = row["command_digest"]
            if not all(
                isinstance(value, str) and value
                for value in (
                    resource_id_value,
                    role_id_value,
                    owner_id_value,
                    executable_value,
                    command_digest_value,
                )
            ):
                raise ValueError("complete text authority fields are required")
            resource_id = resource_id_value
            role_id = role_id_value
            owner_id = owner_id_value
            fence = int(row["next_fence"])
            expires_at = float(row["lease_expires_at"])
            identity = ProcessIdentity(
                pid=int(row["pid"]),
                started_at=float(row["started_at"]),
                executable=executable_value,
                command_digest=command_digest_value,
            )
        except (KeyError, TypeError, ValueError):
            errors.append("corrupt_process_authority")
            continue

        resources.append(resource_id)
        if resource_id in seen_resources:
            errors.append("duplicate_resource_authority")
        seen_resources.add(resource_id)
        if (
            not resource_id
            or not role_id
            or not owner_id
            or fence <= 0
            or identity.pid <= 0
            or identity.started_at <= 0
            or not identity.executable
            or not identity.command_digest
        ):
            errors.append("corrupt_process_authority")
            continue
        if expires_at <= current:
            errors.append("expired_process_authority")
        if _CANONICAL_FARM_AUTHORITIES.get(resource_id) != role_id:
            errors.append("unexpected_writer_authority")

        prior_fence = previous_fences.get(resource_id)
        if prior_fence is not None:
            if fence < int(prior_fence):
                errors.append("fence_regression")
            elif resource_id == "canonical_farm" and fence != int(prior_fence):
                errors.append("canonical_generation_changed")
        parsed.append((row, identity, resource_id, role_id, owner_id, fence))

    canonical = [
        entry for entry in parsed if entry[2] == "canonical_farm" and entry[3] == "farm"
    ]
    if len(canonical) != 1:
        errors.append(
            "canonical_owner_missing" if not canonical else "canonical_owner_ambiguous"
        )

    identities = {entry[1] for entry in parsed}
    if len(identities) > 1:
        errors.append("distinct_process_authority")

    live_by_pid: dict[int, ProcessIdentity | None] = {}
    for identity in identities:
        if identity.pid not in live_by_pid:
            try:
                live_by_pid[identity.pid] = identity_probe(identity.pid)
            except Exception:  # identity probes are fail-closed monitor inputs
                live_by_pid[identity.pid] = None
        if live_by_pid[identity.pid] != identity:
            errors.append("process_identity_mismatch")

    canonical_owner_id = canonical[0][4] if len(canonical) == 1 else None
    canonical_fence = canonical[0][5] if len(canonical) == 1 else None
    canonical_identity = canonical[0][1] if len(canonical) == 1 else None
    if (
        prior_canonical_owner_id is not None
        and canonical_owner_id != prior_canonical_owner_id
    ):
        errors.append("canonical_generation_changed")

    unique_errors = tuple(dict.fromkeys(errors))
    return CanonicalAuthorityAssessment(
        green=not unique_errors,
        distinct_process_authorities=len(identities),
        canonical_owner_id=canonical_owner_id,
        canonical_fence=canonical_fence,
        process_identity=canonical_identity,
        resources=tuple(sorted(resources)),
        errors=unique_errors,
    )


def _command_digest(argv: list[str]) -> str:
    payload = "\0".join(str(part) for part in argv).encode(
        "utf-8", errors="surrogatepass"
    )
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
        raise OwnershipConflictError(
            f"process identity probe failed for pid {pid}"
        ) from exc


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

    @classmethod
    def open_existing(
        cls,
        path: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        identity_probe: IdentityProbe,
        busy_timeout_seconds: float,
    ) -> "OwnershipStore":
        """Open an existing authority store for a bounded renewal path.

        A heartbeat must never create or migrate the authority store, and it
        must not spend most of a lease in SQLite's default 30 second busy wait.
        Acquisition remains responsible for schema/WAL setup; renewal gets a
        short, caller-budgeted read/write connection to that same file.
        """

        if busy_timeout_seconds <= 0:
            raise ValueError("positive busy timeout is required")
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        instance = cls.__new__(cls)
        instance._clock = clock
        instance._identity_probe = identity_probe
        instance.raw_connection = sqlite3.connect(
            f"{resolved.as_uri()}?mode=rw",
            uri=True,
            timeout=float(busy_timeout_seconds),
        )
        instance.raw_connection.row_factory = sqlite3.Row
        instance.raw_connection.execute(
            f"PRAGMA busy_timeout = {max(1, int(busy_timeout_seconds * 1000))}"
        )
        # Validate the expected authority surface without creating anything.
        instance.raw_connection.execute(
            "SELECT resource_id FROM ownership_resources LIMIT 0"
        )
        return instance

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
        if (
            identity.pid <= 0
            or identity.started_at <= 0
            or not identity.executable
            or not identity.command_digest
        ):
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
                        "pid",
                        "started_at",
                        "executable",
                        "command_digest",
                        "lease_expires_at",
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
                    role_id,
                    owner_id,
                    identity.pid,
                    identity.started_at,
                    identity.executable,
                    identity.command_digest,
                    expires_at,
                    fence,
                    now,
                    resource_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return ProcessLease(resource_id, role_id, owner_id, identity, fence, expires_at)

    def _assert_authoritative(
        self,
        lease: ProcessLease,
        *,
        now: float | None = None,
        revalidate_process_identity: bool = True,
    ) -> sqlite3.Row:
        current = float(self._clock()) if now is None else float(now)
        row = self.raw_connection.execute(
            "SELECT * FROM ownership_resources WHERE resource_id=?",
            (lease.resource_id,),
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
        if revalidate_process_identity:
            try:
                live = self._identity_probe(persisted.pid)
            except OwnershipConflictError as exc:
                raise StaleProcessLeaseError(
                    "process identity cannot be revalidated"
                ) from exc
            if not self._same_identity(live, persisted):
                raise StaleProcessLeaseError("process identity no longer matches lease")
        return row

    @staticmethod
    def _assert_local_holder(lease: ProcessLease) -> None:
        """Prove that an in-process renewal is not acting for another PID.

        Acquisition already captured the immutable process identity.  A lease
        object retained by that same Python process cannot survive process
        death or PID reuse.  Local heartbeat/release paths therefore verify the
        current PID and the persisted owner/fence tuple without repeating a
        potentially blocking Windows command-line probe while holding the
        SQLite write transaction.
        """

        if lease.identity.pid != os.getpid():
            raise StaleProcessLeaseError(
                "local process does not hold the persisted process lease"
            )
        try:
            import psutil  # type: ignore[import-untyped]

            local_started_at = float(psutil.Process(os.getpid()).create_time())
        except Exception as exc:
            raise StaleProcessLeaseError(
                "local process start identity cannot be verified"
            ) from exc
        if abs(local_started_at - float(lease.identity.started_at)) > 0.001:
            raise StaleProcessLeaseError(
                "local process generation does not match the persisted lease"
            )

    def is_authoritative(self, lease: ProcessLease) -> bool:
        try:
            self._assert_authoritative(lease)
        except (OwnershipConflictError, StaleProcessLeaseError):
            return False
        return True

    def is_authoritative_local(self, lease: ProcessLease) -> bool:
        """Fail-closed authority check for the process that acquired ``lease``."""

        try:
            self._assert_local_holder(lease)
            self._assert_authoritative(
                lease,
                revalidate_process_identity=False,
            )
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
            "identity_mismatch"
            if live is not None and not same
            else "lease_expired"
            if expired
            else "exclusive_owner"
            if same
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

    def renew(
        self, lease: ProcessLease, *, lease_seconds: float = 30.0
    ) -> ProcessLease:
        return self._renew(
            lease,
            lease_seconds=lease_seconds,
            revalidate_process_identity=True,
        )

    def renew_local(
        self,
        lease: ProcessLease,
        *,
        lease_seconds: float = 30.0,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ProcessLease:
        """Renew only a lease held by this exact Python process."""

        self._assert_local_holder(lease)
        return self._renew(
            lease,
            lease_seconds=lease_seconds,
            revalidate_process_identity=False,
            cancel_requested=cancel_requested,
        )

    def renew_supervised(
        self,
        lease: ProcessLease,
        *,
        lease_seconds: float = 30.0,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ProcessLease:
        """Renew from a dedicated supervisor after proving the owner process.

        The potentially blocking process identity probe deliberately happens
        before the SQLite write transaction. The subsequent fenced update can
        extend only the same persisted owner/fence/process tuple. If the owner
        exits after the probe, at most this one bounded lease extension
        survives; the next probe fails and natural expiry remains intact.
        """

        if cancel_requested is not None and cancel_requested():
            raise StaleProcessLeaseError(
                "process lease renewal cancelled before identity probe"
            )
        try:
            live = self._identity_probe(lease.identity.pid)
        except OwnershipConflictError as exc:
            raise StaleProcessLeaseError(
                "process identity cannot be revalidated"
            ) from exc
        if not self._same_identity(live, lease.identity):
            raise StaleProcessLeaseError(
                "process identity no longer matches supervised lease"
            )
        return self._renew(
            lease,
            lease_seconds=lease_seconds,
            revalidate_process_identity=False,
            cancel_requested=cancel_requested,
        )

    def _renew(
        self,
        lease: ProcessLease,
        *,
        lease_seconds: float,
        revalidate_process_identity: bool,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ProcessLease:
        if lease_seconds <= 0:
            raise ValueError("positive lease is required")
        now = float(self._clock())
        conn = self.raw_connection
        try:
            conn.execute("BEGIN IMMEDIATE")
            if cancel_requested is not None and cancel_requested():
                raise StaleProcessLeaseError(
                    "process lease renewal cancelled before mutation"
                )
            self._assert_authoritative(
                lease,
                now=now,
                revalidate_process_identity=revalidate_process_identity,
            )
            expires_at = now + float(lease_seconds)
            if cancel_requested is not None and cancel_requested():
                raise StaleProcessLeaseError(
                    "process lease renewal cancelled before mutation"
                )
            conn.execute(
                "UPDATE ownership_resources SET lease_expires_at=?, updated_at=? "
                "WHERE resource_id=? AND owner_id=? AND next_fence=?",
                (
                    expires_at,
                    now,
                    lease.resource_id,
                    lease.owner_id,
                    lease.fencing_token,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return replace(lease, lease_expires_at=expires_at)

    def release(self, lease: ProcessLease) -> None:
        self._release(lease, revalidate_process_identity=True)

    def release_local(self, lease: ProcessLease) -> None:
        """Release only a lease held by this exact Python process."""

        self._assert_local_holder(lease)
        self._release(lease, revalidate_process_identity=False)

    def _release(
        self,
        lease: ProcessLease,
        *,
        revalidate_process_identity: bool,
    ) -> None:
        now = float(self._clock())
        conn = self.raw_connection
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_authoritative(
                lease,
                now=now,
                revalidate_process_identity=revalidate_process_identity,
            )
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
        self._acknowledge_stop_intent(
            lease,
            stop_path,
            revalidate_process_identity=True,
        )

    def acknowledge_stop_intent_local(
        self, lease: ProcessLease, stop_path: Path
    ) -> None:
        """Acknowledge a stop marker from its exact in-process owner."""

        self._assert_local_holder(lease)
        self._acknowledge_stop_intent(
            lease,
            stop_path,
            revalidate_process_identity=False,
        )

    def _acknowledge_stop_intent(
        self,
        lease: ProcessLease,
        stop_path: Path,
        *,
        revalidate_process_identity: bool,
    ) -> None:
        conn = self.raw_connection
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_authoritative(
                lease,
                revalidate_process_identity=revalidate_process_identity,
            )
            stop_path.unlink(missing_ok=True)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
