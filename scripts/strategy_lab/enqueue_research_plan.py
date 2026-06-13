# -*- coding: utf-8 -*-
"""Build research jobs from a universe group + timeframe, then dry-run or apply.

Deterministic planning only. `--dry-run` prints the planned jobs and writes
nothing. `--apply` writes each spec under the private research root and queues it
idempotently (rerun does not duplicate identical pending jobs). Missing data is
reported as a skipped reason, never a crash. No LLM, no live trading.

    python -m scripts.strategy_lab.enqueue_research_plan --universe core_market --timeframe 1d --dry-run
    python -m scripts.strategy_lab.enqueue_research_plan --universe l2_high_beta --timeframe 15m --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.data_inventory import build_inventory  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.research_plan import ResearchPlan, build_research_plan  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.state_db import connect, default_db_path, ensure_experiment_queued, init_db  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402
from src.research_lab.universe import load_universe  # noqa: E402

DEFAULT_DATA_GLOB = "scripts/analysis/research/_okxhist/ai_scanner_feasibility/{symbol}_*.json"


def plan_spec_dir(private_root: Path) -> Path:
    return private_root / "plans" / "specs"


def _night_mode(flag: bool) -> bool:
    return flag or os.getenv("STRATEGY_LAB_NIGHT_MODE", "").strip().lower() in {"1", "true", "yes"}


def print_plan(plan: ResearchPlan) -> None:
    if not plan.jobs:
        print(f"plan group={plan.group} timeframe={plan.timeframe} jobs=0 (nothing to run)")
    for job in plan.jobs:
        print(
            f"job experiment={job.experiment_id} role={job.role} "
            f"symbols={len(job.symbols)} families={len(job.families)} variants={job.variant_count}"
        )
        print(f"     symbols: {', '.join(job.symbols)}")
        print(f"     families: {', '.join(job.families)}")
    for skip in plan.skipped:
        print(f"skipped symbol={skip.symbol} reason={skip.reason}")


def apply_plan(plan: ResearchPlan, private_root: Path, *, priority: int, allow_public_output: bool) -> dict:
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    out_dir = plan_spec_dir(private_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = default_db_path(private_root)
    conn = connect(db_path)
    init_db(conn)
    queued, already = 0, 0
    try:
        for job in plan.jobs:
            spec_path = out_dir / f"{job.experiment_id}.json"
            spec_path.write_text(json.dumps(job.spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _, created = ensure_experiment_queued(conn, spec_path.resolve(), priority=priority)
            queued += int(created)
            already += int(not created)
    finally:
        conn.close()
    return {"queued": queued, "already_pending": already, "db_label": f"strategy-lab/state/{db_path.name}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True, help="Universe group, e.g. core_market")
    ap.add_argument("--timeframe", default="1d", help="Timeframe profile key (1d/1h/15m)")
    ap.add_argument("--data-glob", default=DEFAULT_DATA_GLOB)
    ap.add_argument("--full", action="store_true", help="Use full per-timeframe caps instead of the smoke subset")
    ap.add_argument("--night-mode", action="store_true", help="Use relaxed night-mode resource limits")
    ap.add_argument("--priority", type=int, default=80)
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print plan, write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="Write specs and queue them")
    args = ap.parse_args()

    universe = load_universe()
    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=_night_mode(args.night_mode))
    symbols = list(universe.symbols_in(args.universe))
    inventory = build_inventory(args.data_glob, symbols) if symbols else None
    plan = build_research_plan(
        universe, profiles, policy,
        group=args.universe, timeframe=args.timeframe, data_glob=args.data_glob,
        inventory=inventory, smoke=not args.full,
    )
    print_plan(plan)
    if not args.apply:
        print("dry-run: nothing written (use --apply to queue)")
        return
    result = apply_plan(plan, Path(args.private_root), priority=args.priority, allow_public_output=args.allow_public_output)
    print(f"applied queued={result['queued']} already_pending={result['already_pending']} db={result['db_label']}")


if __name__ == "__main__":
    main()
