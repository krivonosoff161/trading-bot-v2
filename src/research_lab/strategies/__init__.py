# -*- coding: utf-8 -*-
"""Deterministic, stdlib-only strategy signal generators for the lab."""

from src.research_lab.strategies.bb_fade import signals_bb_volume_fade
from src.research_lab.strategies.breakout import (
    signals_breakout_retest,
    signals_donchian_breakout,
    signals_momentum_breakout,
    signals_range_breakout,
    signals_volatility_squeeze_breakout,
)
from src.research_lab.strategies.flow_family import (
    signals_oi_funding_squeeze,
    signals_oi_price_quadrant,
)
from src.research_lab.strategies.fractal_family import signals_fractal_swing_break_retest
from src.research_lab.strategies.fvg_family import signals_fvg_reclaim_reject
from src.research_lab.strategies.mean_reversion import (
    signals_mean_reversion_fade,
    signals_rsi_reversal,
    signals_volume_exhaustion_fade,
)
from src.research_lab.strategies.micro_family import signals_microstructure_confirmed_breakout
from src.research_lab.strategies.pump_dump import signals_pump_dump_scalp
from src.research_lab.strategies.range_family import (
    signals_range_volume_breakout,
    signals_volatility_squeeze_breakout_v2,
)
from src.research_lab.strategies.regime_family import signals_main_fast_swing_regime
from src.research_lab.strategies.trend import (
    signals_moving_average_reclaim,
    signals_trend_pullback,
)
from src.research_lab.strategies.volume_flow import (
    signals_impulse_continuation,
    signals_volume_shock_continuation,
)
from src.research_lab.strategies.vwap_family import signals_vwap_reclaim_reject

__all__ = [
    "signals_bb_volume_fade",
    "signals_breakout_retest",
    "signals_donchian_breakout",
    "signals_fractal_swing_break_retest",
    "signals_fvg_reclaim_reject",
    "signals_impulse_continuation",
    "signals_main_fast_swing_regime",
    "signals_mean_reversion_fade",
    "signals_microstructure_confirmed_breakout",
    "signals_momentum_breakout",
    "signals_moving_average_reclaim",
    "signals_oi_funding_squeeze",
    "signals_oi_price_quadrant",
    "signals_pump_dump_scalp",
    "signals_range_breakout",
    "signals_range_volume_breakout",
    "signals_rsi_reversal",
    "signals_trend_pullback",
    "signals_volatility_squeeze_breakout",
    "signals_volatility_squeeze_breakout_v2",
    "signals_volume_exhaustion_fade",
    "signals_volume_shock_continuation",
    "signals_vwap_reclaim_reject",
]
