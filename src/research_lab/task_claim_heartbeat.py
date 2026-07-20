"""Progress-gated renewal for long, pre-materialization farm task claims."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from src.research_lab.farm_tasks_db import FarmTasksDB, StaleTaskClaimError
from src.research_lab.ownership import (
    OwnershipStore,
    ProcessIdentity,
    ProcessLease,
    probe_process_identity,
)


class TaskClaimProgressStalled(StaleTaskClaimError):
    """No confirmed foreground progress arrived within the bounded window."""


IdentityProbe = Callable[[int], ProcessIdentity | None]


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
        if not self.owner_id or self.owner_id != process_lease.owner_id:
            raise ValueError("task claim must belong to the canonical process owner")
        if self.fencing_token <= 0:
            raise ValueError("task claim requires a positive fencing token")
        if min(
            self.lease_seconds,
            self.renew_interval_seconds,
            self.max_no_progress_seconds,
        ) <= 0:
            raise ValueError("heartbeat intervals must be positive")
        if self.renew_interval_seconds >= self.lease_seconds:
            raise ValueError("renew interval must be shorter than the task lease")

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._failure: BaseException | None = None
        self._last_progress_mono = float(self._monotonic())
        self._last_progress_at = float(self._clock())
        self._last_progress_stage = "claim_acquired"
        self._progress_sequence = 1
        self._renewed_progress_sequence = 0
        self._renewals = 0
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
            self._thread.join(timeout=5.0)
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
            failure = TaskClaimProgressStalled(
                f"task {self.task_id} made no confirmed progress for {age:.3f}s"
            )
            self._record_failure(failure)
            raise failure
        store = OwnershipStore(
            self.ownership_path,
            clock=self._clock,
            identity_probe=self._identity_probe,
        )
        try:
            if not store.is_authoritative(self.process_lease):
                failure = StaleTaskClaimError(
                    f"canonical owner fence is stale for task {self.task_id}"
                )
                self._record_failure(failure)
                raise failure
        finally:
            store.close()
        self.task_db.assert_task_claim(
            self.task_id,
            fencing_token=self.fencing_token,
            now=self._clock(),
        )

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
                "failure": None if failure is None else type(failure).__name__,
                "thread_alive": self._thread.is_alive(),
            }

    def _raise_if_inactive(self) -> None:
        if self._stop.is_set() or (self.stop_event is not None and self.stop_event.is_set()):
            raise StaleTaskClaimError(f"task claim heartbeat stopped for task {self.task_id}")
        failure = self.failure
        if failure is not None:
            raise StaleTaskClaimError(f"task claim heartbeat failed for task {self.task_id}") from failure

    def _record_failure(self, failure: BaseException) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = failure
        self._stop.set()

    def _wait_for_renewal(self) -> bool:
        deadline = float(self._monotonic()) + self.renew_interval_seconds
        while True:
            if self._stop.is_set() or (self.stop_event is not None and self.stop_event.is_set()):
                return False
            remaining = deadline - float(self._monotonic())
            if remaining <= 0:
                return True
            self._stop.wait(min(0.25, remaining))

    def _run(self) -> None:
        store = None
        renewal_db = None
        try:
            store = OwnershipStore(
                self.ownership_path,
                clock=self._clock,
                identity_probe=self._identity_probe,
            )
            renewal_db = FarmTasksDB(
                self.task_db.path,
                owner_id=self.owner_id,
                lease_seconds=self.lease_seconds,
                clock=self._clock,
            )
            while self._wait_for_renewal():
                self._renew_if_progressed(store, renewal_db)
        except BaseException as exc:  # captured and checked before materialization
            self._record_failure(exc)
        finally:
            if renewal_db is not None:
                renewal_db.close()
            if store is not None:
                store.close()

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
        if not store.is_authoritative(self.process_lease):
            raise StaleTaskClaimError(
                f"canonical owner fence is stale for task {self.task_id}"
            )
        renewal_db.renew_task_claim_token(
            self.task_id,
            fencing_token=self.fencing_token,
            lease_seconds=self.lease_seconds,
            now=self._clock(),
        )
        with self._lock:
            self._renewed_progress_sequence = progress_sequence
            self._renewals += 1
        return True
