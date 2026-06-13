# -*- coding: utf-8 -*-
"""Import proposals a human saved from an LLM. Validates locally; never calls an API.

Reads a JSON file (a list of proposal objects, or {"proposals": [...]}), coerces
and validates each through the deterministic ProposalValidator, and writes
VALIDATED / REJECTED proposals into the private store. Malformed entries do not
crash the run — they are recorded as REJECTED with a reason. This does NOT queue
anything; run queue_validated_proposals separately. Dry-run by default.

    python -m scripts.strategy_lab.import_llm_proposals --file out.json --dry-run
    python -m scripts.strategy_lab.import_llm_proposals --file out.json --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.proposal_schema import (  # noqa: E402
    REJECTED,
    VALIDATED,
    Proposal,
    coerce_proposal,
    proposal_id_for,
)
from src.research_lab.proposal_store import proposals_path, upsert_proposals  # noqa: E402
from src.research_lab.proposal_validator import validate_and_mark  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402
from src.research_lab.universe import load_universe  # noqa: E402


def _items(raw: object) -> list[dict]:
    if isinstance(raw, dict) and isinstance(raw.get("proposals"), list):
        raw = raw["proposals"]
    return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []


def _reject_stub(item: dict, error: str, created_at: str) -> Proposal:
    return Proposal(
        proposal_id=proposal_id_for(item),
        created_by="llm_review",
        hypothesis=str(item.get("hypothesis") or "invalid proposal"),
        status=REJECTED,
        rejection_reason=f"schema_error:{error}"[:300],
        created_at=created_at,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="JSON file of LLM-produced proposals")
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
    created_at = dt.datetime.now(dt.timezone.utc).isoformat()

    try:
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read JSON file: {exc}")
        raise SystemExit(2) from None

    proposals: list[Proposal] = []
    for item in _items(raw):
        item = {**item, "created_by": item.get("created_by") or "llm_review"}
        try:
            proposal = coerce_proposal({**item, "created_at": item.get("created_at") or created_at})
            proposal = validate_and_mark(
                proposal, universe=universe, timeframe_profiles=profiles, resource_policy=policy
            )
        except ValueError as exc:
            proposal = _reject_stub(item, str(exc), created_at)
        proposals.append(proposal)

    validated = sum(1 for p in proposals if p.status == VALIDATED)
    rejected = sum(1 for p in proposals if p.status == REJECTED)
    for p in proposals:
        flag = "VALIDATED" if p.status == VALIDATED else f"REJECTED({p.rejection_reason})"
        print(f"proposal {p.proposal_id} {flag} family={p.setup_family or '-'}")
    print(f"imported={len(proposals)} validated={validated} rejected={rejected}")

    if not args.apply:
        print("dry-run: nothing written (use --apply to store proposals)")
        return
    stats = upsert_proposals(proposals_path(private_root), proposals)
    print(f"applied added={stats['added']} updated={stats['updated']} total={stats['total']}")


if __name__ == "__main__":
    main()
