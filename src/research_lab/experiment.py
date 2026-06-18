# -*- coding: utf-8 -*-
"""Small deterministic strategy-discovery runner.

The public code defines how experiments are evaluated. The private research
repository stores the actual result tables, candidate scorecards, and graph
exports. Output writers live in src/research_lab/outputs.py.
"""

from __future__ import annotations

import bisect
import glob
import hashlib
import itertools
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.entry_timing import event_anchored_timing
from src.research_lab.regime import regime_at, regime_breakdown, regime_matches
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.validator import validate_candidate
from src.research_lab import gpu_kernels
from src.research_lab.gpu_runtime import (
    GPU,
    GPU_SUPPORTED_FAMILIES,
    array_module,
    resolve_backend,
)

# Descriptor of the exit mode the GPU/batched simulator faithfully reproduces.
GPU_SUPPORTED_SIMULATION_MODES = ("fixed_sl_tp_hold_long_short",)

COST_STRESS_MULT = 1.5  # validator stress: costs scaled by this factor

# A family's required_data token -> (candle field that proves it's present, NEEDS status).
# When a required field is absent on every bar, the family is data-starved: it is
# classified NEEDS_<...>_DATA instead of being presented as an active (and rejected) edge.
_REQUIRED_DATA_FIELD = {
    "oi": ("oi", "NEEDS_OI_DATA"),
    "funding": ("funding", "NEEDS_FLOW_DATA"),
    "microstructure": ("obi_top5", "NEEDS_MICRO_DATA"),
}


def missing_required_data(family: str, candles: list[dict[str, Any]]) -> str | None:
    """NEEDS_<...>_DATA status if the family needs a candle field that is absent, else None."""
    try:
        required = get_strategy(family).required_data
    except ValueError:
        return None
    for token in required:
        field_name, status = _REQUIRED_DATA_FIELD.get(token, (None, None))
        if field_name and not any(c.get(field_name) is not None for c in candles):
            return status
    return None


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    data_glob: str
    symbols: list[str]
    families: list[str]
    parameter_grid: dict[str, list[dict[str, Any]]]
    fees_bps: float = 7.0
    slippage_bps: float = 3.0
    min_trades: int = 20
    split_ratio: float = 0.7
    max_runs: int = 0
    filters: dict[str, list[str]] = field(default_factory=dict)
    regime_params: dict[str, Any] = field(default_factory=dict)
    event_context: dict[str, Any] = field(default_factory=dict)
    plan_meta: dict[str, Any] = field(default_factory=dict)
    timeframe: str = "1d"
    backend: str = "cpu"

    @classmethod
    def from_json(cls, path: Path) -> "ExperimentSpec":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            experiment_id=str(data["experiment_id"]),
            data_glob=str(data["data_glob"]),
            symbols=[str(s) for s in data.get("symbols", [])],
            families=[str(f) for f in data.get("families", [])],
            parameter_grid={str(k): list(v) for k, v in data.get("parameter_grid", {}).items()},
            fees_bps=float(data.get("fees_bps", 7.0)),
            slippage_bps=float(data.get("slippage_bps", 3.0)),
            min_trades=int(data.get("min_trades", 20)),
            split_ratio=float(data.get("split_ratio", 0.7)),
            max_runs=int(data.get("max_runs", 0)),
            filters={str(k): [str(x) for x in v] for k, v in (data.get("filters") or {}).items()},
            regime_params=dict(data.get("regime_params") or {}),
            event_context=dict(data.get("event_context") or {}),
            plan_meta=dict(data.get("plan_meta") or {}),
            timeframe=str(data.get("timeframe") or "1d"),
            backend=str(data.get("backend") or "cpu"),
        )


@dataclass(frozen=True)
class RunResult:
    run_id: str
    symbol: str
    family: str
    params: dict[str, Any]
    metrics: dict[str, Any]
    decision: str
    reasons: list[str]
    validation_status: str = ""
    validation_reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    next_action: str = ""
    regime_summary: dict[str, Any] = field(default_factory=dict)


# Optional flow/microstructure fields carried through to the feature layer when the
# prepared file was enriched (e.g. by enrich_flow_data / the loop's funding enricher).
_OPTIONAL_CANDLE_FIELDS = ("funding", "oi", "index_px", "obi_top5", "spread_bps", "trade_delta_100")


def load_candles(path: Path) -> list[dict[str, float | int | str]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, float | int | str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            candle: dict[str, float | int | str] = {
                "ts": int(row["ts"]),
                "date": str(row.get("date") or ""),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "vol": float(row.get("vol") or 0.0),
            }
        except (KeyError, TypeError, ValueError):
            continue
        for flow_field in _OPTIONAL_CANDLE_FIELDS:
            value = row.get(flow_field)
            if value is not None:
                try:
                    candle[flow_field] = float(value)
                except (TypeError, ValueError):
                    pass
        out.append(candle)
    return sorted(out, key=lambda r: int(r["ts"]))


def _derive_timeframe(candles: list[dict[str, Any]]) -> str:
    """Best-effort timeframe label from candle spacing (e.g. '1d', '15m').

    Returns '' when it cannot be inferred. Normalized to lowercase so it matches
    the timeframe-profile / registry convention (1d, 1h, 4h, 15m, 1m).
    """
    from src.research_lab.data_inventory import infer_timeframe

    ts_values = [int(c["ts"]) for c in candles if "ts" in c]
    label = infer_timeframe(ts_values)
    return "" if label == "unknown" else label.lower()


def choose_symbol_file(
    pattern: str, symbol: str, *, timeframe: str | None = None,
) -> Path | None:
    normalized = symbol.replace("-", "_").replace("/", "_")
    candidates = [Path(p) for p in glob.glob(pattern.format(symbol=normalized))]
    if not candidates:
        candidates = [Path(p) for p in glob.glob(pattern.format(symbol=symbol))]
    if not candidates:
        return None
    if timeframe is None:
        return sorted(candidates, key=lambda p: (_safe_row_count(p), str(p)), reverse=True)[0]
    from src.research_lab.data_inventory import inspect_file
    wanted = str(timeframe).strip().lower()
    matching: list[tuple[int, Path]] = []
    for p in candidates:
        info = inspect_file(p)
        if info.get("timeframe", "").lower() == wanted and info.get("quality_status") == "usable":
            matching.append((int(info.get("rows") or 0), p))
    if not matching:
        return None
    matching.sort(key=lambda rp: (-rp[0], str(rp[1])))
    return matching[0][1]


def _safe_row_count(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return len(data) if isinstance(data, list) else 0


def generate_signals(
    candles: list[dict[str, Any]],
    family: str,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Delegate to the strategy registry; `family` is the strategy_id."""
    return get_strategy(family).generate_signals(candles, params)


def annotate_signals_with_regime(
    candles: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    regime_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    for sig in signals:
        sig["regime"] = regime_at(candles, int(sig["idx"]), regime_params)
    return signals


def filter_signals(signals: list[dict[str, Any]], filters: dict[str, list[str]]) -> list[dict[str, Any]]:
    if not filters:
        return signals
    return [s for s in signals if regime_matches(s.get("regime") or {}, filters)]


def simulate_trades(
    candles: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    params: dict[str, Any],
    *,
    fees_bps: float,
    slippage_bps: float,
) -> list[dict[str, Any]]:
    hold_bars = int(params.get("hold_bars", 5))
    stop_pct = float(params.get("stop_pct", 0.0))
    take_pct = float(params.get("take_pct", 0.0))
    cost_pct = (fees_bps + slippage_bps) / 10000.0
    trades = []
    for sig in signals:
        idx = int(sig["idx"])
        exit_idx = min(idx + hold_bars, len(candles) - 1)
        if idx >= len(candles) or exit_idx <= idx:
            continue
        side = str(sig["side"])
        entry = float(candles[idx]["open"])
        if entry <= 0:
            continue
        exit_price = float(candles[exit_idx]["close"])
        outcome = "time_exit"
        actual_exit_idx = exit_idx
        for j in range(idx, exit_idx + 1):
            high = float(candles[j]["high"])
            low = float(candles[j]["low"])
            if side == "long":
                stop = entry * (1 - stop_pct / 100) if stop_pct > 0 else None
                take = entry * (1 + take_pct / 100) if take_pct > 0 else None
                if stop and low <= stop:
                    exit_price, outcome, actual_exit_idx = stop, "stop", j
                    break
                if take and high >= take:
                    exit_price, outcome, actual_exit_idx = take, "take", j
                    break
            else:
                stop = entry * (1 + stop_pct / 100) if stop_pct > 0 else None
                take = entry * (1 - take_pct / 100) if take_pct > 0 else None
                if stop and high >= stop:
                    exit_price, outcome, actual_exit_idx = stop, "stop", j
                    break
                if take and low <= take:
                    exit_price, outcome, actual_exit_idx = take, "take", j
                    break
        trades.append(
            finalize_trade(candles, idx, actual_exit_idx, side, entry, exit_price, outcome, sig, cost_pct)
        )
    return trades


def finalize_trade(
    candles: list[dict[str, Any]],
    idx: int,
    actual_exit_idx: int,
    side: str,
    entry: float,
    exit_price: float,
    outcome: str,
    sig: dict[str, Any],
    cost_pct: float,
) -> dict[str, Any]:
    """Build one trade dict from a decided exit (entry, exit_price, outcome, bars).

    Single source of truth for the trade arithmetic + rounding, shared by the CPU
    simulator and the GPU/batched simulator so their outputs are identical by
    construction (the GPU path only decides exit_price/outcome/actual_exit_idx).
    """
    direction = 1 if side == "long" else -1
    ret_pct = direction * (exit_price / entry - 1) * 100
    net_pct = ret_pct - cost_pct * 100
    mfe_pct, mae_pct = _trade_excursions(candles, idx, actual_exit_idx, entry, side)
    capture = round(ret_pct / mfe_pct, 4) if mfe_pct > 0 else 0.0
    return {
        "entry_ts": candles[idx]["ts"],
        "exit_ts": candles[actual_exit_idx]["ts"],
        "side": side,
        "entry": round(entry, 10),
        "exit": round(exit_price, 10),
        "net_pct": round(net_pct, 4),
        "outcome": outcome,
        "reason": sig.get("reason"),
        "regime": sig.get("regime") or {},
        "mfe_pct": round(mfe_pct, 4),
        "mae_pct": round(mae_pct, 4),
        "capture_of_mfe": capture,
    }


def _trade_excursions(
    candles: list[dict[str, Any]],
    entry_idx: int,
    exit_idx: int,
    entry: float,
    side: str,
) -> tuple[float, float]:
    """Max favorable / adverse excursion (pct of entry) over the realized hold window."""
    hi = max(float(candles[j]["high"]) for j in range(entry_idx, exit_idx + 1))
    lo = min(float(candles[j]["low"]) for j in range(entry_idx, exit_idx + 1))
    if side == "long":
        favorable, adverse = (hi - entry) / entry * 100, (entry - lo) / entry * 100
    else:
        favorable, adverse = (entry - lo) / entry * 100, (hi - entry) / entry * 100
    return max(0.0, favorable), max(0.0, adverse)


def _entry_timing_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-trade timing: how much favorable move we captured vs heat taken.

    Heuristic trade-quality proxy for the "direction right, entry late" pain.
    The event-precise version (src/research_lab/entry_timing.py) is used in the
    event-driven sweep path where a real pre-move window exists.
    """
    if not trades:
        return {}
    mfes = [float(t.get("mfe_pct") or 0.0) for t in trades]
    maes = [float(t.get("mae_pct") or 0.0) for t in trades]
    captures = [float(t.get("capture_of_mfe") or 0.0) for t in trades]
    late = sum(1 for t in trades if float(t.get("mae_pct") or 0.0) > float(t.get("mfe_pct") or 0.0))
    n = len(trades)
    return {
        "avg_mfe_pct": round(sum(mfes) / n, 4),
        "avg_mae_pct": round(sum(maes) / n, 4),
        "avg_capture_ratio": round(sum(captures) / n, 4),
        "late_entry_rate": round(late / n, 4),
    }


def event_entry_timing_for_run(
    candles: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    event_context: dict[str, Any],
) -> dict[str, Any]:
    """Event-anchored timing for one run, when the spec carries an event context.

    Maps the historical event's ts window onto this symbol's bars and measures the
    first trade that entered at/after the move start. The simulator still made the
    entry decision with no look-ahead; this only adds an event-anchored measurement.
    Returns {} when there is no usable context or no qualifying trade (callers then
    keep the existing proxy metrics unchanged).
    """
    if not event_context or not trades:
        return {}
    try:
        start_ts = int(event_context["move_start_ts"])
        end_ts = int(event_context["move_end_ts"])
    except (KeyError, TypeError, ValueError):
        return {}
    direction = str(event_context.get("direction") or "up")
    ts = [int(c["ts"]) for c in candles]
    n = len(ts)
    if n == 0:
        return {}
    move_start_idx = bisect.bisect_left(ts, start_ts)
    if move_start_idx >= n:
        return {}
    move_end_idx = min(max(bisect.bisect_left(ts, end_ts), move_start_idx), n - 1)
    entry_idx = None
    for trade in trades:
        try:
            entry_ts = int(trade["entry_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if entry_ts >= start_ts:
            j = bisect.bisect_left(ts, entry_ts)
            if 0 <= j < n:
                entry_idx = j
                break
    if entry_idx is None:
        return {}
    eval_end = min(n - 1, max(entry_idx, move_end_idx))
    try:
        return event_anchored_timing(
            candles,
            move_start_idx=move_start_idx,
            move_end_idx=move_end_idx,
            entry_idx=entry_idx,
            direction=direction,
            eval_end_idx=eval_end,
        )
    except ValueError:
        return {}


def compute_metrics(
    trades: list[dict[str, Any]],
    split_ratio: float,
    min_trades: int,
    stress_extra_cost_pct: float = 0.0,
) -> dict[str, Any]:
    n = len(trades)
    returns = [float(t["net_pct"]) for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    split = max(1, min(n, int(n * split_ratio))) if n else 0
    train = returns[:split]
    test = returns[split:]
    total = sum(returns)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    best_share = max((abs(r) for r in returns), default=0.0) / abs(total) if total else 0.0
    avg = total / n if n else 0.0
    return {
        "n_trades": n,
        "win_rate": round(len(wins) / n, 4) if n else 0.0,
        "avg_net_pct": round(avg, 4),
        "total_net_pct": round(total, 4),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else (99.0 if wins else 0.0),
        "max_drawdown_pct": round(_max_drawdown(returns), 4),
        "best_trade_share": round(best_share, 4),
        "train_avg_net_pct": round(sum(train) / len(train), 4) if train else 0.0,
        "test_avg_net_pct": round(sum(test) / len(test), 4) if test else 0.0,
        "test_trades": len(test),
        "min_trades": min_trades,
        "stress_avg_net_pct": round(avg - stress_extra_cost_pct, 4),
        "regime_breakdown": regime_breakdown(trades),
        "entry_timing": _entry_timing_metrics(trades),
    }


def _max_drawdown(returns: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for ret in returns:
        equity += ret
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return dd


def grade_candidate(metrics: dict[str, Any]) -> tuple[str, list[str]]:
    reasons = []
    if metrics["n_trades"] < metrics["min_trades"]:
        reasons.append("too_few_trades")
    if metrics["profit_factor"] < 1.15:
        reasons.append("weak_profit_factor")
    if metrics["avg_net_pct"] <= 0:
        reasons.append("negative_or_flat_average")
    if metrics["test_trades"] < 5:
        reasons.append("weak_oos_sample")
    if metrics["test_avg_net_pct"] <= 0:
        reasons.append("oos_not_positive")
    if metrics["best_trade_share"] > 0.45:
        reasons.append("single_trade_dominance")
    if metrics["max_drawdown_pct"] > abs(metrics["total_net_pct"]) * 0.8 and metrics["total_net_pct"] > 0:
        reasons.append("drawdown_too_large_vs_total")

    if not reasons:
        return "PROMOTE_FOR_PRESSURE_TEST", ["passed_basic_gates"]
    if metrics["n_trades"] >= metrics["min_trades"] and metrics["avg_net_pct"] > 0 and metrics["test_avg_net_pct"] > 0:
        return "OBSERVE", reasons
    return "REJECT", reasons


def stable_run_id(symbol: str, family: str, params: dict[str, Any]) -> str:
    raw = json.dumps({"symbol": symbol, "family": family, "params": params}, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def evaluate_spec(spec: ExperimentSpec, runtime_meta: dict[str, Any] | None = None) -> list[RunResult]:
    """Run a spec. ``backend`` decides the signal-generation path:

    cpu  -> the scalar reference path (unchanged).
    gpu  -> the vectorized array kernel on a GPU backend for supported families
            (others fall back to the scalar path, recorded honestly). Raises if
            ``gpu`` was requested but no GPU backend is available (never silent CPU).
    auto -> gpu when available, else cpu with a recorded fallback reason.

    ``runtime_meta`` (optional) is filled with the resolved backend, gpu
    availability, fallback reason, elapsed_ms, and accelerated-run count.
    """
    resolution = resolve_backend(spec.backend)
    if runtime_meta is not None:
        runtime_meta.update(resolution.to_dict())
        runtime_meta["gpu_supported_families"] = list(GPU_SUPPORTED_FAMILIES)
    if not resolution.ok():
        # gpu explicitly requested but unavailable: explicit error, never CPU-in-disguise.
        raise RuntimeError(resolution.error)
    use_gpu = resolution.effective_backend == GPU
    xp = array_module(resolution.backend_name) if use_gpu else None
    from src.research_lab import gpu_simulator  # local import avoids an import cycle

    started = time.perf_counter()
    accelerated_signal_runs = 0
    accelerated_simulation_runs = 0
    cpu_fallback_families: set[str] = set()
    sim_fallback_reasons: set[str] = set()

    stress_extra = (spec.fees_bps + spec.slippage_bps) / 10000.0 * 100 * (COST_STRESS_MULT - 1)
    results = []
    tf = spec.timeframe if spec.timeframe else None
    for symbol, family in itertools.product(spec.symbols, spec.families):
        path = choose_symbol_file(spec.data_glob, symbol, timeframe=tf)
        if not path:
            continue
        candles = load_candles(path)
        # Record the resolved timeframe so downstream provenance (candidate
        # registry -> hard validation -> setup cards) never loses it. When the
        # spec carries no explicit timeframe, derive it from the candle spacing
        # instead of leaving it blank (the old behavior that produced "unknown").
        file_tf = tf or _derive_timeframe(candles)
        needs = missing_required_data(family, candles)
        if needs is not None:
            zero = compute_metrics([], spec.split_ratio, spec.min_trades, stress_extra)
            zero["data_file_label"] = path.name
            zero["data_file_timeframe"] = file_tf or ""
            results.append(RunResult(
                run_id=stable_run_id(symbol, family, {}), symbol=symbol, family=family,
                params={}, metrics=zero, decision=needs, reasons=["missing_required_data"],
                validation_status=needs, validation_reasons=[], risk_flags=["needs_data"],
                next_action="provide the required OI/funding/microstructure data, then re-run",
                regime_summary={}))
            continue
        gpu_family = use_gpu and gpu_kernels.supported_family(family)
        if use_gpu and not gpu_family:
            cpu_fallback_families.add(family)
        for params in spec.parameter_grid.get(family, []):
            if gpu_family:
                signals = gpu_kernels.generate_signals_vectorized(candles, family, params, xp=xp)
                accelerated_signal_runs += 1
            else:
                signals = generate_signals(candles, family, params)
            signals = annotate_signals_with_regime(candles, signals, spec.regime_params)
            signals = filter_signals(signals, spec.filters)
            # Simulation backend is decided per variant: GPU only for the supported
            # exit mode and a batch that fits the VRAM cap; otherwise CPU with a
            # recorded reason (never a silent wrong-GPU result).
            use_gpu_sim = use_gpu
            if use_gpu:
                ok_mode, mode_reason = gpu_simulator.supported_simulation_mode(params)
                if not ok_mode:
                    use_gpu_sim, reason = False, mode_reason
                elif not gpu_simulator.within_memory_cap(len(signals), int(params.get("hold_bars", 5))):
                    use_gpu_sim, reason = False, "gpu_batch_exceeds_vram_cap"
                if not use_gpu_sim:
                    sim_fallback_reasons.add(reason)
            if use_gpu_sim:
                trades = gpu_simulator.simulate_trades_batched(
                    candles, signals, params,
                    fees_bps=spec.fees_bps, slippage_bps=spec.slippage_bps, xp=xp,
                )
                accelerated_simulation_runs += 1
            else:
                trades = simulate_trades(
                    candles, signals, params,
                    fees_bps=spec.fees_bps, slippage_bps=spec.slippage_bps,
                )
            metrics = compute_metrics(trades, spec.split_ratio, spec.min_trades, stress_extra)
            metrics["data_file_label"] = path.name
            metrics["data_file_timeframe"] = file_tf or ""
            if spec.event_context:
                event_timing = event_entry_timing_for_run(candles, trades, spec.event_context)
                if event_timing:
                    metrics["event_entry_timing"] = event_timing
            decision, reasons = grade_candidate(metrics)
            validation = validate_candidate(metrics, decision)
            results.append(
                RunResult(
                    run_id=stable_run_id(symbol, family, params),
                    symbol=symbol,
                    family=family,
                    params=params,
                    metrics=metrics,
                    decision=decision,
                    reasons=reasons,
                    validation_status=validation.status,
                    validation_reasons=validation.reasons,
                    risk_flags=validation.risk_flags,
                    next_action=validation.next_action,
                    regime_summary=_regime_summary(metrics),
                )
            )
            if spec.max_runs and len(results) >= spec.max_runs:
                _finalize_runtime_meta(runtime_meta, started, accelerated_signal_runs,
                                       accelerated_simulation_runs, cpu_fallback_families,
                                       sim_fallback_reasons)
                return results
    _finalize_runtime_meta(runtime_meta, started, accelerated_signal_runs,
                           accelerated_simulation_runs, cpu_fallback_families,
                           sim_fallback_reasons)
    return results


def _finalize_runtime_meta(
    runtime_meta: dict[str, Any] | None,
    started: float,
    accelerated_signal_runs: int,
    accelerated_simulation_runs: int,
    cpu_fallback_families: set[str],
    sim_fallback_reasons: set[str],
) -> None:
    if runtime_meta is None:
        return
    runtime_meta["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    # accelerated_runs kept as the signal-run count for backward compatibility.
    runtime_meta["accelerated_runs"] = accelerated_signal_runs
    runtime_meta["accelerated_signal_runs"] = accelerated_signal_runs
    runtime_meta["accelerated_simulation_runs"] = accelerated_simulation_runs
    runtime_meta["signal_backend"] = GPU if accelerated_signal_runs > 0 else "cpu"
    runtime_meta["simulation_backend"] = GPU if accelerated_simulation_runs > 0 else "cpu"
    runtime_meta["simulation_fallback_reason"] = "; ".join(sorted(sim_fallback_reasons))
    runtime_meta["gpu_supported_simulation_modes"] = list(GPU_SUPPORTED_SIMULATION_MODES)
    runtime_meta["cpu_fallback_families"] = sorted(cpu_fallback_families)


def _regime_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    breakdown = metrics.get("regime_breakdown") or {}
    if not breakdown:
        return {}
    dominant = max(breakdown.items(), key=lambda kv: kv[1].get("n_trades", 0))
    return {"dominant_bucket": dominant[0], "bucket_count": len(breakdown)}
