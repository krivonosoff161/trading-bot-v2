# -*- coding: utf-8 -*-
"""Queue VALIDATED proposals into the private SQLite queue. Dry-run by default.

Compiles each VALIDATED proposal into a bounded ExperimentSpec, writes the spec
under the private root, and enqueues it idempotently. Re-validates defensively
before queueing. Only VALIDATED proposals are touched; PROPOSED/REJECTED are left
alone. Status flips to QUEUED on apply. No LLM, no public output.

    python -m scripts.strategy_lab.queue_validated_proposals --dry-run
    python -m scripts.strategy_lab.queue_validated_proposals --apply
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

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.proposal_schema import QUEUED, VALIDATED  # noqa: E402
from src.research_lab.proposal_store import (  # noqa: E402
    load_proposals,
    proposals_path,
    queued_spec_dir,
    set_status,
)
from src.research_lab.proposal_validator import compile_proposal, validate_proposal  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.state_db import connect, default_db_path, ensure_experiment_queued, init_db  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402
from src.research_lab.universe import load_universe  # noqa: E402


def _exp_to_dict(exp) -> dict:
    return {
        "experiment_id": exp.experiment_id,
        "data_glob": exp.data_glob,
        "symbols": exp.symbols,
        "families": exp.families,
        "fees_bps": exp.fees_bps,
        "slippage_bps": exp.slippage_bps,
        "min_trades": exp.min_trades,
        "split_ratio": exp.split_ratio,
        "max_runs": exp.max_runs,
        "parameter_grid": exp.parameter_grid,
        "filters": exp.filters,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--priority", type=int, default=72)
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    universe = load_universe()
    profiles = load_timeframe_profiles()
    policy = load_resource_policy()
    proposals = [p for p in load_proposals(proposals_path(private_root)) if p.status == VALIDATED]

    ready = []
    for p in proposals:
        outcome = validate_proposal(p, universe=universe, timeframe_profiles=profiles, resource_policy=policy)
        if outcome.status != VALIDATED:
            print(f"skip {p.proposal_id} no_longer_valid={','.join(outcome.reason_codes)}")
            continue
        ready.append(p)
        print(f"ready {p.proposal_id} family={p.setup_family} symbols={len(p.symbols)} variants={p.max_variants}")
    print(f"validated_waiting={len(proposals)} ready={len(ready)}")

    if not args.apply:
        print("dry-run: nothing queued (use --apply)")
        return

    out_dir = queued_spec_dir(private_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(default_db_path(private_root))
    init_db(conn)
    queued, already, skipped_full = 0, 0, 0
    try:
        pending = int(conn.execute(
            "SELECT COUNT(*) FROM queue WHERE status IN ('queued', 'running')"
        ).fetchone()[0])
        for p in ready:
            if pending + queued >= policy.max_queue_size:
                skipped_full += 1
                continue
            exp = compile_proposal(p, policy=policy)
            spec_path = out_dir / f"{exp.experiment_id}.json"
            spec_path.write_text(json.dumps(_exp_to_dict(exp), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _, created = ensure_experiment_queued(conn, spec_path.resolve(), priority=args.priority)
            queued += int(created)
            already += int(not created)
            set_status(proposals_path(private_root), p.proposal_id, QUEUED)
    finally:
        conn.close()
    msg = f"applied queued={queued} already_pending={already}"
    if skipped_full:
        msg += f" skipped_queue_full={skipped_full} (max_queue_size={policy.max_queue_size})"
    print(msg)


if __name__ == "__main__":
    main()
