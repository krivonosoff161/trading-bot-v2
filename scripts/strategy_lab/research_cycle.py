# -*- coding: utf-8 -*-
"""Controlled event-driven research cycle — orchestrates existing pieces, safely.

One bounded pass: inspect -> generate proposals -> check 1m data -> optionally
prepare it -> queue validated proposals -> run a capped worker step -> write a
status report. Dry-run by default: no queue writes, no worker run, no network. A
real network fetch needs --apply AND --prepare-1m AND --prepare-1m-apply AND a real
provider (okx-public). The worker respects the resource-policy throttle (no storm),
and there is no hidden loop. No live trading, no orders, no paid LLM.

    python -m scripts.strategy_lab.research_cycle --dry-run
    python -m scripts.strategy_lab.research_cycle --apply --max-proposals 5 --max-queue 5 --max-worker-jobs 1
    python -m scripts.strategy_lab.research_cycle --apply --prepare-1m --prepare-1m-apply --provider okx-public
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

from scripts.strategy_lab.generate_next_proposals import generate_proposals  # noqa: E402
from scripts.strategy_lab.queue_validated_proposals import queue_validated  # noqa: E402
from scripts.strategy_lab.worker_once import run_worker_once  # noqa: E402
from src.research_lab.candidate_registry import registry_path, registry_summary  # noqa: E402
from src.research_lab.data_cache import resolve_requirements  # noqa: E402
from src.research_lab.data_prepare import prepare, write_prepare_report  # noqa: E402
from src.research_lab.data_requirements import collect_requirements  # noqa: E402
from src.research_lab.event_microscope import derive_limits  # noqa: E402
from src.research_lab.market_data_provider import get_provider  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, one_minute_data_dir, resolve_private_root  # noqa: E402
from src.research_lab.proposal_store import load_proposals, proposals_path, status_counts  # noqa: E402
from src.research_lab.research_cycle import (  # noqa: E402
    CycleStepResult,
    ResearchCycleConfig,
    ResearchCycleResult,
    write_cycle_report,
)
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402

_MISSING = {"missing", "too_short", "malformed"}


def _next_command(config: ResearchCycleConfig, counts: dict) -> str:
    if not config.apply:
        return ("apply: python -m scripts.strategy_lab.research_cycle --apply "
                "--max-proposals 5 --max-queue 5 --max-worker-jobs 1")
    blocked = counts.get("skipped_missing_data", 0) or counts.get("data_missing", 0)
    if blocked and counts.get("data_prepared", 0) == 0 and config.provider == "null":
        return ("data missing -> prepare it: python -m scripts.strategy_lab.research_cycle "
                "--apply --prepare-1m --prepare-1m-apply --provider okx-public")
    return "python -m scripts.strategy_lab.status"


def run_research_cycle(config: ResearchCycleConfig, *, private_root, allow_public_output: bool = False) -> ResearchCycleResult:
    """Run one bounded cycle. Pure orchestration over existing, individually-safe steps."""
    private_root = resolve_private_root(Path(private_root), allow_public_output=allow_public_output)
    result = ResearchCycleResult(mode=config.mode, provider=config.provider)
    counts = result.counts

    # 1) inspect current state
    reg = registry_summary(registry_path(private_root))
    proposals = load_proposals(proposals_path(private_root))
    result.steps.append(CycleStepResult(
        "inspect", "executed",
        detail=f"candidates={reg.get('entries', 0)} (unique {reg.get('unique_candidates', 0)}), proposals={len(proposals)}",
        counts={"candidates": reg.get("entries", 0), "unique_candidates": reg.get("unique_candidates", 0),
                "proposals": len(proposals), "by_status": status_counts(proposals)},
    ))

    # 2) generate next proposals (resilient: a generator error is recorded, not fatal)
    if config.generate_proposals:
        try:
            gen = generate_proposals(private_root, limit=config.max_proposals, apply=config.apply,
                                     allow_public_output=allow_public_output)
            counts["proposals_generated"] = gen["generated"]
            counts["proposals_validated"] = gen["validated"]
            result.steps.append(CycleStepResult(
                "generate_proposals", "executed",
                detail=f"generated={gen['generated']} validated={gen['validated']} stored={gen['applied']}",
                counts={"generated": gen["generated"], "validated": gen["validated"], "stored": gen["applied"]},
            ))
        except Exception as exc:
            result.steps.append(CycleStepResult("generate_proposals", "failed",
                                                detail=f"proposal_error:{type(exc).__name__}"))
    else:
        result.steps.append(CycleStepResult("generate_proposals", "skipped", detail="--no-proposals"))

    # 3) check required 1m data (resilient: a data-check error blocks prepare/worker safely)
    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=config.night_mode)
    checks: list = []
    try:
        limits = derive_limits(profiles, policy)
        checks = resolve_requirements(
            collect_requirements(private_root, limits=limits),
            data_dir=one_minute_data_dir(private_root),
        )
        missing = sum(1 for c in checks if c.requirement.status in _MISSING)
        counts["data_requirements"] = len(checks)
        counts["data_missing"] = missing
        result.steps.append(CycleStepResult(
            "data_check", "executed", detail=f"requirements={len(checks)} missing={missing}",
            counts={"requirements": len(checks), "missing": missing},
        ))
        data_check_ok = True
    except Exception as exc:
        result.steps.append(CycleStepResult("data_check", "failed",
                                            detail=f"data_check_error:{type(exc).__name__}"))
        data_check_ok = False

    # 4) optionally prepare 1m data (network only if explicitly allowed)
    if config.prepare_1m and data_check_ok:
        try:
            provider = get_provider(config.provider, allow_synthetic=config.allow_synthetic)
            prep_apply = config.apply and config.prepare_1m_apply
            report = prepare(checks, provider=provider, private_root=private_root,
                             apply=prep_apply, allow_public_output=allow_public_output)
            counts["data_prepared"] = report.downloaded
            # Record the prepare result so `status`/dashboard "1m data prep" reflects it.
            write_prepare_report(private_root, report, allow_public_output=allow_public_output)
            result.steps.append(CycleStepResult(
                "prepare_1m", "executed",
                detail=(f"provider={report.provider} mode={'apply' if prep_apply else 'dry_run'} "
                        f"downloaded={report.downloaded} would_download={report.would_download}"),
                counts={"downloaded": report.downloaded, "would_download": report.would_download,
                        "provider_configured": report.provider_configured},
            ))
        except Exception as exc:
            result.steps.append(CycleStepResult("prepare_1m", "failed",
                                                detail=f"prepare_error:{type(exc).__name__}"))
    elif config.prepare_1m:
        result.steps.append(CycleStepResult("prepare_1m", "skipped", detail="data_check failed; prepare skipped"))
    else:
        result.steps.append(CycleStepResult("prepare_1m", "skipped", detail="--prepare-1m not set (no network)"))

    # 5) queue validated proposals (capped, data-ready only -> never queue incomplete data)
    if config.queue:
        try:
            q = queue_validated(private_root, priority=config.priority, apply=config.apply,
                                max_queue=config.max_queue, require_data_ready=True,
                                allow_public_output=allow_public_output,
                                night_mode=config.night_mode)
            counts["proposals_queued"] = q["queued"]
            counts["ready_jobs"] = q["ready"]
            counts["skipped_missing_data"] = q.get("skipped_missing_data", 0)
            counts["skipped_too_short"] = q.get("skipped_too_short", 0)
            counts["skipped_malformed"] = q.get("skipped_malformed", 0)
            result.steps.append(CycleStepResult(
                "queue_proposals", "executed",
                detail=(f"ready={q['ready']} queued={q['queued']} skipped_full={q['skipped_full']} "
                        f"not_ready(missing={q.get('skipped_missing_data', 0)},"
                        f"short={q.get('skipped_too_short', 0)},malformed={q.get('skipped_malformed', 0)}) "
                        f"cap={q['max_queue']}"),
                counts={"ready": q["ready"], "queued": q["queued"], "skipped_full": q["skipped_full"],
                        "skipped_not_ready": q.get("skipped_not_ready", [])},
            ))
        except Exception as exc:
            result.steps.append(CycleStepResult("queue_proposals", "failed",
                                                detail=f"queue_error:{type(exc).__name__}"))
    else:
        result.steps.append(CycleStepResult("queue_proposals", "skipped", detail="--no-queue"))

    # 6) run a capped worker step (respects throttle; no storm; no infinite loop)
    if not config.run_worker:
        result.steps.append(CycleStepResult("worker", "skipped", detail="--no-worker"))
    elif not config.apply:
        result.steps.append(CycleStepResult("worker", "skipped", detail="dry-run: worker not run"))
    else:
        result.steps.append(_run_worker_step(config, private_root, counts, allow_public_output))

    result.safety = {
        "no_live_trading": True, "no_order_engine": True, "no_paid_llm_default": True,
        "provider": config.provider, "network_fetch": config.network_fetch_allowed, "llm_send": False,
    }
    result.next_command = _next_command(config, counts)
    return result


def _run_worker_step(config, private_root, counts, allow_public_output) -> CycleStepResult:
    details: list[str] = []
    status = "executed"
    for _ in range(max(1, int(config.max_worker_jobs))):
        try:
            out = run_worker_once(private_root, allow_public_output=allow_public_output,
                                  night_mode=config.night_mode)
        except Exception as exc:  # job execution failure was already recorded by the worker
            counts["worker_failed"] += 1
            details.append(f"failed:{type(exc).__name__}")
            status = "failed"
            break
        state = out.get("status")
        if state == "completed":
            counts["worker_completed"] += 1
            details.append(f"completed:{out.get('run_label', '')}")
        elif state == "deferred":
            counts["worker_deferred"] += 1
            details.append(f"deferred:{out.get('reason', '')}")
            status = "deferred"
            break  # throttle says wait; do not loop
        elif state == "queue_empty":
            details.append("queue_empty")
            break
        else:
            details.append(str(state or "unknown"))
            break
    return CycleStepResult("worker", status, detail="; ".join(details) or "no jobs",
                           counts={"completed": counts["worker_completed"],
                                   "deferred": counts["worker_deferred"], "failed": counts["worker_failed"]})


def _print_report(result: ResearchCycleResult) -> None:
    print(f"Strategy Lab research cycle ({result.mode.upper()})")
    print("-" * 56)
    print(f"provider : {result.provider}")
    print("steps:")
    for step in result.steps:
        print(f"  [{step.status}] {step.name}: {step.detail}")
    print("-" * 56)
    c = result.counts
    print(f"counts   : proposals gen={c['proposals_generated']}/val={c['proposals_validated']}/queued={c['proposals_queued']}; "
          f"data req={c['data_requirements']}/missing={c['data_missing']}/prepared={c['data_prepared']}; "
          f"worker done={c['worker_completed']}/deferred={c['worker_deferred']}/failed={c['worker_failed']}")
    print(f"safety   : public/research only; no live trading, no order engine, no paid LLM; "
          f"network fetch: {'yes' if result.safety.get('network_fetch') else 'no'}")
    print(f"next     : {result.next_command}")
    print("report   : state/research_cycle/latest.json (private root)")


def main() -> None:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Inspect/plan only; no writes, no queue, no worker, no network (default)")
    mode.add_argument("--apply", action="store_true", help="Store proposals, queue (capped), and run a capped worker step")
    ap.add_argument("--prepare-1m", action="store_true", help="Include a 1m data prepare step")
    ap.add_argument("--prepare-1m-apply", action="store_true", help="Let the prepare step fetch/write (needs --apply + real provider)")
    ap.add_argument("--provider", default="null", choices=["null", "synthetic", "okx-public"])
    ap.add_argument("--max-proposals", type=int, default=10)
    ap.add_argument("--max-queue", type=int, default=None)
    ap.add_argument("--max-worker-jobs", type=int, default=1)
    ap.add_argument("--priority", type=int, default=72)
    ap.add_argument("--no-worker", action="store_true")
    ap.add_argument("--no-proposals", action="store_true")
    ap.add_argument("--json", action="store_true", help="Print the machine-readable result JSON")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()

    allow_synthetic = os.getenv("STRATEGY_LAB_ALLOW_SYNTHETIC", "").strip().lower() in {"1", "true", "yes"}
    config = ResearchCycleConfig(
        apply=bool(args.apply),
        generate_proposals=not args.no_proposals,
        max_proposals=max(1, args.max_proposals),
        queue=True,
        max_queue=args.max_queue,
        priority=args.priority,
        run_worker=not args.no_worker,
        max_worker_jobs=max(1, args.max_worker_jobs),
        prepare_1m=bool(args.prepare_1m),
        prepare_1m_apply=bool(args.prepare_1m_apply),
        provider=args.provider,
        allow_synthetic=allow_synthetic,
        night_mode=os.getenv("STRATEGY_LAB_NIGHT_MODE", "").strip().lower() in {"1", "true", "yes"},
    )
    result = run_research_cycle(config, private_root=args.private_root, allow_public_output=args.allow_public_output)
    write_cycle_report(Path(args.private_root), result, allow_public_output=args.allow_public_output)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(result)


if __name__ == "__main__":
    main()
