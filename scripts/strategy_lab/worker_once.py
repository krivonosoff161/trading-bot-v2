# -*- coding: utf-8 -*-
"""Run one queued strategy-lab job.

This is the smallest safe 24/7 building block: an external loop can call it
periodically, while the worker itself handles one job and exits.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import os
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab import ExperimentSpec, evaluate_spec, write_run_outputs  # noqa: E402
from src.research_lab.outputs import publish_run_indexes  # noqa: E402
from src.research_lab.ownership import (  # noqa: E402
    OwnershipConflictError,
    OwnershipStore,
    current_process_identity,
    probe_process_identity,
)
from src.research_lab.candle_store import CandleStore  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.runtime_policy import (  # noqa: E402
    cadence_path,
    effective_variant_cap,
    evaluate_cadence,
    load_recent_starts,
    record_start,
    worker_status_path,
    write_worker_status,
)
from src.research_lab.state_db import (  # noqa: E402
    claim_next_job,
    connect,
    default_db_path,
    fail_job,
    init_db,
    mark_job_executing,
    mark_publication_indexes_published,
    publish_completed_job,
    reap_stale_jobs,
    recover_pending_publications,
    renew_job_lease,
)
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.process_lease_heartbeat import (  # noqa: E402
    ProcessLeaseHeartbeat,
    ProcessLeaseHeartbeatLifecycleError,
)
from src.research_lab.search_trial_evidence import (  # noqa: E402
    build_search_trial_evidence,
    write_search_trial_evidence,
)

_LEASE_SECONDS = 90.0
_RENEW_SECONDS = 30.0
_HEARTBEAT_STOP_SECONDS = 35.0
_JOB_RENEWAL_BUSY_TIMEOUT_SECONDS = 2.0
_JOB_RENEWAL_RETRY_SECONDS = 0.25
_JOB_MAX_RENEWAL_CONTENTION_SECONDS = 30.0
_JOB_LEASE_SAFETY_MARGIN_SECONDS = 5.0


class WorkerLeaseLifecycleError(RuntimeError):
    """The compute worker can no longer prove or release its process authority."""


class JobLeaseRenewalContentionExceeded(RuntimeError):
    """SQLite contention outlived the compute claim's bounded safety budget."""


def _worker_lock_path(private_root: Path) -> Path:
    return private_root / "state" / "worker.lock"


def _legacy_worker_present(private_root: Path) -> bool:
    """Fail closed while an un-migrated pathname lock is present."""
    return _worker_lock_path(private_root).exists()


class _ProcessLeaseHeartbeat(ProcessLeaseHeartbeat):
    """Compatibility adapter for the shared bounded process heartbeat."""

    def __init__(
        self,
        *,
        ownership_path: Path,
        process_lease,
    ) -> None:
        super().__init__(
            ownership_path,
            process_lease,
            lease_seconds=_LEASE_SECONDS,
            renew_interval_seconds=_RENEW_SECONDS,
            renewal_busy_timeout_seconds=min(2.0, _LEASE_SECONDS / 20.0),
            renewal_retry_seconds=min(0.25, _LEASE_SECONDS / 50.0),
            max_transient_seconds=min(30.0, _LEASE_SECONDS / 3.0),
            lease_safety_margin_seconds=min(5.0, _LEASE_SECONDS / 10.0),
            thread_name="worker-process-lease-heartbeat",
        )

    def stop(self, *, timeout: float | None = None) -> None:
        try:
            super().stop(
                timeout=_HEARTBEAT_STOP_SECONDS if timeout is None else timeout
            )
        except ProcessLeaseHeartbeatLifecycleError as exc:
            raise WorkerLeaseLifecycleError(
                "compute worker process lease heartbeat did not stop"
            ) from exc

    def start(self) -> None:
        try:
            super().start()
        except ProcessLeaseHeartbeatLifecycleError as exc:
            raise WorkerLeaseLifecycleError(
                "compute worker process lease heartbeat failed to initialize"
            ) from exc


class _JobLeaseHeartbeat:
    """Renew one queue claim until its fenced terminal transition succeeds."""

    def __init__(
        self,
        *,
        db_path: Path,
        job_id: int,
        owner_id: str,
        fencing_token: int,
        claim_expires_at: float,
        lease_seconds: float | None = None,
        renew_interval_seconds: float | None = None,
        renewal_busy_timeout_seconds: float | None = None,
        renewal_retry_seconds: float | None = None,
        max_renewal_contention_seconds: float | None = None,
        lease_safety_margin_seconds: float | None = None,
        clock=time.time,
        monotonic=time.monotonic,
    ) -> None:
        self.db_path = db_path
        self.job_id = int(job_id)
        self.owner_id = str(owner_id)
        self.fencing_token = int(fencing_token)
        self.lease_seconds = float(
            _LEASE_SECONDS if lease_seconds is None else lease_seconds
        )
        self.renew_interval_seconds = float(
            _RENEW_SECONDS
            if renew_interval_seconds is None
            else renew_interval_seconds
        )
        self.renewal_busy_timeout_seconds = float(
            min(_JOB_RENEWAL_BUSY_TIMEOUT_SECONDS, self.lease_seconds / 20.0)
            if renewal_busy_timeout_seconds is None
            else renewal_busy_timeout_seconds
        )
        self.renewal_retry_seconds = float(
            min(_JOB_RENEWAL_RETRY_SECONDS, self.lease_seconds / 50.0)
            if renewal_retry_seconds is None
            else renewal_retry_seconds
        )
        self.max_renewal_contention_seconds = float(
            min(_JOB_MAX_RENEWAL_CONTENTION_SECONDS, self.lease_seconds / 3.0)
            if max_renewal_contention_seconds is None
            else max_renewal_contention_seconds
        )
        self.lease_safety_margin_seconds = float(
            min(_JOB_LEASE_SAFETY_MARGIN_SECONDS, self.lease_seconds / 10.0)
            if lease_safety_margin_seconds is None
            else lease_safety_margin_seconds
        )
        if min(
            self.lease_seconds,
            self.renew_interval_seconds,
            self.renewal_busy_timeout_seconds,
            self.renewal_retry_seconds,
            self.max_renewal_contention_seconds,
            self.lease_safety_margin_seconds,
        ) <= 0:
            raise ValueError("job heartbeat intervals must be positive")
        if self.renew_interval_seconds >= self.lease_seconds:
            raise ValueError("job renew interval must be shorter than the lease")
        if (
            self.renewal_busy_timeout_seconds
            > self.max_renewal_contention_seconds
        ):
            raise ValueError("job renewal busy timeout must fit the contention budget")
        if (
            self.renew_interval_seconds
            + self.max_renewal_contention_seconds
            + self.lease_safety_margin_seconds
            >= self.lease_seconds
        ):
            raise ValueError("job contention budget must expire before the claim")
        self._clock = clock
        self._monotonic = monotonic
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self._state_lock = threading.Lock()
        self._failure: BaseException | None = None
        self._claim_expires_at = float(claim_expires_at)
        if self._claim_expires_at <= float(self._clock()):
            raise ValueError("job heartbeat requires an unexpired claim")
        self._renewals = 0
        self._renewal_contention_started_mono: float | None = None
        self._renewal_contention_events = 0
        self._last_renewal_contention: str | None = None
        self._transition_lock = threading.Lock()
        self._terminal = False
        self._started = False
        self.thread = threading.Thread(
            target=self._run,
            name="worker-job-lease-heartbeat",
            daemon=True,
        )

    @property
    def failure(self) -> BaseException | None:
        with self._state_lock:
            return self._failure

    def start(self) -> None:
        if self._started:
            raise RuntimeError("compute job lease heartbeat already started")
        self.thread.start()
        self._started = True
        startup_budget = (
            self.max_renewal_contention_seconds
            + self.renewal_busy_timeout_seconds
            + self.renewal_retry_seconds
            + 1.0
        )
        if not self.ready_event.wait(timeout=startup_budget):
            self._record_failure(
                TimeoutError(
                    "compute job lease heartbeat did not initialize"
                )
            )
        if self.failure is not None:
            raise WorkerLeaseLifecycleError(
                "compute job lease heartbeat failed to initialize"
            ) from self.failure

    def stop(self) -> None:
        self.stop_event.set()
        if not self._started:
            return
        self.thread.join(timeout=_HEARTBEAT_STOP_SECONDS)
        if self.thread.is_alive():
            raise WorkerLeaseLifecycleError(
                "compute job lease heartbeat did not stop"
            )

    def _run(self) -> None:
        job_conn = None
        try:
            job_conn = self._connect_with_bounded_contention()
            if job_conn is None:
                return
            self.ready_event.set()
            while self._wait_for_renewal():
                self._renew_with_bounded_contention(job_conn)
        except BaseException as exc:
            self._record_failure(exc)
        finally:
            self.ready_event.set()
            if job_conn is not None:
                job_conn.close()

    def assert_active(self, *, stage: str) -> None:
        failure = self.failure
        if failure is not None:
            raise WorkerLeaseLifecycleError(
                f"compute job lease heartbeat failed during {stage}"
            ) from failure
        if self.stop_event.is_set() and not self._terminal:
            raise WorkerLeaseLifecycleError(
                f"compute job lease heartbeat stopped during {stage}"
            )

    def snapshot(self) -> dict:
        with self._state_lock:
            failure = self._failure
            contention_started = self._renewal_contention_started_mono
            return {
                "job_id": self.job_id,
                "fencing_token": self.fencing_token,
                "claim_expires_at": self._claim_expires_at,
                "renewals": self._renewals,
                "renewal_contention_active": contention_started is not None,
                "renewal_contention_age_seconds": (
                    0.0
                    if contention_started is None
                    else max(
                        0.0,
                        float(self._monotonic()) - contention_started,
                    )
                ),
                "renewal_contention_events": self._renewal_contention_events,
                "last_renewal_contention": self._last_renewal_contention,
                "failure": None if failure is None else type(failure).__name__,
                "thread_alive": self.thread.is_alive(),
            }

    def _record_failure(self, failure: BaseException) -> None:
        with self._state_lock:
            if self._failure is None:
                self._failure = failure
        self.stop_event.set()

    def _wait_for_renewal(self) -> bool:
        deadline = float(self._monotonic()) + self.renew_interval_seconds
        while True:
            if self.stop_event.is_set():
                return False
            remaining = deadline - float(self._monotonic())
            if remaining <= 0:
                return True
            self.stop_event.wait(min(0.25, remaining))

    def _connect_with_bounded_contention(self):
        while not self.stop_event.is_set():
            attempt_started = float(self._monotonic())
            try:
                job_conn = connect(
                    self.db_path,
                    clock=self._clock,
                    busy_timeout_seconds=self.renewal_busy_timeout_seconds,
                    configure_journal_mode=False,
                    required_journal_mode="wal",
                )
            except sqlite3.OperationalError as exc:
                if not self._is_sqlite_contention(exc):
                    raise
                self._record_renewal_contention(attempt_started, exc)
                self._assert_contention_budget()
                if not self._wait_for_contention_retry():
                    return None
                continue
            self._clear_active_contention()
            return job_conn
        return None

    def _renew_with_bounded_contention(self, job_conn) -> bool:
        while not self.stop_event.is_set():
            attempt_started = float(self._monotonic())
            try:
                with self._transition_lock:
                    if self._terminal or self.stop_event.is_set():
                        self._clear_active_contention()
                        return False
                    claim_expires_at = renew_job_lease(
                        job_conn,
                        self.job_id,
                        owner_id=self.owner_id,
                        fencing_token=self.fencing_token,
                        lease_seconds=self.lease_seconds,
                        now=self._clock(),
                    )
            except sqlite3.OperationalError as exc:
                if not self._is_sqlite_contention(exc):
                    raise
                job_conn.rollback()
                self._record_renewal_contention(attempt_started, exc)
                self._assert_contention_budget()
                if not self._wait_for_contention_retry():
                    return False
                continue
            with self._state_lock:
                self._claim_expires_at = float(claim_expires_at)
                self._renewals += 1
                # Keep success and contention-clear atomic for observers.
                self._renewal_contention_started_mono = None
            self._clear_active_contention()
            return True
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
        self,
        attempt_started: float,
        exc: sqlite3.OperationalError,
    ) -> None:
        with self._state_lock:
            if self._renewal_contention_started_mono is None:
                self._renewal_contention_started_mono = attempt_started
            self._renewal_contention_events += 1
            self._last_renewal_contention = str(
                getattr(exc, "sqlite_errorname", None) or "SQLITE_LOCKED"
            )[:64]

    def _clear_active_contention(self) -> None:
        with self._state_lock:
            self._renewal_contention_started_mono = None

    def _assert_contention_budget(self) -> None:
        with self._state_lock:
            started = self._renewal_contention_started_mono
            claim_expires_at = self._claim_expires_at
        now_mono = float(self._monotonic())
        age = 0.0 if started is None else max(0.0, now_mono - started)
        next_attempt_latest = (
            float(self._clock())
            + self.renewal_retry_seconds
            + self.renewal_busy_timeout_seconds
            + self.lease_safety_margin_seconds
        )
        if (
            age >= self.max_renewal_contention_seconds
            or next_attempt_latest >= claim_expires_at
        ):
            raise JobLeaseRenewalContentionExceeded(
                f"job {self.job_id} renewal contention exceeded its bounded budget"
            )

    def _wait_for_contention_retry(self) -> bool:
        deadline = float(self._monotonic()) + self.renewal_retry_seconds
        while True:
            if self.stop_event.is_set():
                return False
            remaining = deadline - float(self._monotonic())
            if remaining <= 0:
                return True
            self.stop_event.wait(min(0.25, remaining))

    @contextmanager
    def terminal_transition(self):
        """Serialize the terminal queue write against the final renewal."""

        with self._transition_lock:
            self.assert_active(stage="terminal publication")
            yield
            self._terminal = True
            self.stop_event.set()


def _raise_process_heartbeat_failure(
    heartbeat: _ProcessLeaseHeartbeat,
    *,
    stage: str,
) -> None:
    if heartbeat.failure is not None:
        raise WorkerLeaseLifecycleError(
            f"compute worker process lease renewal failed during {stage}"
        ) from heartbeat.failure


def _night_mode(flag: bool) -> bool:
    return flag or os.getenv("STRATEGY_LAB_NIGHT_MODE", "").strip().lower() in {"1", "true", "yes"}


def run_worker_once(
    private_root,
    *,
    night_mode: bool = False,
    ignore_cadence: bool = False,
    include_rejects: bool = False,
    allow_public_output: bool = False,
    verbose: bool = False,
) -> dict:
    """Run at most one queued job. Returns a status dict; raises only if a claimed
    job's execution fails (after recording it). Callers (the research cycle) can wrap
    this to record worker_failed without crashing the whole run.

    status in {deferred, queue_empty, completed} on return; on job-execution error the
    failure is recorded to the DB/status file and the exception is re-raised.
    """
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    if _legacy_worker_present(private_root):
        status_path = worker_status_path(private_root)
        write_worker_status(
            status_path,
            status="deferred",
            reason="worker_already_running",
            reason_code="legacy_worker_lock_present",
            wait_seconds=60,
            mode="unknown",
        )
        if verbose:
            print("deferred reason=worker_already_running wait_seconds=60")
        return {
            "status": "deferred",
            "reason": "worker_already_running",
            "reason_code": "legacy_worker_lock_present",
            "wait_seconds": 60,
            "mode": "unknown",
        }
    owner_id = f"worker-{os.getpid()}-{uuid.uuid4().hex}"
    ownership_path = private_root / "state" / "ownership.sqlite"
    ownership_store = OwnershipStore(
        ownership_path,
        identity_probe=probe_process_identity,
    )
    try:
        process_lease = ownership_store.acquire(
            resource_id="strategy_lab_worker",
            role_id="compute_worker",
            owner_id=owner_id,
            identity=current_process_identity(),
            lease_seconds=_LEASE_SECONDS,
        )
    except OwnershipConflictError as exc:
        ownership_store.close()
        status_path = worker_status_path(private_root)
        reason_code = str(exc) or type(exc).__name__
        if reason_code != "resource already owned":
            write_worker_status(
                status_path,
                status="failed",
                reason="worker_ownership_unavailable",
                reason_code=reason_code,
                mode="unknown",
            )
            raise WorkerLeaseLifecycleError(
                f"compute worker ownership unavailable: {reason_code}"
            ) from exc
        write_worker_status(
            status_path, status="deferred", reason="worker_already_running",
            reason_code="active_worker_owner",
            wait_seconds=60, mode="unknown",
        )
        return {
            "status": "deferred",
            "reason": "worker_already_running",
            "reason_code": "active_worker_owner",
            "wait_seconds": 60,
            "mode": "unknown",
        }
    process_heartbeat = _ProcessLeaseHeartbeat(
        ownership_path=ownership_path,
        process_lease=process_lease,
    )
    conn = None
    try:
        process_heartbeat.start()
        _raise_process_heartbeat_failure(
            process_heartbeat,
            stage="initialization",
        )
        policy = load_resource_policy(night_mode=_night_mode(night_mode))
        db_path = default_db_path(private_root)
        conn = connect(db_path)
        init_db(conn)
        cad_path = cadence_path(private_root)
        status_path = worker_status_path(private_root)
    except Exception:
        if conn is not None:
            conn.close()
        process_heartbeat.stop()
        try:
            ownership_store.release_local(process_lease)
        finally:
            ownership_store.close()
        raise
    try:
        recovered = recover_pending_publications(conn, private_root)
        if recovered and verbose:
            print(f"recovered pending publications={recovered}")
        stale = reap_stale_jobs(conn)
        if stale and verbose:
            print(f"requeued stale jobs={stale} db=strategy-lab/state/{db_path.name}")

        now = time.time()
        if not ignore_cadence:
            decision = evaluate_cadence(policy, load_recent_starts(cad_path), now)
            if not decision.allowed:
                if verbose:
                    print(f"deferred reason={decision.reason} wait_seconds={decision.wait_seconds} "
                          f"mode={policy.mode} (resource policy throttle)")
                write_worker_status(
                    status_path, status="deferred", reason=decision.reason,
                    wait_seconds=decision.wait_seconds, mode=policy.mode,
                )
                return {"status": "deferred", "reason": decision.reason,
                        "wait_seconds": decision.wait_seconds, "mode": policy.mode}

        job = claim_next_job(
            conn, owner_id=owner_id, lease_seconds=_LEASE_SECONDS,
        )
        if not job:
            if verbose:
                print(f"db=strategy-lab/state/{db_path.name} queue=empty")
            write_worker_status(status_path, status="queue_empty", mode=policy.mode)
            return {"status": "queue_empty", "mode": policy.mode}

        record_start(cad_path, now)
        job_id = int(job["job_id"])
        fencing_token = int(job["fencing_token"])
        job_heartbeat = _JobLeaseHeartbeat(
            db_path=db_path,
            job_id=job_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            claim_expires_at=float(job["claim_expires_at"]),
        )
        job_heartbeat.start()
        job_heartbeat.assert_active(stage="initialization")
        spec = None
        runtime_meta: dict = {}
        try:
            spec_path = Path(str(job["spec_path"]))
            expected_digest = str(job.get("materialization_digest") or "")
            if expected_digest:
                actual_digest = "sha256:" + hashlib.sha256(
                    spec_path.read_text(encoding="utf-8").encode("utf-8")
                ).hexdigest()
                if actual_digest != expected_digest:
                    raise RuntimeError(
                        "queued materialization digest does not match spec content"
                    )
            spec = ExperimentSpec.from_json(spec_path)
            mark_job_executing(
                conn, job_id, owner_id=owner_id,
                fencing_token=fencing_token,
            )
            write_worker_status(
                status_path,
                status="running",
                job_id=job_id,
                spec_path=str(job["spec_path"]),
                experiment_id=spec.experiment_id,
                symbols=len(spec.symbols),
                families=len(spec.families),
                max_runs=spec.max_runs,
                mode=policy.mode,
                job_lease=job_heartbeat.snapshot(),
            )
            if verbose:
                print(
                    f"started job_id={job_id} experiment={spec.experiment_id} "
                    f"symbols={len(spec.symbols)} families={len(spec.families)} "
                    f"max_runs={spec.max_runs or 'unlimited'} mode={policy.mode}",
                    flush=True,
                )
            cap, capped = effective_variant_cap(policy, spec.max_runs)
            if capped:
                raise RuntimeError(
                    "queued search-family resource policy drift: "
                    f"bound execution_cap={spec.max_runs or 'unlimited'} current_cap={cap}; "
                    "recompile instead of mutating the family at the worker"
                )
            # The worker reads one immutable bounded series from the canonical
            # candle library for all variants. JSON remains a migration fallback.
            last_progress_status = 0.0

            def compute_progress(stage: str) -> None:
                nonlocal last_progress_status
                _raise_process_heartbeat_failure(
                    process_heartbeat,
                    stage=f"evaluation:{stage}",
                )
                job_heartbeat.assert_active(stage=f"evaluation:{stage}")
                sampled = time.monotonic()
                if (
                    sampled - last_progress_status >= 5.0
                    or stage.startswith("evaluation_completed:")
                ):
                    write_worker_status(
                        status_path,
                        status="running",
                        job_id=job_id,
                        experiment_id=spec.experiment_id,
                        progress_stage=stage,
                        mode=policy.mode,
                        job_lease=job_heartbeat.snapshot(),
                    )
                    last_progress_status = sampled

            results = evaluate_spec(
                spec,
                runtime_meta,
                candle_store=CandleStore(private_root),
                progress=compute_progress,
            )
            job_heartbeat.assert_active(stage="provisional output")
            if verbose:
                print(f"backend requested={runtime_meta.get('requested_backend')} "
                      f"effective={runtime_meta.get('effective_backend')} "
                      f"gpu_available={runtime_meta.get('gpu_available')} "
                      f"accelerated_runs={runtime_meta.get('accelerated_runs')} "
                      f"elapsed_ms={runtime_meta.get('elapsed_ms')}"
                      + (f" fallback={runtime_meta.get('fallback_reason')}" if runtime_meta.get('fallback_reason') else ""))
            run_dir = write_run_outputs(
                spec, results, private_root,
                allow_public_output=allow_public_output, include_rejects=include_rejects,
                runtime_meta=runtime_meta,
                output_state="provisional",
                publication_generation={
                    "schema": "strategy_lab_publication_generation.v1",
                    "job_id": job_id,
                    "owner_id": owner_id,
                    "fencing_token": fencing_token,
                },
            )
            _raise_process_heartbeat_failure(
                process_heartbeat,
                stage="pre-publication",
            )
            with job_heartbeat.terminal_transition():
                final_dir, _ = publish_completed_job(
                    conn,
                    private_root,
                    run_dir,
                    job_id=job_id,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                )
            job_heartbeat.stop()
            _raise_process_heartbeat_failure(
                process_heartbeat,
                stage="secondary-index-publication",
            )
            index_error = ""
            try:
                publish_run_indexes(
                    spec, results, private_root, final_dir,
                    include_rejects=include_rejects,
                    allow_public_output=allow_public_output,
                )
                mark_publication_indexes_published(
                    conn, job_id, fencing_token
                )
            except Exception as exc:
                # Queue/run publication is already authoritative and must not
                # be rewritten as failed. The durable generation remains
                # directory_published for a bounded repair/rebuild.
                index_error = type(exc).__name__
            _raise_process_heartbeat_failure(
                process_heartbeat,
                stage="post-publication",
            )
            label = str(final_dir.relative_to(private_root)).replace("\\", "/")
            write_worker_status(
                status_path, status="completed", job_id=job_id, run_label=label,
                results=len(results), mode=policy.mode,
                index_publication_pending=bool(index_error),
                job_lease=job_heartbeat.snapshot(),
                index_error=index_error,
            )
            if verbose:
                print(f"completed job_id={job_id} run={label} results={len(results)}")
            return {"status": "completed", "job_id": job_id, "run_label": label,
                    "results": len(results), "mode": policy.mode,
                    "index_publication_pending": bool(index_error)}
        except Exception as exc:
            reason = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            if spec is not None:
                try:
                    failed_dir = (
                        Path(private_root)
                        / "experiments"
                        / "failed"
                        / f"job_{job_id}_{spec.search_family_id[:16]}"
                    )
                    failed_dir.mkdir(parents=True, exist_ok=True)
                    failure_runtime = {
                        **runtime_meta,
                        "worker_failure_type": type(exc).__name__,
                        "worker_failure_stage": "before_complete_outputs",
                    }
                    failure_evidence = build_search_trial_evidence(
                        spec, [], failure_runtime
                    )
                    write_search_trial_evidence(failed_dir, failure_evidence)
                except Exception:
                    # Preserve the original job failure. A failure to write the
                    # secondary ledger must not impersonate a successful run.
                    pass
            try:
                fail_job(
                    conn, job_id, reason, owner_id=owner_id,
                    fencing_token=fencing_token,
                )
            except Exception:
                # Losing the lease must not be disguised as an authoritative
                # failure transition by the stale worker.
                pass
            write_worker_status(
                status_path,
                status="failed",
                job_id=job_id,
                reason=reason[:300],
                mode=policy.mode,
                job_lease=job_heartbeat.snapshot(),
            )
            if verbose:
                print(f"failed job_id={job_id} error={exc}")
            raise
    finally:
        heartbeat = locals().get("job_heartbeat")
        if heartbeat is not None:
            heartbeat.stop()
        process_heartbeat.stop()
        conn.close()
        release_error = None
        try:
            ownership_store.release_local(process_lease)
        except Exception as exc:
            release_error = exc
            write_worker_status(
                worker_status_path(private_root),
                status="failed",
                reason="worker_process_lease_release_failed",
                reason_code=type(exc).__name__,
                mode="unknown",
            )
        finally:
            ownership_store.close()
        if release_error is not None:
            raise WorkerLeaseLifecycleError(
                "compute worker process lease release failed"
            ) from release_error


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Private strategy-lab root",
    )
    ap.add_argument("--night-mode", action="store_true", help="Opt in to relaxed night-mode resource limits")
    ap.add_argument("--ignore-cadence", action="store_true", help="Skip the throttle check (manual single run)")
    ap.add_argument("--include-rejects", action="store_true", help="Debug: also upsert REJECT rows into the candidate registry")
    ap.add_argument("--allow-public-output", action="store_true", help="Allow writing under this public repo")
    args = ap.parse_args()
    run_worker_once(
        args.private_root,
        night_mode=args.night_mode,
        ignore_cadence=args.ignore_cadence,
        include_rejects=args.include_rejects,
        allow_public_output=args.allow_public_output,
        verbose=True,
    )


if __name__ == "__main__":
    main()
