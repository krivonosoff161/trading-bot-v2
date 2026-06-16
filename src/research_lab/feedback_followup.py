# -*- coding: utf-8 -*-
"""Turn hard-validation feedback recommendations into bounded next-step plans.

The feedback reader produces farm recommendations (PROMOTE / SUPPRESS / ...).
This module decides, deterministically and safely, what each recommendation
becomes:

    NARROW_PARAMS   -> a bounded follow-up SweepSpec (narrower grid around the
                       candidate's own params) that the worker can run.
    WIDEN_PARAMS    -> a follow-up SweepSpec ONLY if every widened axis has a
                       hard cap; otherwise a note (widen_unsafe_no_cap).
    REGIME_SWEEP    -> a note only: the sweep/compile path cannot apply a regime
                       filter today (not_queued_reason=regime_filter_not_implemented).
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
from src.research_lab.strategy_registry import REGISTRY, get_strategy
from src.research_lab.sweep_spec import SweepSpec

# Actions that may produce a queued follow-up sweep.
QUEUEABLE_ACTIONS = {fr.NARROW_PARAMS, fr.WIDEN_PARAMS}
# A safe absolute ceiling for any widened axis (multiple of the family default).
WIDEN_CEILING_MULT = 2


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


def _narrow_grids(family: str, params: dict[str, Any], *, lower_turnover: bool) -> tuple[dict, dict]:
    """Build a tight, deterministic neighborhood around the candidate's params.

    Narrows lookback and hold_bars only (the axes every sweep family exposes),
    biasing hold_bars upward for lower-turnover (cost-failure) follow-ups. Pure
    function of the candidate params -> identical input yields an identical grid.
    """
    defaults = dict(get_strategy(family).parameter_defaults)
    lb = int(params.get("lookback", defaults.get("lookback", 20)) or defaults.get("lookback", 20))
    hb = int(params.get("hold_bars", defaults.get("hold_bars", 5)) or defaults.get("hold_bars", 5))
    setup_grid = {"lookback": sorted({max(2, lb), max(2, lb + max(1, lb // 4))})}
    if lower_turnover:
        exit_grid = {"hold_bars": sorted({hb, hb + 1, hb + 2})}
    else:
        exit_grid = {"hold_bars": sorted({max(2, hb - 1), hb, hb + 1})}
    return setup_grid, exit_grid


def _widen_grids(family: str, params: dict[str, Any]) -> tuple[dict, dict, bool]:
    """Widen with a hard ceiling. Returns (setup_grid, exit_grid, capped_ok)."""
    defaults = dict(get_strategy(family).parameter_defaults)
    lb_def = int(defaults.get("lookback", 20))
    hb_def = int(defaults.get("hold_bars", 5))
    lb = int(params.get("lookback", lb_def) or lb_def)
    hb = int(params.get("hold_bars", hb_def) or hb_def)
    lb_ceiling = lb_def * WIDEN_CEILING_MULT
    hb_ceiling = hb_def * WIDEN_CEILING_MULT
    if lb_ceiling <= 0 or hb_ceiling <= 0:
        return ({}, {}, False)
    setup_grid = {"lookback": sorted({max(2, lb), min(lb_ceiling, lb * 2)})}
    exit_grid = {"hold_bars": sorted({max(2, hb), min(hb_ceiling, hb + 2)})}
    return (setup_grid, exit_grid, True)


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
    if rec.action == fr.REGIME_SWEEP:
        return _note_plan(rec, cid, reason="regime_filter_not_implemented",
                          note="regime-filtered sweep cannot be compiled today "
                               "(compile_sweep does not forward filters); recorded as TODO, not queued")

    if rec.action not in QUEUEABLE_ACTIONS:
        return _note_plan(rec, cid, reason="unknown_action", note=f"no handler for action {rec.action}")

    # Queueable: NARROW_PARAMS / WIDEN_PARAMS -> need a valid candidate, family, timeframe.
    if not cid or candidate_params is None:
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

    if rec.action == fr.WIDEN_PARAMS:
        setup_grid, exit_grid, ok = _widen_grids(rec.strategy_id, candidate_params)
        if not ok:
            return _note_plan(rec, cid, reason="widen_unsafe_no_cap",
                              note="cannot derive a safe widening ceiling; not queued")
        note = "bounded widen follow-up (hard-capped axes)"
    else:  # NARROW_PARAMS
        lower_turnover = rec.hard_status in ("FAILED_COSTS",)
        setup_grid, exit_grid = _narrow_grids(rec.strategy_id, candidate_params, lower_turnover=lower_turnover)
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
        max_variants=capped_variants,
        backend="cpu",
        resource_class="normal",
        private_output_policy="private_only",
    )
    return FollowupPlan(
        action=rec.action, candidate_id=cid, symbol=rec.symbol, strategy_id=rec.strategy_id,
        timeframe=tf, queued=True, not_queued_reason=None, note=note, sweep=sweep,
        grid_preview={"setup_grid": setup_grid, "exit_grid": exit_grid},
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
