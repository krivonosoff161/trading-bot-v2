# -*- coding: utf-8 -*-
"""LLM proposal loop: advisory only, export-only by default, code decides.

The cheap model is a research dispatcher. It may propose bounded JSON candidates,
but it never runs code, never computes backtests, never trades, and never decides
what enters the queue. The deterministic proposal validator owns that boundary.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.research_lab.llm_provider import SCANNER_ENV_PROVIDER, load_provider
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
KNOWN_PROVIDERS = ("alibaba", "qwen", "openai-compatible", "ollama")
DEFAULT_MAX_CANDIDATES = 8
DEFAULT_MAX_REVIEWS = 3
_HARD_ITEM_CAP = 200
DEFAULT_CONTRACT_FAILURE_THRESHOLD = 3

_DENY_KEYS = {
    "code", "shell", "exec", "eval", "command", "cmd", "script",
    "order", "orders", "place_order", "live_trade", "auto_trade", "autotrade",
    "api_key", "apikey", "secret", "password", "private_key", "token",
}

_REJECT_MAP = {
    "unknown_family": "unknown_strategy_family",
    "unknown_symbol": "unknown_symbol",
    "disallowed_timeframe": "unknown_timeframe",
    "one_minute_full_sweep_blocked": "unknown_timeframe",
    "too_many_variants": "variants_too_large",
    "heavy_job_not_allowed": "variants_too_large",
    "unsafe_wording": "unsafe_field",
    "output_boundary_violation": "unsafe_field",
    "missing_hypothesis": "missing_rationale",
    "not_compilable": "malformed_json",
}
_CONTRACT_FAILURE_REASONS = {
    "malformed_json", "json_parse_error", "wrong_top_level_shape",
    "missing_proposals_array", "unsafe_field", "unknown_strategy_family",
    "unknown_timeframe", "variants_too_large", "missing_rationale",
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
    provider = str(env.get(ENV_PROVIDER, "") or env.get(SCANNER_ENV_PROVIDER, "") or "").strip().lower()
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


def evaluate_llm_loop_gates(
    config: LLMLoopConfig,
    *,
    send_requested: bool,
    dry_run: bool,
    spent_today: float = 0.0,
) -> LLMSendDecision:
    decision = evaluate_send_gates(
        dry_run=dry_run,
        send_requested=send_requested,
        enabled=config.enabled,
        provider_configured=config.provider_configured,
        cap=config.daily_cap_value,
        spent_today=spent_today,
    )
    if not decision.allowed:
        return LLMSendDecision(False, decision.reason)
    if not config.provider:
        return LLMSendDecision(False, f"env_{ENV_PROVIDER}_not_set")
    return LLMSendDecision(True, "all_gates_passed")


def allowed_timeframes(profiles: TimeframeProfiles) -> list[str]:
    return [tf for tf in profiles.names() if tf != "1m"]


def build_proposal_prompt(
    summary: str,
    *,
    universe: Universe,
    profiles: TimeframeProfiles,
    max_candidates: int,
) -> tuple[str, str]:
    families = ", ".join(strategy_ids())
    tfs = ", ".join(allowed_timeframes(profiles))
    symbols = ", ".join(sorted(universe.all_symbols())[:60])
    example = {
        "proposals": [
            {
                "setup_family": "momentum_breakout",
                "requested_timeframe": "1d",
                "symbols": ["BTC_USDT_SWAP"],
                "parameter_grid": {
                    "momentum_breakout": [
                        {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}
                    ]
                },
                "hypothesis": "Test whether a conservative breakout variant still enters too late.",
                "expected_validation": "Reject if trades are too few, fragile, or late-entry dominated.",
                "risk_flags": [],
                "max_variants": 4,
            }
        ]
    }
    system = (
        "You are a weak local model used only as an advisory research dispatcher for a "
        "local backtesting lab. You are not the controller of the farm. You only propose "
        "bounded experiment candidates as strict JSON; deterministic code validates every "
        "candidate before anything can be queued. You never run code, never start or stop "
        "processes, never change files or configs, never trade, never promote paper/live "
        "status, and never make profitability claims. Output a JSON object with exactly "
        "one top-level key: proposals. Do not output prose, markdown, code fences, XML "
        "tags, comments, reasoning traces, or alternative formats. "
        "Every proposal must include setup_family, requested_timeframe, symbols, "
        "parameter_grid, hypothesis, expected_validation, risk_flags, and max_variants. "
        f"setup_family must be one of the known families. requested_timeframe must be one of: {tfs}. "
        "parameter_grid must be keyed by setup_family and contain a short list of parameter "
        "objects. hypothesis must name the market behavior being tested, not a trading "
        "recommendation. expected_validation must name a reject/observe/pass condition "
        "for the validator. Do not include code, shell, order, account, key, secret, path, "
        "Telegram, main-engine, paper-promotion, or live-trading fields. Keep each proposal "
        "small: one to two symbols and one to four grid variants."
    )
    user = (
        f"Known families: {families}\n"
        f"Allowed timeframes: {tfs}\n"
        f"Known symbols (subset): {symbols}\n"
        f"Propose at most {max_candidates} bounded candidates as JSON.\n"
        "Treat paper trading as downstream evidence only: PAPER_FORWARD_READY can only be "
        "assigned by hard validation, never by you.\n"
        "Prefer proposals that explain a current failure mode: too few trades, late entry, "
        "fragility, poor MFE/MAE, regime mismatch, missing OI/funding context, or repeated "
        "validator rejection.\n\n"
        f"Exact response shape example:\n{json.dumps(example, ensure_ascii=False, sort_keys=True)}\n\n"
        f"Recent lab state to react to:\n{summary}\n"
    )
    return system, user


def parse_llm_proposals(text: str) -> list[dict[str, Any]]:
    cleaned = _extract_json_payload(str(text))
    data = json.loads(cleaned)
    if isinstance(data, dict):
        for key in ("proposals", "candidates", "experiments"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            if _looks_like_candidate(data):
                data = [data]
            else:
                raise ValueError("missing_proposals_array")
    if not isinstance(data, list):
        raise ValueError("wrong_top_level_shape")
    return [x for x in data if isinstance(x, dict)]


def _extract_json_payload(text: str) -> str:
    cleaned = str(text or "").strip().lstrip("\ufeff")
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    cleaned = _strip_think_blocks(cleaned)
    if cleaned.startswith("{") or cleaned.startswith("["):
        end = _balanced_json_end(cleaned, 0)
        if end < 0:
            raise ValueError("json_parse_error")
        return cleaned[:end]
    start = min([p for p in (cleaned.find("{"), cleaned.find("[")) if p >= 0], default=-1)
    if start < 0:
        raise ValueError("json_parse_error")
    end = _balanced_json_end(cleaned, start)
    if end < 0:
        raise ValueError("json_parse_error")
    return cleaned[start:end]


def _strip_think_blocks(text: str) -> str:
    out = text
    while True:
        low = out.lower()
        start = low.find("<think>")
        end = low.find("</think>")
        if start < 0 or end < start:
            return out.strip()
        out = (out[:start] + out[end + len("</think>"):]).strip()


def _balanced_json_end(text: str, start: int) -> int:
    opener = text[start]
    stack = ["}" if opener == "{" else "]"]
    in_str = False
    esc = False
    for idx in range(start + 1, len(text)):
        ch = text[idx]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or ch != stack[-1]:
                return -1
            stack.pop()
            if not stack:
                return idx + 1
    return -1


def _looks_like_candidate(data: dict[str, Any]) -> bool:
    keys = {str(k) for k in data}
    return bool(keys & {"setup_family", "strategy", "family", "requested_timeframe", "timeframe", "parameter_grid", "params"})


@dataclass(frozen=True)
class LLMProposalBatch:
    validated: list[Proposal] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)

    def reject_reasons(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.rejected:
            out[item["reason"]] = out.get(item["reason"], 0) + 1
        return out

    def contract_failures(self) -> int:
        return sum(1 for item in self.rejected if item["reason"] in _CONTRACT_FAILURE_REASONS)

    def should_disable_for_run(self, *, threshold: int = DEFAULT_CONTRACT_FAILURE_THRESHOLD) -> bool:
        return self.contract_failures() >= max(1, int(threshold))

    def to_summary(self) -> dict[str, Any]:
        failures = self.contract_failures()
        return {
            "validated": len(self.validated),
            "rejected": len(self.rejected),
            "reject_reasons": self.reject_reasons(),
            "contract_failures": failures,
            "disable_for_run": failures >= DEFAULT_CONTRACT_FAILURE_THRESHOLD,
        }


def _has_unsafe_field(item: dict[str, Any]) -> bool:
    return any(str(k).strip().lower() in _DENY_KEYS for k in item.keys())


def _normalize_candidate(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    if not out.get("setup_family"):
        out["setup_family"] = out.get("strategy") or out.get("strategy_id") or out.get("family") or out.get("setup")
    if not out.get("requested_timeframe"):
        out["requested_timeframe"] = out.get("timeframe") or out.get("tf")
    if not out.get("hypothesis"):
        out["hypothesis"] = out.get("rationale") or out.get("reason") or out.get("idea")
    if not out.get("expected_validation"):
        out["expected_validation"] = out.get("expected_failure_mode") or out.get("validation") or out.get("check")
    if isinstance(out.get("symbols"), str):
        out["symbols"] = [out["symbols"]]
    family = str(out.get("setup_family") or "")
    raw_grid = out.get("parameter_grid")
    if not raw_grid:
        params = out.get("params") or out.get("parameters") or out.get("parameter_set")
        if isinstance(params, dict) and family:
            variants = params.get(family) if isinstance(params.get(family), list) else None
            out["parameter_grid"] = {family: variants or [params]}
        elif isinstance(params, list) and family:
            out["parameter_grid"] = {family: [dict(v) for v in params if isinstance(v, dict)]}
    if not out.get("risk_flags"):
        out["risk_flags"] = []
    if not out.get("max_variants"):
        grid = out.get("parameter_grid") or {}
        variants = grid.get(family) if isinstance(grid, dict) else []
        symbol_count = len(out.get("symbols") or [])
        out["max_variants"] = max(1, min(4, symbol_count * len(variants or [])))
    return out


def _coerce_error_reason(exc: ValueError) -> str:
    msg = str(exc)
    if "hypothesis" in msg:
        return "missing_rationale"
    if "requested_timeframe" in msg:
        return "unknown_timeframe"
    if "setup_family" in msg:
        return "unknown_strategy_family"
    return "malformed_json"


def validate_llm_candidates(
    items: list[dict[str, Any]],
    *,
    universe: Universe,
    timeframe_profiles: TimeframeProfiles,
    resource_policy: ResourcePolicy,
    created_at: str,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> LLMProposalBatch:
    validated: list[Proposal] = []
    rejected: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for raw in list(items)[:_HARD_ITEM_CAP]:
        if not isinstance(raw, dict):
            rejected.append({"id": "?", "reason": "malformed_json"})
            continue
        item = _normalize_candidate(raw)
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
            rejected.append({"id": proposal_id_for(item), "reason": _coerce_error_reason(exc)})
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
                rejected.append({"id": marked.proposal_id, "reason": "variants_too_large"})
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
    system, user = build_proposal_prompt(summary, universe=universe, profiles=timeframe_profiles,
                                         max_candidates=max_candidates)
    text, usage = provider.generate(system, user)
    try:
        items = parse_llm_proposals(text)
    except ValueError as exc:
        reason = str(exc) if str(exc) in {"json_parse_error", "wrong_top_level_shape", "missing_proposals_array"} else "json_parse_error"
        return LLMProposalBatch(validated=[], rejected=[{"id": "?", "reason": reason}]), usage
    except json.JSONDecodeError:
        return LLMProposalBatch(validated=[], rejected=[{"id": "?", "reason": "json_parse_error"}]), usage
    batch = validate_llm_candidates(
        items, universe=universe, timeframe_profiles=timeframe_profiles,
        resource_policy=resource_policy, created_at=created_at, max_candidates=max_candidates,
    )
    return batch, usage


def chief_review_candidates(batch: LLMProposalBatch, config: LLMLoopConfig) -> list[Proposal]:
    return list(batch.validated)[: max(0, int(config.max_reviews))]
