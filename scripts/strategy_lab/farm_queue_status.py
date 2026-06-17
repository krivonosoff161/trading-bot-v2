# -*- coding: utf-8 -*-
"""Farm queue status — visible counters for the scanner -> farm hand-off.

Reads open scanner watches (logs/scout/watch_queue.jsonl), runs the pure farm
scheduler, and prints the operator-visible status counters (resolved/unresolved,
data usable/too-short/pending, queued, skipped-missing-instrument/data, ...) plus
the prioritized plan and the explicit skip reasons. Read-only by default — it
NEVER queues a job or touches an order path. Queueing stays in
``generate_event_sweeps --from-scanner``.

    python -m scripts.strategy_lab.farm_queue_status
    python -m scripts.strategy_lab.farm_queue_status --include-expired --refill-universe core_market
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.farm_scheduler import DEFAULT_MAX_JOBS, plan_jobs  # noqa: E402


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-jobs", type=int, default=DEFAULT_MAX_JOBS)
    ap.add_argument("--include-expired", action="store_true",
                    help="Backlog mode: include expired WATCH/GO rows too")
    ap.add_argument("--refill-universe", default="",
                    help="Universe group to top up the plan when fresh WATCH/GO is thin")
    args = ap.parse_args()

    watches = _load_watches(args.include_expired)
    plan = plan_jobs(watches, max_jobs=args.max_jobs, backlog_symbols=_backlog(args.refill_universe))

    print("Farm queue status (read-only; paper research, no orders)")
    print("-" * 56)
    counters = plan["counters"]
    for key in ("watches_read", "resolved", "unresolved", "data_usable", "data_too_short",
                "pending_recheck", "queued", "already_pending", "already_completed",
                "skipped_missing_instrument", "skipped_missing_data"):
        print(f"  {key:<28}: {counters.get(key, 0)}")

    print(f"\nplanned jobs ({len(plan['jobs'])}):")
    for job in plan["jobs"]:
        tf = job.timeframe or "?"
        print(f"  [{job.priority}] {job.source_kind:<18} {job.okx_inst or job.symbol} tf={tf} ({job.reason})")

    if plan["skipped"]:
        print(f"\nskipped ({len(plan['skipped'])}):")
        for row in plan["skipped"][:50]:
            print(f"  skip {row.get('symbol')}: {row.get('reason')}")

    print("\nnext: python -m scripts.strategy_lab.generate_event_sweeps --from-scanner --dry-run")


if __name__ == "__main__":
    main()
