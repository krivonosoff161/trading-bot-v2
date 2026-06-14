# -*- coding: utf-8 -*-
"""Full hard-validation pipeline command.

Runs: export → validate → feedback → setup library → summary.

Default: dry-run. No network. No LLM. No live trading.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.research_lab.hard_validation_contract import (
    HardValidationReport,
)
from src.research_lab.hard_validation_export import export_requests
from src.research_lab.honest_backtest_bridge import (
    bridge_available,
    run_validation_batch,
)
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
from src.research_lab.setup_library import build_setup_card, write_setup_library
from src.research_lab.validation_feedback import generate_feedback, write_feedback


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Full hard-validation pipeline."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write artifacts (default: dry-run)",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Max candidates per step",
    )
    parser.add_argument(
        "--private-root", type=str, default=str(DEFAULT_PRIVATE_ROOT),
        help="Path to private research root",
    )
    args = parser.parse_args()

    private_root = Path(args.private_root)
    dry_run = not args.apply
    mode = "DRY-RUN" if dry_run else "APPLY"

    print(f"=== HARD VALIDATION PIPELINE [{mode}] ===")
    print(f"Private root: {private_root}")
    print()

    status = bridge_available()
    print(f"Bridge: numpy={status['numpy']}, "
          f"backtest_sanity={status['backtest_sanity']}")
    print()

    # Step 1: Export
    print("--- Step 1: Export candidates ---")
    export_summary = export_requests(
        private_root, dry_run=dry_run, limit=args.limit,
    )
    print(f"  Registry entries: {export_summary['total_registry']}")
    print(f"  Eligible found:   {export_summary['eligible_found']}")
    print(f"  Exported:         {export_summary['exported']}")
    print()

    # Step 2: Validate
    print("--- Step 2: Hard validation ---")
    requests_dir = private_root / "hard_validation" / "requests"
    val_summary = run_validation_batch(
        requests_dir, private_root, dry_run=dry_run, limit=args.limit,
    )
    print(f"  Total requests:   {val_summary['total']}")
    print(f"  Validated:        {val_summary['validated']}")
    print(f"  Errors:           {val_summary['errors']}")
    print()

    # Step 3: Feedback
    print("--- Step 3: Generate feedback ---")
    feedback_count = 0
    verdicts_dir = private_root / "hard_validation" / "verdicts"
    if verdicts_dir.exists():
        for vf in sorted(verdicts_dir.glob("*.json"))[:args.limit]:
            try:
                vdata = json.loads(vf.read_text(encoding="utf-8"))
                report = HardValidationReport(
                    candidate_id=vdata.get("candidate_id", ""),
                    symbol="",
                    timeframe="",
                    strategy_id="",
                    verdict=vdata,
                    checks_summary={},
                )
                fb = generate_feedback(report)
                if fb:
                    write_feedback(private_root, fb, dry_run=dry_run)
                    feedback_count += 1
            except Exception:
                continue
    print(f"  Feedback entries: {feedback_count}")
    print()

    # Step 4: Setup library
    print("--- Step 4: Build setup library ---")
    reports_dir = private_root / "hard_validation" / "reports"
    cards = []
    if reports_dir.exists():
        for rf in sorted(reports_dir.glob("*.json"))[:args.limit]:
            try:
                rdata = json.loads(rf.read_text(encoding="utf-8"))
                card = build_setup_card(rdata)
                cards.append(card)
            except Exception:
                continue
    lib_summary = write_setup_library(private_root, cards, dry_run=dry_run)
    print(f"  Setup cards: {lib_summary['cards_written']}")
    print()

    # Summary
    print("=== PIPELINE SUMMARY ===")
    print(f"  Candidates eligible:    {export_summary['eligible_found']}")
    print(f"  Requests exported:      {export_summary['exported']}")
    print(f"  Validated:              {val_summary['validated']}")
    print(f"  Feedback entries:       {feedback_count}")
    print(f"  Setup cards created:    {lib_summary['cards_written']}")
    print("  Main engine ready:      0 (disabled by design)")
    print()

    if dry_run:
        print("  (dry-run: no files written)")
        print("  To apply: run with --apply")
    else:
        print(f"  Artifacts in: {private_root}")


if __name__ == "__main__":
    main()
