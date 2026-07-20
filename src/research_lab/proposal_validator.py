# -*- coding: utf-8 -*-
"""Validate typed proposals against safety, resource and boundary rules.

A proposal is VALIDATED only if it is compilable, bounded by the resource policy,
references known symbols/families, stays off 1m full sweeps, contains no unsafe
(live-trading / guaranteed-profit) wording, and never points output at the public
repo. Otherwise it is REJECTED with explicit reason codes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.research_lab.data_fingerprint import params_hash
from src.research_lab.experiment import ExperimentSpec
from src.research_lab.paths import PROJECT_ROOT
from src.research_lab.param_schemas import validate_horizon, validate_parameter_grid
from src.research_lab.proposal_schema import REJECTED, VALIDATED, Proposal
from src.research_lab.resource_policy import ResourcePolicy
from src.research_lab.runtime_policy import effective_variant_cap
from src.research_lab.setup_outcome_memory import GateIndex, proposal_verdict
from src.research_lab.strategy_registry import REGISTRY
from src.research_lab.timeframes import TimeframeProfiles
from src.research_lab.universe import Universe

DEFAULT_DATA_GLOB = "scripts/analysis/research/_okxhist/ai_scanner_feasibility/{symbol}_*.json"

_UNSAFE_PHRASES = (
    "guaranteed", "risk-free", "risk free", "live trade", "live-trade",
    "live tradable", "live-tradable", "auto_trade", "autotrade", "auto trade",
    "place order", "order engine", "100% win", "guaranteed profit",
)
_PUBLIC_MARKERS = ("\\trading-bot-v2\\", "/trading-bot-v2/")


@dataclass(frozen=True)
class ProposalOutcome:
    status: str
    reason_codes: list[str] = field(default_factory=list)


def variant_count(proposal: Proposal) -> int:
    grid = proposal.parameter_grid.get(proposal.setup_family) or []
    return max(0, len(proposal.symbols)) * max(0, len(grid))


def compile_proposal(
    proposal: Proposal,
    *,
    policy: ResourcePolicy,
    data_glob: str = DEFAULT_DATA_GLOB,
    data_snapshot_id: str = "",
    data_evidence_hash: str = "",
    data_snapshot_bindings: list[dict] | None = None,
) -> ExperimentSpec:
    """Compile a proposal into a bounded ExperimentSpec (raises if not compilable)."""
    grid = proposal.parameter_grid.get(proposal.setup_family)
    if proposal.setup_family not in REGISTRY:
        raise ValueError(f"unknown setup_family: {proposal.setup_family}")
    if not proposal.symbols or not grid:
        raise ValueError("proposal needs symbols and a parameter grid for its setup_family")
    runs = len(proposal.symbols) * len(grid)
    cap, _ = effective_variant_cap(policy, min(runs, proposal.max_variants) if proposal.max_variants else runs)
    return ExperimentSpec(
        experiment_id=f"proposal_{proposal.proposal_id}",
        data_glob=data_glob,
        symbols=list(proposal.symbols),
        families=[proposal.setup_family],
        parameter_grid={proposal.setup_family: list(grid)},
        filters=dict(proposal.filters),
        max_runs=cap,
        timeframe=proposal.requested_timeframe or "1d",
        data_snapshot_id=data_snapshot_id,
        data_evidence_hash=data_evidence_hash,
        data_snapshot_bindings=list(data_snapshot_bindings or []),
    )


def validate_proposal(
    proposal: Proposal,
    *,
    universe: Universe,
    timeframe_profiles: TimeframeProfiles,
    resource_policy: ResourcePolicy,
    memory_index: GateIndex | None = None,
) -> ProposalOutcome:
    reasons: list[str] = []
    if not proposal.hypothesis.strip():
        reasons.append("missing_hypothesis")
    if proposal.setup_family not in REGISTRY:
        reasons.append("unknown_family")
    else:
        grid = proposal.parameter_grid.get(proposal.setup_family) or []
        param_result = validate_parameter_grid(
            proposal.setup_family,
            list(grid),
            require_executable=True,
        )
        reasons.extend(param_result.errors)

    unknown = [s for s in proposal.symbols if s.upper() not in set(universe.all_symbols())]
    if not proposal.symbols or unknown:
        reasons.append("unknown_symbol")

    _timeframe_reasons(proposal, timeframe_profiles, reasons)
    _resource_reasons(proposal, resource_policy, reasons)
    _wording_and_boundary_reasons(proposal, reasons)
    _horizon_reasons(proposal, reasons)
    _memory_reasons(proposal, memory_index, reasons)

    try:
        compile_proposal(proposal, policy=resource_policy)
    except ValueError:
        if "not_compilable" not in reasons:
            reasons.append("not_compilable")

    deduped = sorted(set(reasons))
    return ProposalOutcome(VALIDATED if not deduped else REJECTED, deduped)


def validate_and_mark(
    proposal: Proposal,
    *,
    universe: Universe,
    timeframe_profiles: TimeframeProfiles,
    resource_policy: ResourcePolicy,
    memory_index: GateIndex | None = None,
) -> Proposal:
    """Validate and return a copy with status set to VALIDATED or REJECTED."""
    outcome = validate_proposal(
        proposal, universe=universe, timeframe_profiles=timeframe_profiles,
        resource_policy=resource_policy, memory_index=memory_index,
    )
    if outcome.status == VALIDATED:
        return replace(proposal, status=VALIDATED, rejection_reason="")
    return replace(proposal, status=REJECTED, rejection_reason=",".join(outcome.reason_codes))


def _horizon_reasons(proposal: Proposal, reasons: list[str]) -> None:
    """Reject a grid variant whose hold_bars is outside the timeframe's holding-horizon band."""
    if proposal.setup_family not in REGISTRY:
        return
    for variant in proposal.parameter_grid.get(proposal.setup_family) or []:
        if validate_horizon(proposal.requested_timeframe, variant):
            reasons.append("wrong_horizon")
            return


def _memory_reasons(proposal: Proposal, memory_index: GateIndex | None, reasons: list[str]) -> None:
    """Reject only when EVERY symbol x variant the proposal would run is already known-bad in the
    Setup Outcome Memory (nothing fresh to learn). A single fresh variant keeps the proposal alive."""
    if memory_index is None or proposal.setup_family not in REGISTRY:
        return
    grid = proposal.parameter_grid.get(proposal.setup_family) or []
    if not proposal.symbols or not grid:
        return
    checked = 0
    known_bad = 0
    for sym in proposal.symbols:
        for variant in grid:
            checked += 1
            verdict = proposal_verdict(memory_index, symbol=str(sym).replace("-", "_").upper(),
                                       timeframe=proposal.requested_timeframe, family=proposal.setup_family,
                                       params_hash=params_hash(variant))
            if verdict.action == "known_bad":
                known_bad += 1
    if checked and known_bad == checked:
        reasons.append("known_bad_in_memory")


def _timeframe_reasons(proposal: Proposal, profiles: TimeframeProfiles, reasons: list[str]) -> None:
    tf = proposal.requested_timeframe.lower()
    if tf == "1m":
        reasons.append("one_minute_full_sweep_blocked")
        return
    try:
        profiles.get(proposal.requested_timeframe)
    except ValueError:
        reasons.append("disallowed_timeframe")


def _resource_reasons(proposal: Proposal, policy: ResourcePolicy, reasons: list[str]) -> None:
    cap = policy.max_variants_per_job
    if proposal.max_variants > cap or variant_count(proposal) > cap:
        reasons.append("too_many_variants")
    if "heavy" in {r.lower() for r in proposal.risk_flags} and not policy.allow_heavy_jobs:
        reasons.append("heavy_job_not_allowed")


def _wording_and_boundary_reasons(proposal: Proposal, reasons: list[str]) -> None:
    text = " ".join([proposal.hypothesis, proposal.private_notes, proposal.expected_validation]).lower()
    if any(phrase in text for phrase in _UNSAFE_PHRASES):
        reasons.append("unsafe_wording")
    project = str(PROJECT_ROOT).replace("\\", "/").lower()
    blob = " ".join(
        [proposal.hypothesis, proposal.private_notes, proposal.expected_validation]
    ).replace("\\", "/").lower()
    if project in blob or any(marker.replace("\\", "/") in blob for marker in _PUBLIC_MARKERS):
        reasons.append("output_boundary_violation")
