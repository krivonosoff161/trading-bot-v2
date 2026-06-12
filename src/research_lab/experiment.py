# -*- coding: utf-8 -*-
"""Small deterministic strategy-discovery runner.

The public code defines how experiments are evaluated. The private research
repository stores the actual result tables, candidate scorecards, and graph
exports. Output writers live in src/research_lab/outputs.py.
"""

from __future__ import annotations

import glob
import hashlib
import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.regime import regime_at, regime_breakdown, regime_matches
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.validator import validate_candidate

COST_STRESS_MULT = 1.5  # validator stress: costs scaled by this factor


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


def load_candles(path: Path) -> list[dict[str, float | int | str]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, float | int | str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            out.append(
                {
                    "ts": int(row["ts"]),
                    "date": str(row.get("date") or ""),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "vol": float(row.get("vol") or 0.0),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda r: int(r["ts"]))


def choose_symbol_file(pattern: str, symbol: str) -> Path | None:
    normalized = symbol.replace("-", "_").replace("/", "_")
    candidates = [Path(p) for p in glob.glob(pattern.format(symbol=normalized))]
    if not candidates:
        candidates = [Path(p) for p in glob.glob(pattern.format(symbol=symbol))]
    if not candidates:
        return None
    # Prefer the file with the largest number of candles, then deterministic path.
    return sorted(candidates, key=lambda p: (_safe_row_count(p), str(p)), reverse=True)[0]


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
        direction = 1 if side == "long" else -1
        ret_pct = direction * (exit_price / entry - 1) * 100
        net_pct = ret_pct - cost_pct * 100
        trades.append(
            {
                "entry_ts": candles[idx]["ts"],
                "exit_ts": candles[actual_exit_idx]["ts"],
                "side": side,
                "entry": round(entry, 10),
                "exit": round(exit_price, 10),
                "net_pct": round(net_pct, 4),
                "outcome": outcome,
                "reason": sig.get("reason"),
                "regime": sig.get("regime") or {},
            }
        )
    return trades


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


def evaluate_spec(spec: ExperimentSpec) -> list[RunResult]:
    stress_extra = (spec.fees_bps + spec.slippage_bps) / 10000.0 * 100 * (COST_STRESS_MULT - 1)
    results = []
    for symbol, family in itertools.product(spec.symbols, spec.families):
        path = choose_symbol_file(spec.data_glob, symbol)
        if not path:
            continue
        candles = load_candles(path)
        for params in spec.parameter_grid.get(family, []):
            signals = generate_signals(candles, family, params)
            signals = annotate_signals_with_regime(candles, signals, spec.regime_params)
            signals = filter_signals(signals, spec.filters)
            trades = simulate_trades(
                candles,
                signals,
                params,
                fees_bps=spec.fees_bps,
                slippage_bps=spec.slippage_bps,
            )
            metrics = compute_metrics(trades, spec.split_ratio, spec.min_trades, stress_extra)
            metrics["data_file_label"] = path.name
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
                return results
    return results


def _regime_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    breakdown = metrics.get("regime_breakdown") or {}
    if not breakdown:
        return {}
    dominant = max(breakdown.items(), key=lambda kv: kv[1].get("n_trades", 0))
    return {"dominant_bucket": dominant[0], "bucket_count": len(breakdown)}
