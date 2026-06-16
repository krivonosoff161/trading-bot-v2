# -*- coding: utf-8 -*-
"""Bounded, visible GPU/auto sweep probe (paper/research only).

Runs ONE tiny momentum_breakout sweep on a local symbol with the requested
backend and prints the resolved backend, accelerated-run count, and elapsed_ms.
With ``--parity`` and a real GPU it also checks that the GPU kernel matches the
CPU scalar reference exactly.

No order engine, no live trading, no secrets, no network. If the GPU backend is
unavailable, ``--backend gpu`` reports the explicit reason (it never silently
runs on CPU); ``--backend auto`` falls back to CPU with a recorded reason.

    python -m scripts.strategy_lab.gpu_probe --backend auto
    python -m scripts.strategy_lab.gpu_probe --backend gpu --parity
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.experiment import (  # noqa: E402
    ExperimentSpec,
    choose_symbol_file,
    evaluate_spec,
    load_candles,
)
from src.research_lab.gpu_runtime import doctor, resolve_backend  # noqa: E402

DATA_GLOB = "scripts/analysis/research/_okxhist/ai_scanner_feasibility/{symbol}_*.json"
DEFAULT_GRID = [
    {"lookback": 20, "threshold_pct": 0.0},
    {"lookback": 10, "threshold_pct": 1.0},
    {"lookback": 30, "threshold_pct": 0.0},
]


def _parity_check(symbol: str, timeframe: str) -> str:
    import numpy as np

    from src.research_lab.gpu_kernels import generate_signals_vectorized
    from src.research_lab.gpu_runtime import array_module, detect_gpu
    from src.research_lab.strategies.breakout import signals_momentum_breakout

    path = choose_symbol_file(DATA_GLOB, symbol, timeframe=timeframe)
    if not path:
        return "parity: skipped (no local data file)"
    candles = load_candles(path)
    cap = detect_gpu()
    xp = array_module(cap.backend_name) if cap.gpu_available else np
    mism = 0
    for params in DEFAULT_GRID:
        scalar = signals_momentum_breakout(candles, params)
        vec = generate_signals_vectorized(candles, "momentum_breakout", params, xp=xp)
        if scalar != vec:
            mism += 1
    where = "GPU" if cap.gpu_available else "CPU(numpy)"
    return f"parity ({where} kernel vs scalar): {len(DEFAULT_GRID) - mism}/{len(DEFAULT_GRID)} match"


def main() -> None:
    ap = argparse.ArgumentParser(description="Bounded GPU/auto sweep probe (paper-only).")
    ap.add_argument("--backend", choices=["cpu", "gpu", "auto"], default="auto")
    ap.add_argument("--symbol", default="DOGE_USDT_SWAP")
    ap.add_argument("--timeframe", default="1d")
    ap.add_argument("--parity", action="store_true", help="Also check GPU/CPU kernel parity")
    args = ap.parse_args()

    print("=" * 60)
    print(f"  GPU PROBE  backend={args.backend}  symbol={args.symbol}  (paper-only)")
    print("=" * 60)
    cap = doctor()
    print(f"  gpu_available={cap['gpu_available']} backend_name={cap['backend_name']} "
          f"device={cap['device_name'] or '-'}")
    if not cap["gpu_available"]:
        print(f"  reason: {cap['reason_if_unavailable']}")
    print()

    resolution = resolve_backend(args.backend)
    if not resolution.ok():
        print(f"  backend={args.backend} -> NOT RUN: {resolution.error}")
        print("  (explicit reject; no silent CPU). Use --backend auto for a CPU fallback.")
        print("=" * 60)
        return

    spec = ExperimentSpec(
        experiment_id=f"gpu_probe_{args.backend}",
        data_glob=DATA_GLOB,
        symbols=[args.symbol],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": DEFAULT_GRID},
        timeframe=args.timeframe,
        backend=args.backend,
    )
    runtime: dict = {}
    results = evaluate_spec(spec, runtime)
    print(f"  runs={len(results)}")
    print(f"  requested_backend={runtime.get('requested_backend')} effective_backend={runtime.get('effective_backend')}")
    print(f"  signal_backend={runtime.get('signal_backend')} simulation_backend={runtime.get('simulation_backend')}")
    print(f"  accelerated_signal_runs={runtime.get('accelerated_signal_runs')} "
          f"accelerated_simulation_runs={runtime.get('accelerated_simulation_runs')} "
          f"elapsed_ms={runtime.get('elapsed_ms')}")
    if runtime.get("fallback_reason"):
        print(f"  fallback_reason={runtime.get('fallback_reason')}")
    if runtime.get("simulation_fallback_reason"):
        print(f"  simulation_fallback_reason={runtime.get('simulation_fallback_reason')}")
    if args.parity:
        print("  " + _parity_check(args.symbol, args.timeframe))
    print()
    print("  No live trading. No order engine. Research probe only.")
    print("=" * 60)


if __name__ == "__main__":
    main()
