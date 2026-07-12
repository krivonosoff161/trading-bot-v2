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
import hashlib
import itertools
import math
from pathlib import Path
from typing import Any

from src.research_lab.scanner_farm_pipeline import event_spec_dir
from src.research_lab.state_db import ensure_experiment_queued
from src.research_lab.param_schemas import (
    executable_exit_params,
    parameter_search_contract,
    search_variant_is_valid,
)
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.sweep_compile import compile_sweep
from src.research_lab.sweep_spec import SweepSpec

DEFAULT_TIER = "normal"
_DYNAMIC_EXIT_MODES = ("baseline", "trailing", "trailing_tight", "break_even", "partial_tp")

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


def _num_levels(
    base: float,
    factors: tuple[float, ...],
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> list[float]:
    if base != 0:
        return sorted({round(base * f, 6) for f in factors if base * f > 0})
    if minimum is None or maximum is None or maximum <= minimum:
        return [0.0]
    span = maximum - minimum
    return sorted(
        {round(minimum + span * fraction, 6) for fraction in (0.0, 0.25, 0.5, 0.75, 1.0)}
    )


def _normal_dimensions(dimensions: tuple[str, ...] | list[str] | None) -> set[str]:
    out: set[str] = set()
    for raw in dimensions or ():
        text = str(raw or "").lower().replace("-", "_").strip()
        if not text:
            continue
        if "entry" in text or "timing" in text:
            out.add("entry_timing")
        if "stop" in text or "risk" in text:
            out.add("stop")
        if "take" in text or "profit" in text or "rr" in text:
            out.add("take_profit")
        if "hold" in text or "time" in text or "horizon" in text:
            out.add("hold")
        if "trail" in text or "break_even" in text or "partial" in text or "exit_mode" in text:
            out.add("dynamic_exit")
        if "regime" in text or "filter" in text:
            out.add("regime_filter")
    return out


def _dimension_cfg(tier: str, dimensions: set[str]) -> dict[str, tuple[float, ...]]:
    cfg = dict(_TIERS.get(tier, _TIERS[DEFAULT_TIER]))
    if "dynamic_exit" in dimensions:
        cfg["size"] = (1.0,)
        cfg["stop"] = (1.0,)
        cfg["rr"] = (2.0,)
        cfg["hold"] = (1.0,)
    if "entry_timing" in dimensions:
        cfg["size"] = tuple(sorted(set(cfg["size"]) | {0.35, 0.75, 1.25, 2.0}))
    if "stop" in dimensions:
        cfg["stop"] = tuple(sorted(set(cfg["stop"]) | {0.45, 0.6, 1.25, 1.6}))
    if "take_profit" in dimensions:
        cfg["rr"] = tuple(sorted(set(cfg["rr"]) | {1.5, 2.5, 4.0, 5.0}))
    if "hold" in dimensions:
        cfg["hold"] = tuple(sorted(set(cfg["hold"]) | {0.35, 0.75, 1.5, 2.5, 3.0}))
    return cfg


def _family_grids(family: str, defaults: dict[str, Any], tier: str,
                  dimensions: tuple[str, ...] | list[str] | None = None) -> tuple[dict, dict]:
    """A real per-family grid: one size knob + varied stop/take(RR)/hold, RR>=2 preserved.

    take levels are derived from the LARGEST stop in the set (take = max_stop * rr), so every
    stop x take combination in the cartesian product still satisfies take >= 2 * stop and
    passes the executable reward/risk gate — no invalid variants are generated.
    """
    dims = _normal_dimensions(dimensions)
    cfg = _dimension_cfg(tier, dims)
    exit_keys = {"hold_bars", "stop_pct", "take_pct"}
    setup: dict[str, list[Any]] = {k: [v] for k, v in defaults.items() if k not in exit_keys}
    for axis in parameter_search_contract(family).adaptive_axes:
        base = defaults[axis.name]
        setup[axis.name] = (
            _int_levels(int(base), cfg["size"])
            if axis.value_type == "int"
            else _num_levels(
                float(base), cfg["size"], minimum=axis.minimum, maximum=axis.maximum
            )
        )
        setup[axis.name] = [
            value for value in setup[axis.name]
            if axis.minimum <= float(value) <= axis.maximum
        ] or [base]

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
    if "dynamic_exit" in dims:
        exit_grid["exit_mode"] = list(_DYNAMIC_EXIT_MODES)
    return setup, exit_grid


def build_sweep_spec(symbol: str, timeframe: str, family: str, *, fingerprint: str | None,
                     backend: str = "auto", tier: str = DEFAULT_TIER,
                     max_variants: int = MAX_VARIANTS,
                     dimensions: tuple[str, ...] | list[str] | None = None) -> SweepSpec:
    tier = tier if tier in _TIERS else DEFAULT_TIER
    defaults = executable_exit_params(family, get_strategy(family).parameter_defaults)
    setup_grid, exit_grid = _family_grids(family, defaults, tier, dimensions)
    fp_tag = (fingerprint or "nofp")[:10]
    dim_tag = ""
    norm_dims = sorted(_normal_dimensions(dimensions))
    if norm_dims:
        dim_tag = "_" + "_".join(norm_dims)[:36]
    return SweepSpec(
        sweep_id=f"farm_{_safe(symbol)}_{timeframe}_{family}_{tier}{dim_tag}_{fp_tag}",
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
    raw_space_size = math.prod(
        len(values)
        for values in {**spec.setup_grid, **spec.entry_grid, **spec.exit_grid}.values()
    )
    merged_grid = {**spec.setup_grid, **spec.entry_grid, **spec.exit_grid}
    grid_keys = sorted(merged_grid)
    valid_space_size = sum(
        1
        for values in itertools.product(*(merged_grid[key] for key in grid_keys))
        if search_variant_is_valid(spec.setup_family, dict(zip(grid_keys, values)))
    )
    search_contract = parameter_search_contract(spec.setup_family)
    seed_material = f"{spec.sweep_id}|{spec.setup_family}|{spec.timeframe}"
    variant_hashes = [
        hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        for row in variants
    ]
    payload = {
        "experiment_id": exp.experiment_id, "data_glob": exp.data_glob, "symbols": exp.symbols,
        "timeframe": exp.timeframe, "families": exp.families, "fees_bps": exp.fees_bps,
        "slippage_bps": exp.slippage_bps, "min_trades": exp.min_trades, "split_ratio": exp.split_ratio,
        "max_runs": exp.max_runs, "parameter_grid": exp.parameter_grid, "filters": exp.filters,
        "event_context": exp.event_context, "backend": exp.backend, "data_fingerprint": fingerprint,
        "variant_tier": getattr(spec, "variant_tier", "smoke"), "variant_count": len(variants),
        "parameter_search_contract": search_contract.version,
        "search_axes": [
            {
                "name": axis.name,
                "value_type": axis.value_type,
                "minimum": axis.minimum,
                "maximum": axis.maximum,
                "default": axis.default,
                "unit": axis.unit,
                "searchable": axis.searchable,
                "dependencies": list(axis.dependencies),
            }
            for axis in search_contract.adaptive_axes
        ],
        "raw_search_space_size": raw_space_size,
        "dependency_valid_space_size": valid_space_size,
        "dependency_rejected_count": max(0, raw_space_size - valid_space_size),
        "raw_grids": {
            "setup": spec.setup_grid,
            "entry": spec.entry_grid,
            "exit": spec.exit_grid,
        },
        "sampler_version": search_contract.sampler_version,
        "sampler_seed_material": seed_material,
        "tested_variant_hashes": variant_hashes,
        "tested_variant_count": len(variants),
        "omitted_variant_count": max(0, valid_space_size - len(variants)),
    }
    spec_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    job_id, created = ensure_experiment_queued(conn, spec_path.resolve(), priority=int(priority))
    return exp.experiment_id, int(job_id), bool(created)
