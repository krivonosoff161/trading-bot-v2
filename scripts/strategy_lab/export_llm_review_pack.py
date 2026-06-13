# -*- coding: utf-8 -*-
"""Export a candidate review pack for an LLM advisor. No paid API call here.

Always builds and writes a summaries-only pack from the private candidate
registry, even with no API key. Actually SENDING the pack to a model goes through
the gated boundary in `llm_review_sender.py`: it requires the --send flag, env
STRATEGY_LAB_LLM_ENABLED=1, a configured provider, and a daily budget cap. The
only sender shipped today is NullReviewSender (never calls a network), so --send
always falls back to export-only and explains why. Nothing here can spend money.

    python -m scripts.strategy_lab.export_llm_review_pack --limit 10
    python -m scripts.strategy_lab.export_llm_review_pack --limit 10 --send   # still export-only today
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.llm_review_sender import (  # noqa: E402
    NullReviewSender,
    daily_cap,
    env_enabled,
    record_send_intent,
    send_review_pack,
)
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.review_export import export_review_pack  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10, help="Max candidates in the pack")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    ap.add_argument("--send", action="store_true", help="Attempt a gated send (still export-only today)")
    args = ap.parse_args()

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    result = export_review_pack(
        private_root,
        limit=max(1, args.limit),
        allow_public_output=args.allow_public_output,
    )
    print(
        f"review pack written under private root: {result['pack_label']} "
        f"candidates={result['candidate_count']} registry_entries={result['registry_entries']}"
    )
    if not args.send:
        print("LLM review is export-only (no API call). Review the pack manually.")
        return

    sender = NullReviewSender()
    decision, send_result = send_review_pack(
        result.get("pack") or {},
        sender=sender,
        dry_run=False,
        send_requested=True,
        cap=daily_cap(),
        enabled=env_enabled(),
        meta={"pack_label": result["pack_label"]},
    )
    record_send_intent(
        private_root, decision=decision, pack_label=result["pack_label"],
        provider=sender.name, result=send_result,
    )
    if decision.allowed and send_result and send_result.status == "sent":
        print(f"LLM send: sent via {sender.name} (recorded privately).")
    else:
        reason = decision.reason if not decision.allowed else (send_result.reason if send_result else "unknown")
        print(f"LLM send skipped (export-only): {reason}. No API call, no spend.")


if __name__ == "__main__":
    main()
