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
    StrategyDef(
        "main_fast_swing_regime", "Main Regime-Gated Breakout", "regime",
        "Single-TF FAST/SWING/DRIFT/RANGING regime gate (ported from signal_engine); "
        "fire a continuation breakout only while trending in the EMA-bias direction.",
        s.signals_main_fast_swing_regime,
        parameter_defaults={"ema_fast": 20, "ema_slow": 50, "adx_period": 14, "adx_trend": 22.0,
                            "di_trend": 10.0, "breakout_lookback": 20, "min_vol_ratio": 1.2,
                            "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        risk_notes="Research candidate. Audit: 15m scalp entry-timing & RR were NOT levers; "
                   "DRIFT/RANGING-fade were NO-GO. Validate OOS before any interpretation.",
    ),
    StrategyDef(
        "range_volume_breakout", "Range Accumulation Breakout", "breakout",
        "Tight sideways range with accumulated volume, then a volume-confirmed break out.",
        s.signals_range_volume_breakout,
        parameter_defaults={"range_lookback": 20, "max_range_pct": 12.0, "min_accumulation": 1.0,
                            "min_vol_ratio": 1.5, "vol_period": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        risk_notes="Accumulation ratio and range cap are data-dependent; few signals by design.",
    ),
    StrategyDef(
        "volatility_squeeze_breakout_v2", "Volatility Squeeze Breakout v2", "breakout",
        "BB/ATR squeeze + ATR-percentile compression + volume-confirmed break (v2 of the pure-range v1).",
        s.signals_volatility_squeeze_breakout_v2,
        parameter_defaults={"squeeze_lookback": 20, "squeeze_ratio": 0.6, "atr_pct_max": 40.0,
                            "min_vol_ratio": 1.3, "atr_period": 14, "vol_period": 20,
                            "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        risk_notes="Needs 2x lookback of history; volume confirmation reduces but does not remove false breaks.",
    ),
    StrategyDef(
        "vwap_reclaim_reject", "VWAP Reclaim / Reject", "vwap",
        "Reclaim/lose the rolling VWAP with an EMA-bias and day-position filter.",
        s.signals_vwap_reclaim_reject,
        parameter_defaults={"vwap_period": 20, "ema_fast": 20, "ema_slow": 50,
                            "max_day_position": 0.7, "use_day_filter": True,
                            "hold_bars": 5, "stop_pct": 8, "take_pct": 14},
        risk_notes="Rolling VWAP (no true session anchor on perps); day filter uses UTC-day position.",
    ),
    StrategyDef(
        "fvg_reclaim_reject", "FVG Reclaim / Reject", "structure",
        "Enter on a reaction to the nearest fair-value gap (bull reclaim / bear reject) close to price.",
        s.signals_fvg_reclaim_reject,
        parameter_defaults={"fvg_lookback": 30, "max_distance_pct": 3.0,
                            "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        risk_notes="ICT structure idea; many gaps never get a clean reaction. Research candidate.",
    ),
    StrategyDef(
        "fractal_swing_break_retest", "Fractal Break + Retest", "structure",
        "Break a confirmed fractal pivot, enter on the first successful retest+hold of the level.",
        s.signals_fractal_swing_break_retest,
        parameter_defaults={"swing_lookback": 3, "retest_window": 5, "retest_tol_pct": 1.0,
                            "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        risk_notes="Misses runaway moves that never retest; pivot lookback sets confirmation lag.",
    ),
    StrategyDef(
        "oi_funding_squeeze", "OI + Funding Squeeze", "flow",
        "Crowded funding + building open interest -> fade the crowded side (squeeze hypothesis). "
        "Needs OI/funding data; otherwise emits nothing (NEEDS_DATA).",
        s.signals_oi_funding_squeeze,
        parameter_defaults={"oi_lookback": 5, "oi_surge_min": 1.0, "funding_warn": 0.0005,
                            "funding_block": 0.001, "hold_bars": 4, "stop_pct": 8, "take_pct": 12},
        risk_notes="Research candidate; single-regime/survivor-biased history. Forward-validate only.",
    ),
    StrategyDef(
        "oi_price_quadrant", "OI x Price Quadrant", "flow",
        "dOI x dPrice quadrant continuation (new_longs->long, new_shorts->short). Needs OI data.",
        s.signals_oi_price_quadrant,
        parameter_defaults={"oi_lookback": 5, "fade_traps": False,
                            "hold_bars": 4, "stop_pct": 8, "take_pct": 12},
        risk_notes="Audit verdict: NULL/inconclusive by construction. Candidate feature, NOT a money edge.",
    ),
    StrategyDef(
        "bb_volume_fade", "Bollinger Volume Fade", "mean_reversion",
        "Fade a Bollinger extreme on fading volume in a non-trending regime (ported BB-fade).",
        s.signals_bb_volume_fade,
        compatible_timeframes=("15m", "1H", "4H"),
        parameter_defaults={"bb_period": 20, "bb_std": 2.0, "pct_b_extreme_high": 95.0,
                            "pct_b_extreme_low": 5.0, "max_vol_ratio": 0.7, "max_adx": 20.0,
                            "min_width_pct": 2.0, "adx_period": 14, "vol_period": 20,
                            "hold_bars": 4, "stop_pct": 6, "take_pct": 8},
        risk_notes="Live BB-fade's disease was the EXIT (gave back big moves); candidate pending validation.",
    ),
    StrategyDef(
        "pump_dump_scalp", "Pump/Dump Volume-Shock Scalp", "volume",
        "Volume-shock impulse bar continued in the body direction; short hold, strict cost stress.",
        s.signals_pump_dump_scalp,
        compatible_timeframes=("15m", "1H"),
        parameter_defaults={"min_body_pct": 3.0, "vol_mult": 3.0, "vol_period": 20,
                            "min_close_pos": 0.6, "hold_bars": 2, "stop_pct": 8, "take_pct": 12},
        risk_notes="The detector caught moves; the live EXIT was the disease and BSB overfit killed it. "
                   "Forward NO-GO historically — candidate only, must clear cost stress.",
    ),
    StrategyDef(
        "microstructure_confirmed_breakout", "Microstructure-Confirmed Breakout", "microstructure",
        "Range break confirmed by book imbalance + signed trade delta + tight spread. "
        "Needs a captured microstructure snapshot; otherwise emits nothing (NEEDS_DATA).",
        s.signals_microstructure_confirmed_breakout,
        compatible_timeframes=("15m", "1H"),
        parameter_defaults={"range_lookback": 20, "min_obi": 0.15, "min_trade_delta": 0.0,
                            "max_spread_bps": 5.0, "hold_bars": 3, "stop_pct": 8, "take_pct": 12},
        risk_notes="Microstructure is not reconstructable from OHLCV; only public snapshots, no private endpoints.",
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
