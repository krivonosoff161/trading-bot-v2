# -*- coding: utf-8 -*-
"""Turn hard-validation feedback recommendations into bounded next-step plans.

The feedback reader produces farm recommendations (PROMOTE / SUPPRESS / ...).
This module decides, deterministically and safely, what each recommendation
becomes:

    NARROW_PARAMS   -> a bounded follow-up SweepSpec (narrower grid around the
                       candidate's own params) that the worker can run.
    WIDEN_PARAMS    -> a follow-up SweepSpec ONLY if every widened axis has a
                       hard cap; otherwise a note (widen_unsafe_no_cap).
    REGIME_SWEEP    -> a bounded follow-up SweepSpec filtered to the candidate's
                       stored strong regime bucket, when evidence is available.
    REQUIRE_MORE_DATA -> a data-requirement / prepare hint note (no sweep).
    PROMOTE         -> a forward-paper tracking note (no queue, not main-engine ready).
    SUPPRESS/REJECT -> a suppress/archive note (no queue).

It is pure: it builds plans and SweepSpecs from a recommendation + the
candidate's stored params. It never queues, writes, validates against the
resource policy, or touches live trading -- the CLI does the deterministic
validation + bounded queueing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.research_lab import feedback_reader as fr
from src.research_lab.data_inventory import normalize_timeframe
from src.research_lab.scanner_bridge import MAX_VARIANTS_CAP
from src.research_lab.param_schemas import AdaptiveAxis, parameter_search_contract
from src.research_lab.strategy_registry import REGISTRY, get_strategy
from src.research_lab.sweep_spec import SweepSpec

# Actions that may produce a queued follow-up sweep.
QUEUEABLE_ACTIONS = {fr.NARROW_PARAMS, fr.WIDEN_PARAMS, fr.REGIME_SWEEP}
# A safe absolute ceiling for any widened axis (multiple of the family default).
@dataclass(frozen=True)
class FollowupPlan:
    """One decided next step for a recommendation."""

    action: str
    candidate_id: str
    symbol: str
    strategy_id: str
    timeframe: str
    queued: bool
    not_queued_reason: str | None
    note: str
    sweep: SweepSpec | None = None
    grid_preview: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "candidate_id": self.candidate_id,
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "timeframe": self.timeframe,
            "queued": self.queued,
            "not_queued_reason": self.not_queued_reason,
            "note": self.note,
            "grid_preview": dict(self.grid_preview),
            "sweep_id": self.sweep.sweep_id if self.sweep else None,
        }


def _note_plan(rec: fr.Recommendation, cid: str, *, reason: str, note: str) -> FollowupPlan:
    return FollowupPlan(
        action=rec.action, candidate_id=cid, symbol=rec.symbol, strategy_id=rec.strategy_id,
        timeframe=rec.timeframe, queued=False, not_queued_reason=reason, note=note,
    )


def _axis_levels(axis: AdaptiveAxis, values: set[float]) -> list[Any]:
    bounded = {min(axis.maximum, max(axis.minimum, value)) for value in values}
    if axis.value_type == "int":
        return sorted({int(round(value)) for value in bounded})
    return sorted({round(value, 6) for value in bounded})


def _narrow_grids(family: str, params: dict[str, Any], *, lower_turnover: bool) -> tuple[dict, dict]:
    """Build a tight, deterministic neighborhood around the candidate's params.

    Narrows the family's declared primary axis and hold_bars,
    biasing hold_bars upward for lower-turnover (cost-failure) follow-ups. Pure
    function of the candidate params -> identical input yields an identical grid.
    """
    defaults = dict(get_strategy(family).parameter_defaults)
    contract = parameter_search_contract(family)
    axis = contract.primary_axis
    hb = int(params.get("hold_bars", defaults.get("hold_bars", 5)) or defaults.get("hold_bars", 5))
    setup_grid: dict[str, list[Any]] = {}
    if axis is not None:
        base = float(params.get(axis.name, defaults[axis.name]) or defaults[axis.name])
        setup_grid[axis.name] = _axis_levels(
            axis, {base * factor for factor in contract.followup.narrow_factors}
        )
    if lower_turnover:
        exit_grid = {"hold_bars": sorted({hb, hb + 1, hb + 2})}
    else:
        exit_grid = {"hold_bars": sorted({max(2, hb - 1), hb, hb + 1})}
    return setup_grid, exit_grid


def _widen_grids(family: str, params: dict[str, Any]) -> tuple[dict, dict, bool]:
    """Widen with a hard ceiling. Returns (setup_grid, exit_grid, capped_ok)."""
    defaults = dict(get_strategy(family).parameter_defaults)
    contract = parameter_search_contract(family)
    axis = contract.primary_axis
    hb_def = int(defaults.get("hold_bars", 5))
    hb = int(params.get("hold_bars", hb_def) or hb_def)
    hb_ceiling = hb_def * contract.followup.max_default_multiplier
    if axis is None or hb_ceiling <= 0:
        return ({}, {}, False)
    axis_default = float(defaults[axis.name])
    axis_value = float(params.get(axis.name, axis_default) or axis_default)
    ceiling = axis_default * contract.followup.max_default_multiplier
    setup_grid = {
        axis.name: _axis_levels(
            axis,
            {min(ceiling, axis_value * factor) for factor in contract.followup.widen_factors},
        )
    }
    exit_grid = {"hold_bars": sorted({max(2, hb), min(hb_ceiling, hb + 2)})}
    return (setup_grid, exit_grid, True)


def _candidate_params(candidate_context: dict[str, Any] | None) -> dict[str, Any] | None:
    if candidate_context is None:
        return None
    params = candidate_context.get("params")
    if isinstance(params, dict):
        return dict(params)
    return dict(candidate_context)


def _filters_from_bucket(bucket: str) -> dict[str, list[str]]:
    parts = [p for p in str(bucket or "").split("|") if p and p != "unknown"]
    keys = ("volatility", "trend", "volume")
    return {key: [value] for key, value in zip(keys, parts) if value}


def _regime_filters(rec: fr.Recommendation, candidate_context: dict[str, Any] | None) -> dict[str, list[str]]:
    if candidate_context:
        filters = candidate_context.get("filters")
        if isinstance(filters, dict) and filters:
            return {str(k): [str(x) for x in v] for k, v in filters.items() if v}
        for reason in candidate_context.get("validation_reasons") or []:
            text = str(reason)
            if text.startswith("strong_regime_bucket:"):
                return _filters_from_bucket(text.split(":", 1)[1])
        summary = candidate_context.get("regime_summary")
        if isinstance(summary, dict):
            filters = _filters_from_bucket(str(summary.get("dominant_bucket") or ""))
            if filters:
                return filters
    for reason in rec.reason_codes:
        text = str(reason)
        if text.startswith("strong_regime_bucket:"):
            return _filters_from_bucket(text.split(":", 1)[1])
    return {}


def plan_followup(
    rec: fr.Recommendation,
    candidate_params: dict[str, Any] | None,
    *,
    max_variants: int = 8,
) -> FollowupPlan:
    """Decide the bounded next step for one recommendation."""
    cid = rec.candidate_ids[0] if rec.candidate_ids else ""

    if rec.action == fr.PROMOTE:
        return _note_plan(rec, cid, reason="promote_is_forward_paper_only",
                          note="track in forward-paper log; not main-engine ready; no sweep queued")
    if rec.action in (fr.REJECT, fr.SUPPRESS):
        return _note_plan(rec, cid, reason="suppressed_or_rejected",
                          note="archive / stop spending on this setup; no sweep queued")
    if rec.action == fr.REQUIRE_MORE_DATA:
        return _note_plan(rec, cid, reason="needs_more_data",
                          note=f"collect more history for {rec.symbol}@{rec.timeframe} "
                               f"(prepare_market_data / wait for forward period); no sweep queued")
    if rec.action not in QUEUEABLE_ACTIONS:
        return _note_plan(rec, cid, reason="unknown_action", note=f"no handler for action {rec.action}")

    # Queueable actions need a valid candidate, family, timeframe.
    params = _candidate_params(candidate_params)
    if not cid or params is None:
        return _note_plan(rec, cid, reason="no_candidate_params",
                          note="no stored candidate params to narrow around; not queued")
    tf = normalize_timeframe(rec.timeframe)
    if not tf:
        return _note_plan(rec, cid, reason="unknown_timeframe",
                          note=f"timeframe '{rec.timeframe}' is not a known timeframe; not queued "
                               f"(run repair_hard_validation_metadata)")
    if rec.strategy_id not in REGISTRY:
        return _note_plan(rec, cid, reason="unknown_strategy",
                          note=f"strategy '{rec.strategy_id}' not in registry; not queued")
    lineage = dict(candidate_params or {})
    nested = lineage.get("params")
    if isinstance(nested, dict):
        lineage = {**nested, **lineage}
    parent_family_id = str(lineage.get("search_family_id") or "")
    parent_trial_id = str(lineage.get("search_trial_id") or "")
    parent_effective_n_trials = int(
        lineage.get("effective_n_trials") or 0
    )
    if not (parent_family_id and parent_trial_id and parent_effective_n_trials > 0):
        return _note_plan(
            rec,
            cid,
            reason="unbound_parent_search_family",
            note="follow-up requires verified parent family/trial accounting; legacy context not queued",
        )

    filter_grid: dict[str, list[str]] = {}
    if rec.action == fr.REGIME_SWEEP:
        filter_grid = _regime_filters(rec, candidate_params)
        if not filter_grid:
            return _note_plan(rec, cid, reason="missing_regime_filter",
                              note="REGIME_SWEEP needs a stored strong_regime_bucket/filter; not queued")
        setup_grid, exit_grid = _narrow_grids(rec.strategy_id, params, lower_turnover=False)
        note = "bounded regime-filtered follow-up around candidate params"
    elif rec.action == fr.WIDEN_PARAMS:
        setup_grid, exit_grid, ok = _widen_grids(rec.strategy_id, params)
        if not ok:
            return _note_plan(rec, cid, reason="widen_unsafe_no_cap",
                              note="cannot derive a safe widening ceiling; not queued")
        note = "bounded widen follow-up (hard-capped axes)"
    else:  # NARROW_PARAMS
        lower_turnover = rec.hard_status in ("FAILED_COSTS",)
        setup_grid, exit_grid = _narrow_grids(rec.strategy_id, params, lower_turnover=lower_turnover)
        note = "bounded narrow follow-up around candidate params"

    capped_variants = min(max(1, int(max_variants)), MAX_VARIANTS_CAP)
    sweep = SweepSpec(
        sweep_id=f"fb_{cid}_{rec.strategy_id}_{rec.action.lower()}",
        anchor_symbol=rec.symbol,
        related_symbols=(),
        timeframe=tf,
        setup_family=rec.strategy_id,
        setup_grid=setup_grid,
        exit_grid=exit_grid,
        filter_grid=filter_grid,
        max_variants=capped_variants,
        backend="cpu",
        resource_class="normal",
        private_output_policy="private_only",
        parent_family_id=parent_family_id,
        parent_trial_id=parent_trial_id,
        parent_effective_n_trials=parent_effective_n_trials,
        cumulative_family_policy="cumulative",
    )
    return FollowupPlan(
        action=rec.action, candidate_id=cid, symbol=rec.symbol, strategy_id=rec.strategy_id,
        timeframe=tf, queued=True, not_queued_reason=None, note=note, sweep=sweep,
        grid_preview={"setup_grid": setup_grid, "exit_grid": exit_grid, "filter_grid": filter_grid},
    )


def plan_followups(
    recs: list[fr.Recommendation],
    params_by_candidate: dict[str, dict[str, Any]],
    *,
    max_recommendations: int = 10,
    max_variants: int = 8,
    max_symbols: int = 5,
    allowed_actions: set[str] | None = None,
) -> list[FollowupPlan]:
    """Plan bounded next steps for a capped set of recommendations.

    Caps: at most ``max_recommendations`` processed; queued sweeps limited to
    ``max_symbols`` distinct symbols; only ``allowed_actions`` may queue.
    """
    allowed = allowed_actions if allowed_actions is not None else set(QUEUEABLE_ACTIONS)
    plans: list[FollowupPlan] = []
    queued_symbols: set[str] = set()
    for rec in recs[: max(0, int(max_recommendations))]:
        cid = rec.candidate_ids[0] if rec.candidate_ids else ""
        plan = plan_followup(rec, params_by_candidate.get(cid), max_variants=max_variants)
        if plan.queued:
            if plan.action not in allowed:
                plan = _note_plan(rec, cid, reason="action_not_allowed",
                                  note=f"action {plan.action} disabled by --allowed-actions")
            elif rec.symbol.upper() not in queued_symbols and len(queued_symbols) >= max(1, int(max_symbols)):
                plan = _note_plan(rec, cid, reason="symbol_cap_reached",
                                  note="max follow-up symbols reached; not queued")
            else:
                queued_symbols.add(rec.symbol.upper())
        plans.append(plan)
    return plans
