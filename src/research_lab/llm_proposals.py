# -*- coding: utf-8 -*-
"""LLM proposal loop — advisory only, export-only by default, code decides everything.

The cheap model is a research DISPATCHER: it proposes bounded JSON candidates only
(strategy family, symbols, timeframe, parameter grid, filters, a short rationale, and
an expected failure mode to check). It never computes backtests, never makes trading
decisions, and its output is never executed. `proposal_validator` validates every
candidate and code decides what enters the queue.

Built ON TOP of existing safe pieces:
- the real provider client + cost log live in `llm_provider.py` (gated, off by default);
- candidate validation reuses `proposal_validator.validate_and_mark`;
- the gated SEND boundary reuses `llm_review_sender`.

No paid call happens unless every gate passes AND a real provider is configured. No API
keys are stored or logged (env names only).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.research_lab.llm_provider import load_provider
from src.research_lab.llm_review_sender import (
    NullReviewSender,
    daily_cap,
    env_enabled,
    evaluate_send_gates,
)
from src.research_lab.proposal_schema import VALIDATED, Proposal, coerce_proposal, proposal_id_for
from src.research_lab.proposal_validator import validate_and_mark
from src.research_lab.resource_policy import ResourcePolicy
from src.research_lab.strategy_registry import strategy_ids
from src.research_lab.timeframes import TimeframeProfiles
from src.research_lab.universe import Universe

ENV_PROVIDER = "STRATEGY_LAB_LLM_PROVIDER"
KNOWN_PROVIDERS = ("alibaba", "qwen", "openai-compatible")
DEFAULT_MAX_CANDIDATES = 8
DEFAULT_MAX_REVIEWS = 3
_HARD_ITEM_CAP = 200

# Structural fields a candidate must never carry (defence-in-depth; LLM output is data).
_DENY_KEYS = {
    "code", "shell", "exec", "eval", "command", "cmd", "script",
    "order", "orders", "place_order", "live_trade", "auto_trade", "autotrade",
    "api_key", "apikey", "secret", "password", "private_key", "token",
}

# Validator reason code -> the loop's reject vocabulary.
_REJECT_MAP = {
    "unknown_family": "unknown_strategy_family",
    "unknown_symbol": "unknown_symbol",
    "disallowed_timeframe": "unknown_timeframe",
    "one_minute_full_sweep_blocked": "unknown_timeframe",
    "too_many_variants": "variants_too_large",
    "unsafe_wording": "unsafe_field",
    "output_boundary_violation": "unsafe_field",
    "missing_hypothesis": "missing_rationale",
    "not_compilable": "malformed_json",
}

NullProposalSender = NullReviewSender


@dataclass(frozen=True)
class LLMLoopConfig:
    enabled: bool = False
    provider: str = ""
    provider_configured: bool = False
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    max_reviews: int = DEFAULT_MAX_REVIEWS
    daily_cap_value: float | None = None

    @property
    def mode(self) -> str:
        if not self.enabled:
            return "disabled"
        return "ready" if self.provider_configured else "export_only"

    def to_summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider or "none",
            "provider_configured": self.provider_configured,
            "daily_cap_present": self.daily_cap_value is not None,
            "max_candidates": self.max_candidates,
            "max_reviews": self.max_reviews,
            "mode": self.mode,
            "note": "advisory only; code validates; no paid call unless all gates pass; LLM output never executed",
        }


def load_llm_loop_config(
    environ: Mapping[str, str] | None = None,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_reviews: int = DEFAULT_MAX_REVIEWS,
    allow_synthetic: bool = False,
) -> LLMLoopConfig:
    env = environ if environ is not None else os.environ
    provider = str(env.get(ENV_PROVIDER, "") or "").strip().lower()
    # provider_configured reflects a REAL (network) provider being fully set up.
    configured = load_provider(env, allow_synthetic=allow_synthetic).configured and provider != "synthetic"
    return LLMLoopConfig(
        enabled=env_enabled(env),
        provider=provider,
        provider_configured=configured,
        max_candidates=max(0, int(max_candidates)),
        max_reviews=max(0, int(max_reviews)),
        daily_cap_value=daily_cap(env),
    )


@dataclass(frozen=True)
class LLMSendDecision:
    allowed: bool
    reason: str


def evaluate_llm_loop_gates(config: LLMLoopConfig, *, send_requested: bool, dry_run: bool) -> LLMSendDecision:
    """All gates required to make a paid call. Reuses the review-sender gates + provider gate."""
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


# ── strict cheap-model contract ────────────────────────────────────────────────

def allowed_timeframes(profiles: TimeframeProfiles) -> list[str]:
    """Timeframes a proposal may target (everything except the trigger-only 1m)."""
    return [tf for tf in profiles.names() if tf != "1m"]


def build_proposal_prompt(
    summary: str,
    *,
    universe: Universe,
    profiles: TimeframeProfiles,
    max_candidates: int,
) -> tuple[str, str]:
    """Build (system, user). The model must return JSON only — no prose, no code."""
    families = ", ".join(strategy_ids())
    tfs = ", ".join(allowed_timeframes(profiles))
    symbols = ", ".join(sorted(universe.all_symbols())[:60])
    system = (
        "You are a research dispatcher for a local backtesting lab. You ONLY propose "
        "bounded experiment candidates as STRICT JSON. You never run code, never trade, "
        "never make profitability claims. Output a JSON object {\"proposals\": [ ... ]} and "
        "NOTHING else (no prose, no markdown). Each proposal MUST have: setup_family (one of "
        f"the known families), requested_timeframe (one of: {tfs}), symbols (known symbols), "
        "parameter_grid (a small grid keyed by setup_family), hypothesis (short rationale), "
        "expected_failure_mode (what to check). Do NOT include any code, shell, order, or "
        "live-trading fields. Keep each proposal small (few symbols, few variants)."
    )
    user = (
        f"Known families: {families}\n"
        f"Allowed timeframes: {tfs}\n"
        f"Known symbols (subset): {symbols}\n"
        f"Propose at most {max_candidates} bounded candidates as JSON.\n\n"
        f"Recent lab state to react to:\n{summary}\n"
    )
    return system, user


def parse_llm_proposals(text: str) -> list[dict[str, Any]]:
    """Parse strict-JSON model output into a list of proposal dicts. Raises on non-JSON."""
    cleaned = str(text).strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    data = json.loads(cleaned)
    if isinstance(data, dict) and isinstance(data.get("proposals"), list):
        data = data["proposals"]
    if not isinstance(data, list):
        raise ValueError("expected a JSON array or {proposals: [...]}")
    return [x for x in data if isinstance(x, dict)]


@dataclass(frozen=True)
class LLMProposalBatch:
    validated: list[Proposal] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)

    def reject_reasons(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.rejected:
            out[item["reason"]] = out.get(item["reason"], 0) + 1
        return out

    def to_summary(self) -> dict[str, Any]:
        return {"validated": len(self.validated), "rejected": len(self.rejected),
                "reject_reasons": self.reject_reasons()}


def _has_unsafe_field(item: dict[str, Any]) -> bool:
    return any(str(k).strip().lower() in _DENY_KEYS for k in item.keys())


def validate_llm_candidates(
    items: list[dict[str, Any]],
    *,
    universe: Universe,
    timeframe_profiles: TimeframeProfiles,
    resource_policy: ResourcePolicy,
    created_at: str,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> LLMProposalBatch:
    """Validate cheap-model candidates deterministically (no exec). Maps reject reasons."""
    validated: list[Proposal] = []
    rejected: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for item in list(items)[:_HARD_ITEM_CAP]:
        if not isinstance(item, dict):
            rejected.append({"id": "?", "reason": "malformed_json"})
            continue
        if _has_unsafe_field(item):
            rejected.append({"id": proposal_id_for(item), "reason": "unsafe_field"})
            continue
        try:
            proposal = coerce_proposal({
                **item,
                "created_by": item.get("created_by") or "llm_review",
                "created_at": item.get("created_at") or created_at,
            })
        except ValueError as exc:
            reason = "missing_rationale" if "hypothesis" in str(exc) else "malformed_json"
            rejected.append({"id": proposal_id_for(item), "reason": reason})
            continue
        if proposal.proposal_id in seen_ids:
            rejected.append({"id": proposal.proposal_id, "reason": "duplicate_candidate"})
            continue
        seen_ids.add(proposal.proposal_id)
        marked = validate_and_mark(
            proposal, universe=universe, timeframe_profiles=timeframe_profiles, resource_policy=resource_policy,
        )
        if marked.status == VALIDATED:
            if len(validated) < max(0, int(max_candidates)):
                validated.append(marked)
            else:
                rejected.append({"id": marked.proposal_id, "reason": "variants_too_large"})  # over cap
        else:
            codes = marked.rejection_reason.split(",") if marked.rejection_reason else []
            reason = next((_REJECT_MAP[c] for c in codes if c in _REJECT_MAP), "malformed_json")
            rejected.append({"id": marked.proposal_id, "reason": reason})
    return LLMProposalBatch(validated=validated, rejected=rejected)


def generate_proposals_via_llm(
    provider,
    *,
    summary: str,
    universe: Universe,
    timeframe_profiles: TimeframeProfiles,
    resource_policy: ResourcePolicy,
    created_at: str,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
):
    """Ask the provider for candidates, parse strict JSON, validate. Returns (batch, usage)."""
    system, user = build_proposal_prompt(summary, universe=universe, profiles=timeframe_profiles,
                                         max_candidates=max_candidates)
    text, usage = provider.generate(system, user)  # may raise LLMProviderError (caller handles)
    try:
        items = parse_llm_proposals(text)
    except (ValueError, json.JSONDecodeError):
        return LLMProposalBatch(validated=[], rejected=[{"id": "?", "reason": "malformed_json"}]), usage
    batch = validate_llm_candidates(
        items, universe=universe, timeframe_profiles=timeframe_profiles,
        resource_policy=resource_policy, created_at=created_at, max_candidates=max_candidates,
    )
    return batch, usage


def chief_review_candidates(batch: LLMProposalBatch, config: LLMLoopConfig) -> list[Proposal]:
    """The chief pass only ever sees the VALIDATED subset, capped to max_reviews."""
    return list(batch.validated)[: max(0, int(config.max_reviews))]
