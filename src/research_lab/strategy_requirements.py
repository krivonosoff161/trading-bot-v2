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

from src.research_lab.strategy_registry import REGISTRY

# Parameter names that imply how much history a strategy needs (a subset of the
# generator's integer keys: only the ones that set a window, not hold/exit bars).
_LOOKBACK_KEYS = ("lookback", "trend_ma", "ma", "period", "pullback_ma")
DEFAULT_LOOKBACK = 20
MIN_TRADE_BUFFER = 30  # extra bars so a job can produce at least a few trades


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
    reason: str = ""
    status: str = "candidate"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _max_lookback(strategy_id: str, params: dict[str, Any] | None) -> int:
    defaults = dict(REGISTRY[strategy_id].parameter_defaults) if strategy_id in REGISTRY else {}
    merged = {**defaults, **(params or {})}
    values = [
        int(merged[key]) for key in _LOOKBACK_KEYS
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
    lookback = _max_lookback(strategy_id, params)
    # A few strategies (squeeze/volatility) need ~2x lookback of context; warm-up =
    # lookback covers that conservatively for all of them.
    warmup = lookback
    min_rows = lookback + warmup + MIN_TRADE_BUFFER
    family = REGISTRY[strategy_id].family if strategy_id in REGISTRY else ""
    return StrategyDataRequirement(
        strategy_id=strategy_id,
        symbol=str(symbol).upper(),
        timeframe=str(timeframe),
        lookback_bars=lookback,
        warmup_bars=warmup,
        min_rows=min_rows,
        needs_volume=(family == "volume"),
        needs_1m_microscope=bool(needs_1m_microscope),
        reason=f"lookback {lookback} + warmup {warmup} + buffer {MIN_TRADE_BUFFER}",
    )
