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

_ADAPTIVE_AXES_BY_STRATEGY = {
    "momentum_breakout": ("lookback", "threshold_pct"),
    "donchian_breakout": ("lookback",),
    "range_breakout": ("lookback", "max_range_pct"),
    "volatility_squeeze_breakout": ("lookback", "squeeze_ratio"),
    "breakout_retest": ("lookback", "retest_window", "retest_tol_pct"),
    "mean_reversion_fade": ("lookback", "move_pct"),
    "rsi_reversal": ("period", "oversold", "overbought"),
    "volume_exhaustion_fade": ("lookback", "move_bars", "min_move_pct", "vol_mult"),
    "trend_pullback": ("trend_ma", "pullback_ma"),
    "moving_average_reclaim": ("ma", "below_bars"),
    "volume_shock_continuation": ("lookback", "vol_mult", "min_body_pct"),
    "impulse_continuation": ("min_body_pct", "min_close_pos"),
    "main_fast_swing_regime": ("ema_fast", "ema_slow", "adx_period", "adx_trend", "di_trend", "breakout_lookback", "min_vol_ratio"),
    "range_volume_breakout": ("range_lookback", "max_range_pct", "min_accumulation", "min_vol_ratio", "vol_period"),
    "volatility_squeeze_breakout_v2": ("squeeze_lookback", "squeeze_ratio", "atr_pct_max", "min_vol_ratio", "atr_period", "vol_period"),
    "vwap_reclaim_reject": ("vwap_period", "ema_fast", "ema_slow", "max_day_position"),
    "fvg_reclaim_reject": ("fvg_lookback", "max_distance_pct"),
    "fractal_swing_break_retest": ("swing_lookback", "retest_window", "retest_tol_pct"),
    "exhaustion_fade": ("run_lookback", "run_pct", "vol_climax_mult"),
    "sfp_liquidity_sweep": ("lookback", "vol_mult", "reclaim_buf_pct"),
    "oi_funding_squeeze": ("oi_lookback", "oi_surge_min", "funding_warn", "funding_block"),
    "oi_price_quadrant": ("oi_lookback",),
    "oi_price_quadrant_continuation": ("oi_lookback",),
    "oi_price_quadrant_trap_fade": ("oi_lookback",),
    "bb_volume_fade": ("bb_period", "bb_std", "pct_b_extreme_high", "pct_b_extreme_low", "max_vol_ratio", "max_adx", "min_width_pct", "adx_period", "vol_period"),
    "pump_dump_scalp": ("min_body_pct", "vol_mult", "vol_period", "min_close_pos"),
    "microstructure_confirmed_breakout": ("range_lookback", "min_obi", "min_trade_delta", "max_spread_bps"),
}


@dataclass(frozen=True)
class HistoryFormula:
    """One candidate warm-up expression; the strategy uses the maximum candidate."""

    terms: tuple[tuple[str, int], ...] = ()
    offset: int = 0

    def evaluate(self, params: dict[str, Any]) -> int:
        return max(1, int(self.offset) + sum(
            int(params.get(key, 0) or 0) * int(multiplier)
            for key, multiplier in self.terms
        ))

    def label(self) -> str:
        chunks = [f"{multiplier}*{key}" for key, multiplier in self.terms]
        if self.offset:
            chunks.append(str(self.offset))
        return "+".join(chunks) or "1"


def _hf(*terms: tuple[str, int], offset: int = 0) -> HistoryFormula:
    return HistoryFormula(tuple(terms), int(offset))


# These formulas mirror the actual first usable index in each signal generator.
# Keeping all 27 here makes an omitted manifest a construction-time error rather
# than a silent fallback to a guessed parameter name.
_HISTORY_FORMULAS_BY_STRATEGY: dict[str, tuple[HistoryFormula, ...]] = {
    "momentum_breakout": (_hf(("lookback", 1)),),
    "donchian_breakout": (_hf(("lookback", 1)),),
    "range_breakout": (_hf(("lookback", 1)),),
    "volatility_squeeze_breakout": (_hf(("lookback", 2)),),
    "breakout_retest": (_hf(("lookback", 1), ("retest_window", 1)),),
    "mean_reversion_fade": (_hf(("lookback", 1)),),
    "rsi_reversal": (_hf(("period", 1), offset=1),),
    "volume_exhaustion_fade": (
        _hf(("lookback", 1)), _hf(("move_bars", 1)),
    ),
    "trend_pullback": (_hf(("trend_ma", 1)), _hf(("pullback_ma", 1))),
    "moving_average_reclaim": (_hf(("ma", 1), ("below_bars", 1)),),
    "volume_shock_continuation": (_hf(("lookback", 1)),),
    "impulse_continuation": (_hf(offset=1),),
    "main_fast_swing_regime": (
        _hf(("adx_period", 2)), _hf(("ema_slow", 1)),
        _hf(("breakout_lookback", 1)), _hf(("vol_period", 1)),
    ),
    "range_volume_breakout": (
        _hf(("range_lookback", 2)), _hf(("vol_period", 1)),
    ),
    "volatility_squeeze_breakout_v2": (
        _hf(("squeeze_lookback", 2)), _hf(("atr_period", 1), offset=12),
        _hf(("vol_period", 1)),
    ),
    "vwap_reclaim_reject": (
        _hf(("vwap_period", 1), offset=1), _hf(("ema_slow", 1)),
    ),
    "fvg_reclaim_reject": (_hf(("fvg_lookback", 1)), _hf(offset=3)),
    "fractal_swing_break_retest": (
        _hf(("swing_lookback", 2), ("retest_window", 1)),
    ),
    "exhaustion_fade": (_hf(("run_lookback", 1)),),
    "sfp_liquidity_sweep": (_hf(("lookback", 1)),),
    "oi_funding_squeeze": (_hf(("oi_lookback", 1)),),
    "oi_price_quadrant": (_hf(("oi_lookback", 1)),),
    "oi_price_quadrant_continuation": (_hf(("oi_lookback", 1)),),
    "oi_price_quadrant_trap_fade": (_hf(("oi_lookback", 1)),),
    "bb_volume_fade": (
        _hf(("bb_period", 1)), _hf(("adx_period", 2)),
        _hf(("vol_period", 1)),
    ),
    "pump_dump_scalp": (_hf(("vol_period", 1)),),
    "microstructure_confirmed_breakout": (_hf(("range_lookback", 1)),),
}


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
    adaptive_parameter_axes: tuple[str, ...] = ()
    risk_notes: str = ""
    # Optional candle data a family needs beyond OHLCV. When absent on the data, the
    # farm classifies the result as NEEDS_<...>_DATA instead of pretending it's active.
    # Tokens: "oi" | "funding" | "microstructure".
    required_data: tuple[str, ...] = ()
    history_formulas: tuple[HistoryFormula, ...] = ()

    def __post_init__(self) -> None:
        axes = self.adaptive_parameter_axes or _ADAPTIVE_AXES_BY_STRATEGY.get(self.strategy_id, ())
        unknown = set(axes) - set(self.parameter_defaults)
        if unknown:
            raise ValueError(f"unknown adaptive axes for {self.strategy_id}: {sorted(unknown)}")
        object.__setattr__(self, "adaptive_parameter_axes", axes)
        formulas = self.history_formulas or _HISTORY_FORMULAS_BY_STRATEGY.get(self.strategy_id, ())
        if not formulas:
            raise ValueError(f"missing candle-history manifest for {self.strategy_id}")
        formula_keys = {key for formula in formulas for key, _ in formula.terms}
        unknown_history = formula_keys - set(self.parameter_defaults)
        if unknown_history:
            raise ValueError(
                f"unknown history parameters for {self.strategy_id}: {sorted(unknown_history)}"
            )
        object.__setattr__(self, "history_formulas", formulas)

    def required_history_bars(self, params: dict[str, Any] | None = None) -> int:
        merged = {**self.parameter_defaults, **(params or {})}
        return max(formula.evaluate(merged) for formula in self.history_formulas)

    def history_formula_labels(self) -> tuple[str, ...]:
        return tuple(formula.label() for formula in self.history_formulas)


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
                            "vol_period": 20,
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
        "exhaustion_fade", "Exhaustion Fade", "mean_reversion",
        "Fade a parabolic run's exhaustion: a +run_pct move over K bars with a climax bar tends to snap "
        "back (short); mirror on a -run_pct capitulation (long). Conditional MR, aimed at live movers.",
        s.signals_exhaustion_fade,
        parameter_defaults={"run_lookback": 6, "run_pct": 15.0, "vol_climax_mult": 1.5,
                            "hold_bars": 6, "stop_pct": 6, "take_pct": 12},
        risk_notes="Fading strength is dangerous in a real trend; the climax filter + run threshold are "
                   "the guard. Few signals on calm symbols by design.",
    ),
    StrategyDef(
        "sfp_liquidity_sweep", "SFP / Liquidity Sweep", "structure",
        "Stop-run reversal: price pierces a prior swing extreme (sweeps the liquidity beyond it) then "
        "closes back inside the range (failed breakout). Sweep above -> short; sweep below -> long. "
        "Optional volume confirmation on the sweep bar.",
        s.signals_sfp_liquidity_sweep,
        parameter_defaults={"lookback": 20, "vol_mult": 0.0, "reclaim_buf_pct": 0.0,
                            "hold_bars": 8, "stop_pct": 5, "take_pct": 10},
        risk_notes="Reversal idea: a real breakout (no reclaim) produces no signal by design; thin on "
                   "quiet ranges. Best where liquidity sits beyond obvious swings (movers).",
    ),
    StrategyDef(
        "oi_funding_squeeze", "OI + Funding Squeeze", "flow",
        "Crowded funding + building open interest -> fade the crowded side (squeeze hypothesis). "
        "Needs OI/funding data; otherwise emits nothing (NEEDS_DATA).",
        s.signals_oi_funding_squeeze,
        parameter_defaults={"oi_lookback": 5, "oi_surge_min": 1.0, "funding_warn": 0.0005,
                            "funding_block": 0.001, "hold_bars": 4, "stop_pct": 8, "take_pct": 12},
        risk_notes="Research candidate; single-regime/survivor-biased history. Forward-validate only.",
        required_data=("oi", "funding"),
    ),
    StrategyDef(
        "oi_price_quadrant", "OI x Price Quadrant", "flow",
        "dOI x dPrice quadrant continuation (new_longs->long, new_shorts->short). Needs OI data. "
        "Backward-compat alias; prefer the explicit continuation/trap_fade A/B variants.",
        s.signals_oi_price_quadrant,
        parameter_defaults={"oi_lookback": 5, "fade_traps": False,
                            "hold_bars": 4, "stop_pct": 8, "take_pct": 12},
        risk_notes="Audit verdict: NULL/inconclusive by construction. Candidate feature, NOT a money edge.",
        required_data=("oi",),
    ),
    StrategyDef(
        "oi_price_quadrant_continuation", "OI x Price Quadrant (Continuation A)", "flow",
        "A-hypothesis: fresh positions push the move. new_longs->long, new_shorts->short. Needs OI.",
        s.signals_oi_price_quadrant_continuation,
        parameter_defaults={"oi_lookback": 5, "hold_bars": 4, "stop_pct": 8, "take_pct": 12},
        risk_notes="A side of an explicit A/B vs trap_fade. NULL by construction; forward-validate only.",
        required_data=("oi",),
    ),
    StrategyDef(
        "oi_price_quadrant_trap_fade", "OI x Price Quadrant (Trap-Fade B)", "flow",
        "B-hypothesis (old research note): fresh positions are a trap. new_shorts->long (fade up), "
        "new_longs->short (fade down). Opposite of continuation. Needs OI.",
        s.signals_oi_price_quadrant_trap_fade,
        parameter_defaults={"oi_lookback": 5, "hold_bars": 4, "stop_pct": 8, "take_pct": 12},
        risk_notes="B side of an explicit A/B. Preserves the OI-up+price-down = short-trap reading. "
                   "NULL by construction; forward-validate only.",
        required_data=("oi",),
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
        required_data=("microstructure",),
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
            "history_formulas": list(d.history_formula_labels()),
            "required_data": list(d.required_data),
            "risk_notes": d.risk_notes,
        }
        for d in list_strategies()
    ]
