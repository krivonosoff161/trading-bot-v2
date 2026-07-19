from __future__ import annotations

"""Synthetic history-boundary proof for the public strategy registry.

This module does not read market history or private paper/runtime state. It builds
deterministic public-safe OHLCV fixtures and checks that every registered strategy
can be evaluated at its declared candle-history boundary without pretending that a
missing side-data family was satisfied by extra candles.
"""

from dataclasses import dataclass
from typing import Any

from src.research_lab.strategy_registry import REGISTRY, StrategyDef


FIXTURE_SCOPE = "public_synthetic_ohlcv"
CLAIM_BOUNDARY = (
    "formula_generator_history_alignment_only_no_private_history_or_profitability_claim"
)


@dataclass(frozen=True)
class ParameterBoundaryCheck:
    parameter: str
    default_value: int
    boundary_value: int
    required_history_bars: int
    status: str
    reason: str
    signal_count: int


@dataclass(frozen=True)
class StrategyHistoryProof:
    strategy_id: str
    required_history_bars: int
    before_boundary_rows: int
    boundary_rows: int
    before_boundary_status: str
    boundary_status: str
    boundary_reason: str
    required_data: tuple[str, ...]
    required_data_missing_status: str
    required_data_missing_reason: str
    parameter_boundary_checks: dict[str, ParameterBoundaryCheck]
    fixture_scope: str = FIXTURE_SCOPE
    claim_boundary: str = CLAIM_BOUNDARY


def synthetic_candles(
    rows: int,
    *,
    include_required_data: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Build deterministic synthetic candles, optionally with side-data columns."""
    out: list[dict[str, Any]] = []
    price = 100.0
    for index in range(max(0, int(rows))):
        drift = 1.0 + (0.001 if index % 7 else -0.0005)
        price *= drift
        candle: dict[str, Any] = {
            "ts": 1_700_000_000_000 + index * 3_600_000,
            "date": f"synthetic-{index:04d}",
            "open": round(price * 0.999, 8),
            "high": round(price * 1.004, 8),
            "low": round(price * 0.996, 8),
            "close": round(price, 8),
            "vol": float(1_000 + (index % 11) * 25),
        }
        if "funding" in include_required_data:
            candle["funding"] = 0.0001 if index % 2 == 0 else -0.0001
        if "oi" in include_required_data:
            candle["oi"] = float(10_000 + index * 10)
        if "microstructure" in include_required_data:
            candle.update(
                {
                    "obi_top5": 0.25 if index % 2 == 0 else -0.25,
                    "spread_bps": 2.0,
                    "trade_delta_100": 100.0 if index % 2 == 0 else -100.0,
                }
            )
        out.append(candle)
    return out


def _signals_status(
    definition: StrategyDef,
    rows: int,
    params: dict[str, Any],
    *,
    include_required_data: bool,
) -> tuple[str, str, int]:
    required_data = definition.required_data if include_required_data else ()
    candles = synthetic_candles(rows, include_required_data=required_data)
    signals = definition.generate_signals(candles, dict(params))
    invalid = [
        signal for signal in signals
        if int(signal.get("idx", -1)) < 0 or int(signal.get("idx", rows)) > max(0, rows - 1)
    ]
    if invalid:
        return (
            "invalid_signal_index",
            "generator_returned_signal_outside_fixture_rows",
            len(signals),
        )
    if signals:
        return "signals", "synthetic_fixture_satisfied_entry_predicate", len(signals)
    return (
        "no_signal_predicate",
        "synthetic_fixture_evaluated_but_entry_predicate_was_not_satisfied",
        0,
    )


def _before_boundary_status(
    definition: StrategyDef,
    rows: int,
    params: dict[str, Any],
) -> tuple[str, str, int]:
    if rows <= 0:
        return "no_usable_rows_before_boundary", "zero_rows_before_declared_boundary", 0
    status, reason, count = _signals_status(
        definition,
        rows,
        params,
        include_required_data=True,
    )
    if status == "signals":
        return "signal_before_boundary", reason, count
    return "no_signal_before_boundary", reason, count


def _term_boundary_value(default: Any) -> int:
    if isinstance(default, bool):
        return 1
    try:
        value = int(default)
    except (TypeError, ValueError):
        value = 1
    return max(value + 1, value * 2, 2)


def _parameter_boundary_checks(
    definition: StrategyDef,
    default_params: dict[str, Any],
    default_required: int,
) -> dict[str, ParameterBoundaryCheck]:
    terms = sorted({
        key for formula in definition.history_formulas for key, _multiplier in formula.terms
    })
    checks: dict[str, ParameterBoundaryCheck] = {}
    for parameter in terms:
        params = dict(default_params)
        default_value = int(params.get(parameter, 1) or 1)
        boundary_value = _term_boundary_value(default_value)
        params[parameter] = boundary_value
        required = definition.required_history_bars(params)
        status, reason, signal_count = _signals_status(
            definition,
            required,
            params,
            include_required_data=True,
        )
        checks[parameter] = ParameterBoundaryCheck(
            parameter=parameter,
            default_value=default_value,
            boundary_value=boundary_value,
            required_history_bars=required,
            status=status,
            reason=reason,
            signal_count=signal_count,
        )
        if required < default_required:
            checks[parameter] = ParameterBoundaryCheck(
                parameter=parameter,
                default_value=default_value,
                boundary_value=boundary_value,
                required_history_bars=required,
                status="history_decreased",
                reason="increasing_formula_parameter_decreased_required_history",
                signal_count=signal_count,
            )
    return checks


def _required_data_missing_status(
    definition: StrategyDef,
    rows: int,
    params: dict[str, Any],
) -> tuple[str, str]:
    if not definition.required_data:
        return "not_required", ""
    status, reason, signal_count = _signals_status(
        definition,
        rows,
        params,
        include_required_data=False,
    )
    if signal_count:
        return (
            "side_data_leak",
            f"generator_emitted_{signal_count}_signals_without_declared_side_data:{reason}",
        )
    return (
        "side_data_unavailable",
        "declared_side_data_absent_from_synthetic_fixture_and_not_replaced_by_extra_candles",
    )


def build_history_proofs() -> dict[str, StrategyHistoryProof]:
    """Return one deterministic proof record for every public registry strategy."""
    proofs: dict[str, StrategyHistoryProof] = {}
    for strategy_id, definition in REGISTRY.items():
        params = dict(definition.parameter_defaults)
        required = definition.required_history_bars(params)
        before_rows = max(0, required - 1)
        before_status, _before_reason, _before_count = _before_boundary_status(
            definition,
            before_rows,
            params,
        )
        boundary_status, boundary_reason, _boundary_count = _signals_status(
            definition,
            required,
            params,
            include_required_data=True,
        )
        missing_status, missing_reason = _required_data_missing_status(
            definition,
            required + 100,
            params,
        )
        proofs[strategy_id] = StrategyHistoryProof(
            strategy_id=strategy_id,
            required_history_bars=required,
            before_boundary_rows=before_rows,
            boundary_rows=required,
            before_boundary_status=before_status,
            boundary_status=boundary_status,
            boundary_reason=boundary_reason,
            required_data=definition.required_data,
            required_data_missing_status=missing_status,
            required_data_missing_reason=missing_reason,
            parameter_boundary_checks=_parameter_boundary_checks(definition, params, required),
        )
    return proofs
