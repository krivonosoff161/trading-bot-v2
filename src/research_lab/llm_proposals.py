# -*- coding: utf-8 -*-
"""LLM proposal loop — advisory only, export-only by default, code decides everything.

Design (cheap -> chief), built ON TOP of existing safe pieces, not duplicating them:
- "cheap" model produces candidate proposals as strict JSON (the request pack is the
  one in `review_export.export_review_pack`). The model never runs code or shells; its
  output is a JSON file a human/model saves.
- code validates EVERY candidate via the existing `proposal_validator.validate_and_mark`
  (known family/symbol/timeframe, bounded variants, safe wording, private boundary).
- only the VALIDATED subset is eligible for a "chief" second pass, capped by policy.
- sending is gated and disabled by default: it reuses `llm_review_sender` (NullSender,
  `evaluate_send_gates`, daily cap, env flag) plus a provider gate. No paid call ever
  happens unless every gate passes AND a real provider is wired (none ships today).

This module never executes LLM output, never calls a network by default, and never
stores API keys (env names only).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.research_lab.llm_review_sender import (
    NullReviewSender,
    daily_cap,
    env_enabled,
    evaluate_send_gates,
)
from src.research_lab.proposal_schema import VALIDATED, Proposal, coerce_proposal, proposal_id_for
from src.research_lab.proposal_validator import validate_and_mark
from src.research_lab.resource_policy import ResourcePolicy
from src.research_lab.timeframes import TimeframeProfiles
from src.research_lab.universe import Universe

ENV_PROVIDER = "STRATEGY_LAB_LLM_PROVIDER"
# Providers that COULD be wired later (documented). None is shipped, so all paths are
# export-only until a real, audited client is added behind these gates.
KNOWN_PROVIDERS = ("alibaba", "qwen")
DEFAULT_MAX_CANDIDATES = 8   # cheap-model output cap
DEFAULT_MAX_REVIEWS = 3      # chief-model review cap
_HARD_ITEM_CAP = 200         # never even iterate an absurd cheap-output blob

# Re-export the null sender under a proposal-loop name (it is the export-only default).
NullProposalSender = NullReviewSender


@dataclass(frozen=True)
class LLMLoopConfig:
    enabled: bool = False
    provider: str = ""
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    max_reviews: int = DEFAULT_MAX_REVIEWS
    daily_cap_value: float | None = None

    @property
    def provider_configured(self) -> bool:
        # No real client ships; a future provider must set this only when a key is
        # actually present. Today it is always False -> export-only.
        return False

    @property
    def mode(self) -> str:
        return "enabled" if self.enabled else "disabled"

    def to_summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider or "none",
            "provider_configured": self.provider_configured,
            "daily_cap_present": self.daily_cap_value is not None,
            "max_candidates": self.max_candidates,
            "max_reviews": self.max_reviews,
            "mode": "export_only",  # always, until a real provider is wired
            "note": "advisory only; code validates; no paid call by default; LLM output never executed",
        }


def load_llm_loop_config(
    environ: Mapping[str, str] | None = None,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_reviews: int = DEFAULT_MAX_REVIEWS,
) -> LLMLoopConfig:
    env = environ if environ is not None else os.environ
    provider = str(env.get(ENV_PROVIDER, "") or "").strip().lower()
    return LLMLoopConfig(
        enabled=env_enabled(env),
        provider=provider,
        max_candidates=max(0, int(max_candidates)),
        max_reviews=max(0, int(max_reviews)),
        daily_cap_value=daily_cap(env),
    )


@dataclass(frozen=True)
class LLMSendDecision:
    allowed: bool
    reason: str


def evaluate_llm_loop_gates(config: LLMLoopConfig, *, send_requested: bool, dry_run: bool) -> LLMSendDecision:
    """All gates required to SEND. Reuses the review-sender gates + a provider gate."""
    decision = evaluate_send_gates(
        dry_run=dry_run,
        send_requested=send_requested,
        enabled=config.enabled,
        provider_configured=config.provider_configured,
        cap=config.daily_cap_value,
    )
    if not decision.allowed:
        return LLMSendDecision(False, decision.reason)
    if not config.provider:
        return LLMSendDecision(False, f"env_{ENV_PROVIDER}_not_set")
    return LLMSendDecision(True, "all_gates_passed")


@dataclass(frozen=True)
class LLMProposalBatch:
    validated: list[Proposal] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        return {"validated": len(self.validated), "rejected": len(self.rejected),
                "rejected_reasons": self.rejected[:10]}


def validate_llm_candidates(
    items: list[dict[str, Any]],
    *,
    universe: Universe,
    timeframe_profiles: TimeframeProfiles,
    resource_policy: ResourcePolicy,
    created_at: str,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> LLMProposalBatch:
    """Validate cheap-model JSON candidates with the deterministic validator (no exec)."""
    validated: list[Proposal] = []
    rejected: list[dict[str, str]] = []
    for item in list(items)[:_HARD_ITEM_CAP]:
        if not isinstance(item, dict):
            rejected.append({"id": "?", "reason": "not_an_object"})
            continue
        try:
            proposal = coerce_proposal({
                **item,
                "created_by": item.get("created_by") or "llm_review",
                "created_at": item.get("created_at") or created_at,
            })
            proposal = validate_and_mark(
                proposal, universe=universe, timeframe_profiles=timeframe_profiles,
                resource_policy=resource_policy,
            )
        except ValueError as exc:
            rejected.append({"id": proposal_id_for(item), "reason": f"schema_error:{str(exc)[:160]}"})
            continue
        if proposal.status == VALIDATED:
            if len(validated) < max(0, int(max_candidates)):
                validated.append(proposal)
        else:
            rejected.append({"id": proposal.proposal_id, "reason": proposal.rejection_reason})
    return LLMProposalBatch(validated=validated, rejected=rejected)


def chief_review_candidates(batch: LLMProposalBatch, config: LLMLoopConfig) -> list[Proposal]:
    """The chief pass only ever sees the VALIDATED subset, capped to max_reviews."""
    return list(batch.validated)[: max(0, int(config.max_reviews))]
