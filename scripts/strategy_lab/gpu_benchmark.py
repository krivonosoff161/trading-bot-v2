# -*- coding: utf-8 -*-
"""Benchmark the GPU sweep backend vs CPU-vectorized — decide before adding kernels.

Times the two GPU-accelerated stages (momentum_breakout signal kernel + batched trade
simulation) on numpy (CPU) and the resolved GPU array module (cupy) across realistic farm
batch sizes, then prints a per-stage recommendation. The point of Phase 1.4 is to MEASURE
whether GPU actually pays off on this box (a 3GB GTX 1050 with tiny batches) before
investing in more kernels — no speculative GPU work.

    python -m scripts.strategy_lab.gpu_benchmark
    python -m scripts.strategy_lab.gpu_benchmark --json --repeats 7

Read-only research/diagnostic: synthetic candles only, no network, no orders, no .env.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.gpu_kernels import momentum_breakout_signals  # noqa: E402
from src.research_lab.gpu_runtime import array_module, detect_gpu  # noqa: E402
from src.research_lab.gpu_simulator import simulate_trades_batched, within_memory_cap  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402

SHAPES = (200, 500, 1000, 2000)
SIGNAL_KW = {"lookback": 20, "threshold_pct": 0.5}
SIM_PARAMS = {"hold_bars": 10, "stop_pct": 1.0, "take_pct": 2.0}


def _synth_candles(n: int, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    price, rows = 100.0, []
    for i in range(n):
        price *= 1 + rng.uniform(-0.012, 0.012)
        hi = price * (1 + abs(rng.uniform(0, 0.006)))
        lo = price * (1 - abs(rng.uniform(0, 0.006)))
        rows.append({"ts": i * 60000, "open": price, "high": hi, "low": lo,
                     "close": price, "vol": rng.uniform(1, 10)})
    return rows


def _best_ms(fn, *, repeats: int, sync=None) -> float:
    fn()  # warmup (GPU JIT-compiles on first use)
    if sync:
        sync()
    best = math.inf
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        if sync:
            sync()
        best = min(best, (time.perf_counter() - t0) * 1000.0)
    return round(best, 4)


def _bench_one(n: int, xp, sync, repeats: int) -> dict:
    candles = _synth_candles(n)
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    sig_ms = _best_ms(lambda: momentum_breakout_signals(highs, lows, closes, xp=xp, **SIGNAL_KW),
                      repeats=repeats, sync=sync)
    # Fixed CPU-built signals so the simulation timing is apples-to-apples.
    import numpy as _np
    signals = momentum_breakout_signals(highs, lows, closes, xp=_np, **SIGNAL_KW)
    sim_ms = math.nan
    if signals and within_memory_cap(len(signals), SIM_PARAMS["hold_bars"]):
        sim_ms = _best_ms(lambda: simulate_trades_batched(candles, signals, SIM_PARAMS,
                                                          fees_bps=7.0, slippage_bps=3.0, xp=xp),
                          repeats=repeats, sync=sync)
    return {"n_bars": n, "n_signals": len(signals), "signal_ms": sig_ms, "sim_ms": sim_ms}


def _recommend(stage: str, rows: list[dict]) -> dict:
    key = "signal_ms" if stage == "signal" else "sim_ms"
    pairs = [(r["n_bars"], r["cpu"][key], r["gpu"][key]) for r in rows
             if not math.isnan(r["cpu"][key]) and not math.isnan(r["gpu"][key])]
    if not pairs:
        return {"stage": stage, "recommend": "cpu", "reason": "no comparable samples"}
    gpu_wins = [n for n, c, g in pairs if g < c]
    largest_n, c_last, g_last = max(pairs, key=lambda p: p[0])
    if not gpu_wins:
        rec, reason = "cpu", "GPU never beat CPU-vectorized at these batch sizes"
    elif g_last < c_last and len(gpu_wins) == len(pairs):
        rec, reason = "gpu", "GPU faster across all measured shapes"
    elif g_last < c_last:
        rec, reason = "gpu_large_only", f"GPU pays off only for larger shapes {sorted(gpu_wins)}"
    else:
        rec, reason = "cpu", "GPU only marginally faster on small shapes; not worth it at scale"
    return {"stage": stage, "recommend": rec, "reason": reason,
            "largest_n": largest_n, "cpu_ms": c_last, "gpu_ms": g_last,
            "speedup_at_largest": round(c_last / g_last, 3) if g_last else None}


def run_benchmark(repeats: int = 5) -> dict:
    cap = detect_gpu()
    import numpy as _np
    out: dict = {"gpu_available": cap.gpu_available, "backend": cap.backend_name,
                 "repeats": repeats, "shapes": list(SHAPES), "rows": [], "recommendations": []}
    if not cap.gpu_available:
        out["note"] = f"GPU unavailable ({cap.reason_if_unavailable}); CPU is the only backend."
        return out
    gpu_xp = array_module(cap.backend_name)
    sync = getattr(getattr(gpu_xp, "cuda", None), "Stream", None)
    gpu_sync = (lambda: gpu_xp.cuda.Stream.null.synchronize()) if sync else None
    for n in SHAPES:
        out["rows"].append({"n_bars": n,
                            "cpu": _bench_one(n, _np, None, repeats),
                            "gpu": _bench_one(n, gpu_xp, gpu_sync, repeats)})
    out["recommendations"] = [_recommend("signal", out["rows"]), _recommend("simulation", out["rows"])]
    return out


def _print(report: dict) -> None:
    print(f"GPU benchmark - backend={report.get('backend')} available={report.get('gpu_available')} "
          f"repeats={report.get('repeats')}")
    if not report.get("gpu_available"):
        print("  " + report.get("note", "GPU unavailable"))
        return
    print(f"  {'n_bars':>7} {'sig':>4} {'cpu_sig':>9} {'gpu_sig':>9} {'cpu_sim':>9} {'gpu_sim':>9}")
    for r in report["rows"]:
        c, g = r["cpu"], r["gpu"]
        print(f"  {r['n_bars']:>7} {c['n_signals']:>4} {c['signal_ms']:>9} {g['signal_ms']:>9} "
              f"{c['sim_ms']:>9} {g['sim_ms']:>9}")
    for rec in report["recommendations"]:
        print(f"  -> {rec['stage']}: {rec['recommend']} ({rec['reason']})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark GPU vs CPU-vectorized sweep stages.")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=str(Path(DEFAULT_PRIVATE_ROOT) / "state" / "gpu_benchmark.json"))
    args = ap.parse_args()
    report = run_benchmark(repeats=args.repeats)
    try:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["written_to"] = str(out_path)
    except OSError:
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else "")
    if not args.json:
        _print(report)


if __name__ == "__main__":
    main()
