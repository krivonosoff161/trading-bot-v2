# -*- coding: utf-8 -*-
"""Generate private follow-up specs from the Strategy Lab candidate registry."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402
from src.research_lab.proposals import generate_and_write_from_registry  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-proposals", type=int, default=8)
    ap.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Private strategy-lab root",
    )
    ap.add_argument("--allow-public-output", action="store_true", help="Allow writing under this public repo")
    args = ap.parse_args()

    result = generate_and_write_from_registry(
        Path(args.private_root),
        max_proposals=max(1, args.max_proposals),
        allow_public_output=args.allow_public_output,
    )
    print(
        f"registry_entries={result['registry_entries']} generated={result['generated']} "
        f"written={result['written']} already_known={result['already_known']}"
    )
    for row in result["specs"]:
        print(
            f"proposal={row['proposal_id']} experiment={row['experiment_id']} "
            f"status={row['source_status']} spec={row['spec_label']}"
        )


if __name__ == "__main__":
    main()
