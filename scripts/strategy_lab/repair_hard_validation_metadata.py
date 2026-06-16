# -*- coding: utf-8 -*-
"""Backfill lost strategy timeframe in hard-validation + setup-library artifacts.

Dry-run by default: shows how many artifacts would be repaired and how many are
unrecoverable. ``--apply`` writes the recovered timeframe in place (private root
only). Deterministic, offline: no network, no LLM, no live trading.

    python -m scripts.strategy_lab.repair_hard_validation_metadata --dry-run
    python -m scripts.strategy_lab.repair_hard_validation_metadata --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.metadata_repair import repair_metadata  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Repair lost timeframe metadata in hard-validation artifacts.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Write repairs (default: dry-run)")
    mode.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    dry_run = not args.apply

    summary = repair_metadata(private_root, dry_run=dry_run)

    print(f"=== TIMEFRAME METADATA REPAIR [{'DRY-RUN' if dry_run else 'APPLY'}] ===")
    print(f"  recovery index size: {summary['index_size']} candidates")
    print()
    for name, counts in summary["artifacts"].items():
        print(f"  {name:<32} repaired={counts['repaired']:<4} unresolved={counts['unresolved']}")
    print()
    print(f"  TOTAL repaired={summary['total_repaired']}  unresolved (unrecoverable)={summary['total_unresolved']}")
    if dry_run:
        print("  (dry-run: nothing written; re-run with --apply to repair)")
    else:
        print("  artifacts updated under the private research root")
    if summary["total_unresolved"]:
        print("  note: unresolved rows have no recoverable timeframe and stay 'unknown' (honest).")


if __name__ == "__main__":
    main()
