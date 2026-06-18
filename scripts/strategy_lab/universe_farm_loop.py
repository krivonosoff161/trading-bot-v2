# -*- coding: utf-8 -*-
"""First-class universe-driven farm loop — grind the universe, do not wait for news.

Each cycle rotates through (group x timeframe x families) work units, prepares any
missing candles (bounded, with per-symbol backoff), queues the multi-family research
plans, and drains a few worker jobs so the farm actually COMPUTES (not just queues).
A persisted cursor makes coverage systematic across cycles.

    python -m scripts.strategy_lab.universe_farm_loop --once --dry-run
    python -m scripts.strategy_lab.universe_farm_loop --once --apply --units-per-cycle 2 --run-worker
    python -m scripts.strategy_lab.universe_farm_loop --loop --apply --night-mode --run-worker --sleep-seconds 120

Safety: paper/research only. Public OKX market data only. Never touches .env,
AUTO_TRADE, order execution, private exchange endpoints, or Telegram credentials.
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

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402
from src.research_lab.universe import load_universe  # noqa: E402
from src.research_lab.universe_refill import build_worklist, rotate_worklist  # noqa: E402
from src.research_lab.universe_refill_runner import RefillState, run_refill_cycle  # noqa: E402


def _night_mode(flag: bool) -> bool:
    return flag or os.getenv("STRATEGY_LAB_NIGHT_MODE", "").strip().lower() in {"1", "true", "yes"}


def _state_path(private_root: Path) -> Path:
    return private_root / "state" / "universe_refill_state.json"


def _drain_worker(private_root: Path, max_jobs: int, night_mode: bool) -> list[dict]:
    if max_jobs <= 0:
        return []
    from scripts.strategy_lab.worker_once import run_worker_once
    out: list[dict] = []
    for _ in range(max_jobs):
        try:
            status = run_worker_once(private_root, night_mode=night_mode, ignore_cadence=True)
        except Exception as exc:
            out.append({"status": "error", "reason": str(exc)[:200]})
            break
        out.append(status)
        if status.get("status") in {"queue_empty", "deferred"}:
            break
    return out


def _print_cycle(units, result: dict, worker: list[dict], enrich: dict | None = None) -> None:
    c = result["counters"]
    print(f"\nunits={c['units']} ({', '.join(u.key() for u in units)})")
    print(f"  queued={c['jobs_queued']} already={c['already_queued']} prepared={c['prepared']} "
          f"would_prepare={c['would_prepare']} would_queue={c['would_queue']} "
          f"prepare_failed={c['prepare_failed']} backoff_skips={c['prepare_skipped_backoff']}")
    if enrich is not None:
        print("  flow enrich: " + " ".join(f"{k}={v}" for k, v in enrich.items() if v))
    for d in result["decisions"][:20]:
        tag = d.get("experiment_id") or f"{len(d.get('families', []))}fam"
        print(f"  plan {d.get('group')}/{d.get('timeframe')} -> {d.get('action')} "
              f"({d.get('symbols')} symbols, {tag})")
    for s in result["skipped"][:15]:
        print(f"  skip {s.get('symbol')}: {s.get('reason')}")
    if worker:
        print("  worker: " + ", ".join(str(w.get("status")) for w in worker))


def _resolve_universe(args):
    """Manual universe.yaml, or the OKX-discovered snapshot when --discover-okx-universe."""
    if not args.discover_okx_universe:
        return load_universe()
    from src.research_lab.instrument_discovery import discovered_universe, load_snapshot
    snap = load_snapshot(Path(args.private_root))
    if snap.get("instruments"):
        return discovered_universe(snap)
    print("no discovery snapshot found - run discover_okx_universe --apply first; using manual universe")
    return load_universe()


def _run_cycle(args, *, apply: bool) -> dict:
    universe = _resolve_universe(args)
    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=_night_mode(args.night_mode))
    groups = [g.strip() for g in args.groups.split(",") if g.strip()] or None
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()] or None
    units_all = build_worklist(universe, groups=groups, timeframes=timeframes)
    if not units_all:
        print("worklist empty (no group/timeframe/family combination available)")
        return {"counters": {}, "decisions": [], "skipped": []}

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output) \
        if apply else Path(args.private_root)
    state_path = _state_path(private_root)
    state = RefillState.load(state_path) if apply else RefillState()
    units, next_cursor = rotate_worklist(units_all, state.cursor, args.units_per_cycle)

    provider = None
    conn = None
    if apply:
        from src.research_lab.market_data_provider import get_provider
        from src.research_lab.state_db import connect, default_db_path, init_db
        provider = get_provider(args.provider, allow_synthetic=(args.provider == "synthetic"))
        conn = connect(default_db_path(private_root))
        init_db(conn)
    try:
        result = run_refill_cycle(
            units, universe=universe, profiles=profiles, policy=policy, private_root=private_root,
            provider=provider, state=state, apply=apply, now_ms=int(time.time() * 1000), conn=conn,
            max_prepares=args.max_prepares_per_cycle, data_days=args.data_days, full=args.full,
            backend=args.backend, allow_public_output=args.allow_public_output,
        )
    finally:
        if conn is not None:
            conn.close()
    enrich = _maybe_enrich(args, units, universe, profiles, private_root, apply)
    worker = _drain_worker(private_root, args.max_worker_jobs_per_cycle, _night_mode(args.night_mode)) \
        if (apply and args.run_worker) else []
    if apply:
        state.cursor = next_cursor
        state.save(state_path)
    _print_cycle(units, result, worker, enrich)
    return result


def _maybe_enrich(args, units, universe, profiles, private_root, apply) -> dict | None:
    """Optional bounded funding enrichment of the cycle's prepared files."""
    if not args.enrich_funding:
        return None
    if apply and args.flow_provider == "null":
        return None
    from src.research_lab.flow_enrich import FlowEnrichState, run_flow_enrich
    provider = None
    if apply and args.flow_provider == "okx-public-funding":
        from src.research_lab.providers.okx_flow import OkxPublicFundingProvider
        provider = OkxPublicFundingProvider()
    state_path = private_root / "state" / "flow_enrich_state.json"
    fstate = FlowEnrichState.load(state_path) if apply else FlowEnrichState()
    result = run_flow_enrich(
        units, universe=universe, profiles=profiles, private_root=private_root, provider=provider,
        state=fstate, apply=apply, now_ms=int(time.time() * 1000),
        max_enrich=args.max_flow_enrich_per_cycle, full=args.full,
        allow_public_output=args.allow_public_output)
    if apply:
        fstate.save(state_path)
    return result["counters"]


def main() -> None:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan only; write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="prepare data + queue plans + write state")
    run = ap.add_mutually_exclusive_group()
    run.add_argument("--once", action="store_true", help="one cycle then exit (default)")
    run.add_argument("--loop", action="store_true", help="run continuously until stop-file / Ctrl+C")
    ap.add_argument("--units-per-cycle", type=int, default=2)
    ap.add_argument("--max-prepares-per-cycle", type=int, default=6)
    ap.add_argument("--max-worker-jobs-per-cycle", type=int, default=0)
    ap.add_argument("--run-worker", action="store_true", help="drain a few queued farm jobs each cycle")
    ap.add_argument("--discover-okx-universe", action="store_true",
                    help="use the OKX-discovered instrument snapshot instead of universe.yaml "
                         "(run discover_okx_universe --apply first; groups are discovered_<group>)")
    ap.add_argument("--groups", default="", help="comma-separated universe groups (default: all)")
    ap.add_argument("--timeframes", default="", help="comma-separated timeframes (default: 1h,4h,1d,15m)")
    ap.add_argument("--full", action="store_true", help="full per-timeframe caps instead of the smoke subset")
    ap.add_argument("--night-mode", action="store_true")
    ap.add_argument("--provider", choices=["okx-public", "synthetic"], default="okx-public",
                    help="market-data provider; synthetic = offline deterministic demo (not real data)")
    ap.add_argument("--backend", choices=["cpu", "auto", "gpu"], default="auto",
                    help="compute backend; auto = GPU when available, else honest CPU fallback")
    ap.add_argument("--enrich-funding", action="store_true",
                    help="bounded: forward-fill public OKX funding onto prepared files for flow families")
    ap.add_argument("--max-flow-enrich-per-cycle", type=int, default=4)
    ap.add_argument("--flow-provider", choices=["okx-public-funding", "null"], default="okx-public-funding")
    ap.add_argument("--data-days", type=int, default=None)
    ap.add_argument("--sleep-seconds", type=int, default=120)
    ap.add_argument("--stop-file", default="")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()
    apply = bool(args.apply)

    print(f"universe_farm_loop mode={'APPLY' if apply else 'DRY-RUN'} "
          f"run={'loop' if args.loop else 'once'} private_root={args.private_root}")
    print("safety: paper-only; public OKX market data only; no orders / .env / AUTO_TRADE / private endpoints")

    if not args.loop:
        _run_cycle(args, apply=apply)
        return
    try:
        while True:
            if args.stop_file and Path(args.stop_file).exists():
                print(f"stop-file present ({args.stop_file}) — exiting loop")
                break
            _run_cycle(args, apply=apply)
            time.sleep(max(1, args.sleep_seconds))
    except KeyboardInterrupt:
        print("\ninterrupted — graceful stop")


if __name__ == "__main__":
    main()
