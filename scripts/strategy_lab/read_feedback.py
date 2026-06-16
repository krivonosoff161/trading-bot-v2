# -*- coding: utf-8 -*-
"""Read hard-validation feedback + setup cards and print farm recommendations.

Deterministic and read-only by default. Aggregates per-candidate failure
feedback and passed setup cards into farm-level recommendations (promote /
suppress / narrow / widen / regime-sweep / require-more-data / reject).

    python -m scripts.strategy_lab.read_feedback            # print recommendations
    python -m scripts.strategy_lab.read_feedback --apply    # also persist to private root

No LLM. No queueing. No live trading. Turning a recommendation into a queued
sweep stays a separate, explicit step.
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

from src.research_lab.feedback_reader import build_recommendations, summarize  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.validation_feedback import load_feedback_queue  # noqa: E402


def _load_cards(private_root: Path) -> list[dict]:
    cards_dir = private_root / "setup_library" / "cards"
    if not cards_dir.exists():
        return []
    cards = []
    for path in sorted(cards_dir.glob("*.json")):
        try:
            cards.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return cards


def main() -> None:
    ap = argparse.ArgumentParser(description="Read hard-validation feedback -> farm recommendations.")
    ap.add_argument("--apply", action="store_true", help="Persist recommendations to private root")
    ap.add_argument("--limit", type=int, default=0, help="Max recommendations to print (0 = all)")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)

    feedback_rows = load_feedback_queue(private_root)
    cards = _load_cards(private_root)
    recs = build_recommendations(feedback_rows, cards)

    summary = summarize(recs)
    print("=== FEEDBACK -> FARM RECOMMENDATIONS ===")
    print(f"  feedback rows: {len(feedback_rows)}  setup cards: {len(cards)}")
    print(f"  recommendations: {summary['total']}")
    print(f"  by action:   {summary['by_action']}")
    print(f"  by priority: {summary['by_priority']}")
    print()

    shown = recs if args.limit <= 0 else recs[: args.limit]
    for rec in shown:
        print(f"  {rec.action:<18} {rec.strategy_id}/{rec.symbol}@{rec.timeframe}"
              f"  [{rec.hard_status}] {rec.reason}")

    if not args.apply:
        print()
        print("  (read-only: use --apply to persist recommendations.jsonl)")
        print("  Recommendations never auto-queue sweeps or touch live trading.")
        return

    out_dir = private_root / "hard_validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "recommendations.jsonl"
    out_path.write_text(
        "".join(json.dumps(r.to_dict(), ensure_ascii=False) + "\n" for r in recs),
        encoding="utf-8",
    )
    print()
    print(f"  wrote {len(recs)} recommendations to hard_validation/recommendations.jsonl (private root)")


if __name__ == "__main__":
    main()
