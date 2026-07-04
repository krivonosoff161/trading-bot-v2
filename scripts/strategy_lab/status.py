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
from src.research_lab.paper_readiness import summarize_paper_readiness  # noqa: E402
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


def _count_files(root: Path, rel: str, pattern: str = "*.json") -> int:
    path = root / rel
    if not path.exists():
        return 0
    return len(list(path.glob(pattern)))


def _count_jsonl_rows(root: Path, rel: str, filename: str) -> int:
    path = root / rel / filename
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", default=None)
    args = ap.parse_args()

    env_root = os.getenv("TRADING_BOT_RESEARCH_ROOT")
    if args.private_root:
        root = Path(args.private_root).expanduser()
        configured = "configured (--private-root)"
    elif env_root:
        root = Path(env_root).expanduser()
        configured = "configured (env)"
    else:
        root = DEFAULT_PRIVATE_ROOT
        configured = "default"
    exists = "exists" if root.exists() else "NOT created yet"

    state = load_dashboard_state(root)
    state_db = state.get("state_db") or {}
    totals = state.get("totals") or {}
    latest = state.get("latest_run") or {}
    registry = state.get("candidate_registry") or {}
    proposals = state.get("proposals") or {}
    llm = state.get("llm_review") or {}
    microscope = state.get("event_microscope") or {}
    prep = state.get("last_prepare_1m") or {}
    market_prep = state.get("last_prepare_market_data") or {}
    prep_cfg = state.get("prepare_workflow") or {}
    cycle = state.get("last_cycle") or {}
    session = state.get("last_session") or {}
    loop = state.get("last_loop") or {}
    llm_loop = state.get("llm_loop") or {}
    farm_cockpit = state.get("farm_cockpit") or {}

    print("Strategy Lab status")
    print("-" * 48)
    print(f"Private root : {configured}, {exists}")
    print(f"              label: {state.get('private_root_label', 'strategy-lab')}")
    print(f"Worker       : {_worker_line(state)}")
    qc = state_db.get("queue_counts") or {}
    print(
        f"Queue        : pending: {qc.get('queued', 0)}, running: {qc.get('running', 0)}, "
        f"completed: {qc.get('completed', 0)}, failed: {qc.get('failed', 0)}"
    )
    print(f"Runs         : {totals.get('run_count', 0)} total; latest verdicts: "
          f"{_fmt_counts(latest.get('reducer_verdicts'))}")
    print(
        f"Candidates   : {registry.get('entries', 0)} registry rows, "
        f"{registry.get('unique_candidates', 0)} unique "
        f"({_fmt_counts(registry.get('by_validation_status'))})"
    )
    print(f"Proposals    : {proposals.get('total', 0)} ({_fmt_counts(proposals.get('by_status'))}); "
          f"validated waiting for queue: {proposals.get('validated_waiting', 0)}")
    print(
        "Hard valid.  : "
        f"requests: {_count_files(root, 'hard_validation/requests')}, "
        f"reports: {_count_files(root, 'hard_validation/reports')}, "
        f"verdicts: {_count_files(root, 'hard_validation/verdicts')}, "
        f"feedback rows: {_count_jsonl_rows(root, 'hard_validation/feedback', 'feedback.jsonl')}"
    )
    print(
        "Setup library: "
        f"cards: {_count_files(root, 'setup_library/cards')}, "
        f"reports: {_count_files(root, 'setup_library/reports', '*.md')}, "
        f"index rows: {_count_jsonl_rows(root, 'setup_library', 'setup_index.jsonl')}"
    )
    paper_ready = summarize_paper_readiness(root, check_local_data=False)
    blockers = paper_ready.get("blocked_reasons") or {}
    blocker_text = " ".join(f"{k}={v}" for k, v in list(blockers.items())[:4]) or "none"
    print(
        "Paper ready  : "
        f"checked={paper_ready.get('checked_cards', 0)}, "
        f"ready={paper_ready.get('paper_forward_ready', 0)}, "
        f"plan_ready={paper_ready.get('plan_ready', 0)}; "
        f"blocked={blocker_text}"
    )
    lifecycle = farm_cockpit.get("lifecycle") or {}
    results = farm_cockpit.get("results") or {}
    if lifecycle.get("available"):
        print(
            "Farm core    : "
            f"tasks {_fmt_counts(lifecycle.get('by_state'))}; "
            f"unique={lifecycle.get('unique_candidates', 0)}; "
            f"hard={_fmt_counts(lifecycle.get('validation'))}; "
            f"paper={_fmt_counts(lifecycle.get('paper_status'))}"
        )
        print(
            "Paper loop   : "
            f"outcomes={results.get('paper_outcomes', 0)}; "
            f"farm paper={_fmt_counts(results.get('paper_status'))}"
        )
    else:
        print("Farm core    : not initialized (run farm_loop --once --dry-run/apply)")
    if cycle.get("available"):
        print(f"Research cyc : last {cycle.get('mode')} (proposals queued: {cycle.get('proposals_queued', 0)}, "
              f"data missing: {cycle.get('data_missing', 0)}, worker done: {cycle.get('worker_completed', 0)}, "
              f"deferred: {cycle.get('worker_deferred', 0)})")
    else:
        print("Research cyc : not run yet (python -m scripts.strategy_lab.research_cycle --dry-run)")
    if session.get("available"):
        print(f"Research ses : last {session.get('mode')} (ready: {session.get('ready_jobs', 0)}, "
              f"missing data: {session.get('skipped_missing_data', 0)}, queued: {session.get('proposals_queued', 0)}, "
              f"LLM: {session.get('llm_mode', 'disabled')})")
    else:
        print("Research ses : not run yet (python -m scripts.strategy_lab.research_session --dry-run)")
    if loop.get("available"):
        lw = loop.get("last_worker") or {}
        print(f"Research loop: last {loop.get('mode')} ({loop.get('iterations', 0)} iters, "
              f"{loop.get('duration_minutes', 0)} min; queued: {loop.get('proposals_queued', 0)}, "
              f"missing data: {loop.get('skipped_missing_data', 0)}, "
              f"worker done/deferred: {lw.get('completed', 0)}/{lw.get('deferred', 0)})")
        if loop.get("last_llm_status"):
            reasons = _fmt_counts(loop.get("last_llm_reject_reasons"))
            print(f"             : last LLM {loop.get('last_llm_status')} "
                  f"(validated: {loop.get('llm_validated', 0)}, rejects: {reasons})")
            if loop.get("last_llm_reason"):
                print(f"             : reason: {loop.get('last_llm_reason')}")
            if loop.get("last_llm_next_action"):
                print(f"             : next: {loop.get('last_llm_next_action')}")
    else:
        print("Research loop: not run yet (python -m scripts.strategy_lab.research_loop --dry-run --duration-minutes 5)")
    llm_send_status = "enabled" if llm_loop.get("enabled") else "disabled"
    spend = llm_loop.get("today_spend") or {}
    print(f"LLM loop     : {llm_loop.get('mode', 'disabled')} (advisory; send {llm_send_status}; "
          f"provider={llm_loop.get('provider', 'none')}; code validates, LLM never executed)")
    print(f"LLM spend    : today {spend.get('requests', 0)} req, {spend.get('tokens', 0)} tok, "
          f"{spend.get('cost_rub', 0.0)} RUB (lab-private log; cap "
          f"{'set' if llm_loop.get('daily_cap_present') else 'none'})")
    print(f"Obsidian     : {state.get('obsidian_notes', 0)} candidate notes")
    micro_state = "enabled" if microscope.get("enabled") else f"disabled ({microscope.get('disabled_reason', 'n/a')})"
    print(f"Microscope   : 1m {micro_state}, trigger-only; data {_fmt_counts(microscope.get('availability_counts'))}")
    if prep.get("available"):
        print(f"1m data prep : last {prep.get('mode')} via {prep.get('provider')} provider "
              f"(missing: {prep.get('missing', 0)}, downloaded: {prep.get('downloaded', 0)})")
    else:
        print("1m data prep : not run yet (prepare_1m_data --dry-run shows what 1m data is needed)")
    if market_prep:
        parts = []
        for tf in ("15m", "1h", "4h", "1d"):
            item = market_prep.get(tf) or {}
            if item.get("available"):
                parts.append(
                    f"{tf}:{item.get('mode')} via {item.get('provider')} "
                    f"dl={item.get('downloaded', 0)}"
                )
            else:
                parts.append(f"{tf}:not_run")
        print("Market prep  : " + "; ".join(parts))
    if prep_cfg.get("enabled"):
        print(f"auto-prepare : on start: {prep_cfg.get('mode')}, provider={prep_cfg.get('provider')} "
              f"(network fetch: {'yes' if prep_cfg.get('will_fetch_network') else 'no'})")
    else:
        print("auto-prepare : disabled (start does not fetch; set STRATEGY_LAB_PREPARE_1M=1 to enable)")
    print("-" * 48)
    cap = "daily cap set" if llm.get("daily_cap_present") else "no daily cap"
    print(f"LLM rev.pack : (registry review-pack send, separate from the proposal loop) export-only; "
          f"auto-send {'ENABLED' if llm.get('auto_send') else 'disabled'}; "
          f"send gate: {llm.get('would_send', 'export_only')} ({cap})")
    print("Proposal apply: manual only (queue requires explicit --apply)")
    print("Safety       : no live trading, no order engine, no paid API by default")
    print("Dashboard    : python scripts/strategy_lab/serve_dashboard.py  -> http://127.0.0.1:8765")


if __name__ == "__main__":
    main()
