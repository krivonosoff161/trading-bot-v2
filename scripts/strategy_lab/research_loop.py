# -*- coding: utf-8 -*-
"""Time-bounded controlled research loop — NOT a daemon.

Runs research-cycle iterations until a wall-clock duration or an iteration cap,
sleeping between iterations and writing a heartbeat each time. Each iteration may
(optionally, gated) ask a cheap LLM for bounded JSON proposals, validate them with
the deterministic validator, store the validated ones, then run one readiness-aware
research cycle (queue only data-ready + capped worker step). A deferred worker is
recorded and the loop simply waits for the next iteration — no storm, no daemon.

Default: dry-run, no network, no LLM, writes only status/heartbeat/report.

    python -m scripts.strategy_lab.research_loop --dry-run --duration-minutes 5
    python -m scripts.strategy_lab.research_loop --apply --duration-minutes 30 --sleep-seconds 60 --max-worker-jobs-per-iteration 1
    # real cheap-LLM propose needs STRATEGY_LAB_LLM_ENABLED=1 + provider env + --apply --llm-propose
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.strategy_lab.research_cycle import run_research_cycle  # noqa: E402
from src.research_lab.candidate_registry import registry_path, registry_summary  # noqa: E402
from src.research_lab.env_file import load_env_file  # noqa: E402
from src.research_lab.llm_proposals import (  # noqa: E402
    evaluate_llm_loop_gates,
    generate_proposals_via_llm,
    load_llm_loop_config,
)
from src.research_lab.llm_provider import LLMProviderError, load_provider, record_usage, today_usage  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.proposal_store import load_proposals, proposals_path, status_counts, upsert_proposals  # noqa: E402
from src.research_lab.research_cycle import ResearchCycleConfig  # noqa: E402
from src.research_lab.research_loop import write_heartbeat, write_loop_report  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.state_db import connect, default_db_path, init_db  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402
from src.research_lab.universe import load_universe  # noqa: E402

_HARD_ITER_CAP = 10_000  # backstop; the loop is bounded by duration/max-iterations anyway
_MAX_DURATION_MIN = 240   # quiet desktop backstop ceiling
_NIGHT_MAX_DURATION_MIN = 720
_IDLE_FLOOR_SECONDS = 60  # when apply-mode work is throttled/idle, wait at least this (no busy-spin)
_CONTRACT_FAILURE_REASONS = {
    "json_parse_error",
    "wrong_top_level_shape",
    "missing_proposals_array",
    "malformed_json",
}


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _build_summary(private_root: Path) -> str:
    reg = registry_summary(registry_path(private_root))
    props = status_counts(load_proposals(proposals_path(private_root)))
    return (f"candidates={reg.get('entries', 0)} (unique {reg.get('unique_candidates', 0)}), "
            f"by_validation={reg.get('by_validation_status', {})}, proposals_by_status={props}")


def _active_queue_count(private_root: Path) -> int:
    db_path = default_db_path(private_root)
    if not db_path.exists():
        return 0
    conn = connect(db_path)
    try:
        init_db(conn)
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM queue WHERE status IN ('queued', 'running')"
        ).fetchone()
        return int(row["n"] if row is not None else 0)
    finally:
        conn.close()


def _synthetic_candidates(universe) -> list[dict]:
    syms = sorted(universe.all_symbols())[:2] or ["BTC_USDT_SWAP"]
    return [{
        "setup_family": "momentum_breakout", "requested_timeframe": "1d", "symbols": [s],
        "parameter_grid": {"momentum_breakout": [{"lookback": 20, "hold_bars": 5}]},
        "hypothesis": f"synthetic offline test for momentum_breakout on {s}",
        "expected_failure_mode": "late entry / whipsaw in ranges",
    } for s in syms]


def _is_contract_failure(llm: dict) -> bool:
    if llm.get("status") != "ok":
        return False
    if int(llm.get("validated", 0) or 0) > 0:
        return False
    reasons = llm.get("reject_reasons") or {}
    if not isinstance(reasons, dict) or not reasons:
        return False
    return all(str(reason) in _CONTRACT_FAILURE_REASONS for reason in reasons)


def _llm_step(args, private_root, universe, profiles, policy) -> dict:
    """Optional cheap-LLM propose -> validate -> store. Gated; dry-run never calls."""
    if not args.llm_propose:
        return {"status": "disabled"}
    if not args.apply:
        return {"status": "dry_run_no_call"}
    active_queue = _active_queue_count(private_root)
    if active_queue >= max(1, int(args.max_queued)):
        return {"status": "queue_at_cap", "active_queue": active_queue, "max_queued": int(args.max_queued)}
    allow_synth = _truthy("STRATEGY_LAB_ALLOW_SYNTHETIC")
    provider = load_provider(allow_synthetic=allow_synth, synthetic_candidates=_synthetic_candidates(universe))
    if not getattr(provider, "configured", False):
        return {"status": "provider_not_configured"}
    cfg = load_llm_loop_config(max_candidates=max(1, args.max_candidates), allow_synthetic=allow_synth)
    spent = today_usage(private_root)["cost_rub"]
    if cfg.daily_cap_value is not None and spent >= cfg.daily_cap_value:
        return {"status": "daily_cap_exhausted", "spent_today_rub": spent, "cost_rub": 0.0}
    if provider.name != "synthetic":  # real network provider needs all spend gates
        gate = evaluate_llm_loop_gates(cfg, send_requested=True, dry_run=False, spent_today=spent)
        if not gate.allowed:
            return {"status": f"blocked:{gate.reason}"}
    created_at = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        batch, usage = generate_proposals_via_llm(
            provider, summary=_build_summary(private_root), universe=universe,
            timeframe_profiles=profiles, resource_policy=policy, created_at=created_at,
            max_candidates=max(1, args.max_candidates),
        )
    except LLMProviderError as exc:
        return {"status": f"error:{type(exc).__name__}"}
    record_usage(private_root, usage, allow_public_output=args.allow_public_output)
    if batch.validated:
        upsert_proposals(proposals_path(private_root), batch.validated)
    return {
        "status": "ok", "provider": provider.name,
        "validated": len(batch.validated), "rejected": len(batch.rejected),
        "reject_reasons": batch.reject_reasons(), "cost_rub": usage.cost_rub,
    }


def _iteration_config(args) -> ResearchCycleConfig:
    allow_synth = _truthy("STRATEGY_LAB_ALLOW_SYNTHETIC")
    return ResearchCycleConfig(
        apply=bool(args.apply),
        generate_proposals=True,
        max_proposals=max(1, args.max_candidates),
        queue=True,
        max_queue=args.max_queued,
        run_worker=True,
        max_worker_jobs=max(1, args.max_worker_jobs_per_iteration),
        prepare_1m=bool(args.prepare_missing_data),
        prepare_1m_apply=bool(args.prepare_missing_data) and bool(args.apply),
        provider=args.provider,
        allow_synthetic=allow_synth,
        night_mode=bool(args.night_mode),
    )


def run_loop(args) -> dict:
    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    if args.load_env:
        load_env_file(args.load_env, override=False)
    universe = load_universe()
    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=bool(args.night_mode))
    config = _iteration_config(args)

    duration_cap = _NIGHT_MAX_DURATION_MIN if args.night_mode else _MAX_DURATION_MIN
    duration_s = min(max(0.0, args.duration_minutes), duration_cap) * 60.0
    max_iter = min(max(1, args.max_iterations), _HARD_ITER_CAP)
    sleep_s = max(0, int(args.sleep_seconds))
    mode = "apply" if args.apply else "dry_run"

    totals = {k: 0 for k in ("proposals_queued", "ready_jobs", "skipped_missing_data",
                             "worker_completed", "worker_deferred", "worker_failed",
                             "llm_validated", "llm_rejected")}
    llm_cost = 0.0
    llm_contract_failures = 0
    iterations: list[dict] = []
    start = time.monotonic()
    i = 0
    while True:
        i += 1
        write_heartbeat(private_root, {"status": "running", "iteration": i, "mode": mode,
                                       "elapsed_seconds": round(time.monotonic() - start, 1)},
                        allow_public_output=args.allow_public_output)
        try:  # one bad iteration must not kill the bounded run
            if args.llm_propose and llm_contract_failures >= max(1, int(args.max_llm_contract_failures)):
                llm = {
                    "status": "contract_breaker",
                    "reason": "too_many_consecutive_contract_failures",
                    "consecutive_failures": llm_contract_failures,
                    "cost_rub": 0.0,
                }
            else:
                llm = _llm_step(args, private_root, universe, profiles, policy)
            cycle = run_research_cycle(config, private_root=private_root, allow_public_output=args.allow_public_output)
            c = cycle.counts
        except Exception as exc:  # noqa: BLE001 - record + continue; the loop is the safety boundary
            llm, c = {"status": f"iteration_error:{type(exc).__name__}"}, {}
        totals["llm_validated"] += int(llm.get("validated", 0) or 0)
        totals["llm_rejected"] += int(llm.get("rejected", 0) or 0)
        llm_cost += float(llm.get("cost_rub", 0.0) or 0.0)
        if _is_contract_failure(llm):
            llm_contract_failures += 1
        elif llm.get("status") not in {"contract_breaker"}:
            llm_contract_failures = 0
        for key in ("proposals_queued", "ready_jobs", "skipped_missing_data",
                    "worker_completed", "worker_deferred", "worker_failed"):
            totals[key] += int(c.get(key, 0) or 0)

        iterations.append({
            "iteration": i, "llm": llm,
            "queued": c.get("proposals_queued", 0), "ready": c.get("ready_jobs", 0),
            "missing": c.get("skipped_missing_data", 0),
            "worker": {"completed": c.get("worker_completed", 0), "deferred": c.get("worker_deferred", 0),
                       "failed": c.get("worker_failed", 0)},
        })
        elapsed = time.monotonic() - start
        write_heartbeat(private_root, {"status": "iteration_done", "iteration": i, "mode": mode,
                                       "elapsed_seconds": round(elapsed, 1), "last": iterations[-1]},
                        allow_public_output=args.allow_public_output)
        _write_running_report(private_root, mode, args, iterations, totals, llm_cost, start, args.allow_public_output)

        if i >= max_iter or elapsed >= duration_s:
            break
        # Avoid busy-spin: in apply mode, if the worker is throttled (deferred) or the
        # queue is idle, nothing can progress until the cool-down passes -> wait at least
        # the idle floor regardless of --sleep-seconds. Always cap by remaining time.
        worker = iterations[-1]["worker"]
        idle = bool(args.apply) and (worker["deferred"]
                                     or (not c.get("ready_jobs", 0) and not c.get("proposals_queued", 0)))
        eff_sleep = max(sleep_s, _IDLE_FLOOR_SECONDS) if idle else sleep_s
        eff_sleep = min(eff_sleep, max(0.0, duration_s - (time.monotonic() - start)))
        if eff_sleep > 0:
            time.sleep(eff_sleep)

    report = _final_report(mode, args, iterations, totals, llm_cost, start)
    write_loop_report(private_root, report, allow_public_output=args.allow_public_output)
    write_heartbeat(private_root, {"status": "finished", "iterations": len(iterations), "mode": mode,
                                   "elapsed_seconds": round(time.monotonic() - start, 1)},
                    allow_public_output=args.allow_public_output)
    return report


def _final_report(mode, args, iterations, totals, llm_cost, start) -> dict:
    return {
        "mode": mode, "iterations": len(iterations),
        "duration_minutes": round((time.monotonic() - start) / 60.0, 2),
        "totals": totals, "llm_cost_rub": round(llm_cost, 4),
        "iteration_log": iterations[-20:],
        "next_command": "python -m scripts.strategy_lab.status",
        "safety": {"no_live_trading": True, "no_order_engine": True, "no_paid_llm_default": True,
                   "data_provider": args.provider, "single_run": True,
                   "night_mode": bool(args.night_mode)},
    }


def _write_running_report(private_root, mode, args, iterations, totals, llm_cost, start, allow_public) -> None:
    write_loop_report(private_root, _final_report(mode, args, iterations, totals, llm_cost, start),
                      allow_public_output=allow_public)


def _print(report: dict) -> None:
    t = report["totals"]
    print(f"Strategy Lab research loop ({report['mode'].upper()}) - {report['iterations']} iteration(s), "
          f"{report['duration_minutes']} min")
    print(f"  proposals queued={t['proposals_queued']} ready={t['ready_jobs']} missing_data={t['skipped_missing_data']}")
    print(f"  LLM validated={t['llm_validated']} rejected={t['llm_rejected']} cost_rub={report['llm_cost_rub']}")
    print(f"  worker completed={t['worker_completed']} deferred={t['worker_deferred']} failed={t['worker_failed']}")
    print("  safety: single bounded run; no live trading, no order engine, no paid LLM by default")
    print("  report: state/research_loop/latest.json (private root)")


def main() -> None:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="No writes (except status), no worker, no network, no LLM (default)")
    mode.add_argument("--apply", action="store_true", help="Queue data-ready proposals, run capped worker per iteration")
    ap.add_argument("--duration-minutes", type=float, default=5.0)
    ap.add_argument("--max-iterations", type=int, default=1000)
    ap.add_argument("--sleep-seconds", type=int, default=60)
    ap.add_argument("--llm-propose", action="store_true", help="Ask the cheap LLM for candidates (gated; dry-run never calls)")
    ap.add_argument("--max-llm-contract-failures", type=int, default=3,
                    help="Disable LLM proposals for this run after N consecutive parse/schema failures")
    ap.add_argument("--load-env", default="", help="Optional .env file to load before provider resolution")
    ap.add_argument("--night-mode", action="store_true", help="Use opt-in night resource policy and longer duration cap")
    ap.add_argument("--prepare-missing-data", action="store_true")
    ap.add_argument("--provider", default="null", choices=["null", "synthetic", "okx-public"], help="DATA provider for prepare")
    ap.add_argument("--max-candidates", type=int, default=5)
    ap.add_argument("--max-queued", type=int, default=5)
    ap.add_argument("--max-worker-jobs-per-iteration", type=int, default=1)
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()
    _print(run_loop(args))


if __name__ == "__main__":
    main()
