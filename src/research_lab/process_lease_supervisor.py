"""Out-of-process renewal and liveness supervision for canonical farm ownership.

The farm performs large JSON and derived-memory operations that can hold the
foreground interpreter's GIL. A thread in that same process cannot renew or
fail closed while the interpreter is frozen. This supervisor owns no trading
or task authority: it receives one already-acquired process lease, verifies
the exact parent process identity and fence, renews only that lease, and writes
bounded private lifecycle evidence. On failure it stops renewing and creates
the existing canonical farm stop intent so the RCC can perform its documented
dependency-ordered graceful stop.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.research_lab.ownership import (
    OwnershipConflictError,
    OwnershipStore,
    ProcessLease,
    StaleProcessLeaseError,
    probe_process_identity,
)
from src.research_lab.process_lease_heartbeat import (
    FailureCallback,
    ProcessLeaseHeartbeatLifecycleError,
    ProcessLeaseRenewalBudgetExceeded,
)


class ProcessLeaseProgressStalled(ProcessLeaseRenewalBudgetExceeded):
    """The canonical owner stopped publishing real progress."""


@dataclass(frozen=True)
class _SupervisorConfig:
    path: str
    status_path: str
    alert_path: str
    stop_path: str
    lease_seconds: float
    renew_interval_seconds: float
    renewal_busy_timeout_seconds: float
    renewal_retry_seconds: float
    max_transient_seconds: float
    lease_safety_margin_seconds: float
    max_no_progress_seconds: float


def _iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _owner_hash(lease: ProcessLease) -> str:
    return hashlib.sha256(lease.owner_id.encode("utf-8")).hexdigest()[:16]


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    encoded = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                # Windows readers can briefly deny replace sharing. Keep the
                # retry budget short so observability failure still fails
                # closed instead of delaying the next lease renewal.
                time.sleep(0.01 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def _append_jsonl(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _request_stop_once(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("canonical farm lease supervisor fail-closed stop\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def _read_progress(
    progress_mono: Any,
    progress_sequence: Any,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[int, float]:
    with progress_mono.get_lock():
        last_progress = float(progress_mono.value)
    with progress_sequence.get_lock():
        sequence = int(progress_sequence.value)
    return sequence, max(0.0, float(monotonic()) - last_progress)


def _status(
    *,
    state: str,
    lease: ProcessLease,
    renewals: int,
    progress_sequence: int,
    progress_age_seconds: float,
    failure_type: str | None = None,
    failure_detected_at: float | None = None,
    stop_intent_committed_at: float | None = None,
    transient_events: int = 0,
    last_transient_type: str | None = None,
) -> dict[str, object]:
    return {
        "schema": "ProcessLeaseSupervisorStatus.v1",
        "state": state,
        "updated_at": time.time(),
        "updated_at_utc": _iso(),
        "owner_pid": lease.identity.pid,
        "owner_started_at": lease.identity.started_at,
        "owner_hash": _owner_hash(lease),
        "fencing_token": lease.fencing_token,
        "lease_expires_at": lease.lease_expires_at,
        "renewals": renewals,
        "progress_sequence": progress_sequence,
        "last_progress_age_seconds": round(progress_age_seconds, 3),
        "transient_events": transient_events,
        "last_transient_type": last_transient_type,
        "failure_type": failure_type,
        "failure_detected_at": failure_detected_at,
        "stop_intent_committed_at": stop_intent_committed_at,
        "paper_only": True,
        "execution_allowed": False,
    }


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.OperationalError):
        code = getattr(exc, "sqlite_errorcode", None)
        if isinstance(code, int) and (code & 0xFF) in {
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        }:
            return True
        message = str(exc).casefold()
        return "database is locked" in message or "database table is locked" in message
    return bool(
        isinstance(exc, StaleProcessLeaseError)
        and isinstance(exc.__cause__, OwnershipConflictError)
    )


def _supervisor_main(
    config: _SupervisorConfig,
    initial_lease: ProcessLease,
    stop_event: Any,
    ready_event: Any,
    failure_event: Any,
    progress_mono: Any,
    progress_sequence: Any,
) -> None:
    status_path = Path(config.status_path)
    alert_path = Path(config.alert_path)
    stop_path = Path(config.stop_path)
    lease = initial_lease
    renewals = 0
    transient_events = 0
    last_transient_type: str | None = None
    store: OwnershipStore | None = None
    try:
        store = OwnershipStore.open_existing(
            config.path,
            identity_probe=probe_process_identity,
            busy_timeout_seconds=config.renewal_busy_timeout_seconds,
        )
        sequence, progress_age = _read_progress(
            progress_mono,
            progress_sequence,
        )
        _atomic_json(
            status_path,
            _status(
                state="running",
                lease=lease,
                renewals=renewals,
                progress_sequence=sequence,
                progress_age_seconds=progress_age,
            ),
        )
        ready_event.set()
        next_renewal = time.monotonic()
        transient_started: float | None = None
        while not stop_event.wait(0.05):
            sequence, progress_age = _read_progress(
                progress_mono,
                progress_sequence,
            )
            if progress_age > config.max_no_progress_seconds:
                raise ProcessLeaseProgressStalled(
                    "canonical farm stopped publishing real progress"
                )
            now_mono = time.monotonic()
            if now_mono < next_renewal:
                continue
            try:
                lease = store.renew_supervised(
                    lease,
                    lease_seconds=config.lease_seconds,
                    cancel_requested=stop_event.is_set,
                )
            except BaseException as exc:
                if not _retryable(exc):
                    raise
                if transient_started is None:
                    transient_started = now_mono
                transient_events += 1
                last_transient_type = type(exc).__name__
                transient_age = max(0.0, now_mono - transient_started)
                next_attempt_latest = (
                    time.time()
                    + config.renewal_retry_seconds
                    + config.renewal_busy_timeout_seconds
                    + config.lease_safety_margin_seconds
                )
                if (
                    transient_age >= config.max_transient_seconds
                    or next_attempt_latest >= lease.lease_expires_at
                ):
                    raise ProcessLeaseRenewalBudgetExceeded(
                        "supervised renewal exceeded its bounded safety budget"
                    ) from exc
                stop_event.wait(config.renewal_retry_seconds)
                next_renewal = time.monotonic()
                continue
            renewals += 1
            transient_started = None
            next_renewal = time.monotonic() + config.renew_interval_seconds
            _atomic_json(
                status_path,
                _status(
                    state="running",
                    lease=lease,
                    renewals=renewals,
                    progress_sequence=sequence,
                    progress_age_seconds=progress_age,
                    transient_events=transient_events,
                    last_transient_type=last_transient_type,
                ),
            )
        sequence, progress_age = _read_progress(
            progress_mono,
            progress_sequence,
        )
        _atomic_json(
            status_path,
            _status(
                state="stopped",
                lease=lease,
                renewals=renewals,
                progress_sequence=sequence,
                progress_age_seconds=progress_age,
                transient_events=transient_events,
                last_transient_type=last_transient_type,
            ),
        )
    except BaseException as exc:
        sequence, progress_age = _read_progress(
            progress_mono,
            progress_sequence,
        )
        failure = type(exc).__name__
        failure_detected_at = time.time()
        payload = _status(
            state="failed",
            lease=lease,
            renewals=renewals,
            progress_sequence=sequence,
            progress_age_seconds=progress_age,
            failure_type=failure,
            failure_detected_at=failure_detected_at,
            transient_events=transient_events,
            last_transient_type=last_transient_type,
        )
        try:
            stop_intent_created = _request_stop_once(stop_path)
        except OSError:
            stop_intent_created = False
        else:
            # _request_stop_once returns only after the exclusive marker write
            # is flushed.  This timestamp is the causal fail-closed boundary,
            # unlike a later parent bridge callback that Windows may schedule
            # after the lease has naturally expired.
            if stop_intent_created:
                payload["stop_intent_committed_at"] = time.time()
        try:
            _atomic_json(status_path, payload)
        except OSError:
            # The canonical stop intent is the fail-closed control path.
            # Status publication remains best effort once that durable request
            # has been attempted.
            pass
        finally:
            # The foreground must receive the failure after the durable stop
            # intent and failure status, but it must not wait for optional
            # append-only alert evidence.
            failure_event.set()
            ready_event.set()
        alert = dict(payload)
        alert["schema"] = "ProcessLeaseSupervisorAlert.v1"
        alert["alert_id"] = f"process_lease_supervisor:{time.time_ns()}"
        alert["stop_intent_created"] = stop_intent_created
        try:
            _append_jsonl(alert_path, alert)
        except OSError:
            pass
    finally:
        if store is not None:
            store.close()


class ProcessLeaseSupervisor:
    """Keep one canonical process lease outside the owner's GIL domain."""

    def __init__(
        self,
        path: Path,
        lease: ProcessLease,
        *,
        status_path: Path,
        alert_path: Path,
        stop_path: Path,
        lease_seconds: float = 90.0,
        renew_interval_seconds: float = 30.0,
        renewal_busy_timeout_seconds: float = 2.0,
        renewal_retry_seconds: float = 0.25,
        max_transient_seconds: float = 30.0,
        lease_safety_margin_seconds: float = 5.0,
        max_no_progress_seconds: float = 300.0,
        on_failure: FailureCallback | None = None,
        context_name: str = "spawn",
        startup_timeout_seconds: float = 15.0,
    ) -> None:
        values = (
            lease_seconds,
            renew_interval_seconds,
            renewal_busy_timeout_seconds,
            renewal_retry_seconds,
            max_transient_seconds,
            lease_safety_margin_seconds,
            max_no_progress_seconds,
            startup_timeout_seconds,
        )
        if min(values) <= 0:
            raise ValueError("process lease supervisor intervals must be positive")
        if renew_interval_seconds >= lease_seconds:
            raise ValueError("renew interval must be shorter than the lease")
        if (
            renew_interval_seconds
            + max_transient_seconds
            + lease_safety_margin_seconds
            >= lease_seconds
        ):
            raise ValueError("renewal failure budget must fit before lease expiry")
        self.path = Path(path)
        self.initial_lease = lease
        self.status_path = Path(status_path)
        self.alert_path = Path(alert_path)
        self.stop_path = Path(stop_path)
        self.on_failure = on_failure
        self.startup_timeout_seconds = float(startup_timeout_seconds)
        self._context: Any = multiprocessing.get_context(context_name)
        self._stop_event = self._context.Event()
        self._ready_event = self._context.Event()
        self.failure_event = self._context.Event()
        self._progress_mono = self._context.Value("d", time.monotonic())
        self._progress_sequence = self._context.Value("Q", 0)
        self._last_progress_stage = "supervisor_start"
        self._failure: BaseException | None = None
        self._started = False
        config = _SupervisorConfig(
            path=str(self.path),
            status_path=str(self.status_path),
            alert_path=str(self.alert_path),
            stop_path=str(self.stop_path),
            lease_seconds=float(lease_seconds),
            renew_interval_seconds=float(renew_interval_seconds),
            renewal_busy_timeout_seconds=float(renewal_busy_timeout_seconds),
            renewal_retry_seconds=float(renewal_retry_seconds),
            max_transient_seconds=float(max_transient_seconds),
            lease_safety_margin_seconds=float(lease_safety_margin_seconds),
            max_no_progress_seconds=float(max_no_progress_seconds),
        )
        process_factory: Any = getattr(self._context, "Process")
        self.process = process_factory(
            target=_supervisor_main,
            args=(
                config,
                lease,
                self._stop_event,
                self._ready_event,
                self.failure_event,
                self._progress_mono,
                self._progress_sequence,
            ),
            name="farm-process-lease-supervisor",
            daemon=True,
        )
        self.bridge_thread = threading.Thread(
            target=self._bridge_failure,
            name="farm-process-lease-supervisor-bridge",
            daemon=True,
        )

    @property
    def failure(self) -> BaseException | None:
        if self._failure is not None:
            return self._failure
        if self.failure_event.is_set():
            return ProcessLeaseHeartbeatLifecycleError(
                "farm process lease supervisor failed"
            )
        return None

    def start(self) -> None:
        if self._started:
            raise RuntimeError("process lease supervisor already started")
        self.process.start()
        self._started = True
        self.bridge_thread.start()
        if not self._ready_event.wait(self.startup_timeout_seconds):
            raise ProcessLeaseHeartbeatLifecycleError(
                "farm process lease supervisor did not initialize"
            )
        if self.failure is not None or not self.process.is_alive():
            raise ProcessLeaseHeartbeatLifecycleError(
                "farm process lease supervisor failed to initialize"
            ) from self.failure

    def record_progress(self, stage: str) -> None:
        if not stage:
            raise ValueError("real progress stage is required")
        with self._progress_mono.get_lock():
            self._progress_mono.value = time.monotonic()
        with self._progress_sequence.get_lock():
            self._progress_sequence.value += 1
        self._last_progress_stage = str(stage)[:120]
        self.assert_active(stage=stage)

    def assert_active(self, *, stage: str) -> None:
        failure = self.failure
        if failure is not None:
            raise ProcessLeaseHeartbeatLifecycleError(
                f"farm process lease supervisor failed during {stage}"
            ) from failure
        if self._started and not self.process.is_alive():
            raise ProcessLeaseHeartbeatLifecycleError(
                f"farm process lease supervisor exited during {stage}"
            )

    def snapshot(self) -> dict[str, object]:
        try:
            value = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        status = value if isinstance(value, dict) else {}
        status.pop("owner_id", None)
        status["process_alive"] = self.process.is_alive() if self._started else False
        status["bridge_thread_alive"] = self.bridge_thread.is_alive()
        status["last_progress_stage"] = self._last_progress_stage
        return status

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if not self._started:
            return
        self.process.join(max(0.0, float(timeout)))
        self.bridge_thread.join(max(0.0, float(timeout)))
        if self.process.is_alive() or self.bridge_thread.is_alive():
            raise ProcessLeaseHeartbeatLifecycleError(
                "farm process lease supervisor did not stop"
            )

    def _bridge_failure(self) -> None:
        while not self._stop_event.is_set():
            if not self.failure_event.wait(0.1):
                continue
            failure = ProcessLeaseHeartbeatLifecycleError(
                "farm process lease supervisor failed"
            )
            self._failure = failure
            if self.on_failure is not None:
                snapshot = self.snapshot()
                snapshot["failure_kind"] = "process_lease"
                try:
                    self.on_failure(failure, snapshot)
                except BaseException:
                    pass
            return
