# -*- coding: utf-8 -*-
"""Coarse-sweep spec: a public, testable schema for a future sweep.

This describes WHAT a coarse sweep would run (anchor, related symbols, timeframe,
setup/entry/exit/filter grids, variant budget, backend, resource class) and
validates it against the timeframe profiles and resource policy. It does NOT run
anything; execution happens after compilation through ExperimentSpec/evaluate_spec.
The goal is to make the architecture explicit and enforce the desktop-safety gates
up front.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.paths import PROJECT_ROOT
from src.research_lab.resource_policy import ResourcePolicy
from src.research_lab.timeframes import TimeframeProfiles

BACKENDS = ("cpu", "gpu", "auto")
RESOURCE_CLASSES = ("light", "normal", "heavy", "event_1m")
PRIVATE_OUTPUT_TOKENS = ("private_only", "private_registry")

Grid = dict[str, list[Any]]


@dataclass(frozen=True)
class SweepSpec:
    sweep_id: str
    anchor_symbol: str
    related_symbols: tuple[str, ...]
    timeframe: str
    setup_family: str
    setup_grid: Grid = field(default_factory=dict)
    entry_grid: Grid = field(default_factory=dict)
    exit_grid: Grid = field(default_factory=dict)
    filter_grid: Grid = field(default_factory=dict)
    max_variants: int = 16
    backend: str = "cpu"
    resource_class: str = "normal"
    private_output_policy: str = "private_only"

    def symbol_scope(self) -> int:
        return 1 + len(self.related_symbols)

    def variant_count(self) -> int:
        total = 1
        for grid in (self.setup_grid, self.entry_grid, self.exit_grid):
            for values in grid.values():
                if values:
                    total *= len(values)
        return total


@dataclass(frozen=True)
class SweepValidation:
    ok: bool
    errors: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    effective_max_variants: int = 0


def validate_sweep_spec(
    spec: SweepSpec,
    *,
    timeframe_profiles: TimeframeProfiles,
    resource_policy: ResourcePolicy,
) -> SweepValidation:
    """Validate a sweep against timeframe limits and the resource policy."""
    errors: list[str] = []
    notes: list[str] = []

    if spec.backend not in BACKENDS:
        errors.append(f"unknown backend '{spec.backend}' (allowed: {', '.join(BACKENDS)})")
    elif spec.backend == "gpu":
        # Capability-checked, not blanket-rejected. 'gpu' requires a real GPU
        # backend; if absent it is an error here (never a silent CPU run). 'auto'
        # is always allowed and resolves to CPU at runtime with a recorded reason.
        from src.research_lab.gpu_runtime import detect_gpu
        cap = detect_gpu()
        if not cap.gpu_available:
            errors.append(f"backend 'gpu' requested but unavailable: {cap.reason_if_unavailable}")
    if spec.resource_class not in RESOURCE_CLASSES:
        errors.append(f"unknown resource_class '{spec.resource_class}'")

    profile = None
    try:
        profile = timeframe_profiles.get(spec.timeframe)
    except ValueError as exc:
        errors.append(str(exc))

    effective_max = max(1, int(spec.max_variants))
    if profile is not None:
        effective_max = _check_variants(spec, profile.max_variants_per_setup, errors, notes)
        _check_timeframe_gates(spec, profile, resource_policy, errors)

    _check_resource_gates(spec, resource_policy, errors)
    _check_output_policy(spec, errors)

    return SweepValidation(
        ok=not errors,
        errors=tuple(errors),
        notes=tuple(notes),
        effective_max_variants=effective_max,
    )


def ensure_valid(
    spec: SweepSpec,
    *,
    timeframe_profiles: TimeframeProfiles,
    resource_policy: ResourcePolicy,
) -> SweepValidation:
    result = validate_sweep_spec(
        spec, timeframe_profiles=timeframe_profiles, resource_policy=resource_policy
    )
    if not result.ok:
        raise ValueError("invalid sweep spec: " + "; ".join(result.errors))
    return result


def _check_variants(spec: SweepSpec, profile_cap: int, errors: list[str], notes: list[str]) -> int:
    effective = min(max(1, int(spec.max_variants)), int(profile_cap))
    if spec.max_variants > profile_cap:
        notes.append(f"max_variants clipped from {spec.max_variants} to {effective} by timeframe profile")
    grid = spec.variant_count()
    if grid > effective:
        errors.append(f"variant grid ({grid}) exceeds max_variants ({effective})")
    return effective


def _check_timeframe_gates(
    spec: SweepSpec, profile: Any, resource_policy: ResourcePolicy, errors: list[str]
) -> None:
    if not profile.trigger_only:
        return
    # Trigger-only timeframe (the 1m event microscope): tightly scoped only.
    if spec.resource_class != "event_1m":
        errors.append("1m/trigger-only timeframe requires resource_class 'event_1m'")
    if spec.symbol_scope() > profile.max_symbols_per_cycle:
        errors.append(
            f"1m scope {spec.symbol_scope()} exceeds max_symbols_per_cycle "
            f"{profile.max_symbols_per_cycle} (trigger-only microscope, not full-universe)"
        )
    if not resource_policy.allows_1m(spec.resource_class):
        errors.append("resource policy does not allow 1m jobs in this mode")


def _check_resource_gates(spec: SweepSpec, resource_policy: ResourcePolicy, errors: list[str]) -> None:
    if spec.resource_class == "heavy" and not resource_policy.allow_heavy_jobs:
        errors.append(f"heavy job rejected under resource policy mode '{resource_policy.mode}'")
    if spec.resource_class == "event_1m" and not resource_policy.allows_1m("event_1m"):
        errors.append("event_1m job rejected by resource policy")


def _check_output_policy(spec: SweepSpec, errors: list[str]) -> None:
    value = str(spec.private_output_policy or "").strip()
    if not value:
        errors.append("private_output_policy must be set")
        return
    if value in PRIVATE_OUTPUT_TOKENS:
        return
    try:
        resolved = Path(value).expanduser().resolve()
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return  # path resolves outside the public repo -> acceptable private target
    errors.append("private_output_policy points inside the public repo (must stay private)")
