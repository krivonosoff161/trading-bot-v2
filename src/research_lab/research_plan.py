# -*- coding: utf-8 -*-
"""Deterministic research-plan builder: universe + timeframe -> experiment specs.

Turns "test this universe group on this timeframe" into bounded ExperimentSpec
jobs the existing worker can run. It is pure (no DB, no disk): it selects
symbols, attaches relation hints and the timeframe role, picks timeframe-
compatible strategy families, and caps variants with the resource policy. The
CLI (scripts/strategy_lab/enqueue_research_plan.py) handles dry-run/apply and
queueing. No private results are produced here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from src.research_lab.resource_policy import ResourcePolicy
from src.research_lab.runtime_policy import effective_variant_cap
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.timeframes import TimeframeProfiles
from src.research_lab.universe import Universe

DEFAULT_FAMILIES = (
    "momentum_breakout",
    "donchian_breakout",
    "mean_reversion_fade",
    "rsi_reversal",
    "trend_pullback",
    "moving_average_reclaim",
)
SMOKE_SYMBOLS = 3
SMOKE_FAMILIES = 3
_TF_TO_REGISTRY = {"1d": "1D", "1h": "1H", "15m": "15m", "4h": "4H", "1m": "1m"}


@dataclass(frozen=True)
class SkippedSymbol:
    symbol: str
    reason: str


@dataclass(frozen=True)
class PlannedJob:
    experiment_id: str
    group: str
    timeframe: str
    role: str
    symbols: tuple[str, ...]
    families: tuple[str, ...]
    variant_count: int
    output_label: str
    spec: dict[str, Any]


@dataclass(frozen=True)
class ResearchPlan:
    group: str
    timeframe: str
    jobs: list[PlannedJob] = field(default_factory=list)
    skipped: list[SkippedSymbol] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "timeframe": self.timeframe,
            "jobs": [asdict(j) for j in self.jobs],
            "skipped": [asdict(s) for s in self.skipped],
        }


def usable_symbols(inventory: dict[str, Any] | None) -> set[str]:
    if not inventory:
        return set()
    return {
        str(row.get("symbol", "")).upper()
        for row in inventory.get("rows", [])
        if row.get("quality_status") == "usable"
    }


def compatible_families(timeframe: str, requested: tuple[str, ...]) -> list[str]:
    tf = _TF_TO_REGISTRY.get(timeframe.lower(), timeframe)
    out = []
    for fam in requested:
        if tf in get_strategy(fam).compatible_timeframes:
            out.append(fam)
    return out


def build_research_plan(
    universe: Universe,
    profiles: TimeframeProfiles,
    policy: ResourcePolicy,
    *,
    group: str,
    timeframe: str,
    data_glob: str,
    families: tuple[str, ...] = DEFAULT_FAMILIES,
    inventory: dict[str, Any] | None = None,
    smoke: bool = True,
) -> ResearchPlan:
    profile = profiles.get(timeframe)
    group_symbols = universe.symbols_in(group)
    if not group_symbols:
        raise ValueError(f"unknown or empty universe group: {group}")

    fams = compatible_families(timeframe, families)
    if smoke:
        fams = fams[:SMOKE_FAMILIES]
    if not fams:
        return ResearchPlan(group=group, timeframe=timeframe, skipped=[
            SkippedSymbol(s, "no_timeframe_compatible_family") for s in group_symbols
        ])

    selected, skipped = _select_symbols(group_symbols, profile, inventory, smoke)
    if not selected:
        return ResearchPlan(group=group, timeframe=timeframe, skipped=skipped)

    job = _build_job(universe, policy, group, timeframe, profile.role, selected, fams, data_glob)
    return ResearchPlan(group=group, timeframe=timeframe, jobs=[job], skipped=skipped)


def _select_symbols(
    group_symbols: tuple[str, ...],
    profile: Any,
    inventory: dict[str, Any] | None,
    smoke: bool,
) -> tuple[list[str], list[SkippedSymbol]]:
    usable = usable_symbols(inventory)
    cap = min(profile.max_symbols_per_cycle, SMOKE_SYMBOLS) if smoke else profile.max_symbols_per_cycle
    selected: list[str] = []
    skipped: list[SkippedSymbol] = []
    for symbol in group_symbols:
        if inventory is not None and symbol.upper() not in usable:
            skipped.append(SkippedSymbol(symbol, "no_usable_data"))
            continue
        if len(selected) < cap:
            selected.append(symbol)
        else:
            skipped.append(SkippedSymbol(symbol, "over_symbol_cap"))
    return selected, skipped


def _build_job(
    universe: Universe,
    policy: ResourcePolicy,
    group: str,
    timeframe: str,
    role: str,
    symbols: list[str],
    families: list[str],
    data_glob: str,
) -> PlannedJob:
    grid = {fam: [dict(get_strategy(fam).parameter_defaults)] for fam in families}
    raw_variants = len(symbols) * sum(len(v) for v in grid.values())
    cap, _ = effective_variant_cap(policy, raw_variants)
    variant_count = min(raw_variants, cap)
    plan_meta = {
        "group": group,
        "timeframe": timeframe,
        "timeframe_role": role,
        "relation_hints": {s: list(universe.related(s)) for s in symbols},
        "note": "auto-generated research plan; research labels only, not a profitability claim",
    }
    digest = _short_hash({"group": group, "tf": timeframe, "symbols": symbols, "families": families})
    experiment_id = f"plan_{group}_{timeframe}_{digest}"
    spec = {
        "experiment_id": experiment_id,
        "data_glob": data_glob,
        "symbols": symbols,
        "families": families,
        "fees_bps": 7.0,
        "slippage_bps": 3.0,
        "min_trades": 20,
        "split_ratio": 0.7,
        "max_runs": cap,
        "parameter_grid": grid,
        "plan_meta": plan_meta,
    }
    return PlannedJob(
        experiment_id=experiment_id,
        group=group,
        timeframe=timeframe,
        role=role,
        symbols=tuple(symbols),
        families=tuple(families),
        variant_count=variant_count,
        output_label="experiments/completed/<timestamp>_" + experiment_id,
        spec=spec,
    )


def _short_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
