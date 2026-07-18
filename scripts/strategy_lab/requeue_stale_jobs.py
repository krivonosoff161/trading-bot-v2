# -*- coding: utf-8 -*-
"""Requeue stale/running jobs when the operator confirms the worker is not alive.

Shows running/stale jobs by default (dry-run). Use --apply to actually change
their status back to queued so they can be picked up on next worker start.

    python -m scripts.strategy_lab.requeue_stale_jobs --dry-run
    python -m scripts.strategy_lab.requeue_stale_jobs --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.state_db import (  # noqa: E402
    connect,
    default_db_path,
    init_db,
    reap_stale_jobs as reap_expired_claims,
)
from src.research_lab.stop_intent import read_stop_intent  # noqa: E402

STALE_THRESHOLD_MINUTES = 30


def find_stale_jobs(db_path: Path, threshold_minutes: int = STALE_THRESHOLD_MINUTES) -> list[dict]:
    """Find running jobs whose fenced lease has expired (age is display-only)."""
    if not db_path.exists():
        return []
    conn = connect(db_path)
    try:
        init_db(conn)
        rows = conn.execute(
            """SELECT job_id, spec_path, created_at, started_at, claim_expires_at
               FROM queue WHERE status = 'running'"""
        ).fetchall()
        now = dt.datetime.now(dt.timezone.utc)
        now_epoch = time.time()
        stale = []
        for row in rows:
            started = row["started_at"]
            if float(row["claim_expires_at"] or 0) <= now_epoch:
                try:
                    started_dt = dt.datetime.fromisoformat(started)
                    if started_dt.tzinfo is None:
                        started_dt = started_dt.replace(tzinfo=dt.timezone.utc)
                    age_minutes = (now - started_dt).total_seconds() / 60
                    if age_minutes >= threshold_minutes:
                        stale.append({
                            "job_id": row["job_id"], "spec_path": row["spec_path"],
                            "started_at": started, "age_minutes": round(age_minutes, 1),
                        })
                except (ValueError, TypeError):
                    stale.append({
                        "job_id": row["job_id"], "spec_path": row["spec_path"],
                        "started_at": started, "age_minutes": -1,
                    })
        return stale
    finally:
        conn.close()


def requeue_stale_jobs(db_path: Path, threshold_minutes: int = STALE_THRESHOLD_MINUTES) -> int:
    """Requeue expired fenced claims; started_at age never grants authority."""
    if not db_path.exists():
        return 0
    conn = connect(db_path)
    try:
        init_db(conn)
        return reap_expired_claims(
            conn,
            max_age_seconds=max(1, int(threshold_minutes)) * 60,
            now=time.time(),
        )
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Requeue stale/running jobs (dry-run by default)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="Show stale jobs without changing them (default)")
    mode.add_argument("--apply", action="store_true",
                      help="Actually requeue stale running jobs")
    ap.add_argument("--threshold-minutes", type=int, default=STALE_THRESHOLD_MINUTES,
                    help=f"Minutes before a running job is considered stale (default {STALE_THRESHOLD_MINUTES})")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    db_path = default_db_path(private_root)

    stop = read_stop_intent(private_root)
    if stop:
        print(f"Stop intent active: requested at {stop.get('requested_at', '?')} reason={stop.get('reason', '?')}")

    stale = find_stale_jobs(db_path, args.threshold_minutes)
    if not stale:
        print("No stale running jobs found.")
    else:
        print(f"Found {len(stale)} stale running job(s) (threshold={args.threshold_minutes}min):")
        for job in stale:
            spec_label = Path(job.get("spec_path", "")).name or "unknown"
            print(f"  job_id={job['job_id']} spec={spec_label} "
                  f"started={job['started_at']} age={job['age_minutes']}min")

    if args.apply and stale:
        requeued = requeue_stale_jobs(db_path, args.threshold_minutes)
        print(f"Requeued {requeued} job(s).")
    elif args.apply:
        print("Nothing to requeue.")


if __name__ == "__main__":
    main()
