# -*- coding: utf-8 -*-
"""Materialize a run_sweep task into the proven compute queue (strategy_lab.sqlite).

Builds one bounded :class:`SweepSpec` for a (symbol, timeframe, family), compiles it
through the existing ``compile_sweep`` path, and idempotently queues it via
``ensure_experiment_queued`` — so the EXISTING worker computes it unchanged.

The crucial fix for the ``already_queued`` saturation lives here: the spec filename
embeds the DATA FINGERPRINT, so a sweep on fresh candles gets a NEW spec_path and is
recomputed, while identical data hits the existing completed-job dedup. Paper only:
no order path, writes only the private spec file + the queue row.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.scanner_farm_pipeline import event_spec_dir
from src.research_lab.state_db import ensure_experiment_queued
from src.research_lab.param_schemas import executable_exit_params
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.sweep_compile import compile_sweep
from src.research_lab.sweep_spec import SweepSpec

# The family "size" knob we vary (first match wins) — lookback/period type parameters.
_SIZE_KEYS = ("lookback", "period", "trend_ma", "oi_lookback", "range_lookback",
              "swing_lookback", "fvg_lookback", "squeeze_lookback", "vwap_period", "bb_period")
DEFAULT_TIER = "normal"

# Per-tier search depth. The product of these axes (plus a fixed RR set) is the grid;
# smoke ~= the old tiny grid, normal/deep actually search stop/take/hold + the size knob.
_TIERS = {
    "smoke":  {"size": (1.0,),           "stop": (1.0,),           "rr": (2.0,),      "hold": (0.5, 1.0)},
    "normal": {"size": (0.5, 1.0, 1.6),  "stop": (0.7, 1.0),       "rr": (2.0, 3.0),  "hold": (0.5, 1.0)},
    "deep":   {"size": (0.5, 1.0, 1.6),  "stop": (0.7, 1.0, 1.4),  "rr": (2.0, 3.0),  "hold": (0.5, 1.0, 2.0)},
}
# Absolute cap so even a fat grid stays desktop-safe (also clipped by tier/profile downstream).
MAX_VARIANTS = 48


def _safe(symbol: str) -> str:
    return str(symbol).replace("-", "_").replace("/", "_").upper()


def _int_levels(base: int, factors: tuple[float, ...]) -> list[int]:
    return sorted({max(2, int(round(base * f))) for f in factors})


def _num_levels(base: float, factors: tuple[float, ...]) -> list[float]:
    return sorted({round(base * f, 6) for f in factors if base * f > 0})


def _family_grids(defaults: dict[str, Any], tier: str) -> tuple[dict, dict]:
    """A real per-family grid: one size knob + varied stop/take(RR)/hold, RR>=2 preserved.

    take levels are derived from the LARGEST stop in the set (take = max_stop * rr), so every
    stop x take combination in the cartesian product still satisfies take >= 2 * stop and
    passes the executable reward/risk gate — no invalid variants are generated.
    """
    cfg = _TIERS.get(tier, _TIERS[DEFAULT_TIER])
    exit_keys = {"hold_bars", "stop_pct", "take_pct"}
    setup: dict[str, list[Any]] = {k: [v] for k, v in defaults.items() if k not in exit_keys}
    for key in _SIZE_KEYS:
        if key in defaults and isinstance(defaults[key], (int, float)) and not isinstance(defaults[key], bool):
            setup[key] = _int_levels(int(defaults[key]), cfg["size"])
            break

    exit_grid: dict[str, list[Any]] = {}
    base_stop = defaults.get("stop_pct")
    if isinstance(base_stop, (int, float)) and base_stop > 0:
        stop_set = _num_levels(float(base_stop), cfg["stop"])
        max_stop = max(stop_set)
        take_set = sorted({round(max_stop * rr, 6) for rr in cfg["rr"]})
        exit_grid["stop_pct"] = stop_set
        exit_grid["take_pct"] = take_set
    if "hold_bars" in defaults:
        exit_grid["hold_bars"] = _int_levels(int(defaults["hold_bars"]), cfg["hold"])
    return setup, exit_grid


def build_sweep_spec(symbol: str, timeframe: str, family: str, *, fingerprint: str | None,
                     backend: str = "auto", tier: str = DEFAULT_TIER,
                     max_variants: int = MAX_VARIANTS) -> SweepSpec:
    tier = tier if tier in _TIERS else DEFAULT_TIER
    defaults = executable_exit_params(family, get_strategy(family).parameter_defaults)
    setup_grid, exit_grid = _family_grids(defaults, tier)
    fp_tag = (fingerprint or "nofp")[:10]
    return SweepSpec(
        sweep_id=f"farm_{_safe(symbol)}_{timeframe}_{family}_{tier}_{fp_tag}",
        anchor_symbol=symbol, related_symbols=(), timeframe=timeframe, setup_family=family,
        setup_grid=setup_grid, exit_grid=exit_grid, max_variants=max_variants,
        variant_tier=tier, backend=backend, resource_class="normal",
        private_output_policy="private_only",
    )


def queue_sweep(conn, spec: SweepSpec, *, private_root: Path, profiles, policy, data_glob: str,
                priority: int, fingerprint: str | None, event_context: dict[str, Any] | None = None
                ) -> tuple[str, int, bool]:
    """Compile + write spec file + idempotently enqueue. Returns (experiment_id, job_id, created)."""
    exp = compile_sweep(spec, data_glob=data_glob, timeframe_profiles=profiles,
                        resource_policy=policy, event_context=event_context or {})
    out_dir = event_spec_dir(private_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = out_dir / f"{exp.experiment_id}.json"
    variants = (exp.parameter_grid or {}).get(spec.setup_family, [])
    payload = {
        "experiment_id": exp.experiment_id, "data_glob": exp.data_glob, "symbols": exp.symbols,
        "timeframe": exp.timeframe, "families": exp.families, "fees_bps": exp.fees_bps,
        "slippage_bps": exp.slippage_bps, "min_trades": exp.min_trades, "split_ratio": exp.split_ratio,
        "max_runs": exp.max_runs, "parameter_grid": exp.parameter_grid, "filters": exp.filters,
        "event_context": exp.event_context, "backend": exp.backend, "data_fingerprint": fingerprint,
        "variant_tier": getattr(spec, "variant_tier", "smoke"), "variant_count": len(variants),
    }
    spec_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    job_id, created = ensure_experiment_queued(conn, spec_path.resolve(), priority=int(priority))
    return exp.experiment_id, int(job_id), bool(created)
