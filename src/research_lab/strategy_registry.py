# -*- coding: utf-8 -*-
"""Strategy registry: metadata + signal generator for every lab strategy.

The registry is the single public catalog of what the lab can test. It claims
nothing about profitability; every entry is a hypothesis generator only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.research_lab import strategies as s

SignalFn = Callable[[list[dict[str, Any]], dict[str, Any]], list[dict[str, Any]]]

_CRYPTO = ("crypto_perp",)
_ALL_TF = ("15m", "1H", "4H", "1D")
_DAILY_UP = ("4H", "1D")


@dataclass(frozen=True)
class StrategyDef:
    strategy_id: str
    display_name: str
    family: str
    description: str
    generate_signals: SignalFn
    compatible_asset_classes: tuple[str, ...] = _CRYPTO
    compatible_timeframes: tuple[str, ...] = _ALL_TF
    parameter_defaults: dict[str, Any] = field(default_factory=dict)
    risk_notes: str = ""


_DEFS: list[StrategyDef] = [
    StrategyDef(
        "momentum_breakout", "Momentum Breakout", "breakout",
        "Close beyond the prior N-bar high/low plus an optional threshold.",
        s.signals_momentum_breakout,
        parameter_defaults={"lookback": 20, "threshold_pct": 0.0, "hold_bars": 5, "stop_pct": 10, "take_pct": 20},
        risk_notes="Late entries in fast markets; whipsaw in ranges.",
    ),
    StrategyDef(
        "donchian_breakout", "Donchian Channel Pierce", "breakout",
        "Intrabar pierce of the N-bar Donchian channel (earlier, noisier than close-confirmed).",
        s.signals_donchian_breakout,
        parameter_defaults={"lookback": 20, "hold_bars": 5, "stop_pct": 10, "take_pct": 20},
        risk_notes="Intrabar trigger fires on wicks; expect more false breaks.",
    ),
    StrategyDef(
        "range_breakout", "Tight Range Breakout", "breakout",
        "Breakout only out of a consolidation whose total width is capped.",
        s.signals_range_breakout,
        parameter_defaults={"lookback": 30, "max_range_pct": 12.0, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        risk_notes="Few signals by design; range width cap is data-dependent.",
    ),
    StrategyDef(
        "volatility_squeeze_breakout", "Volatility Squeeze Breakout", "breakout",
        "Range contraction vs the prior window, then a close outside the squeeze.",
        s.signals_volatility_squeeze_breakout,
        parameter_defaults={"lookback": 20, "squeeze_ratio": 0.6, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        risk_notes="Squeeze detection needs 2x lookback of history.",
    ),
    StrategyDef(
        "breakout_retest", "Breakout Retest Hold", "breakout",
        "Skip the break itself; enter on the first successful retest of the broken level.",
        s.signals_breakout_retest,
        parameter_defaults={"lookback": 20, "retest_window": 5, "retest_tol_pct": 1.0, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        risk_notes="Misses runaway moves that never retest.",
    ),
    StrategyDef(
        "mean_reversion_fade", "Mean Reversion Fade", "mean_reversion",
        "Fade an N-bar move larger than a threshold.",
        s.signals_mean_reversion_fade,
        parameter_defaults={"lookback": 5, "move_pct": 8.0, "hold_bars": 3, "stop_pct": 10, "take_pct": 8},
        risk_notes="Catches falling knives in strong trends.",
    ),
    StrategyDef(
        "rsi_reversal", "RSI Zone Exit Reversal", "mean_reversion",
        "Enter when simple-average RSI crosses back out of oversold/overbought.",
        s.signals_rsi_reversal,
        parameter_defaults={"period": 14, "oversold": 30, "overbought": 70, "hold_bars": 4, "stop_pct": 8, "take_pct": 12},
        risk_notes="RSI stays pinned in strong trends; cross-out reduces but does not remove this.",
    ),
    StrategyDef(
        "volume_exhaustion_fade", "Volume Exhaustion Fade", "mean_reversion",
        "Fade an extended move that ends on a volume climax bar.",
        s.signals_volume_exhaustion_fade,
        parameter_defaults={"lookback": 20, "move_bars": 3, "min_move_pct": 10.0, "vol_mult": 3.0, "hold_bars": 3, "stop_pct": 12, "take_pct": 10},
        risk_notes="Climax can extend; needs honest volume data.",
    ),
    StrategyDef(
        "trend_pullback", "Trend Pullback Hold", "trend",
        "In an MA-defined trend, enter when a pullback to the fast MA holds.",
        s.signals_trend_pullback,
        compatible_timeframes=_DAILY_UP,
        parameter_defaults={"trend_ma": 30, "pullback_ma": 10, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        risk_notes="MA lag; trend definition is crude by design.",
    ),
    StrategyDef(
        "moving_average_reclaim", "Moving Average Reclaim", "trend",
        "Price reclaims its MA after staying on the other side for K bars.",
        s.signals_moving_average_reclaim,
        compatible_timeframes=_DAILY_UP,
        parameter_defaults={"ma": 20, "below_bars": 3, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        risk_notes="Chops around the MA in sideways regimes.",
    ),
    StrategyDef(
        "volume_shock_continuation", "Volume Shock Continuation", "volume",
        "Abnormal volume plus a large body; continue in the body direction.",
        s.signals_volume_shock_continuation,
        parameter_defaults={"lookback": 20, "vol_mult": 2.0, "min_body_pct": 3.0, "hold_bars": 2, "stop_pct": 8, "take_pct": 14},
        risk_notes="Volume spikes often mark local extremes, not continuations.",
    ),
    StrategyDef(
        "impulse_continuation", "Impulse Continuation", "volume",
        "Large directional bar closing near its extreme; continuation entry. "
        "Crypto perps have no session gaps, so this replaces gap-continuation.",
        s.signals_impulse_continuation,
        parameter_defaults={"min_body_pct": 6.0, "min_close_pos": 0.7, "hold_bars": 3, "stop_pct": 10, "take_pct": 16},
        risk_notes="Impulse bars are frequently exhaustion bars.",
    ),
]

REGISTRY: dict[str, StrategyDef] = {d.strategy_id: d for d in _DEFS}


def get_strategy(strategy_id: str) -> StrategyDef:
    try:
        return REGISTRY[strategy_id]
    except KeyError:
        raise ValueError(f"unknown strategy: {strategy_id}") from None


def list_strategies() -> list[StrategyDef]:
    return sorted(REGISTRY.values(), key=lambda d: (d.family, d.strategy_id))


def strategy_ids() -> list[str]:
    return sorted(REGISTRY)


def registry_summary() -> list[dict[str, Any]]:
    """Public-safe catalog rows (no result data, only definitions)."""
    return [
        {
            "strategy_id": d.strategy_id,
            "display_name": d.display_name,
            "family": d.family,
            "description": d.description,
            "compatible_asset_classes": list(d.compatible_asset_classes),
            "compatible_timeframes": list(d.compatible_timeframes),
            "parameter_defaults": dict(d.parameter_defaults),
            "risk_notes": d.risk_notes,
        }
        for d in list_strategies()
    ]
