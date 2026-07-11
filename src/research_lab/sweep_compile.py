# -*- coding: utf-8 -*-
"""Compile a validated SweepSpec into one bounded ExperimentSpec.

This is the bridge between the coarse-sweep contract (SweepSpec) and the existing
deterministic executor (evaluate_spec over ExperimentSpec). It expands the
setup/entry/exit grids into a capped list of parameter variants, never an
unbounded cartesian explosion, and applies the timeframe-profile + resource
policy gates first. It does not execute the run; it forwards the requested
backend into ExperimentSpec for evaluate_spec.
"""

from __future__ import annotations

import itertools
import hashlib
import math
import random
from typing import Any

from src.research_lab.experiment import ExperimentSpec
from src.research_lab.param_schemas import executable_exit_params, search_variant_is_valid
from src.research_lab.resource_policy import ResourcePolicy
from src.research_lab.runtime_policy import effective_variant_cap
from src.research_lab.sweep_spec import SweepSpec, validate_sweep_spec
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.timeframes import TimeframeProfiles


def expand_grids(*grids: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Cartesian product of the merged grids -> list of param dicts (>=1)."""
    merged: dict[str, list[Any]] = {}
    for grid in grids:
        for key, values in grid.items():
            if values:
                merged[key] = list(values)
    if not merged:
        return [{}]
    keys = sorted(merged)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(merged[k] for k in keys))]


def bounded_uniform_sample(
    variants: list[dict[str, Any]], cap: int, *, seed_material: str
) -> list[dict[str, Any]]:
    """Uniformly sample without replacement using a stable, cross-process seed."""
    limit = max(1, int(cap))
    if len(variants) <= limit:
        return variants
    digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:16], "big"))
    indices = sorted(rng.sample(range(len(variants)), limit))
    return [variants[index] for index in indices]


def expand_grids_bounded(
    *grids: dict[str, list[Any]], cap: int, seed_material: str,
    baseline: dict[str, Any] | None = None,
    strategy_id: str = "",
) -> list[dict[str, Any]]:
    """Sample a Cartesian grid without materializing the full search space."""
    merged: dict[str, list[Any]] = {}
    for grid in grids:
        for key, values in grid.items():
            if values:
                merged[key] = list(values)
    if not merged:
        return [{}]
    keys = sorted(merged)
    axes = [merged[key] for key in keys]
    total = math.prod(len(axis) for axis in axes)
    def decode(flat_index: int) -> dict[str, Any]:
        cursor = flat_index
        values: list[Any] = [None] * len(axes)
        for pos in range(len(axes) - 1, -1, -1):
            cursor, offset = divmod(cursor, len(axes[pos]))
            values[pos] = axes[pos][offset]
        return dict(zip(keys, values))

    valid_indices = [
        index for index in range(total)
        if not strategy_id or search_variant_is_valid(strategy_id, decode(index))
    ]
    if not valid_indices:
        raise ValueError("parameter grid has no variants satisfying cross-axis dependencies")
    limit = min(len(valid_indices), max(1, int(cap)))
    if len(valid_indices) <= limit:
        indices: list[int] = valid_indices
    else:
        digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
        rng = random.Random(int.from_bytes(digest[:16], "big"))
        baseline = baseline or {}
        offsets = [
            axis.index(baseline.get(key)) if baseline.get(key) in axis else 0
            for key, axis in zip(keys, axes)
        ]

        def flat_index(parts: list[int]) -> int:
            index = 0
            for offset, axis in zip(parts, axes):
                index = index * len(axis) + offset
            return index

        baseline_index = flat_index(offsets)
        mandatory: list[int] = [baseline_index] if baseline_index in valid_indices else []
        for axis_pos, axis in enumerate(axes):
            coverage_levels = sorted({0, offsets[axis_pos], len(axis) - 1})
            for level in coverage_levels:
                parts = list(offsets)
                parts[axis_pos] = level
                index = flat_index(parts)
                if index in valid_indices and index not in mandatory:
                    mandatory.append(index)
                if len(mandatory) >= limit:
                    break
            if len(mandatory) >= limit:
                break
        chosen = set(mandatory)
        while len(chosen) < limit:
            chosen.add(rng.choice(valid_indices))
        indices = sorted(chosen)
    variants: list[dict[str, Any]] = []
    for flat_index in indices:
        variants.append(decode(flat_index))
    return variants


def compile_sweep(
    spec: SweepSpec,
    *,
    data_glob: str,
    timeframe_profiles: TimeframeProfiles,
    resource_policy: ResourcePolicy,
    fees_bps: float = 7.0,
    slippage_bps: float = 3.0,
    min_trades: int = 20,
    event_context: dict[str, Any] | None = None,
) -> ExperimentSpec:
    """Validate safety gates, expand grids (bounded by cap), build an ExperimentSpec.

    Safety gates (backend, 1m/trigger scope, heavy mode, output policy, known
    timeframe) are strict. An over-sized variant grid is NOT an error here: the
    compiler deterministically samples it to the effective cap so a fat grid yields
    a bounded job instead of being rejected.
    """
    result = validate_sweep_spec(
        spec, timeframe_profiles=timeframe_profiles, resource_policy=resource_policy
    )
    blocking = [e for e in result.errors if "variant grid" not in e]
    if blocking:
        raise ValueError("invalid sweep spec: " + "; ".join(blocking))

    defaults = executable_exit_params(
        spec.setup_family, get_strategy(spec.setup_family).parameter_defaults
    )
    baseline = {
        key: defaults.get(key, "baseline" if key == "exit_mode" else values[0])
        for grid in (spec.setup_grid, spec.entry_grid, spec.exit_grid)
        for key, values in grid.items()
        if values
    }
    variants = expand_grids_bounded(
        spec.setup_grid, spec.entry_grid, spec.exit_grid,
        cap=result.effective_max_variants,
        seed_material=f"{spec.sweep_id}|{spec.setup_family}|{spec.timeframe}",
        baseline=baseline,
        strategy_id=spec.setup_family,
    )
    filters = {
        str(key): [str(value) for value in values]
        for key, values in spec.filter_grid.items()
        if values
    }

    symbols = [spec.anchor_symbol, *spec.related_symbols]
    runs = len(symbols) * len(variants)
    job_cap, _ = effective_variant_cap(resource_policy, runs)

    return ExperimentSpec(
        experiment_id=f"sweep_{spec.sweep_id}",
        data_glob=data_glob,
        symbols=symbols,
        families=[spec.setup_family],
        parameter_grid={spec.setup_family: variants},
        fees_bps=fees_bps,
        slippage_bps=slippage_bps,
        min_trades=min_trades,
        max_runs=job_cap,
        event_context=dict(event_context or {}),
        timeframe=spec.timeframe,
        filters=filters,
        backend=spec.backend,
    )
