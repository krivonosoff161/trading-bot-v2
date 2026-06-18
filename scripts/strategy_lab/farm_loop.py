# -*- coding: utf-8 -*-
"""Continuous research-farm loop — the self-deciding intake -> compute -> validation cycle.

Each cycle: read scanner WATCH/GO watches (+ optional OKX-discovery refill) -> normalize
to intake events -> plan/prepare/enrich/run_sweep/classify/validate as typed lifecycle
tasks -> never spin on already_queued (it pivots to discovery / deferred work or reports
"blocked: no eligible tasks"). dry-run plans into an in-memory task DB and writes nothing.

    python -m scripts.strategy_lab.farm_loop --once --dry-run
    python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --enrich-funding
    python -m scripts.strategy_lab.farm_loop --loop --apply --run-worker --run-validation --sleep-seconds 180

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


def _discovery_snapshot(private_root: Path):
    from src.research_lab.instrument_discovery import load_snapshot
    snap = load_snapshot(private_root)
    return snap if snap.get("instruments") else None


def _maybe_storage_maintain(private_root: Path, apply: bool) -> None:
    if not apply:
        return
    try:
        from src.research_lab.storage_policy import bound_farm_artifacts, maintain
        maintain(apply=True)
        bound_farm_artifacts(private_root, apply=True)
    except Exception as exc:  # noqa: BLE001 - storage hygiene must never break the loop
        print(f"  storage: skipped ({type(exc).__name__})")


def _providers(args, apply: bool):
    provider = flow_provider = None
    if apply:
        from src.research_lab.market_data_provider import get_provider
        provider = get_provider(args.provider, allow_synthetic=(args.provider == "synthetic"))
        if args.enrich_funding:
            from src.research_lab.providers.okx_flow import OkxPublicFundingProvider
            flow_provider = OkxPublicFundingProvider()
    return provider, flow_provider


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


def _run_once(args, tasks: FarmTasksDB, profiles, policy, private_root: Path, apply: bool) -> dict:
    provider, flow_provider = _providers(args, apply)
    events = _read_intake(args.max_plan_events)
    out = run_coordinator_cycle(
        tasks, private_root=private_root, profiles=profiles, policy=policy, intake_events=events,
        families=DEFAULT_FAMILIES, provider=provider, flow_provider=flow_provider, apply=apply,
        backend=args.backend, data_days=args.data_days, max_plan_events=args.max_plan_events,
        max_prepares=args.max_prepares, max_enrich=args.max_enrich, max_sweeps=args.max_sweeps,
        run_worker=args.run_worker, max_worker_jobs=args.max_worker_jobs, night_mode=args.night_mode,
        allow_public_output=args.allow_public_output, discovery_snapshot=_discovery_snapshot(private_root),
        run_validation=args.run_validation,
    )
    _print_cycle(out)
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
    ap.add_argument("--enrich-funding", action="store_true", help="enable public funding enrichment tasks")
    ap.add_argument("--backend", choices=["cpu", "auto", "gpu"], default="auto")
    ap.add_argument("--provider", choices=["okx-public", "synthetic"], default="okx-public")
    ap.add_argument("--max-plan-events", type=int, default=20)
    ap.add_argument("--max-prepares", type=int, default=4)
    ap.add_argument("--max-enrich", type=int, default=4)
    ap.add_argument("--max-sweeps", type=int, default=4)
    ap.add_argument("--max-worker-jobs", type=int, default=4)
    ap.add_argument("--data-days", type=int, default=None)
    ap.add_argument("--night-mode", action="store_true")
    ap.add_argument("--sleep-seconds", type=int, default=180)
    ap.add_argument("--stop-file", default="")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()
    apply = bool(args.apply)

    print(f"farm_loop mode={'APPLY' if apply else 'DRY-RUN'} run={'loop' if args.loop else 'once'} "
          f"private_root={args.private_root}")
    print("safety: paper-only; public OKX market data only; no orders / .env / AUTO_TRADE / private endpoints")

    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=args.night_mode)
    if apply:
        private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
        tasks = FarmTasksDB(tasks_db_path(private_root))
    else:
        private_root = Path(args.private_root)
        tasks = FarmTasksDB(":memory:")  # dry-run persists nothing

    try:
        if not args.loop:
            _run_once(args, tasks, profiles, policy, private_root, apply)
            return
        while True:
            if args.stop_file and Path(args.stop_file).exists():
                print(f"stop-file present ({args.stop_file}) — exiting loop")
                break
            print(f"\n=== farm cycle @ {int(time.time())} ===")
            _run_once(args, tasks, profiles, policy, private_root, apply)
            time.sleep(max(1, args.sleep_seconds))
    except KeyboardInterrupt:
        print("\ninterrupted — graceful stop")
    finally:
        tasks.close()


if __name__ == "__main__":
    main()
