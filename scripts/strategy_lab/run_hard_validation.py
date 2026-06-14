# -*- coding: utf-8 -*-
"""CLI: run hard validation on exported request files.

Usage:
    python scripts/strategy_lab/run_hard_validation.py [--apply]
    [--limit N] [--private-root PATH]

Default is dry-run: shows pending requests, writes nothing.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.research_lab.honest_backtest_bridge import (
    bridge_available,
    run_validation_batch,
)
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run hard validation on exported request files."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write reports and verdicts (default: dry-run)",
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Max candidates to validate",
    )
    parser.add_argument(
        "--private-root", type=str, default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Path to private research root",
    )
    parser.add_argument("--allow-public-output", action="store_true")
    args = parser.parse_args()

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    requests_dir = private_root / "hard_validation" / "requests"
    dry_run = not args.apply

    mode = "DRY-RUN" if dry_run else "APPLY"
    print(f"[{mode}] Running hard validation")
    print(f"  Bridge status: {bridge_available()}")

    if not requests_dir.exists():
        print("  No requests directory: hard_validation/requests/ (private root)")
        print("  Run export_hard_validation_requests.py first.")
        return

    requests = list(requests_dir.glob("*.json"))
    print(f"  Pending requests: {len(requests)}")

    if not requests:
        print("  Nothing to validate.")
        return

    summary = run_validation_batch(
        requests_dir, private_root, dry_run=dry_run, limit=args.limit,
    )

    print(f"  Validated: {summary['validated']}")
    print(f"  Errors:    {summary['errors']}")
    for r in summary.get("results", []):
        status = r.get("hard_status", r.get("error", "?"))
        cid = r.get("candidate_id", "?")
        print(f"    {cid}: {status}")

    if dry_run:
        print("  (dry-run: no files written)")
    else:
        print("  Reports: hard_validation/reports/ (private root)")
        print("  Verdicts: hard_validation/verdicts/ (private root)")


if __name__ == "__main__":
    main()
