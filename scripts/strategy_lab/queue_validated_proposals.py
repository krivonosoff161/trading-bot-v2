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

from src.research_lab.data_readiness import (  # noqa: E402
    MALFORMED,
    MISSING_DATA,
    TOO_SHORT,
    assess_proposal,
)
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
        "timeframe": exp.timeframe,
        "families": exp.families,
        "fees_bps": exp.fees_bps,
        "slippage_bps": exp.slippage_bps,
        "min_trades": exp.min_trades,
        "split_ratio": exp.split_ratio,
        "max_runs": exp.max_runs,
        "parameter_grid": exp.parameter_grid,
        "filters": exp.filters,
    }


def queue_validated(private_root, *, priority: int = 72, apply: bool = False,
                    max_queue: int | None = None, require_data_ready: bool = False,
                    allow_public_output: bool = False, night_mode: bool = False) -> dict:
    """Queue VALIDATED proposals (idempotent, capped). Dry-run (apply=False) queues nothing.

    The effective queue cap is the resource policy's max_queue_size, optionally lowered
    by `max_queue`. With require_data_ready=True, a proposal whose primary data is
    missing/too short/malformed is NOT queued — it goes to skipped_not_ready with a
    reason (and a suggested prepare command if 1m is missing), so a job never runs on
    incomplete data. Returns counts; no LLM, no network, no public output.
    """
    private_root = resolve_private_root(Path(private_root), allow_public_output=allow_public_output)
    universe = load_universe()
    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=night_mode)
    cap = policy.max_queue_size if max_queue is None else min(policy.max_queue_size, max(0, int(max_queue)))
    proposals = [p for p in load_proposals(proposals_path(private_root)) if p.status == VALIDATED]

    validated_ready = []
    skipped_invalid = []
    for p in proposals:
        outcome = validate_proposal(p, universe=universe, timeframe_profiles=profiles, resource_policy=policy)
        if outcome.status != VALIDATED:
            skipped_invalid.append({"id": p.proposal_id, "reasons": list(outcome.reason_codes)})
            continue
        validated_ready.append(p)

    # Data-completeness gate: never queue a job whose data is not ready.
    ready = []
    skipped_not_ready = []
    counts_not_ready = {MISSING_DATA: 0, TOO_SHORT: 0, MALFORMED: 0}
    if require_data_ready:
        for p in validated_ready:
            readiness = assess_proposal(p, private_root=private_root)
            if readiness.is_ready():
                ready.append(p)
            else:
                counts_not_ready[readiness.status] = counts_not_ready.get(readiness.status, 0) + 1
                skipped_not_ready.append({"id": p.proposal_id, "status": readiness.status,
                                          "suggested_command": readiness.suggested_command})
    else:
        ready = validated_ready

    result = {
        "validated_waiting": len(proposals), "ready": len(ready),
        "ready_items": [{"id": p.proposal_id, "family": p.setup_family,
                         "symbols": len(p.symbols), "variants": p.max_variants} for p in ready],
        "skipped_invalid": skipped_invalid,
        "skipped_not_ready": skipped_not_ready,
        "skipped_missing_data": counts_not_ready[MISSING_DATA],
        "skipped_too_short": counts_not_ready[TOO_SHORT],
        "skipped_malformed": counts_not_ready[MALFORMED],
        "max_queue": cap,
        "queued": 0, "already_pending": 0, "skipped_full": 0, "applied": False,
    }
    if not apply:
        return result

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
            if pending + queued >= cap:
                skipped_full += 1
                continue
            exp = compile_proposal(p, policy=policy)
            spec_path = out_dir / f"{exp.experiment_id}.json"
            spec_path.write_text(json.dumps(_exp_to_dict(exp), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _, created = ensure_experiment_queued(conn, spec_path.resolve(), priority=priority)
            queued += int(created)
            already += int(not created)
            set_status(proposals_path(private_root), p.proposal_id, QUEUED)
    finally:
        conn.close()
    result.update(queued=queued, already_pending=already, skipped_full=skipped_full, applied=True)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--priority", type=int, default=72)
    ap.add_argument("--max-queue", type=int, default=None, help="Optional lower cap (clamped to policy)")
    ap.add_argument("--require-data-ready", action="store_true",
                    help="Only queue proposals whose primary data is ready (skip missing/short/malformed)")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    res = queue_validated(args.private_root, priority=args.priority, apply=args.apply,
                          max_queue=args.max_queue, require_data_ready=args.require_data_ready,
                          allow_public_output=args.allow_public_output)
    for item in res["skipped_invalid"]:
        print(f"skip {item['id']} no_longer_valid={','.join(item['reasons'])}")
    for item in res.get("skipped_not_ready", []):
        cmd = f" -> {item['suggested_command']}" if item.get("suggested_command") else ""
        print(f"skip {item['id']} data_not_ready={item['status']}{cmd}")
    for item in res["ready_items"]:
        print(f"ready {item['id']} family={item['family']} symbols={item['symbols']} variants={item['variants']}")
    print(f"validated_waiting={res['validated_waiting']} ready={res['ready']}")

    if not args.apply:
        print("dry-run: nothing queued (use --apply)")
        return
    msg = f"applied queued={res['queued']} already_pending={res['already_pending']}"
    if res["skipped_full"]:
        msg += f" skipped_queue_full={res['skipped_full']} (max_queue={res['max_queue']})"
    print(msg)


if __name__ == "__main__":
    main()
