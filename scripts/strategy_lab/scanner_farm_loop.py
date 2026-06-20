# -*- coding: utf-8 -*-
"""ARCHIVE-LEGACY scanner -> market-data -> farm loop — superseded by farm_loop.

> DEPRECATED: this flat bridge writes into the shared compute queue and keeps its own
> state/scanner_farm_loop.sqlite checkpoint, bypassing the farm_tasks.sqlite brain. The
> current core reads scanner watches as a file via intake_adapter. Use
> scripts.strategy_lab.farm_loop instead. To run this legacy loop anyway, pass
> --i-understand-legacy.

Closes the gap between a scanner WATCH/GO and a real farm calculation: each cycle
reads fresh enriched watches, auto-materializes the candles eligible rows need,
queues bounded sweeps at the scanner-selected timeframe, and (optionally) drains a
few farm jobs — all checkpointed so a restart never reprocesses, and all with
visible per-cycle counters. No operator log-watching required for normal running.

    python -m scripts.strategy_lab.scanner_farm_loop --once --dry-run
    python -m scripts.strategy_lab.scanner_farm_loop --once --apply
    python -m scripts.strategy_lab.scanner_farm_loop --loop --apply --sleep-seconds 900

Safety: paper/research only. Public OKX market data only. Never touches .env,
AUTO_TRADE, order execution, private exchange endpoints, or Telegram credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402
from src.research_lab.pipeline_state import PipelineState, state_db_path  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.scanner_farm_pipeline import run_cycle  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402


def _load_watches(include_expired: bool) -> list[dict]:
    from src.scout.watch_queue import STATUS_EXPIRED, STATUS_OPEN, _read_rows, open_watches
    if not include_expired:
        return open_watches()
    return [r for r in _read_rows() if r.get("status") in {STATUS_OPEN, STATUS_EXPIRED}]


def _backlog(group: str) -> list[str]:
    if not group:
        return []
    try:
        from src.research_lab.universe import load_universe
        return list(load_universe().symbols_in(group))
    except Exception as exc:
        print(f"backlog universe '{group}' unavailable: {exc}")
        return []


def _maybe_scanner_pass(enabled: bool, dry: bool, limit: int) -> None:
    if not enabled:
        return
    import asyncio
    from src.scout import scanner_v0
    try:
        asyncio.run(scanner_v0.run(limit=limit, dry=dry, use_buffer=True))
    except Exception as exc:  # a scanner hiccup must not kill the coordinator
        print(f"scanner pass error: {exc}")


def _drain_worker(private_root: Path, max_jobs: int) -> list[dict]:
    if max_jobs <= 0:
        return []
    from scripts.strategy_lab.worker_once import run_worker_once
    out: list[dict] = []
    for _ in range(max_jobs):
        try:
            status = run_worker_once(private_root, ignore_cadence=True)
        except Exception as exc:
            out.append({"status": "error", "reason": str(exc)[:200]})
            break
        out.append(status)
        if status.get("status") in {"queue_empty", "deferred"}:
            break
    return out


def _print_cycle(result: dict, worker: list[dict]) -> None:
    c = result["counters"]
    print(f"\ncycle={result['cycle']} "
          f"watches={c.get('watches_read', 0)} resolved={c.get('resolved', 0)} "
          f"planned={c.get('jobs_planned', 0)} queued={c.get('jobs_queued', 0)} "
          f"prepared={c.get('data_prepared', 0)} would_queue={c.get('would_queue', 0)} "
          f"already_queued={c.get('already_queued', 0)} prepare_failed={c.get('prepare_failed', 0)} "
          f"skipped={c.get('skipped_total', 0)}")
    for d in result["decisions"][:20]:
        print(f"  job {d.get('symbol')} tf={d.get('timeframe')} -> {d.get('action')}"
              + (f" ({d.get('experiment_id')})" if d.get("experiment_id") else ""))
    for s in result["skipped"][:20]:
        print(f"  skip {s.get('symbol')}: {s.get('reason')}")
    if worker:
        print("  worker: " + ", ".join(str(w.get("status")) for w in worker))


def _run_one_cycle(args, *, apply: bool) -> dict:
    private_root = Path(args.private_root)
    profiles = load_timeframe_profiles()
    policy = load_resource_policy()
    provider = None
    if apply:
        from src.research_lab.market_data_provider import get_provider
        provider = get_provider("okx-public")

    _maybe_scanner_pass(args.run_scanner_pass, dry=not apply, limit=args.scanner_limit)
    watches = _load_watches(args.include_expired)

    state_path = args.state_path or (str(state_db_path(private_root)) if apply else ":memory:")
    state = PipelineState(state_path if apply else ":memory:")
    try:
        result = run_cycle(
            state, private_root=private_root, provider=provider, watches=watches,
            profiles=profiles, policy=policy, backlog_symbols=_backlog(args.refill_universe),
            max_jobs=args.max_jobs_per_cycle, max_prepares=args.max_data_prepares_per_cycle,
            backend=args.backend, apply=apply, data_days=args.data_days,
            allow_public_output=args.allow_public_output,
        )
        worker = _drain_worker(private_root, args.max_worker_jobs_per_cycle) if (apply and args.run_worker) else []
        _print_cycle(result, worker)
        return {"result": result, "worker": worker, "status": state.status()}
    finally:
        state.close()


def _should_stop(stop_file: str) -> bool:
    return bool(stop_file) and Path(stop_file).exists()


_LEGACY_MSG = (
    "scanner_farm_loop is ARCHIVE-LEGACY: it writes into the shared compute queue and a "
    "separate checkpoint, bypassing the farm_tasks.sqlite brain. Use the current core:\n"
    "  python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --run-paper\n"
    "  (or bat\\strategy_lab_farm_full_cycle_loop.bat)\n"
    "To run this legacy loop anyway, pass --i-understand-legacy."
)


def _legacy_abort_unless_acknowledged(args) -> None:
    if not getattr(args, "i_understand_legacy", False):
        print("ABORT: " + _LEGACY_MSG)
        raise SystemExit(2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--i-understand-legacy", action="store_true",
                    help="acknowledge this is an archived loop and run it anyway")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan only; write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="prepare data + queue sweeps + write state")
    run = ap.add_mutually_exclusive_group()
    run.add_argument("--once", action="store_true", help="one cycle then exit (default)")
    run.add_argument("--loop", action="store_true", help="run continuously until stop-file / Ctrl+C")
    ap.add_argument("--sleep-seconds", type=int, default=900)
    ap.add_argument("--max-jobs-per-cycle", type=int, default=8)
    ap.add_argument("--max-data-prepares-per-cycle", type=int, default=8)
    ap.add_argument("--max-worker-jobs-per-cycle", type=int, default=0)
    ap.add_argument("--run-worker", action="store_true", help="drain a few queued farm jobs each cycle")
    ap.add_argument("--run-scanner-pass", action="store_true", help="run one scanner pass at cycle start")
    ap.add_argument("--scanner-limit", type=int, default=5)
    ap.add_argument("--include-expired", action="store_true")
    ap.add_argument("--refill-universe", default="", help="universe group to keep the GPU busy when fresh is thin")
    ap.add_argument("--backend", choices=["cpu", "gpu", "auto"],
                    default=os.getenv("STRATEGY_LAB_SCANNER_BRIDGE_BACKEND", "auto"))
    ap.add_argument("--data-days", type=int, default=None)
    ap.add_argument("--stop-file", default="")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--state-path", default="")
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()
    _legacy_abort_unless_acknowledged(args)
    apply = bool(args.apply)

    print(f"scanner_farm_loop mode={'APPLY' if apply else 'DRY-RUN'} "
          f"run={'loop' if args.loop else 'once'} private_root={args.private_root}")
    print("safety: paper-only; public OKX market data only; no orders / .env / AUTO_TRADE / private endpoints")

    if not args.loop:
        out = _run_one_cycle(args, apply=apply)
        print("\nstatus: " + json.dumps(out["status"], ensure_ascii=False))
        return

    try:
        while True:
            if _should_stop(args.stop_file):
                print(f"stop-file present ({args.stop_file}) — exiting loop")
                break
            _run_one_cycle(args, apply=apply)
            if _should_stop(args.stop_file):
                break
            time.sleep(max(1, args.sleep_seconds))
    except KeyboardInterrupt:
        print("\ninterrupted — graceful stop")


if __name__ == "__main__":
    main()
