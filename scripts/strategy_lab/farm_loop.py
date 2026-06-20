# -*- coding: utf-8 -*-
"""Continuous research-farm loop — the self-deciding intake -> compute -> validation cycle.

Each cycle: read scanner WATCH/GO watches (+ optional OKX-discovery refill) -> normalize
to intake events -> plan/prepare/enrich/run_sweep/classify/validate as typed lifecycle
tasks -> never spin on already_queued (it pivots to discovery / deferred work or reports
"blocked: no eligible tasks"). dry-run plans into an in-memory task DB and writes nothing.

    python -m scripts.strategy_lab.farm_loop --once --dry-run
    python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --enrich-funding
    python -m scripts.strategy_lab.farm_loop --loop --apply --run-worker --run-validation --run-paper --sleep-seconds 180

Safety: paper/research only. Public OKX market data only. Never touches .env, AUTO_TRADE,
order execution, private exchange endpoints, or Telegram credentials.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.farm_coordinator import DEFAULT_FAMILIES, run_coordinator_cycle  # noqa: E402
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path  # noqa: E402
from src.research_lab.intake_adapter import watches_to_intake  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402


def _read_intake(limit: int) -> list[dict]:
    """Read open scanner watches (file only — never imports the scanner module)."""
    try:
        from src.scout.watch_queue import open_watches
        return watches_to_intake(open_watches())[:limit]
    except Exception as exc:  # noqa: BLE001 - a missing/locked watch file must not crash the farm
        print(f"  intake: no watches ({type(exc).__name__})")
        return []


def _stage_status(args, apply: bool) -> dict:
    """Which closing-the-loop stages are active this run, and why a stage is skipped.

    worker/validation/paper are 'critical': with any of them OFF in apply mode the loop
    only QUEUES work but never computes/validates/papers it — the silent partial-loop
    foot-gun. enrich_* are non-critical (they only widen which families have data).
    """
    def st(enabled, flag: str, critical: bool) -> dict:
        return {
            "enabled": bool(enabled),
            "skipped_reason": None if enabled else f"flag {flag} off",
            "critical": critical,
        }
    return {
        "worker": st(args.run_worker, "--run-worker", True),
        "validation": st(args.run_validation, "--run-validation", True),
        "paper": st(args.run_paper, "--run-paper", True),
        "enrich_funding": st(args.enrich_funding, "--enrich-funding", False),
        "enrich_oi": st(args.enrich_oi, "--enrich-oi", False),
    }


def _print_stages(stages: dict, apply: bool) -> None:
    line = " ".join(f"{name}={'ON' if s['enabled'] else 'OFF'}" for name, s in stages.items())
    print(f"stages: {line}")
    if apply:
        off_critical = [name for name, s in stages.items() if s["critical"] and not s["enabled"]]
        if off_critical:
            flags = {"worker": "--run-worker", "validation": "--run-validation", "paper": "--run-paper"}
            need = " ".join(flags[n] for n in off_critical if n in flags)
            print(
                "WARNING: apply run with critical stage(s) OFF: " + ", ".join(off_critical)
                + " — the loop will QUEUE work but not " + "/".join(off_critical) + " it. "
                + f"Add {need} to close the loop "
                + "(or use bat\\strategy_lab_farm_full_cycle_loop.bat)."
            )


def _discovery_snapshot(private_root: Path):
    from src.research_lab.instrument_discovery import load_snapshot
    snap = load_snapshot(private_root)
    return snap if snap.get("instruments") else None


def _maybe_storage_maintain(private_root: Path, apply: bool) -> None:
    if not apply:
        return
    try:
        from src.research_lab.farm_journal import farm_log_paths
        from src.research_lab.storage_policy import bound_farm_artifacts, maintain
        maintain(farm_log_paths(private_root), apply=True)  # rotate the farm logs too
        bound_farm_artifacts(private_root, apply=True)
    except Exception as exc:  # noqa: BLE001 - storage hygiene must never break the loop
        print(f"  storage: skipped ({type(exc).__name__})")


def _providers(args, apply: bool):
    provider = flow_provider = oi_provider = None
    if apply:
        from src.research_lab.market_data_provider import get_provider
        provider = get_provider(args.provider, allow_synthetic=(args.provider == "synthetic"))
        if args.enrich_funding:
            from src.research_lab.providers.okx_flow import OkxPublicFundingProvider
            flow_provider = OkxPublicFundingProvider()
        if args.enrich_oi:
            from src.research_lab.providers.okx_flow import OkxPublicOpenInterestProvider
            oi_provider = OkxPublicOpenInterestProvider()
    return provider, flow_provider, oi_provider


def _print_cycle(out: dict) -> None:
    c = out["counters"]
    interesting = {k: v for k, v in c.items() if isinstance(v, int) and v}
    print(f"  pivot={out['pivot']} active_tasks={out['active_tasks']}")
    print("  " + (" ".join(f"{k}={v}" for k, v in interesting.items()) or "(no new work)"))
    st = out["status"]
    if st.get("by_state"):
        print("  states: " + " ".join(f"{k}={v}" for k, v in st["by_state"].items()))
    if st.get("blocked_reasons"):
        print("  blocked: " + " ".join(f"{k}={v}" for k, v in st["blocked_reasons"].items()))
    if st.get("deferred_reasons"):
        print("  deferred: " + " ".join(f"{k}={v}" for k, v in st["deferred_reasons"].items()))
    paper = out.get("paper") or {}
    if paper:
        pc = paper.get("counters") or {}
        readiness = paper.get("readiness") or {}
        shown = " ".join(f"{k}={v}" for k, v in pc.items() if isinstance(v, int) and (v or k == "cards"))
        print("  paper: " + (shown or "(no paper work)"))
        if readiness:
            print(
                "  paper_ready: "
                f"checked={readiness.get('checked_cards', 0)} "
                f"ready={readiness.get('paper_forward_ready', 0)} "
                f"plan_ready={readiness.get('plan_ready', 0)} "
                f"local_data_ready={readiness.get('local_data_ready', 0)}"
            )
            blockers = readiness.get("blocked_reasons") or {}
            if blockers:
                print("  paper_blocked: " + " ".join(f"{k}={v}" for k, v in list(blockers.items())[:6]))
    for e in out.get("errors") or []:
        print(f"  ERROR [{e.get('where')}]: {e.get('error')}")


def _cycle_signature(out: dict) -> tuple:
    """A change-signature so --loop doesn't reprint identical state every sleep tick."""
    nz = tuple(sorted(k for k, v in out["counters"].items() if isinstance(v, int) and v))
    by_state = tuple(sorted(((out.get("status") or {}).get("by_state") or {}).items()))
    paper = out.get("paper") or {}
    paper_counters = tuple(sorted((paper.get("counters") or {}).items()))
    paper_ready = tuple(sorted((paper.get("readiness") or {}).get("blocked_reasons", {}).items()))
    return (out.get("pivot"), nz, by_state, paper_counters, paper_ready, bool(out.get("errors")))


def _run_once(args, tasks: FarmTasksDB, profiles, policy, private_root: Path, apply: bool) -> dict:
    provider, flow_provider, oi_provider = _providers(args, apply)
    events = _read_intake(args.max_plan_events)
    out = run_coordinator_cycle(
        tasks, private_root=private_root, profiles=profiles, policy=policy, intake_events=events,
        families=DEFAULT_FAMILIES, provider=provider, flow_provider=flow_provider,
        oi_provider=oi_provider, apply=apply,
        backend=args.backend, data_days=args.data_days, max_plan_events=args.max_plan_events,
        max_prepares=args.max_prepares, max_enrich=args.max_enrich, max_sweeps=args.max_sweeps,
        run_worker=args.run_worker, max_worker_jobs=args.max_worker_jobs, night_mode=args.night_mode,
        allow_public_output=args.allow_public_output, discovery_snapshot=_discovery_snapshot(private_root),
        run_validation=args.run_validation, run_followups=not getattr(args, "no_followups", False),
        max_followups=getattr(args, "max_followups", 10),
    )
    if args.run_paper:
        from src.research_lab.paper_runtime import run_paper_cycle
        paper = run_paper_cycle(private_root, apply=apply, limit=args.max_paper_cards)
        out["paper"] = {
            "counters": paper.get("counters", {}),
            "readiness": paper.get("readiness", {}),
            "results": (paper.get("results") or [])[:10],
        }
    stages = _stage_status(args, apply)
    out["stages"] = stages
    if apply:
        from src.research_lab import farm_journal
        farm_journal.log_cycle(private_root, ts=time.time(), mode="apply", result=out, stages=stages)
        for e in out.get("errors") or []:
            farm_journal.log_error(private_root, where=e.get("where", "cycle"),
                                   error=e.get("error", ""), ts=time.time())
    _maybe_storage_maintain(private_root, apply)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan only; write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="prepare/enrich/queue/classify/validate")
    run = ap.add_mutually_exclusive_group()
    run.add_argument("--once", action="store_true", help="one cycle then exit (default)")
    run.add_argument("--loop", action="store_true", help="run until stop-file / Ctrl+C")
    ap.add_argument("--run-worker", action="store_true", help="drain a few compute jobs each cycle")
    ap.add_argument("--run-validation", action="store_true", help="export + honest-backtest + stamp-back")
    ap.add_argument("--run-paper", action="store_true", help="simulate paper outcomes from validated setup cards")
    ap.add_argument("--enrich-funding", action="store_true", help="enable public funding enrichment tasks")
    ap.add_argument("--enrich-oi", action="store_true", help="enable public open-interest enrichment tasks")
    ap.add_argument("--backend", choices=["cpu", "auto", "gpu"], default="auto")
    ap.add_argument("--provider", choices=["okx-public", "synthetic"], default="okx-public")
    ap.add_argument("--max-plan-events", type=int, default=20)
    ap.add_argument("--max-prepares", type=int, default=4)
    ap.add_argument("--max-enrich", type=int, default=4)
    ap.add_argument("--max-sweeps", type=int, default=4)
    ap.add_argument("--max-worker-jobs", type=int, default=4)
    ap.add_argument("--max-paper-cards", type=int, default=20)
    ap.add_argument("--max-followups", type=int, default=10)
    ap.add_argument("--no-followups", action="store_true", help="disable automatic bounded feedback follow-ups")
    ap.add_argument("--data-days", type=int, default=None)
    ap.add_argument("--night-mode", action="store_true")
    ap.add_argument("--sleep-seconds", type=int, default=180)
    ap.add_argument("--stop-file", default="")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    verb = ap.add_mutually_exclusive_group()
    verb.add_argument("--verbose", action="store_true", help="print the full cycle block every tick")
    verb.add_argument("--quiet", action="store_true", help="print only on change/error (loop heartbeat)")
    args = ap.parse_args()
    apply = bool(args.apply)

    print(f"farm_loop mode={'APPLY' if apply else 'DRY-RUN'} run={'loop' if args.loop else 'once'} "
          f"private_root={args.private_root}")
    print("safety: paper-only; public OKX market data only; no orders / .env / AUTO_TRADE / private endpoints")
    _print_stages(_stage_status(args, apply), apply)

    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=args.night_mode)
    if apply:
        private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
        tasks = FarmTasksDB(tasks_db_path(private_root))
        from src.research_lab.farm_journal import make_transition_sink
        tasks.on_transition = make_transition_sink(private_root)  # durable task-transition audit
    else:
        private_root = Path(args.private_root)
        tasks = FarmTasksDB(":memory:")  # dry-run persists nothing

    try:
        if not args.loop:
            out = _run_once(args, tasks, profiles, policy, private_root, apply)
            _print_cycle(out)  # a single explicit cycle is always shown
            return
        prev_sig = None
        while True:
            if args.stop_file and Path(args.stop_file).exists():
                print(f"stop-file present ({args.stop_file}) — exiting loop")
                break
            out = _run_once(args, tasks, profiles, policy, private_root, apply)
            sig = _cycle_signature(out)
            # Always show a CHANGED cycle or any error (never hide a changed block, even with
            # --quiet). Unchanged cycle: heartbeat by default; with --quiet, print nothing.
            show_full = args.verbose or sig != prev_sig or bool(out.get("errors"))
            if show_full:
                print(f"\n=== farm cycle @ {int(time.time())} ===")
                _print_cycle(out)
            elif not args.quiet:
                print(f"  heartbeat @ {int(time.time())} pivot={out['pivot']} active={out['active_tasks']}")
            prev_sig = sig
            time.sleep(max(1, args.sleep_seconds))
    except KeyboardInterrupt:
        print("\ninterrupted — graceful stop")
    finally:
        tasks.close()


if __name__ == "__main__":
    main()
