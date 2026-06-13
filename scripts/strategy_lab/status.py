# -*- coding: utf-8 -*-
"""One-glance, read-only Strategy Lab status for the operator.

Prints worker state, queue counts, runs/candidates, proposals, the private output
location and the safety posture (no live trading, no API by default, proposal
apply is manual). Reads only; never writes, never calls an API, honors
TRADING_BOT_RESEARCH_ROOT.

    python -m scripts.strategy_lab.status
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.dashboard_state import load_dashboard_state  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402


def _worker_line(state: dict) -> str:
    ws = state.get("worker_status") or {}
    nr = state.get("next_run") or {}
    status = str(ws.get("status") or "")
    if status == "failed":
        return f"error (last failure: {ws.get('reason', '')})"
    if not nr.get("allowed", True):
        return f"deferred ({nr.get('reason', '')}, wait {nr.get('wait_seconds', 0)}s)"
    if not status:
        return "never run yet"
    when = ws.get("updated_at", "")
    return f"idle (last: {status} at {when})"


def _fmt_counts(counts: dict) -> str:
    return ", ".join(f"{k}: {v}" for k, v in sorted((counts or {}).items())) or "none"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    args = ap.parse_args()

    root = Path(args.private_root).expanduser()
    configured = "configured (env)" if os.getenv("TRADING_BOT_RESEARCH_ROOT") else "default"
    exists = "exists" if root.exists() else "NOT created yet"

    state = load_dashboard_state(root)
    state_db = state.get("state_db") or {}
    totals = state.get("totals") or {}
    latest = state.get("latest_run") or {}
    registry = state.get("candidate_registry") or {}
    proposals = state.get("proposals") or {}
    llm = state.get("llm_review") or {}

    print("Strategy Lab status")
    print("-" * 48)
    print(f"Private root : {configured}, {exists}")
    print(f"              label: {state.get('private_root_label', 'strategy-lab')}")
    print(f"Worker       : {_worker_line(state)}")
    print(f"Queue        : {_fmt_counts(state_db.get('queue_counts'))}")
    print(f"Runs         : {totals.get('run_count', 0)} total; latest verdicts: "
          f"{_fmt_counts(latest.get('reducer_verdicts'))}")
    print(
        f"Candidates   : {registry.get('entries', 0)} registry rows, "
        f"{registry.get('unique_candidates', 0)} unique "
        f"({_fmt_counts(registry.get('by_validation_status'))})"
    )
    print(f"Proposals    : {proposals.get('total', 0)} ({_fmt_counts(proposals.get('by_status'))}); "
          f"validated waiting for queue: {proposals.get('validated_waiting', 0)}")
    print(f"Obsidian     : {state.get('obsidian_notes', 0)} candidate notes")
    print("-" * 48)
    print(f"LLM review   : export-only; auto-send {'ENABLED' if llm.get('auto_send') else 'disabled'}")
    print("Proposal apply: manual only (queue requires explicit --apply)")
    print("Safety       : no live trading, no order engine, no paid API by default")
    print("Dashboard    : python scripts/strategy_lab/serve_dashboard.py  -> http://127.0.0.1:8765")


if __name__ == "__main__":
    main()
