"""Progress-gated renewal for long, pre-materialization farm task claims."""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from src.research_lab.farm_tasks_db import FarmTasksDB, StaleTaskClaimError
from src.research_lab.ownership import (
    OwnershipStore,
    ProcessIdentity,
    ProcessLease,
    probe_process_identity,
)


class TaskClaimProgressStalled(StaleTaskClaimError):
    """No confirmed foreground progress arrived within the bounded window."""


class TaskClaimRenewalContentionExceeded(StaleTaskClaimError):
    """SQLite renewal contention outlived its bounded fail-closed budget."""


IdentityProbe = Callable[[int], ProcessIdentity | None]
FailureCallback = Callable[[BaseException, dict[str, Any]], None]
StopRequested = Callable[[], bool]


class TaskClaimHeartbeat:
    """Renew one exact task generation while its canonical owner makes progress.

    The heartbeat owns independent SQLite connections.  Foreground work must
    explicitly call :meth:`progress` after observable milestones; mere thread
    liveness never extends a claim indefinitely.
    """

    def __init__(
        self,
        task_db: FarmTasksDB,
        task: dict[str, Any],
        *,
        ownership_path: Path,
        process_lease: ProcessLease,
        stop_event: threading.Event | None = None,
        lease_seconds: float | None = None,
        renew_interval_seconds: float = 30.0,
        max_no_progress_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        identity_probe: IdentityProbe = probe_process_identity,
        on_failure: FailureCallback | None = None,
        stop_requested: StopRequested | None = None,
        renewal_busy_timeout_seconds: float | None = None,
        renewal_retry_seconds: float | None = None,
        max_renewal_contention_seconds: float | None = None,
    ) -> None:
        if task_db.path == ":memory:":
            raise ValueError("task claim heartbeat requires a filesystem task DB")
        self.task_db = task_db
        self.task_id = int(task["task_id"])
        self.owner_id = str(task.get("claim_owner") or "")
        self.fencing_token = int(task.get("fencing_token") or 0)
        self.ownership_path = Path(ownership_path)
        self.process_lease = process_lease
        self.stop_event = stop_event
        self.lease_seconds = float(lease_seconds or task_db.lease_seconds)
        self.renew_interval_seconds = float(renew_interval_seconds)
        self.max_no_progress_seconds = float(max_no_progress_seconds)
        self._clock = clock
        self._monotonic = monotonic
        self._identity_probe = identity_probe
        self._on_failure = on_failure
        self._stop_requested = stop_requested
        self.renewal_busy_timeout_seconds = float(
            renewal_busy_timeout_seconds
            if renewal_busy_timeout_seconds is not None
            else min(5.0, self.lease_seconds / 20.0)
        )
        self.renewal_retry_seconds = float(
            renewal_retry_seconds
            if renewal_retry_seconds is not None
            else min(1.0, self.lease_seconds / 50.0)
        )
        self.max_renewal_contention_seconds = float(
            max_renewal_contention_seconds
            if max_renewal_contention_seconds is not None
            else min(120.0, self.lease_seconds / 3.0)
        )
        if not self.owner_id or self.owner_id != process_lease.owner_id:
            raise ValueError("task claim must belong to the canonical process owner")
        if self.fencing_token <= 0:
            raise ValueError("task claim requires a positive fencing token")
        if min(
            self.lease_seconds,
            self.renew_interval_seconds,
            self.max_no_progress_seconds,
            self.renewal_busy_timeout_seconds,
            self.renewal_retry_seconds,
            self.max_renewal_contention_seconds,
        ) <= 0:
            raise ValueError("heartbeat intervals must be positive")
        if self.renew_interval_seconds >= self.lease_seconds:
            raise ValueError("renew interval must be shorter than the task lease")
        if self.renewal_busy_timeout_seconds > self.max_renewal_contention_seconds:
            raise ValueError("renewal busy timeout must fit within contention budget")
        if (
            self.renew_interval_seconds + self.max_renewal_contention_seconds
            >= self.lease_seconds
        ):
            raise ValueError("renewal contention budget must expire before the task claim")

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._renewal_gate = threading.Lock()
        self._failure: BaseException | None = None
        self._last_progress_mono = float(self._monotonic())
        self._last_progress_at = float(self._clock())
        self._last_progress_stage = "claim_acquired"
        self._progress_sequence = 1
        self._renewed_progress_sequence = 0
        self._renewals = 0
        self._claim_expires_at = float(task.get("claim_expires_at") or 0.0)
        self._renewal_contention_started_mono: float | None = None
        self._renewal_contention_events = 0
        self._last_renewal_contention: str | None = None
        self._renewal_connection_ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"farm-task-claim-heartbeat-{self.task_id}",
            daemon=True,
        )
        self._started = False

    @property
    def thread(self) -> threading.Thread:
        return self._thread

    @property
    def failure(self) -> BaseException | None:
        with self._lock:
            return self._failure

    def __enter__(self) -> "TaskClaimHeartbeat":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()

    def start(self) -> None:
        if self._started:
            raise RuntimeError("task claim heartbeat already started")
        self.assert_active()
        self._started = True
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._started:
            self._thread.join(
                timeout=max(5.0, self.renewal_busy_timeout_seconds + 2.0)
            )
            if self._thread.is_alive():
                raise RuntimeError("task claim heartbeat did not stop")

    def progress(self, stage: str) -> None:
        """Confirm one completed foreground milestone and expose it to status."""
        self._raise_if_inactive()
        with self._lock:
            self._last_progress_mono = float(self._monotonic())
            self._last_progress_at = float(self._clock())
            self._last_progress_stage = str(stage)[:120]
            self._progress_sequence += 1

    def assert_active(self) -> None:
        """Fail closed before any durable materialization side effect."""
        self._raise_if_inactive()
        with self._lock:
            age = float(self._monotonic()) - self._last_progress_mono
        if age > self.max_no_progress_seconds:
            stalled_failure = TaskClaimProgressStalled(
                f"task {self.task_id} made no confirmed progress for {age:.3f}s"
            )
            self._record_failure(stalled_failure)
            raise stalled_failure
        store = OwnershipStore.open_existing(
            self.ownership_path,
            clock=self._clock,
            identity_probe=self._identity_probe,
            busy_timeout_seconds=self.renewal_busy_timeout_seconds,
        )
        try:
            if not store.is_authoritative_local(self.process_lease):
                owner_failure = StaleTaskClaimError(
                    f"canonical owner fence is stale for task {self.task_id}"
                )
                self._record_failure(owner_failure)
                raise owner_failure
        finally:
            store.close()
        self.task_db.assert_task_claim(
            self.task_id,
            fencing_token=self.fencing_token,
            now=self._clock(),
        )

    @contextmanager
    def foreground_db_write(self) -> Iterator[None]:
        """Serialize a fenced foreground task-DB write against renewal."""
        with self._renewal_gate:
            self.assert_active()
            yield

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            failure = self._failure
            return {
                "task_id": self.task_id,
                "owner_id": self.owner_id,
                "task_fencing_token": self.fencing_token,
                "process_fencing_token": self.process_lease.fencing_token,
                "progress_sequence": self._progress_sequence,
                "renewed_progress_sequence": self._renewed_progress_sequence,
                "last_progress_stage": self._last_progress_stage,
                "last_progress_at": self._last_progress_at,
                "last_progress_age_seconds": max(
                    0.0, float(self._monotonic()) - self._last_progress_mono
                ),
                "renewals": self._renewals,
                "claim_expires_at": self._claim_expires_at,
                "renewal_contention_active": (
                    self._renewal_contention_started_mono is not None
                ),
                "renewal_contention_age_seconds": (
                    0.0
                    if self._renewal_contention_started_mono is None
                    else max(
                        0.0,
                        float(self._monotonic())
                        - self._renewal_contention_started_mono,
                    )
                ),
                "renewal_contention_events": self._renewal_contention_events,
                "last_renewal_contention": self._last_renewal_contention,
                "renewal_connection_ready": self._renewal_connection_ready.is_set(),
                "failure": None if failure is None else type(failure).__name__,
                "thread_alive": self._thread.is_alive(),
            }

    def _raise_if_inactive(self) -> None:
        if self._stopping():
            raise StaleTaskClaimError(f"task claim heartbeat stopped for task {self.task_id}")
        failure = self.failure
        if failure is not None:
            raise StaleTaskClaimError(f"task claim heartbeat failed for task {self.task_id}") from failure

    def _stopping(self) -> bool:
        return bool(
            self._stop.is_set()
            or (self.stop_event is not None and self.stop_event.is_set())
            or (self._stop_requested is not None and self._stop_requested())
        )

    def _record_failure(self, failure: BaseException) -> None:
        first_failure = False
        with self._lock:
            if self._failure is None:
                self._failure = failure
                first_failure = True
        self._stop.set()
        if first_failure and self._on_failure is not None:
            try:
                self._on_failure(failure, self.public_snapshot())
            except BaseException:
                # Failure notification is observability only.  It must never
                # replace or clear the authority failure that stopped renewal.
                pass

    def public_snapshot(self) -> dict[str, Any]:
        """Return failure telemetry without the private owner instance id."""
        snapshot = self.snapshot()
        snapshot.pop("owner_id", None)
        return snapshot

    def _wait_for_renewal(self) -> bool:
        deadline = float(self._monotonic()) + self.renew_interval_seconds
        while True:
            if self._stopping():
                return False
            remaining = deadline - float(self._monotonic())
            if remaining <= 0:
                return True
            self._stop.wait(min(0.25, remaining))

    def _run(self) -> None:
        store = None
        renewal_db = None
        try:
            store = OwnershipStore.open_existing(
                self.ownership_path,
                clock=self._clock,
                identity_probe=self._identity_probe,
                busy_timeout_seconds=self.renewal_busy_timeout_seconds,
            )
            renewal_db = FarmTasksDB(
                self.task_db.path,
                owner_id=self.owner_id,
                lease_seconds=self.lease_seconds,
                clock=self._clock,
            )
            renewal_db.raw_connection.execute(
                "PRAGMA busy_timeout = "
                f"{max(1, int(self.renewal_busy_timeout_seconds * 1000.0))}"
            )
            self._renewal_connection_ready.set()
            while self._wait_for_renewal():
                self._renew_with_bounded_contention(store, renewal_db)
        except BaseException as exc:  # captured and checked before materialization
            self._record_failure(exc)
        finally:
            if renewal_db is not None:
                renewal_db.close()
            if store is not None:
                store.close()

    def _renew_with_bounded_contention(
        self, store: OwnershipStore, renewal_db: FarmTasksDB,
    ) -> bool:
        """Retry only SQLite lock contention within claim and liveness bounds."""
        while not self._stopping():
            attempt_started = float(self._monotonic())
            try:
                with self._renewal_gate:
                    if self._stopping():
                        self._clear_active_contention()
                        return False
                    renewed = self._renew_if_progressed(store, renewal_db)
            except sqlite3.OperationalError as exc:
                if not self._is_sqlite_contention(exc):
                    raise
                if self._stopping():
                    self._clear_active_contention()
                    return False
                self._record_renewal_contention(attempt_started, exc)
                if self._stopping():
                    self._clear_active_contention()
                    return False
                self._assert_contention_budget()
                if not self._wait_for_contention_retry():
                    return False
                continue
            if renewed:
                self._clear_active_contention()
            return renewed
        return False

    @staticmethod
    def _is_sqlite_contention(exc: sqlite3.OperationalError) -> bool:
        code = getattr(exc, "sqlite_errorcode", None)
        if isinstance(code, int) and (code & 0xFF) in {
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        }:
            return True
        message = str(exc).casefold()
        return "database is locked" in message or "database table is locked" in message

    def _record_renewal_contention(
        self, attempt_started: float, exc: sqlite3.OperationalError,
    ) -> None:
        with self._lock:
            if self._renewal_contention_started_mono is None:
                self._renewal_contention_started_mono = attempt_started
            self._renewal_contention_events += 1
            self._last_renewal_contention = str(
                getattr(exc, "sqlite_errorname", None) or "SQLITE_LOCKED"
            )[:64]

    def _clear_active_contention(self) -> None:
        with self._lock:
            self._renewal_contention_started_mono = None

    def _assert_contention_budget(self) -> None:
        with self._lock:
            started = self._renewal_contention_started_mono
            claim_expires_at = self._claim_expires_at
        now_mono = float(self._monotonic())
        age = 0.0 if started is None else max(0.0, now_mono - started)
        next_attempt_latest = (
            float(self._clock())
            + self.renewal_retry_seconds
            + self.renewal_busy_timeout_seconds
        )
        if (
            age >= self.max_renewal_contention_seconds
            or next_attempt_latest >= claim_expires_at
        ):
            raise TaskClaimRenewalContentionExceeded(
                f"task {self.task_id} renewal contention exceeded its bounded budget"
            )

    def _wait_for_contention_retry(self) -> bool:
        deadline = float(self._monotonic()) + self.renewal_retry_seconds
        while True:
            if self._stopping():
                return False
            remaining = deadline - float(self._monotonic())
            if remaining <= 0:
                return True
            self._stop.wait(min(0.25, remaining))

    def _renew_if_progressed(
        self, store: OwnershipStore, renewal_db: FarmTasksDB,
    ) -> bool:
        """Perform one progress-gated renewal; factored for deterministic races."""
        with self._lock:
            age = float(self._monotonic()) - self._last_progress_mono
            progress_sequence = self._progress_sequence
            renewed_sequence = self._renewed_progress_sequence
        if age > self.max_no_progress_seconds:
            raise TaskClaimProgressStalled(
                f"task {self.task_id} made no confirmed progress for {age:.3f}s"
            )
        if progress_sequence == renewed_sequence:
            return False
        if not store.is_authoritative_local(self.process_lease):
            raise StaleTaskClaimError(
                f"canonical owner fence is stale for task {self.task_id}"
            )
        claim_expires_at = renewal_db.renew_task_claim_token(
            self.task_id,
            fencing_token=self.fencing_token,
            lease_seconds=self.lease_seconds,
            now=self._clock(),
        )
        with self._lock:
            self._renewed_progress_sequence = progress_sequence
            self._renewals += 1
            self._claim_expires_at = float(claim_expires_at)
            # Publish a successful renewal and the end of its contention episode
            # as one observable state transition.  Otherwise a snapshot can see
            # renewals incremented while contention still appears active.
            self._renewal_contention_started_mono = None
        return True
