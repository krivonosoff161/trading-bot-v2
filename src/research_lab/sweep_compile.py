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
from typing import Any, Callable

from src.research_lab.experiment import ExperimentSpec
from src.research_lab.param_schemas import (
    PARAMETER_SEARCH_CONTRACT_VERSION,
    SAMPLER_VERSION,
    executable_exit_params,
    load_param_policy,
    search_variant_validity,
    validate_params,
)
from src.research_lab.resource_policy import ResourcePolicy
from src.research_lab.runtime_policy import effective_variant_cap
from src.research_lab.search_family_definition import (
    POINT_LEDGER_ALGORITHM,
    POINT_LEDGER_SCHEMA,
    build_sweep_family_definition,
    canonical_bytes,
)
from src.research_lab.sweep_spec import SweepSpec, validate_sweep_spec
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.timeframes import TimeframeProfiles


ProgressCallback = Callable[[str], None]
PROGRESS_CHUNK_SIZE = 1_024


def _append_point_digest(digest: Any, point: dict[str, Any]) -> None:
    payload = canonical_bytes(point)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def _compact_point_ledger(
    points: list[dict[str, Any]],
) -> dict[str, Any]:
    digest = hashlib.sha256()
    dispositions: dict[str, int] = {}
    reasons: dict[str, int] = {}
    selected: list[dict[str, Any]] = []
    for point in points:
        _append_point_digest(digest, point)
        disposition = str(point["pre_disposition"])
        reason = str(point["reason"])
        dispositions[disposition] = dispositions.get(disposition, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1
        if disposition == "selected":
            selected.append(point)
    return {
        "schema": POINT_LEDGER_SCHEMA,
        "algorithm": POINT_LEDGER_ALGORITHM,
        "record_count": len(points),
        "sha256": digest.hexdigest(),
        "disposition_counts": dict(sorted(dispositions.items())),
        "reason_counts": dict(sorted(reasons.items())),
        "selected_points": selected,
    }


def _completed_chunk_progress(
    progress: ProgressCallback | None,
    stage: str,
    completed: int,
    total: int,
    *,
    chunk_size: int,
) -> None:
    if progress is not None and (completed == total or completed % chunk_size == 0):
        progress(f"{stage}:{completed}/{total}")


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
    return [
        dict(zip(keys, combo))
        for combo in itertools.product(*(merged[k] for k in keys))
    ]


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
    *grids: dict[str, list[Any]],
    cap: int,
    seed_material: str,
    baseline: dict[str, Any] | None = None,
    strategy_id: str = "",
    audit: dict[str, Any] | None = None,
    audit_format: str = "compact",
    progress: ProgressCallback | None = None,
    progress_chunk_size: int = PROGRESS_CHUNK_SIZE,
) -> list[dict[str, Any]]:
    """Sample a Cartesian grid without materializing the full search space."""
    if audit_format not in {"compact", "legacy_full"}:
        raise ValueError("unknown search-space audit format")
    merged: dict[str, list[Any]] = {}
    for grid in grids:
        for key, values in grid.items():
            if values:
                merged[key] = list(values)
    if not merged:
        if strategy_id:
            defaults = dict(get_strategy(strategy_id).parameter_defaults)
            schema = validate_params(strategy_id, defaults)
            valid, reason = search_variant_validity(strategy_id, defaults)
            if not schema.ok or not valid:
                detail = ";".join(schema.errors) if not schema.ok else reason
                raise ValueError(f"baseline parameters are invalid: {detail}")
        if audit is not None:
            baseline_points = [
                {
                    "flat_index": 0,
                    "params": {},
                    "pre_disposition": "selected",
                    "reason": "selected_by_sampler",
                }
            ]
            audit.update(
                {
                    "axis_order": [],
                    "cartesian_total": 1,
                    "eligible_total": 1,
                    "selected_total": 1,
                    "omitted_invalid": 0,
                    "omitted_by_variant_cap": 0,
                    "selected_flat_indices": [0],
                    **(
                        {"points": baseline_points}
                        if audit_format == "legacy_full"
                        else {
                            "point_ledger": _compact_point_ledger(
                                baseline_points
                            )
                        }
                    ),
                }
            )
        return [{}]
    keys = sorted(merged)
    axes = [merged[key] for key in keys]
    total = math.prod(len(axis) for axis in axes)
    chunk_size = max(1, int(progress_chunk_size))

    def decode(flat_index: int) -> dict[str, Any]:
        cursor = flat_index
        values: list[Any] = [None] * len(axes)
        for pos in range(len(axes) - 1, -1, -1):
            cursor, offset = divmod(cursor, len(axes[pos]))
            values[pos] = axes[pos][offset]
        return dict(zip(keys, values))

    invalid: dict[int, tuple[str, str]] = {}
    if not strategy_id:
        # Keep the low-level sampler usable for very large synthetic Cartesian
        # spaces without materializing range(total). Complete point ledgers are
        # created by compile_sweep, which always supplies a strategy id + audit.
        valid_indices: Any = range(total)
    else:
        valid_indices = []
        strategy_defaults = dict(get_strategy(strategy_id).parameter_defaults)
        parameter_policy = load_param_policy()
        for index in range(total):
            params = decode(index)
            complete_params = {**strategy_defaults, **params}
            schema = validate_params(
                strategy_id, complete_params, policy=parameter_policy
            )
            if not schema.ok:
                invalid[index] = ("schema_invalid", ";".join(schema.errors))
            else:
                valid, reason = search_variant_validity(strategy_id, complete_params)
                if not valid:
                    invalid[index] = ("dependency_invalid", reason)
                else:
                    valid_indices.append(index)
            _completed_chunk_progress(
                progress,
                "grid_validation",
                index + 1,
                total,
                chunk_size=chunk_size,
            )
    if not valid_indices:
        raise ValueError(
            "parameter grid has no variants satisfying cross-axis dependencies"
        )
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
        mandatory: list[int] = (
            [baseline_index] if baseline_index in valid_indices else []
        )
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
    for selected_index in indices:
        variants.append(decode(selected_index))
    if audit is not None:
        selected = set(indices)
        points: list[dict[str, Any]] = []
        ledger_digest = hashlib.sha256()
        disposition_counts: dict[str, int] = {}
        reason_counts: dict[str, int] = {}
        selected_points: list[dict[str, Any]] = []
        for candidate_index in range(total):
            params = decode(candidate_index)
            if candidate_index in invalid:
                disposition, reason = invalid[candidate_index]
            elif candidate_index in selected:
                disposition, reason = "selected", "selected_by_sampler"
            else:
                disposition, reason = (
                    "omitted_variant_cap",
                    "eligible_not_selected_by_cap",
                )
            point = {
                "flat_index": candidate_index,
                "params": params,
                "pre_disposition": disposition,
                "reason": reason,
            }
            if audit_format == "legacy_full":
                points.append(point)
            else:
                _append_point_digest(ledger_digest, point)
                disposition_counts[disposition] = (
                    disposition_counts.get(disposition, 0) + 1
                )
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
                if disposition == "selected":
                    selected_points.append(point)
            _completed_chunk_progress(
                progress,
                "grid_ledger",
                candidate_index + 1,
                total,
                chunk_size=chunk_size,
            )
        audit.update(
            {
                "axis_order": keys,
                "cartesian_total": int(total),
                "eligible_total": len(valid_indices),
                "selected_total": int(limit),
                "omitted_invalid": int(total - len(valid_indices)),
                "omitted_by_variant_cap": int(len(valid_indices) - limit),
                "selected_flat_indices": list(indices),
                **(
                    {"points": points}
                    if audit_format == "legacy_full"
                    else {
                        "point_ledger": {
                            "schema": POINT_LEDGER_SCHEMA,
                            "algorithm": POINT_LEDGER_ALGORITHM,
                            "record_count": int(total),
                            "sha256": ledger_digest.hexdigest(),
                            "disposition_counts": dict(
                                sorted(disposition_counts.items())
                            ),
                            "reason_counts": dict(sorted(reason_counts.items())),
                            "selected_points": selected_points,
                        }
                    }
                ),
            }
        )
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
    data_snapshot_id: str = "",
    data_evidence_hash: str = "",
    data_snapshot_bindings: list[dict[str, Any]] | None = None,
    progress: ProgressCallback | None = None,
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
    if progress is not None:
        progress("compile_spec_validated")

    defaults = executable_exit_params(
        spec.setup_family, get_strategy(spec.setup_family).parameter_defaults
    )
    baseline = {
        key: defaults.get(key, "baseline" if key == "exit_mode" else values[0])
        for grid in (spec.setup_grid, spec.entry_grid, spec.exit_grid)
        for key, values in grid.items()
        if values
    }
    search_space: dict[str, Any] = {}
    variants = expand_grids_bounded(
        spec.setup_grid,
        spec.entry_grid,
        spec.exit_grid,
        cap=result.effective_max_variants,
        seed_material=f"{spec.sweep_id}|{spec.setup_family}|{spec.timeframe}",
        baseline=baseline,
        strategy_id=spec.setup_family,
        audit=search_space,
        progress=progress,
    )
    if progress is not None:
        progress("compile_grid_selected")
    filters = {
        str(key): [str(value) for value in values]
        for key, values in spec.filter_grid.items()
        if values
    }

    symbols = [spec.anchor_symbol, *spec.related_symbols]
    runs = len(symbols) * len(variants)
    job_cap, _ = effective_variant_cap(resource_policy, runs)

    family_definition, family_id = build_sweep_family_definition(
        spec,
        symbols=symbols,
        filters=filters,
        search_space=search_space,
        effective_max_variants=result.effective_max_variants,
        execution_cap=job_cap,
        seed_material=f"{spec.sweep_id}|{spec.setup_family}|{spec.timeframe}",
        sampler_version=SAMPLER_VERSION,
        validity_version=PARAMETER_SEARCH_CONTRACT_VERSION,
        resource_policy_contract=resource_policy,
        timeframe_profile=timeframe_profiles.get(spec.timeframe),
        data_snapshot_id=data_snapshot_id,
        data_evidence_hash=data_evidence_hash,
        data_snapshot_bindings=data_snapshot_bindings,
        progress=progress,
    )
    if progress is not None:
        progress("compile_family_bound")

    plan_search_space = dict(search_space)
    if isinstance(plan_search_space.get("point_ledger"), dict):
        plan_ledger = dict(plan_search_space["point_ledger"])
        plan_ledger.pop("selected_points", None)
        plan_search_space["point_ledger"] = plan_ledger

    experiment = ExperimentSpec(
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
        data_snapshot_id=str(data_snapshot_id or ""),
        data_evidence_hash=str(data_evidence_hash or ""),
        data_snapshot_bindings=list(data_snapshot_bindings or []),
        search_family_definition=family_definition,
        search_family_id=family_id,
        plan_meta={
            "search_space": {
                **plan_search_space,
                "symbol_total": len(symbols),
                "selected_run_total": runs,
                "execution_cap": int(job_cap),
                "omitted_by_execution_cap": max(0, runs - int(job_cap)),
            }
        },
        validation_progress=progress,
    )
    if progress is not None:
        progress("compile_experiment_bound")
    return experiment
