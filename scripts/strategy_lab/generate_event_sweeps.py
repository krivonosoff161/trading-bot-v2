# -*- coding: utf-8 -*-
"""Generate event-driven sweep proposals from local candles. Dry-run by default.

Detects strong historical moves on each symbol of a universe group, builds one
bounded sweep per event (probing earlier-entry params), and either prints them
(--dry-run) or compiles + queues them (--apply). Proposals and compiled specs are
written only under the private research root. The event is historical; the sweep
runs the normal no-lookahead simulator. 1m stays trigger-only; no full brute force.

    python -m scripts.strategy_lab.generate_event_sweeps --universe l2_high_beta --timeframe 15m --dry-run
    python -m scripts.strategy_lab.generate_event_sweeps --universe l2_high_beta --timeframe 15m --apply
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

from src.research_lab.event_sweeps import build_event_sweeps, event_context_for, sweep_proposal_dict  # noqa: E402
from src.research_lab.experiment import choose_symbol_file, load_candles  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.sweep_compile import compile_sweep  # noqa: E402
from src.research_lab.state_db import connect, default_db_path, ensure_experiment_queued, init_db  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402
from src.research_lab.universe import load_universe  # noqa: E402

DEFAULT_DATA_GLOB = "scripts/analysis/research/_okxhist/ai_scanner_feasibility/{symbol}_*.json"


def event_spec_dir(private_root: Path) -> Path:
    return private_root / "plans" / "event_specs"


def _night_mode(flag: bool) -> bool:
    return flag or os.getenv("STRATEGY_LAB_NIGHT_MODE", "").strip().lower() in {"1", "true", "yes"}


def collect_sweeps(universe, profiles, policy, *, group, timeframe, data_glob, window, min_move_pct, max_events):
    symbols = universe.symbols_in(group)
    if not symbols:
        raise ValueError(f"unknown or empty universe group: {group}")
    pairs = []
    skipped = []
    for symbol in symbols:
        path = choose_symbol_file(data_glob, symbol)
        if not path:
            skipped.append((symbol, "no_data_file"))
            continue
        candles = load_candles(path)
        if len(candles) < window + 2:
            skipped.append((symbol, "too_few_candles"))
            continue
        pairs.extend(build_event_sweeps(
            candles, anchor_symbol=symbol, timeframe=timeframe, universe=universe,
            window=window, min_move_pct=min_move_pct, max_events=max_events,
        ))
    return pairs, skipped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--data-glob", default=DEFAULT_DATA_GLOB)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--min-move-pct", type=float, default=8.0)
    ap.add_argument("--max-events", type=int, default=3, help="Max events detected per symbol")
    ap.add_argument("--night-mode", action="store_true")
    ap.add_argument("--priority", type=int, default=75)
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    universe = load_universe()
    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=_night_mode(args.night_mode))
    pairs, skipped = collect_sweeps(
        universe, profiles, policy, group=args.universe, timeframe=args.timeframe,
        data_glob=args.data_glob, window=args.window, min_move_pct=args.min_move_pct,
        max_events=max(1, args.max_events),
    )
    # bound generation by the resource policy
    capped = pairs[: max(1, policy.autopilot_generate_max)]
    for event, sweep in capped:
        print(f"sweep {json.dumps(sweep_proposal_dict(event, sweep), ensure_ascii=False)}")
    for symbol, reason in skipped:
        print(f"skipped symbol={symbol} reason={reason}")
    print(f"detected={len(pairs)} proposed={len(capped)} (cap={policy.autopilot_generate_max})")

    if not args.apply:
        print("dry-run: nothing written (use --apply to compile + queue)")
        return

    result = _apply(capped, args, profiles, policy)
    print(f"applied queued={result['queued']} already_pending={result['already_pending']} db={result['db_label']}")


def _apply(capped, args, profiles, policy) -> dict:
    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    out_dir = event_spec_dir(private_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = default_db_path(private_root)
    conn = connect(db_path)
    init_db(conn)
    queued, already = 0, 0
    try:
        for _event, sweep in capped:
            exp = compile_sweep(
                sweep, data_glob=args.data_glob, timeframe_profiles=profiles,
                resource_policy=policy, event_context=event_context_for(_event),
            )
            spec_path = out_dir / f"{exp.experiment_id}.json"
            spec_path.write_text(json.dumps(_exp_to_dict(exp), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _, created = ensure_experiment_queued(conn, spec_path.resolve(), priority=args.priority)
            queued += int(created)
            already += int(not created)
    finally:
        conn.close()
    return {"queued": queued, "already_pending": already, "db_label": f"strategy-lab/state/{db_path.name}"}


def _exp_to_dict(exp) -> dict:
    return {
        "experiment_id": exp.experiment_id,
        "data_glob": exp.data_glob,
        "symbols": exp.symbols,
        "families": exp.families,
        "fees_bps": exp.fees_bps,
        "slippage_bps": exp.slippage_bps,
        "min_trades": exp.min_trades,
        "split_ratio": exp.split_ratio,
        "max_runs": exp.max_runs,
        "parameter_grid": exp.parameter_grid,
        "event_context": exp.event_context,
    }


if __name__ == "__main__":
    main()
