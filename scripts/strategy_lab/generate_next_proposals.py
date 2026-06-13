# -*- coding: utf-8 -*-
"""Generate next-experiment proposals from the private candidate registry.

Deterministic, rule-based, no LLM. Dry-run by default (prints, writes nothing);
--apply upserts typed proposals into the private proposals store. Proposals are
research requests, not profitability claims, and are not queued here.

    python -m scripts.strategy_lab.generate_next_proposals --limit 10 --dry-run
    python -m scripts.strategy_lab.generate_next_proposals --limit 10 --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.candidate_registry import load_entries, registry_path  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.proposal_generator import generate_proposals_from_registry  # noqa: E402
from src.research_lab.proposal_schema import VALIDATED  # noqa: E402
from src.research_lab.proposal_store import proposals_path, upsert_proposals  # noqa: E402
from src.research_lab.proposal_validator import validate_and_mark  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402
from src.research_lab.universe import load_universe  # noqa: E402


def generate_proposals(private_root, *, limit: int = 10, apply: bool = False,
                       allow_public_output: bool = False) -> dict:
    """Generate + validate next-experiment proposals (and store them on apply).

    Returns a dict with counts and the proposal objects. Deterministic, no LLM, no
    network. Dry-run (apply=False) writes nothing.
    """
    private_root = resolve_private_root(Path(private_root), allow_public_output=allow_public_output)
    universe = load_universe()
    profiles = load_timeframe_profiles()
    policy = load_resource_policy()
    entries = load_entries(registry_path(private_root))
    created_at = dt.datetime.now(dt.timezone.utc).isoformat()
    raw = generate_proposals_from_registry(
        entries, universe=universe, created_at=created_at, limit=max(1, limit)
    )
    proposals = [
        validate_and_mark(p, universe=universe, timeframe_profiles=profiles, resource_policy=policy)
        for p in raw
    ]
    validated = sum(1 for p in proposals if p.status == VALIDATED)
    result = {
        "registry_entries": len(entries), "generated": len(proposals), "validated": validated,
        "proposals": proposals, "added": 0, "updated": 0, "total": 0, "applied": False,
    }
    if apply:
        stats = upsert_proposals(proposals_path(private_root), proposals)
        result.update(added=stats["added"], updated=stats["updated"], total=stats["total"], applied=True)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    res = generate_proposals(args.private_root, limit=args.limit, apply=args.apply,
                             allow_public_output=args.allow_public_output)
    for p in res["proposals"]:
        flag = "VALIDATED" if p.status == VALIDATED else f"REJECTED({p.rejection_reason})"
        print(f"proposal {p.proposal_id} {flag} rule={','.join(p.reason_codes)} "
              f"family={p.setup_family} tf={p.requested_timeframe} variants={p.max_variants}")
    print(f"registry_entries={res['registry_entries']} generated={res['generated']} validated={res['validated']}")

    if not args.apply:
        print("dry-run: nothing written (use --apply to store proposals)")
        return
    print(f"applied added={res['added']} updated={res['updated']} total={res['total']} validated={res['validated']}")


if __name__ == "__main__":
    main()
