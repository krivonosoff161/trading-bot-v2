# -*- coding: utf-8 -*-
"""Continuous research-farm loop - the self-deciding intake -> compute -> validation cycle.

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
    """Read open scanner watches (file only - never imports the scanner module)."""
    try:
        from src.scout.watch_queue import open_watches
        return watches_to_intake(open_watches())[:limit]
    except Exception as exc:  # noqa: BLE001 - a missing/locked watch file must not crash the farm
        print(f"  intake: no watches ({type(exc).__name__})")
        return []


def _stage_status(args, apply: bool) -> dict:
    """Which closing-the-loop stages are active this run, and why a stage is skipped.

    worker/validation/paper are 'critical': with any of them OFF in apply mode the loop
    only QUEUES work but never computes/validates/papers it - the silent partial-loop
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
                + " - the loop will QUEUE work but not " + "/".join(off_critical) + " it. "
                + f"Add {need} to close the loop "
                + "(or use bat\\strategy_lab_farm_full_cycle_loop.bat)."
            )


def _discovery(args, private_root: Path, apply: bool):
    """Load the discovery snapshot, auto-refreshing a stale/missing one when allowed.

    A stale snapshot must never silently degrade the farm to blocked:no_eligible: in
    apply mode (unless --no-discovery-refresh) we refresh through the TTL-throttled
    discover() (network only when actually stale); otherwise we warn loudly with the
    command hint and run on whatever snapshot exists.
    Returns (snapshot_or_None, info_dict).
    """
    from src.research_lab import instrument_discovery as idisc
    ttl = args.discovery_ttl_seconds
    now_ms = int(time.time() * 1000)
    refreshed = False
    if apply and not args.no_discovery_refresh:
        try:
            from scripts.strategy_lab.discover_okx_universe import discover
            res = discover(private_root, apply=True, now_ms=now_ms, ttl_seconds=ttl)
            refreshed = res.get("status") == "discovered"
            if res.get("status") == "fetch_failed":
                print(f"  discovery: refresh failed ({res.get('reason')}) - using existing snapshot")
            elif refreshed:
                print(f"  discovery: refreshed snapshot (count={res.get('count')}, "
                      f"new={res.get('diff', {}).get('new')})")
        except Exception as exc:  # noqa: BLE001 - discovery refresh must never crash the farm
            print(f"  discovery: refresh skipped ({type(exc).__name__})")
    snap = idisc.load_snapshot(private_root)
    if not snap.get("instruments"):
        print("  WARNING discovery snapshot MISSING - run: "
              "python -m scripts.strategy_lab.discover_okx_universe --apply")
        return None, {"status": "missing", "age_seconds": None, "count": 0}
    age = idisc.snapshot_age_seconds(snap, now_ms)
    fresh = idisc.is_fresh(snap, now_ms, ttl)
    if not fresh and not refreshed:
        print(f"  WARNING discovery snapshot STALE (age={age}s > ttl={ttl}s) - run: "
              "python -m scripts.strategy_lab.discover_okx_universe --apply "
              "(or remove --no-discovery-refresh in apply mode)")
        status = "stale_no_refresh"
    else:
        status = "refreshed" if refreshed else "fresh"
    return snap, {"status": status, "age_seconds": age, "count": int(snap.get("count") or 0)}


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
    ps_op = out.get("paper_signals") or {}
    if ps_op:
        pfr_c = {k: v for k, v in (ps_op.get("pfr_counts") or {}).items() if isinstance(v, int) and v}
        if pfr_c:
            print("  pfr_lane: " + " ".join(f"{k}={v}" for k, v in pfr_c.items()))
    mb = out.get("main_paper_bridge") or {}
    if mb:
        print(
            "  main_paper_bridge: "
            f"instructions={mb.get('instructions', 0)} "
            f"paper_only={mb.get('paper_only')} execution_allowed={mb.get('execution_allowed')}"
        )
    mc = out.get("main_paper_consumer") or {}
    if mc:
        print(
            "  main_paper_consumer: "
            f"read={mc.get('instructions_read', 0)} "
            f"accepted={mc.get('accepted', 0)} rejected={mc.get('rejected', 0)} "
            f"paper_only={mc.get('paper_only')} execution_allowed={mc.get('execution_allowed')}"
        )
    rtq = out.get("main_paper_runtime_queue") or {}
    if rtq:
        print(
            "  main_paper_runtime_queue: "
            f"read={rtq.get('rows_read', 0)} "
            f"queued={rtq.get('queued', 0)} invalid={rtq.get('invalid', 0)} "
            f"action={rtq.get('runtime_action')} execution_allowed={rtq.get('execution_allowed')}"
        )
    rto = out.get("main_paper_runtime_observation") or {}
    if rto:
        print(
            "  main_paper_runtime_observation: "
            f"read={rto.get('rows_read', 0)} observed={rto.get('observed', 0)} "
            f"reviewed={rto.get('reviewed', 0)} pending={rto.get('pending', 0)} "
            f"invalid={rto.get('invalid', 0)} provider_error={rto.get('provider_error', 0)} "
            f"execution_allowed={rto.get('execution_allowed')}"
        )
    tp = out.get("paper_telegram_preview") or {}
    if tp:
        print(
            "  paper_telegram_preview: "
            f"rendered={tp.get('rendered', 0)} invalid={tp.get('invalid', 0)} "
            f"sends_network={tp.get('sends_network')}"
        )
    train = out.get("paper_signal_training_export") or {}
    if train:
        print(
            "  paper_signal_training_export: "
            f"rows={train.get('rows', 0)} terminal_only={train.get('terminal_only')} "
            f"paper_only={train.get('paper_only')}"
        )
    for e in out.get("errors") or []:
        print(f"  ERROR [{e.get('where')}]: {e.get('error')}")


def _cycle_signature(out: dict) -> tuple:
    """A change-signature so --loop doesn't reprint identical state every sleep tick."""
    nz = tuple(sorted(k for k, v in out["counters"].items() if isinstance(v, int) and v))
    by_state = tuple(sorted(((out.get("status") or {}).get("by_state") or {}).items()))
    paper = out.get("paper") or {}
    paper_counters = tuple(sorted((paper.get("counters") or {}).items()))
    paper_ready = tuple(sorted((paper.get("readiness") or {}).get("blocked_reasons", {}).items()))
    main_consumer = tuple(sorted((out.get("main_paper_consumer") or {}).items()))
    main_runtime_queue = tuple(sorted((out.get("main_paper_runtime_queue") or {}).items()))
    main_runtime_observation = tuple(sorted((out.get("main_paper_runtime_observation") or {}).items()))
    telegram_preview = tuple(sorted((out.get("paper_telegram_preview") or {}).items()))
    training_export = tuple(sorted((out.get("paper_signal_training_export") or {}).items()))
    return (
        out.get("pivot"), nz, by_state, paper_counters, paper_ready,
        main_consumer, main_runtime_queue, main_runtime_observation, telegram_preview,
        training_export,
        bool(out.get("errors")),
    )


def _run_once(args, tasks: FarmTasksDB, profiles, policy, private_root: Path, apply: bool) -> dict:
    provider, flow_provider, oi_provider = _providers(args, apply)
    events = _read_intake(args.max_plan_events)
    snapshot, discovery_info = _discovery(args, private_root, apply)
    out = run_coordinator_cycle(
        tasks, private_root=private_root, profiles=profiles, policy=policy, intake_events=events,
        families=DEFAULT_FAMILIES, provider=provider, flow_provider=flow_provider,
        oi_provider=oi_provider, apply=apply,
        backend=args.backend, data_days=args.data_days, max_plan_events=args.max_plan_events,
        max_prepares=args.max_prepares, max_enrich=args.max_enrich, max_sweeps=args.max_sweeps,
        run_worker=args.run_worker, max_worker_jobs=args.max_worker_jobs, night_mode=args.night_mode,
        allow_public_output=args.allow_public_output, discovery_snapshot=snapshot,
        max_discovery=args.max_plan_events,
        run_validation=args.run_validation, run_followups=not getattr(args, "no_followups", False),
        max_followups=getattr(args, "max_followups", 10), sweep_tier=args.sweep_tier,
    )
    out["discovery"] = discovery_info
    if args.run_paper:
        from src.research_lab.paper_runtime import run_paper_cycle
        paper = run_paper_cycle(private_root, apply=apply, limit=args.max_paper_cards)
        out["paper"] = {
            "counters": paper.get("counters", {}),
            "readiness": paper.get("readiness", {}),
            "results": (paper.get("results") or [])[:10],
        }
    if apply:
        # True-forward research lane: pin boundaries for the current watchlist (idempotent) and
        # accumulate forward outcomes on genuinely new local bars. Bounded + crash-isolated so a
        # research lane can never break the cycle. matured != edge; nothing paper-ready.
        tf_limit = int(getattr(args, "true_forward_max_candidates", 20))
        if tf_limit > 0:
            try:
                from src.research_lab import true_forward
                true_forward.register(private_root, max_candidates=tf_limit)
                tf_res = true_forward.collect_once(private_root, max_candidates=tf_limit)
                out["true_forward"] = tf_res.get("summary", {})
            except Exception as exc:  # noqa: BLE001 - research lane must never break the cycle
                out.setdefault("errors", []).append({"where": "true_forward", "error": str(exc)})
        else:
            out["true_forward"] = {"skipped": "true_forward_max_candidates=0"}
        if getattr(args, "run_paper_signals", False):
            # Operational paper-watch lane: one bounded cycle (observe armed -> close -> remember ->
            # generate new). Crash-isolated; paper/research-only, never an order.
            try:
                from src.research_lab.paper_signals import cycle as paper_cycle
                from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
                _pfr_db = Path(getattr(args, "pfr_db_path", "") or "")
                _pfr_db = _pfr_db if _pfr_db.as_posix() not in ("", ".") else None
                paper_provider = OkxPublicMarketDataProvider(
                    timeout=float(getattr(args, "paper_signals_fetch_timeout", 10.0))
                )
                out["paper_signals"] = paper_cycle.run_cycle(
                    private_root, mode="live", timeframes=("15m", "1h"),
                    max_new=int(getattr(args, "paper_signals_max_new", 5)), apply=True,
                    pfr_db_path=_pfr_db, provider=paper_provider,
                    max_pfr_scan=int(getattr(args, "paper_signals_max_pfr_scan", 30)),
                    max_observe=getattr(args, "paper_signals_max_observe", None))
                try:
                    from src.research_lab.main_paper_bridge import export_main_paper_instructions
                    out["main_paper_bridge"] = export_main_paper_instructions(private_root)
                except Exception as exc:  # noqa: BLE001 - derived bridge must not break the cycle
                    out.setdefault("errors", []).append({"where": "main_paper_bridge", "error": str(exc)})
                try:
                    from src.research_lab.main_paper_consumer import consume_main_paper_instructions
                    out["main_paper_consumer"] = consume_main_paper_instructions(private_root)
                except Exception as exc:  # noqa: BLE001 - paper consumer must not break the cycle
                    out.setdefault("errors", []).append({"where": "main_paper_consumer", "error": str(exc)})
                try:
                    from src.research_lab.main_paper_runtime_adapter import build_main_paper_runtime_queue
                    out["main_paper_runtime_queue"] = build_main_paper_runtime_queue(private_root)
                except Exception as exc:  # noqa: BLE001 - runtime queue must not break the cycle
                    out.setdefault("errors", []).append({"where": "main_paper_runtime_queue", "error": str(exc)})
                try:
                    from src.research_lab.main_paper_runtime import observe_main_paper_runtime
                    runtime_limit = int(getattr(args, "main_paper_runtime_limit", 50))
                    out["main_paper_runtime_observation"] = observe_main_paper_runtime(
                        private_root,
                        limit=runtime_limit,
                        apply=apply and runtime_limit != 0,
                        provider=paper_provider,
                    )
                except Exception as exc:  # noqa: BLE001 - paper observer must not break the cycle
                    out.setdefault("errors", []).append({
                        "where": "main_paper_runtime_observation",
                        "error": str(exc),
                    })
                try:
                    from src.research_lab.paper_telegram_preview import build_paper_telegram_preview
                    out["paper_telegram_preview"] = build_paper_telegram_preview(private_root)
                except Exception as exc:  # noqa: BLE001 - preview surface must not break the cycle
                    out.setdefault("errors", []).append({"where": "paper_telegram_preview", "error": str(exc)})
                try:
                    from src.research_lab.paper_signals.training_export import export_training_rows
                    out["paper_signal_training_export"] = export_training_rows(private_root)
                except Exception as exc:  # noqa: BLE001 - training export must not break the cycle
                    out.setdefault("errors", []).append({
                        "where": "paper_signal_training_export",
                        "error": str(exc),
                    })
            except Exception as exc:  # noqa: BLE001 - paper lane must never break the cycle
                out.setdefault("errors", []).append({"where": "paper_signals", "error": str(exc)})
    stages = _stage_status(args, apply)
    out["stages"] = stages
    if apply:
        from src.research_lab import farm_journal
        farm_journal.log_cycle(private_root, ts=time.time(), mode="apply", result=out,
                               stages=stages, discovery=discovery_info)
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
    ap.add_argument("--run-paper-signals", action="store_true",
                    help="run one bounded operational paper-watch cycle (observe+generate; research-only)")
    ap.add_argument("--true-forward-max-candidates", type=int, default=20,
                    help="max true-forward records collected per apply cycle; set 0 for wiring smoke checks")
    ap.add_argument("--paper-signals-max-new", type=int, default=5,
                    help="max new paper-watch cards generated per cycle; set 0 for wiring smoke checks")
    ap.add_argument("--pfr-db-path", default="",
                    help="path to strategy_lab.sqlite for PFR forward-watch lane "
                         "(requires --run-paper-signals; OFF by default — must be explicit)")
    ap.add_argument("--paper-signals-max-pfr-scan", type=int, default=30,
                    help="max PFR records inspected by --run-paper-signals per farm cycle")
    ap.add_argument("--paper-signals-max-observe", type=int, default=50,
                    help=("max active paper signals observed by --run-paper-signals per cycle "
                          "(set 0 for smoke checks)"))
    ap.add_argument("--paper-signals-fetch-timeout", type=float, default=10.0,
                    help="per-request public OKX timeout used by --run-paper-signals")
    ap.add_argument("--main-paper-runtime-limit", type=int, default=50,
                    help="max main-paper runtime queue items observed per --run-paper-signals cycle")
    ap.add_argument("--enrich-funding", action="store_true", help="enable public funding enrichment tasks")
    ap.add_argument("--enrich-oi", action="store_true", help="enable public open-interest enrichment tasks")
    ap.add_argument("--backend", choices=["cpu", "auto", "gpu"], default="auto")
    ap.add_argument("--sweep-tier", choices=["smoke", "normal", "deep"], default="normal",
                    help="parameter-search depth: smoke=profile cap; normal=x2; deep=x4 (abs-capped)")
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
    ap.add_argument("--discovery-ttl-seconds", type=int, default=6 * 3600,
                    help="treat the discovery snapshot as fresh for this many seconds; refresh when older")
    ap.add_argument("--no-discovery-refresh", action="store_true",
                    help="never auto-refresh the discovery snapshot in apply mode (warn loudly if stale)")
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
        # A single-process loop has no live worker at boot, so any 'running' task is stale from a
        # previous stop. Requeue it once so the next cycle re-drains it instead of masking it as work.
        # Single-process guard: reconcile_orphan_running assumes no other live loop. A fresh lock
        # (younger than 2 cycles) means a second loop is active -> abort rather than corrupt state.
        lock_path = private_root / "state" / "farm_loop.lock"
        if args.loop and lock_path.exists():
            age = time.time() - lock_path.stat().st_mtime
            if age < 2 * max(60, args.sleep_seconds):
                print(f"ABORT: another farm_loop appears active (lock {lock_path}, age {age:.0f}s); "
                      f"stop it or delete the lock to override.")
                return
        if args.loop:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text(str(os.getpid()), encoding="utf-8")
        n_orphan = tasks.reconcile_orphan_running()
        if n_orphan:
            print(f"  reconcile: requeued {n_orphan} orphan running task(s) from a previous stop")
    else:
        private_root = Path(args.private_root)
        lock_path = None
        tasks = FarmTasksDB(":memory:")  # dry-run persists nothing

    try:
        if not args.loop:
            out = _run_once(args, tasks, profiles, policy, private_root, apply)
            _print_cycle(out)  # a single explicit cycle is always shown
            return
        prev_sig = None
        while True:
            if args.stop_file and Path(args.stop_file).exists():
                print(f"stop-file present ({args.stop_file}) - exiting loop")
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
            if apply and lock_path is not None:
                lock_path.write_text(str(os.getpid()), encoding="utf-8")  # keep the lock fresh
            time.sleep(max(1, args.sleep_seconds))
    except KeyboardInterrupt:
        print("\ninterrupted - graceful stop")
    finally:
        tasks.close()
        if apply and args.loop and lock_path is not None:
            try:
                lock_path.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
