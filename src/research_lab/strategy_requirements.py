# -*- coding: utf-8 -*-
"""Explicit data contract for a strategy/setup run.

Strategies imply how much history they need (their longest lookback parameter plus
warm-up) but never declared it; a job could silently run on too few bars. This
module makes the requirement explicit and minimal: enough rows on the primary
timeframe, plus an optional 1m-microscope flag. It does not fetch or read candles —
`data_readiness` checks it against the inventory.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.research_lab.data_inventory import MIN_USABLE_ROWS
from src.research_lab.strategy_registry import REGISTRY

DEFAULT_LOOKBACK = 20
MIN_TRADE_BUFFER = 30  # extra bars so a job can produce at least a few trades

_ALWAYS_VOLUME = {
    "volume_exhaustion_fade",
    "volume_shock_continuation",
    "main_fast_swing_regime",
    "range_volume_breakout",
    "volatility_squeeze_breakout_v2",
    "vwap_reclaim_reject",
    "exhaustion_fade",
    "bb_volume_fade",
    "pump_dump_scalp",
}


@dataclass(frozen=True)
class StrategyDataRequirement:
    strategy_id: str
    symbol: str
    timeframe: str
    lookback_bars: int
    warmup_bars: int
    min_rows: int
    needs_volume: bool = False
    needs_1m_microscope: bool = False
    required_data: tuple[str, ...] = ()
    history_formulas: tuple[str, ...] = ()
    reason: str = ""
    status: str = "candidate"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _max_lookback(strategy_id: str, params: dict[str, Any] | None) -> int:
    definition = REGISTRY.get(strategy_id)
    defaults = dict(definition.parameter_defaults) if definition else {}
    merged = {**defaults, **(params or {})}
    keys = {
        key
        for formula in definition.history_formulas
        for key, _multiplier in formula.terms
    } if definition else set()
    values = [
        int(merged[key]) for key in keys
        if isinstance(merged.get(key), (int, float)) and not isinstance(merged.get(key), bool)
    ]
    return max(values) if values else DEFAULT_LOOKBACK


def derive_requirement(
    strategy_id: str,
    symbol: str,
    timeframe: str,
    *,
    params: dict[str, Any] | None = None,
    needs_1m_microscope: bool = False,
) -> StrategyDataRequirement:
    """Derive the minimal primary-timeframe data requirement for one (strategy, symbol)."""
    definition = REGISTRY.get(strategy_id)
    lookback = _max_lookback(strategy_id, params)
    warmup = definition.required_history_bars(params) if definition else lookback
    merged = {**(definition.parameter_defaults if definition else {}), **(params or {})}
    outcome_buffer = max(MIN_TRADE_BUFFER, int(merged.get("hold_bars", 0) or 0) + 1)
    # Preserve the established conservative sampling margin while replacing the
    # guessed warm-up with the generator's exact manifest.
    min_rows = max(MIN_USABLE_ROWS, lookback + warmup + outcome_buffer)
    required_data = tuple(definition.required_data) if definition else ()
    formulas = definition.history_formula_labels() if definition else ()
    needs_volume = strategy_id in _ALWAYS_VOLUME or (
        strategy_id == "sfp_liquidity_sweep" and float(merged.get("vol_mult", 0) or 0) > 0
    )
    return StrategyDataRequirement(
        strategy_id=strategy_id,
        symbol=str(symbol).upper(),
        timeframe=str(timeframe),
        lookback_bars=lookback,
        warmup_bars=warmup,
        min_rows=min_rows,
        needs_volume=needs_volume,
        needs_1m_microscope=bool(needs_1m_microscope),
        required_data=required_data,
        history_formulas=formulas,
        reason=f"lookback {lookback} + exact warmup {warmup} + outcome buffer {outcome_buffer}",
    )
