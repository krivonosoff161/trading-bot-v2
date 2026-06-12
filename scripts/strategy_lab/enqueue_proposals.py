# -*- coding: utf-8 -*-
"""Queue generated private proposal specs."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.strategy_lab.enqueue_pack import discover_specs, enqueue_pack  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.proposals import spec_dir  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--priority", type=int, default=70)
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Private strategy-lab root",
    )
    ap.add_argument("--allow-public-output", action="store_true", help="Allow writing under this public repo")
    args = ap.parse_args()

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    specs = discover_specs(spec_dir(private_root))[: max(1, args.limit)]
    result = enqueue_pack(
        specs,
        private_root=private_root,
        priority=args.priority,
        ensure=True,
        allow_public_output=args.allow_public_output,
    )
    print(
        f"proposal_specs={len(specs)} queued={result['queued']} "
        f"already_pending={result['already_pending']} db={result['db_label']}"
    )


if __name__ == "__main__":
    main()
