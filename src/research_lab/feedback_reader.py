# -*- coding: utf-8 -*-
"""Feedback reader: turn hard-validation verdicts into farm recommendations.

The hard-validation pipeline writes per-candidate ``FailureFeedback`` rows
(``hard_validation/feedback/feedback.jsonl``) and setup cards for the ones that
pass. This module reads both and produces deterministic, farm-level
recommendations -- what to promote, suppress, narrow, widen, or collect more data
for -- so the next research cycle is informed by what hard validation found.

It is deterministic and read-only: no LLM, no queueing, no live trading. It
*recommends*; turning a recommendation into a queued sweep stays a separate,
explicit, deterministically-validated step (the existing ``--apply`` paths).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Recommendation actions (the minimal feedback contract).
PROMOTE = "PROMOTE"
SUPPRESS = "SUPPRESS"
NARROW_PARAMS = "NARROW_PARAMS"
WIDEN_PARAMS = "WIDEN_PARAMS"
REGIME_SWEEP = "REGIME_SWEEP"
REQUIRE_MORE_DATA = "REQUIRE_MORE_DATA"
REJECT = "REJECT"

# hard_status -> (action, reason, priority). Deterministic, no profitability claim.
_STATUS_ACTION: dict[str, tuple[str, str, str]] = {
    "FAILED_COSTS": (NARROW_PARAMS, "net negative after costs; lower turnover, do not widen", "normal"),
    "FAILED_OOS": (REQUIRE_MORE_DATA, "failed out-of-sample; needs different split / more bars", "normal"),
    "FAILED_FRAGILITY": (NARROW_PARAMS, "fragile parameter neighborhood; test plateau, reject spikes", "normal"),
    "FAILED_OVERFIT": (SUPPRESS, "overfit risk; reduce search space, suppress until longer track record", "high"),
    "FAILED_DATA_QUALITY": (REQUIRE_MORE_DATA, "data quality failure; verify/rebuild candles", "high"),
    "REGIME_ONLY": (REGIME_SWEEP, "works only in a specific regime; run a bounded regime-filtered sweep", "normal"),
    "NEEDS_MORE_DATA": (REQUIRE_MORE_DATA, "insufficient history; collect more / wait for forward period", "low"),
    "HARD_REJECT": (REJECT, "hard reject; archive and stop spending on this setup", "low"),
}


@dataclass(frozen=True)
class Recommendation:
    """One farm-level recommendation derived from hard-validation output."""

    action: str
    strategy_id: str
    symbol: str
    timeframe: str
    reason: str
    hard_status: str
    priority: str
    candidate_ids: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "reason": self.reason,
            "hard_status": self.hard_status,
            "priority": self.priority,
            "candidate_ids": list(self.candidate_ids),
            "reason_codes": list(self.reason_codes),
        }


def _scope_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("strategy_id") or "unknown"),
        str(row.get("symbol") or "unknown"),
        str(row.get("timeframe") or "unknown"),
    )


def recommendations_from_feedback(feedback_rows: list[dict[str, Any]]) -> list[Recommendation]:
    """Aggregate per-candidate failure feedback into per-(strategy,symbol,tf) recs.

    Rows that share a scope and hard_status are merged into a single
    recommendation carrying all contributing candidate ids.
    """
    grouped: dict[tuple[str, str, str, str], dict[str, set[str]]] = {}
    for row in feedback_rows:
        status = str(row.get("hard_status") or "")
        if status == "PAPER_FORWARD_READY":
            continue  # passed: handled by promotions, not failure feedback
        scope = _scope_key(row)
        key = (*scope, status)
        bucket = grouped.setdefault(key, {"candidate_ids": set(), "reason_codes": set()})
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id:
            bucket["candidate_ids"].add(candidate_id)
        for code in row.get("reason_codes") or []:
            if code:
                bucket["reason_codes"].add(str(code))

    out: list[Recommendation] = []
    for (strategy_id, symbol, timeframe, status), data in sorted(grouped.items()):
        action, reason, priority = _STATUS_ACTION.get(
            status, (REJECT, "unknown status; default to reject", "low")
        )
        out.append(Recommendation(
            action=action, strategy_id=strategy_id, symbol=symbol, timeframe=timeframe,
            reason=reason, hard_status=status, priority=priority,
            candidate_ids=sorted(data["candidate_ids"]),
            reason_codes=sorted(data["reason_codes"]),
        ))
    return out


def recommendations_from_cards(cards: list[dict[str, Any]]) -> list[Recommendation]:
    """Emit PROMOTE recommendations from passed setup cards.

    A passed card is forward-paper readiness only. ``main_engine_ready`` stays
    False here; promotion to a live engine is never decided by this reader.
    """
    out: list[Recommendation] = []
    for card in cards:
        hard_status = str(card.get("hard_status") or "")
        if hard_status != "PAPER_FORWARD_READY":
            continue
        out.append(Recommendation(
            action=PROMOTE,
            strategy_id=str(card.get("strategy_id") or "unknown"),
            symbol=str(card.get("symbol") or "unknown"),
            timeframe=str(card.get("timeframe") or "unknown"),
            reason="passed hard validation; track in forward paper log (not main-engine ready)",
            hard_status=hard_status,
            priority="normal",
            candidate_ids=[str(card.get("candidate_id") or "")],
        ))
    return out


def build_recommendations(
    feedback_rows: list[dict[str, Any]],
    cards: list[dict[str, Any]] | None = None,
) -> list[Recommendation]:
    """Full recommendation set: promotions from cards + failure-driven actions."""
    recs = recommendations_from_cards(cards or [])
    recs.extend(recommendations_from_feedback(feedback_rows))
    return recs


def summarize(recs: list[Recommendation]) -> dict[str, Any]:
    """Counts by action and priority for a quick operator summary."""
    by_action: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for r in recs:
        by_action[r.action] = by_action.get(r.action, 0) + 1
        by_priority[r.priority] = by_priority.get(r.priority, 0) + 1
    return {"total": len(recs), "by_action": by_action, "by_priority": by_priority}
