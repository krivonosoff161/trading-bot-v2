# -*- coding: utf-8 -*-
"""CLI: export hard validation requests from the candidate registry.

Usage:
    python scripts/strategy_lab/export_hard_validation_requests.py [--apply]
    [--limit N] [--status STATUS] [--include-regime-specific]
    [--since DATE] [--candidate-id ID] [--private-root PATH]

Default is dry-run: prints what would be exported, writes nothing.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.research_lab.hard_validation_export import export_requests
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export hard validation requests from candidate registry."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write request files (default: dry-run)",
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Max candidates to export",
    )
    parser.add_argument(
        "--status", type=str, default=None,
        help="Filter by exact status",
    )
    parser.add_argument(
        "--include-regime-specific", action="store_true",
        help="Also include REGIME_SPECIFIC candidates",
    )
    parser.add_argument(
        "--since", type=str, default=None,
        help="Only candidates created after this date",
    )
    parser.add_argument(
        "--candidate-id", type=str, default=None,
        help="Export specific candidate",
    )
    parser.add_argument(
        "--private-root", type=str, default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Path to private research root",
    )
    parser.add_argument("--allow-public-output", action="store_true")
    args = parser.parse_args()

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    dry_run = not args.apply

    mode = "DRY-RUN" if dry_run else "APPLY"
    print(f"[{mode}] Exporting hard validation requests")

    summary = export_requests(
        private_root,
        dry_run=dry_run,
        limit=args.limit,
        status_filter=args.status,
        include_regime_specific=args.include_regime_specific,
        since=args.since,
        candidate_id=args.candidate_id,
    )

    print(f"  Registry entries: {summary['total_registry']}")
    print(f"  Eligible found:   {summary['eligible_found']}")
    print(f"  Exported:         {summary['exported']}")
    if summary.get("skipped_no_artifact"):
        print(f"  Skipped (no artifact): {summary['skipped_no_artifact']}")
    if dry_run:
        print("  (dry-run: no files written)")
    else:
        print("  Requests written to: hard_validation/requests/ (private root)")


if __name__ == "__main__":
    main()
