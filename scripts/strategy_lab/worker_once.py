# -*- coding: utf-8 -*-
"""Run one queued strategy-lab job.

This is the smallest safe 24/7 building block: an external loop can call it
periodically, while the worker itself handles one job and exits.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab import ExperimentSpec, evaluate_spec, write_run_outputs  # noqa: E402
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
    complete_job,
    connect,
    default_db_path,
    fail_job,
    import_run_dir,
    init_db,
    reap_stale_jobs,
)
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402

_WORKER_LOCK_MAX_AGE_SECONDS = 6 * 3600


def _worker_lock_path(private_root: Path) -> Path:
    return private_root / "state" / "worker.lock"


def _acquire_worker_lock(private_root: Path) -> tuple[bool, Path]:
    """Best-effort cross-process singleton lock for the quiet desktop worker."""
    path = _worker_lock_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    try:
        if path.exists() and now - path.stat().st_mtime > _WORKER_LOCK_MAX_AGE_SECONDS:
            path.unlink()
    except OSError:
        pass
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False, path
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"pid": os.getpid(), "created_at": time.time()}))
    return True, path


def _release_worker_lock(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


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
    locked, lock_path = _acquire_worker_lock(private_root)
    if not locked:
        status_path = worker_status_path(private_root)
        write_worker_status(status_path, status="deferred", reason="worker_already_running",
                            wait_seconds=60, mode="unknown")
        if verbose:
            print("deferred reason=worker_already_running wait_seconds=60")
        return {"status": "deferred", "reason": "worker_already_running", "wait_seconds": 60, "mode": "unknown"}
    policy = load_resource_policy(night_mode=_night_mode(night_mode))
    db_path = default_db_path(private_root)
    conn = connect(db_path)
    init_db(conn)
    cad_path = cadence_path(private_root)
    status_path = worker_status_path(private_root)
    try:
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

        job = claim_next_job(conn)
        if not job:
            if verbose:
                print(f"db=strategy-lab/state/{db_path.name} queue=empty")
            write_worker_status(status_path, status="queue_empty", mode=policy.mode)
            return {"status": "queue_empty", "mode": policy.mode}

        record_start(cad_path, now)
        job_id = int(job["job_id"])
        try:
            spec = ExperimentSpec.from_json(Path(str(job["spec_path"])))
            cap, capped = effective_variant_cap(policy, spec.max_runs)
            if capped:
                if verbose:
                    print(f"variant cap applied job_id={job_id} max_runs={spec.max_runs or 'unlimited'} -> {cap} mode={policy.mode}")
                spec = dataclasses.replace(spec, max_runs=cap)
            results = evaluate_spec(spec)
            run_dir = write_run_outputs(
                spec, results, private_root,
                allow_public_output=allow_public_output, include_rejects=include_rejects,
            )
            import_run_dir(conn, private_root, run_dir)
            conn.commit()
            label = str(run_dir.relative_to(private_root)).replace("\\", "/")
            complete_job(conn, job_id, label)
            write_worker_status(
                status_path, status="completed", job_id=job_id, run_label=label,
                results=len(results), mode=policy.mode,
            )
            if verbose:
                print(f"completed job_id={job_id} run={label} results={len(results)}")
            return {"status": "completed", "job_id": job_id, "run_label": label,
                    "results": len(results), "mode": policy.mode}
        except Exception as exc:
            reason = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            fail_job(conn, job_id, reason)
            write_worker_status(status_path, status="failed", job_id=job_id, reason=reason[:300], mode=policy.mode)
            if verbose:
                print(f"failed job_id={job_id} error={exc}")
            raise
    finally:
        conn.close()
        _release_worker_lock(lock_path)


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
