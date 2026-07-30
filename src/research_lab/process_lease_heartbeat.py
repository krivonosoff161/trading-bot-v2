"""Bounded, fail-closed renewal for durable process ownership.

The foreground process remains responsible for all mutations. This helper
only preserves an already-acquired lease while exact owner, process identity
and fence still match. Transient SQLite contention or a temporarily
unavailable process probe may be retried, but never beyond a fixed budget or
the current lease's safety margin.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from src.research_lab.ownership import (
    OwnershipConflictError,
    OwnershipStore,
    IdentityProbe,
    ProcessLease,
    StaleProcessLeaseError,
    probe_process_identity,
)


class ProcessLeaseHeartbeatLifecycleError(RuntimeError):
    """The heartbeat cannot prove a safe lifecycle."""


class ProcessLeaseRenewalBudgetExceeded(RuntimeError):
    """Transient renewal failures outlived the bounded safety budget."""


FailureCallback = Callable[[BaseException, dict[str, object]], None]


class ProcessLeaseHeartbeat:
    """Renew one fenced process lease and publish a fail-closed signal."""

    def __init__(
        self,
        path: Path,
        lease: ProcessLease,
        *,
        lease_seconds: float = 90.0,
        renew_interval_seconds: float = 30.0,
        renewal_busy_timeout_seconds: float = 2.0,
        renewal_retry_seconds: float = 0.25,
        max_transient_seconds: float = 30.0,
        lease_safety_margin_seconds: float = 5.0,
        thread_name: str = "process-lease-heartbeat",
        on_failure: FailureCallback | None = None,
        identity_probe: IdentityProbe = probe_process_identity,
        current_pid: Callable[[], int] = os.getpid,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        values = (
            lease_seconds,
            renew_interval_seconds,
            renewal_busy_timeout_seconds,
            renewal_retry_seconds,
            max_transient_seconds,
            lease_safety_margin_seconds,
        )
        if min(values) <= 0:
            raise ValueError("process heartbeat intervals must be positive")
        if renew_interval_seconds >= lease_seconds:
            raise ValueError("renew interval must be shorter than the lease")
        if renewal_busy_timeout_seconds > max_transient_seconds:
            raise ValueError("busy timeout must fit the transient budget")
        if (
            renew_interval_seconds
            + max_transient_seconds
            + lease_safety_margin_seconds
            >= lease_seconds
        ):
            raise ValueError("transient budget must fail before lease expiry")
        self.path = Path(path)
        self.lease_seconds = float(lease_seconds)
        self.renew_interval_seconds = float(renew_interval_seconds)
        self.renewal_busy_timeout_seconds = float(renewal_busy_timeout_seconds)
        self.renewal_retry_seconds = float(renewal_retry_seconds)
        self.max_transient_seconds = float(max_transient_seconds)
        self.lease_safety_margin_seconds = float(lease_safety_margin_seconds)
        self.on_failure = on_failure
        self._identity_probe = identity_probe
        self._current_pid = current_pid
        self._clock = clock
        self._monotonic = monotonic
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.failure_event = threading.Event()
        self._state_lock = threading.Lock()
        self._lease = lease
        self._failure: BaseException | None = None
        self._renewals = 0
        self._transient_started_mono: float | None = None
        self._transient_events = 0
        self._last_transient_type: str | None = None
        self._renew_attempt_started_mono: float | None = None
        self._last_renewal_completed_mono = float(self._monotonic())
        self._started = False
        self.thread = threading.Thread(
            target=self._run,
            name=thread_name,
            daemon=True,
        )
        self.supervisor_thread = threading.Thread(
            target=self._supervise,
            name=f"{thread_name}-supervisor",
            daemon=True,
        )

    @property
    def lease(self) -> ProcessLease:
        with self._state_lock:
            return self._lease

    @property
    def failure(self) -> BaseException | None:
        with self._state_lock:
            return self._failure

    def start(self) -> None:
        if self._started:
            raise RuntimeError("process lease heartbeat already started")
        try:
            local_holder = self.lease.identity.pid == int(self._current_pid())
        except (AttributeError, TypeError, ValueError) as exc:
            failure = StaleProcessLeaseError("invalid process lease")
            failure.__cause__ = exc
            local_holder = False
        else:
            failure = StaleProcessLeaseError(
                "process lease heartbeat must run in the acquiring process"
            )
        if not local_holder:
            self._record_failure(failure)
            raise ProcessLeaseHeartbeatLifecycleError(
                "process lease heartbeat failed to initialize"
            ) from failure
        self.thread.start()
        self.supervisor_thread.start()
        self._started = True
        startup_budget = (
            self.max_transient_seconds
            + self.renewal_busy_timeout_seconds
            + self.renewal_retry_seconds
            + 1.0
        )
        if not self.ready_event.wait(timeout=startup_budget):
            self._record_failure(
                TimeoutError("process lease heartbeat did not initialize")
            )
        if self.failure is not None:
            raise ProcessLeaseHeartbeatLifecycleError(
                "process lease heartbeat failed to initialize"
            ) from self.failure

    def stop(self, *, timeout: float | None = None) -> None:
        self.stop_event.set()
        if not self._started:
            return
        stop_timeout = (
            self.renewal_busy_timeout_seconds + 2.0
            if timeout is None
            else max(0.0, float(timeout))
        )
        self.thread.join(timeout=stop_timeout)
        self.supervisor_thread.join(timeout=stop_timeout)
        if self.thread.is_alive() or self.supervisor_thread.is_alive():
            raise ProcessLeaseHeartbeatLifecycleError(
                "process lease heartbeat did not stop"
            )

    def assert_active(self, *, stage: str) -> None:
        failure = self.failure
        if failure is not None:
            raise ProcessLeaseHeartbeatLifecycleError(
                f"process lease heartbeat failed during {stage}"
            ) from failure
        if self.stop_event.is_set():
            raise ProcessLeaseHeartbeatLifecycleError(
                f"process lease heartbeat stopped during {stage}"
            )

    def snapshot(self) -> dict[str, object]:
        with self._state_lock:
            failure = self._failure
            lease = self._lease
            transient_started = self._transient_started_mono
            return {
                "resource_id": lease.resource_id,
                "role_id": lease.role_id,
                "fencing_token": lease.fencing_token,
                "lease_expires_at": lease.lease_expires_at,
                "renewals": self._renewals,
                "transient_active": transient_started is not None,
                "transient_age_seconds": (
                    0.0
                    if transient_started is None
                    else max(0.0, float(self._monotonic()) - transient_started)
                ),
                "transient_events": self._transient_events,
                "last_transient_type": self._last_transient_type,
                "failure": None if failure is None else type(failure).__name__,
                "thread_alive": self.thread.is_alive(),
                "supervisor_alive": self.supervisor_thread.is_alive(),
                "renew_attempt_age_seconds": (
                    0.0
                    if self._renew_attempt_started_mono is None
                    else max(
                        0.0,
                        float(self._monotonic())
                        - self._renew_attempt_started_mono,
                    )
                ),
                "last_renewal_completed_age_seconds": max(
                    0.0,
                    float(self._monotonic()) - self._last_renewal_completed_mono,
                ),
            }

    def _run(self) -> None:
        store: OwnershipStore | None = None
        try:
            store = self._connect_with_bounded_retries()
            if store is None:
                return
            self.ready_event.set()
            while self._wait(self.renew_interval_seconds):
                if not self._renew_with_bounded_retries(store):
                    return
        except BaseException as exc:
            self._record_failure(exc)
        finally:
            self.ready_event.set()
            if store is not None:
                store.close()

    def _supervise(self) -> None:
        """Latch a visible failure even if the renewal worker stops returning.

        SQLite busy waits are configured below the lease budget, and local
        renewals avoid the unbounded Windows command-line identity probe.
        This independent clock is the final fail-closed guard: an unexpected
        C-extension or filesystem stall cannot remain invisible until expiry.
        """

        while not self.stop_event.wait(0.1):
            if self.failure is not None:
                return
            with self._state_lock:
                attempt_started = self._renew_attempt_started_mono
                lease_expires_at = self._lease.lease_expires_at
            now_mono = float(self._monotonic())
            now_wall = float(self._clock())
            attempt_age = (
                0.0
                if attempt_started is None
                else max(0.0, now_mono - attempt_started)
            )
            if (
                attempt_started is not None
                and attempt_age >= self.max_transient_seconds
            ) or (
                now_wall + self.lease_safety_margin_seconds
                >= lease_expires_at
            ):
                self._record_failure(
                    ProcessLeaseRenewalBudgetExceeded(
                        "process lease renewal stopped making bounded progress"
                    )
                )
                return

    def _connect_with_bounded_retries(self) -> OwnershipStore | None:
        while not self.stop_event.is_set():
            attempt_started = float(self._monotonic())
            try:
                store = OwnershipStore.open_existing(
                    self.path,
                    identity_probe=self._identity_probe,
                    busy_timeout_seconds=self.renewal_busy_timeout_seconds,
                    clock=self._clock,
                )
            except sqlite3.OperationalError as exc:
                if not self._is_retryable(exc):
                    raise
                self._record_transient(attempt_started, exc)
                self._assert_retry_budget()
                if not self._wait(self.renewal_retry_seconds):
                    return None
                continue
            with self._state_lock:
                self._transient_started_mono = None
            return store
        return None

    def _renew_with_bounded_retries(self, store: OwnershipStore) -> bool:
        while not self.stop_event.is_set():
            attempt_started = float(self._monotonic())
            with self._state_lock:
                self._renew_attempt_started_mono = attempt_started
            try:
                renewed = store.renew_local(
                    self.lease,
                    lease_seconds=self.lease_seconds,
                    cancel_requested=self.stop_event.is_set,
                )
            except BaseException as exc:
                with self._state_lock:
                    self._renew_attempt_started_mono = None
                if not self._is_retryable(exc):
                    raise
                self._record_transient(attempt_started, exc)
                self._assert_retry_budget()
                if not self._wait(self.renewal_retry_seconds):
                    return False
                continue
            with self._state_lock:
                self._lease = renewed
                self._renewals += 1
                self._transient_started_mono = None
                self._renew_attempt_started_mono = None
                self._last_renewal_completed_mono = float(self._monotonic())
            return True
        return False

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        if isinstance(exc, sqlite3.OperationalError):
            code = getattr(exc, "sqlite_errorcode", None)
            if isinstance(code, int) and (code & 0xFF) in {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            }:
                return True
            message = str(exc).casefold()
            return (
                "database is locked" in message
                or "database table is locked" in message
            )
        # The store wraps an unavailable process probe as a stale lease with
        # OwnershipConflictError as its cause. An actual owner/fence/identity
        # mismatch has no such cause and must fail immediately.
        return bool(
            isinstance(exc, StaleProcessLeaseError)
            and isinstance(exc.__cause__, OwnershipConflictError)
        )

    def _record_transient(self, started: float, exc: BaseException) -> None:
        with self._state_lock:
            if self._transient_started_mono is None:
                self._transient_started_mono = started
            self._transient_events += 1
            self._last_transient_type = type(exc).__name__

    def _assert_retry_budget(self) -> None:
        with self._state_lock:
            started = self._transient_started_mono
            expires_at = self._lease.lease_expires_at
        age = (
            0.0
            if started is None
            else max(0.0, float(self._monotonic()) - started)
        )
        next_attempt_latest = (
            float(self._clock())
            + self.renewal_retry_seconds
            + self.renewal_busy_timeout_seconds
            + self.lease_safety_margin_seconds
        )
        if age >= self.max_transient_seconds or next_attempt_latest >= expires_at:
            raise ProcessLeaseRenewalBudgetExceeded(
                "process lease renewal exceeded its bounded safety budget"
            )

    def _wait(self, seconds: float) -> bool:
        deadline = float(self._monotonic()) + float(seconds)
        while not self.stop_event.is_set():
            remaining = deadline - float(self._monotonic())
            if remaining <= 0:
                return True
            self.stop_event.wait(min(0.25, remaining))
        return False

    def _record_failure(self, failure: BaseException) -> None:
        should_notify = False
        with self._state_lock:
            if self._failure is None:
                self._failure = failure
                should_notify = True
        self.failure_event.set()
        self.stop_event.set()
        if should_notify and self.on_failure is not None:
            try:
                snapshot = self.snapshot()
                snapshot["failure_kind"] = "process_lease"
                self.on_failure(failure, snapshot)
            except BaseException:
                # Callback observability cannot replace the latched failure.
                pass
