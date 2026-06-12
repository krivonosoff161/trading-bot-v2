# -*- coding: utf-8 -*-
"""Deterministic, stdlib-only strategy signal generators for the lab."""

from src.research_lab.strategies.breakout import (
    signals_breakout_retest,
    signals_donchian_breakout,
    signals_momentum_breakout,
    signals_range_breakout,
    signals_volatility_squeeze_breakout,
)
from src.research_lab.strategies.mean_reversion import (
    signals_mean_reversion_fade,
    signals_rsi_reversal,
    signals_volume_exhaustion_fade,
)
from src.research_lab.strategies.trend import (
    signals_moving_average_reclaim,
    signals_trend_pullback,
)
from src.research_lab.strategies.volume_flow import (
    signals_impulse_continuation,
    signals_volume_shock_continuation,
)

__all__ = [
    "signals_breakout_retest",
    "signals_donchian_breakout",
    "signals_impulse_continuation",
    "signals_mean_reversion_fade",
    "signals_momentum_breakout",
    "signals_moving_average_reclaim",
    "signals_range_breakout",
    "signals_rsi_reversal",
    "signals_trend_pullback",
    "signals_volatility_squeeze_breakout",
    "signals_volume_exhaustion_fade",
    "signals_volume_shock_continuation",
]
