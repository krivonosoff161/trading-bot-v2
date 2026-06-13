# -*- coding: utf-8 -*-
"""Research SESSION — one controlled, data-complete pass with an LLM advisory layer.

A session = (optional) LLM proposal request export -> one readiness-aware research
cycle (generate -> data-ready check -> optional prepare -> queue only READY -> one
throttled worker step) -> a status report. Dry-run by default: no queue writes, no
worker, no network, no paid LLM. This is NOT a daemon — it is a single pass.

LLM is advisory only: a "cheap" model proposes JSON candidates, code validates every
one, only validated ones are eligible for a "chief" pass, and any SEND is gated and
export-only by default (no provider ships). The LLM never runs code or shell.

    python -m scripts.strategy_lab.research_session --dry-run
    python -m scripts.strategy_lab.research_session --apply --max-candidates 5 --max-queued 5 --max-worker-jobs 1
    python -m scripts.strategy_lab.research_session --apply --prepare-1m --prepare-1m-apply --provider okx-public
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

from scripts.strategy_lab.research_cycle import run_research_cycle  # noqa: E402
from src.research_lab.llm_proposals import evaluate_llm_loop_gates, load_llm_loop_config  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.research_cycle import ResearchCycleConfig  # noqa: E402
from src.research_lab.research_session import SessionReport, write_session_report  # noqa: E402
from src.research_lab.review_export import export_review_pack  # noqa: E402


def _build_cycle_config(args) -> ResearchCycleConfig:
    allow_synthetic = os.getenv("STRATEGY_LAB_ALLOW_SYNTHETIC", "").strip().lower() in {"1", "true", "yes"}
    return ResearchCycleConfig(
        apply=bool(args.apply),
        generate_proposals=True,
        max_proposals=max(1, args.max_candidates),
        queue=True,
        max_queue=args.max_queued,
        priority=args.priority,
        run_worker=not args.no_worker,
        max_worker_jobs=max(1, args.max_worker_jobs),
        prepare_1m=bool(args.prepare_1m),
        prepare_1m_apply=bool(args.prepare_1m_apply),
        provider=args.provider,
        allow_synthetic=allow_synthetic,
    )


def _llm_step(args, private_root: Path, allow_public_output: bool) -> dict:
    """LLM advisory layer: export-only by default, send strictly gated. No paid call here."""
    cfg = load_llm_loop_config(max_candidates=max(1, args.max_candidates))
    summary = cfg.to_summary()
    summary["export_requested"] = bool(args.llm_export or args.llm_send)
    summary["pack_label"] = ""
    summary["export_done"] = False

    if args.llm_export or args.llm_send:
        if args.apply:
            try:
                pack = export_review_pack(private_root, limit=max(1, args.max_candidates),
                                          allow_public_output=allow_public_output)
                summary["pack_label"] = pack["pack_label"]
                summary["export_done"] = True
            except Exception as exc:  # never crash the session on an export hiccup
                summary["export_error"] = type(exc).__name__
        else:
            summary["pack_label"] = "(dry-run: would export request pack)"

    if args.llm_send:
        decision = evaluate_llm_loop_gates(cfg, send_requested=True, dry_run=not args.apply)
        summary["send_decision"] = decision.reason
        summary["sent"] = bool(decision.allowed)  # False today: no provider configured
    else:
        summary["send_decision"] = "not_requested"
        summary["sent"] = False
    return summary


def run_session(args) -> SessionReport:
    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    config = _build_cycle_config(args)

    llm_summary = _llm_step(args, private_root, args.allow_public_output)
    cycle = run_research_cycle(config, private_root=private_root, allow_public_output=args.allow_public_output)

    counts = cycle.counts
    readiness = {k: counts.get(k, 0) for k in
                 ("ready_jobs", "skipped_missing_data", "skipped_too_short", "skipped_malformed", "data_missing")}
    report = SessionReport(
        mode=config.mode,
        cycle={"mode": cycle.mode, "provider": cycle.provider, **counts, "next_command": cycle.next_command},
        llm=llm_summary,
        readiness=readiness,
        next_command=cycle.next_command,
    )
    if args.session_budget_hours is not None:
        report.cycle["session_budget_hours"] = float(args.session_budget_hours)  # advisory only; single pass
    return report


def _print_report(report: SessionReport) -> None:
    c = report.cycle
    llm = report.llm
    print(f"Strategy Lab research session ({report.mode.upper()})")
    print("-" * 60)
    print(f"LLM      : {llm.get('mode', 'export_only')} (enabled={llm.get('enabled')}, provider={llm.get('provider')}, "
          f"send={llm.get('send_decision')})")
    if llm.get("pack_label"):
        print(f"           request pack: {llm['pack_label']}")
    print(f"proposals: generated={c.get('proposals_generated', 0)} validated={c.get('proposals_validated', 0)} "
          f"queued={c.get('proposals_queued', 0)}")
    print(f"readiness: ready={report.readiness.get('ready_jobs', 0)} "
          f"missing={report.readiness.get('skipped_missing_data', 0)} "
          f"too_short={report.readiness.get('skipped_too_short', 0)} "
          f"malformed={report.readiness.get('skipped_malformed', 0)}")
    print(f"data     : requirements={c.get('data_requirements', 0)} missing={c.get('data_missing', 0)} "
          f"prepared={c.get('data_prepared', 0)}")
    print(f"worker   : completed={c.get('worker_completed', 0)} deferred={c.get('worker_deferred', 0)} "
          f"failed={c.get('worker_failed', 0)}")
    print("-" * 60)
    print("safety   : single pass (not a daemon); no live trading, no order engine, no paid LLM by default")
    print(f"next     : {report.next_command}")
    print("report   : state/research_session/latest.json (private root)")


def main() -> None:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Plan only: no queue writes, no worker, no network, no paid LLM (default)")
    mode.add_argument("--apply", action="store_true", help="Queue only data-ready validated proposals; run a capped worker step")
    ap.add_argument("--max-candidates", type=int, default=8)
    ap.add_argument("--max-queued", type=int, default=None)
    ap.add_argument("--max-worker-jobs", type=int, default=1)
    ap.add_argument("--prepare-1m", action="store_true")
    ap.add_argument("--prepare-1m-apply", action="store_true")
    ap.add_argument("--provider", default="null", choices=["null", "synthetic", "okx-public"])
    ap.add_argument("--llm-export", action="store_true", help="Export an LLM proposal-request pack (no API call)")
    ap.add_argument("--llm-send", action="store_true", help="Attempt a gated LLM send (export-only unless all gates pass)")
    ap.add_argument("--session-budget-hours", type=float, default=None, help="Advisory only; the session is a single pass")
    ap.add_argument("--no-worker", action="store_true")
    ap.add_argument("--priority", type=int, default=72)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()

    report = run_session(args)
    write_session_report(Path(args.private_root), report, allow_public_output=args.allow_public_output)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(report)


if __name__ == "__main__":
    main()
