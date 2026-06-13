# -*- coding: utf-8 -*-
"""Export a candidate review pack for an LLM advisor. No paid API call here.

Always builds and writes a summaries-only pack from the private candidate
registry, even with no API key. Actually sending the pack to a model requires
BOTH an explicit env flag (STRATEGY_LAB_LLM_ENABLED=1) and the --send CLI flag;
no paid client is wired, so --send only reports that sending is not configured
and exits cleanly. Nothing here can spend money.

    python -m scripts.strategy_lab.export_llm_review_pack --limit 10
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402
from src.research_lab.review_export import export_review_pack  # noqa: E402


def _llm_enabled() -> bool:
    return os.getenv("STRATEGY_LAB_LLM_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10, help="Max candidates in the pack")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    ap.add_argument("--send", action="store_true", help="Attempt to send to an LLM (requires STRATEGY_LAB_LLM_ENABLED=1)")
    args = ap.parse_args()

    result = export_review_pack(
        Path(args.private_root),
        limit=max(1, args.limit),
        allow_public_output=args.allow_public_output,
    )
    print(
        f"review pack written under private root: {result['pack_label']} "
        f"candidates={result['candidate_count']} registry_entries={result['registry_entries']}"
    )
    if args.send:
        if _llm_enabled():
            print("LLM send requested but no paid client is configured; pack written only (no spend).")
        else:
            print("LLM send ignored: STRATEGY_LAB_LLM_ENABLED is not set. Pack written only.")
    else:
        print("LLM review is export-only (no API call). Review the pack manually.")


if __name__ == "__main__":
    main()
