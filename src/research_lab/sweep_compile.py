# -*- coding: utf-8 -*-
"""Compile a validated SweepSpec into one bounded ExperimentSpec.

This is the bridge between the coarse-sweep contract (SweepSpec) and the existing
deterministic executor (evaluate_spec over ExperimentSpec). It expands the
setup/entry/exit grids into a capped list of parameter variants — never an
unbounded cartesian explosion — and applies the timeframe-profile + resource
policy gates first. No GPU, no execution here.
"""

from __future__ import annotations

import itertools
from typing import Any

from src.research_lab.experiment import ExperimentSpec
from src.research_lab.resource_policy import ResourcePolicy
from src.research_lab.runtime_policy import effective_variant_cap
from src.research_lab.sweep_spec import SweepSpec, validate_sweep_spec
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
    compiler deterministically clips it to the effective cap so a fat grid yields
    a bounded job instead of being rejected.
    """
    result = validate_sweep_spec(
        spec, timeframe_profiles=timeframe_profiles, resource_policy=resource_policy
    )
    blocking = [e for e in result.errors if "variant grid" not in e]
    if blocking:
        raise ValueError("invalid sweep spec: " + "; ".join(blocking))

    variants = expand_grids(spec.setup_grid, spec.entry_grid, spec.exit_grid)
    variants = variants[: max(1, result.effective_max_variants)]
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
    )
