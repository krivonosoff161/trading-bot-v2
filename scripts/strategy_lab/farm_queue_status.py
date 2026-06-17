# -*- coding: utf-8 -*-
"""Farm queue status — one read-only view of the whole scanner -> farm pipeline.

Shows: scanner rows seen, eligible, prepared-data count, queued jobs, skips + top
reasons, the farm queue (pending/running/completed/failed), and the age of the last
successful coordinator cycle. Read-only — it never queues a job or touches an order
path. Queueing is done by scanner_farm_loop / generate_event_sweeps.

    python -m scripts.strategy_lab.farm_queue_status
    python -m scripts.strategy_lab.farm_queue_status --include-expired --refill-universe core_market
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.farm_scheduler import DEFAULT_MAX_JOBS, plan_jobs  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402


def _load_watches(include_expired: bool) -> list[dict]:
    from src.scout.watch_queue import STATUS_EXPIRED, STATUS_OPEN, _read_rows, open_watches
    if not include_expired:
        return open_watches()
    return [r for r in _read_rows() if r.get("status") in {STATUS_OPEN, STATUS_EXPIRED}]


def _backlog(group: str | None) -> list[str]:
    if not group:
        return []
    try:
        from src.research_lab.universe import load_universe
        return list(load_universe().symbols_in(group))
    except Exception as exc:
        print(f"backlog universe '{group}' unavailable: {exc}")
        return []


def _pipeline_status(private_root: Path, state_path: str) -> dict:
    from src.research_lab.pipeline_state import PipelineState, state_db_path
    path = state_path or str(state_db_path(private_root))
    if not Path(path).exists():
        return {}
    state = PipelineState(path)
    try:
        return state.status()
    finally:
        state.close()


def _farm_queue_counts(private_root: Path) -> dict:
    from src.research_lab.state_db import dashboard_snapshot, default_db_path
    snap = dashboard_snapshot(default_db_path(private_root))
    return snap.get("queue_counts", {}) if snap.get("exists") else {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-jobs", type=int, default=DEFAULT_MAX_JOBS)
    ap.add_argument("--include-expired", action="store_true")
    ap.add_argument("--refill-universe", default="")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--state-path", default="")
    args = ap.parse_args()
    private_root = Path(args.private_root)

    watches = _load_watches(args.include_expired)
    plan = plan_jobs(watches, max_jobs=args.max_jobs, backlog_symbols=_backlog(args.refill_universe))
    counters = plan["counters"]

    print("Farm queue status (read-only; paper research, no orders)")
    print("-" * 60)
    print("[current queue preview — live recompute of the present watch_queue]")
    for key in ("watches_read", "resolved", "unresolved", "eligible", "data_usable", "data_too_short",
                "pending_recheck", "queued", "already_pending", "already_completed",
                "skipped_missing_instrument", "skipped_missing_data"):
        print(f"  {key:<28}: {counters.get(key, 0)}")

    pipe = _pipeline_status(private_root, args.state_path)
    if pipe:
        last = pipe.get("last_cycle") or {}
        print("\n[coordinator state]")
        print(f"  processed_watches           : {pipe.get('processed_watches', 0)}")
        print(f"  queued_jobs                 : {pipe.get('queued_jobs', 0)}")
        print(f"  prepared_data               : {pipe.get('prepared_data', 0)}")
        print(f"  total_cycles                : {pipe.get('total_cycles', 0)}")
        print(f"  last_cycle_age_seconds      : {last.get('age_seconds', 'n/a')}")
        cc = last.get("counters") or {}
        if cc:  # what the loop actually processed last cycle (from cycles.counters_json)
            print("  last_cycle_processed        : "
                  + ", ".join(f"{k}={cc.get(k, 0)}" for k in
                              ("watches_read", "eligible", "jobs_queued", "data_prepared", "skipped_total")))
        if pipe.get("top_skip_reasons"):
            print("  top_skip_reasons            : "
                  + ", ".join(f"{k}={v}" for k, v in pipe["top_skip_reasons"].items()))
    else:
        print("\n[coordinator state] none yet (run scanner_farm_loop --apply)")

    farm = _farm_queue_counts(private_root)
    print("\n[farm queue]")
    if farm:
        for status in ("queued", "running", "completed", "failed"):
            print(f"  {status:<28}: {farm.get(status, 0)}")
    else:
        print("  (no farm queue db yet)")

    print(f"\nplanned jobs ({len(plan['jobs'])}):")
    for job in plan["jobs"]:
        print(f"  [{job.priority}] {job.source_kind:<18} {job.okx_inst or job.symbol} "
              f"tf={job.timeframe or '?'} ({job.reason})")
    if plan["skipped"]:
        print(f"\nskipped ({len(plan['skipped'])}):")
        for row in plan["skipped"][:50]:
            print(f"  skip {row.get('symbol')}: {row.get('reason')}")

    print("\nnext: python -m scripts.strategy_lab.scanner_farm_loop --once --apply")


if __name__ == "__main__":
    main()
