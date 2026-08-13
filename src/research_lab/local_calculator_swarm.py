"""Sequential local mini-swarm for bounded farm calculation hypotheses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

from src.research_lab.agent_role_registry import role_by_id, validate_role_payload
from src.research_lab.calculator_advisor import (
    CalculatorAdvice,
    PUBLIC_ANALYSIS_STATEMENTS,
    advice_path,
    normalize_advice_payload,
    validate_advice_payload,
)
from src.research_lab.feature_packet import FeaturePacket
from src.research_lab.lineage_contract import append_jsonl, stable_id, utc_now
from src.research_lab.llm_invocation_ledger import preflight_invocation, record_invocation
from src.research_lab.llm_provider import LLMProviderError, ProposalProvider, record_usage
from src.research_lab.local_model_context import build_local_model_context
from src.research_lab.local_model_eval import evaluate_role_output

SCHEMA = "LocalCalculatorSwarm.v1"
NORMALIZER_VERSION = "local_calculator_swarm_normalizer.v2"

PASS_SPECS = (
    (
        "calculator_context_classifier",
        "Return exactly one JSON object and no prose. Exact shape: "
        '{"situation_class":"unclear","missing_data":[],"confidence":0.0,"warnings":[]}. '
        "situation_class must be exactly one of trend, range, unclear. "
        "Use only these four keys. Never add prices, side, orders, verdicts, or execution fields.",
    ),
    (
        "calculator_hypothesis_proposer",
        "Return exactly one JSON object and no prose. Exact shape: "
        '{"advisory_reason":"short reason","user_facing_analysis":"approved '
        'statement","sweep_suggestions":["hold"],"confidence":0.0,"warnings":[]}. '
        "user_facing_analysis must be exactly one of: "
        + " | ".join(sorted(PUBLIC_ANALYSIS_STATEMENTS))
        + ". Do not create or modify the statement. "
        "Suggestions may only be entry_timing, stop, take_profit, hold, trailing, timeframe, family, "
        "regime_filter. Use only these five keys. Never output numeric trade levels or a direction.",
    ),
    (
        "calculator_hypothesis_critic",
        "Return exactly one JSON object and no prose. Exact shape: "
        '{"proposal_quality":"accept|reject","rejection_reason":"","confidence":0.0,"warnings":[]}. '
        "Use only these four keys. Never replace the hypothesis with prices, side, orders, or verdicts.",
    ),
)


@dataclass(frozen=True)
class CalculatorSwarmPass:
    role_id: str
    accepted: bool
    payload: dict[str, Any]
    problems: list[str] = field(default_factory=list)


def swarm_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "llm_advice" / "calculator_swarm.jsonl"


def _input(packet: FeaturePacket, prior: list[CalculatorSwarmPass]) -> dict[str, Any]:
    return {
        "schema": "LocalCalculatorSwarmInput.v1",
        "feature_packet": packet.to_dict(),
        "prior_passes": [
            {"role_id": item.role_id, "payload": item.payload}
            for item in prior
            if item.accepted
        ],
        "hard_rules": {
            "paper_only": True,
            "execution_allowed": False,
            "numeric_trade_levels_forbidden": True,
            "validator_or_readiness_mutation_forbidden": True,
        },
    }


def _semantic_problems(role_id: str, payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if role_id == "calculator_context_classifier" and payload.get("situation_class") not in {
        "trend",
        "range",
        "unclear",
    }:
        problems.append("situation_class_must_be_trend_range_or_unclear")
    if role_id == "calculator_context_classifier" and not isinstance(payload.get("missing_data", []), list):
        problems.append("missing_data_must_be_list")
    if role_id == "calculator_hypothesis_proposer" and not isinstance(
        payload.get("sweep_suggestions", []), list
    ):
        problems.append("sweep_suggestions_must_be_list")
    if role_id == "calculator_hypothesis_critic" and payload.get("proposal_quality") not in {"accept", "reject"}:
        problems.append("proposal_quality_must_be_accept_or_reject")
    return problems


def _normalize_pass_payload(role_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    role = role_by_id(role_id)
    allowed = set(role.allowed_fields)
    forbidden = set(role.forbidden_fields)
    normalized: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in payload.items():
        if key in allowed or key in forbidden:
            normalized[key] = value
        else:
            dropped.append(str(key))
    if dropped:
        warnings = normalized.get("warnings")
        if not isinstance(warnings, list):
            warnings = [] if warnings is None else [str(warnings)]
        warnings.extend(f"dropped_unknown_field:{key}" for key in sorted(dropped))
        normalized["warnings"] = warnings
    for key in ("warnings", "missing_data", "sweep_suggestions"):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = [value]
    confidence = normalized.get("confidence")
    if isinstance(confidence, str):
        text = confidence.strip()
        try:
            normalized["confidence"] = float(text.rstrip("%")) / (100.0 if text.endswith("%") else 1.0)
        except ValueError:
            pass
    if "proposal_quality" in normalized:
        normalized["proposal_quality"] = str(normalized["proposal_quality"]).strip().lower()
    return normalized


def request_local_calculator_swarm(
    private_root: Path,
    packet: FeaturePacket,
    provider: ProposalProvider,
    *,
    allow_public_output: bool = False,
) -> CalculatorAdvice:
    passes: list[CalculatorSwarmPass] = []
    provider_name = str(getattr(provider, "name", "unknown") or "unknown")
    model_name = str(getattr(provider, "model_name", "") or "")
    for role_id, system_prompt in PASS_SPECS:
        input_payload = _input(packet, passes)
        input_payload["versioned_context"] = build_local_model_context(
            role_id, query=json.dumps(packet.features, ensure_ascii=False, sort_keys=True),
        )
        input_payload["prompt_hash"] = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
        input_payload["normalizer_version"] = NORMALIZER_VERSION
        permit = preflight_invocation(
            private_root,
            role_id=role_id,
            source_ref=packet.feature_packet_id,
            input_payload=input_payload,
            provider=provider,
            local_only=True,
        )
        if not permit.allowed:
            item = CalculatorSwarmPass(role_id, False, {}, [permit.reason])
            passes.append(item)
            if permit.reason != "duplicate_completed":
                record_invocation(private_root, permit, status="blocked", problems=item.problems)
            break
        try:
            text, usage = provider.generate(
                system_prompt,
                json.dumps(input_payload, ensure_ascii=False, sort_keys=True),
            )
            record_usage(private_root, usage, allow_public_output=allow_public_output)
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError("swarm pass must be a JSON object")
            payload = _normalize_pass_payload(role_id, payload)
            ok, problems = validate_role_payload(role_id, payload)
            problems = [*problems, *_semantic_problems(role_id, payload)]
            ok = not problems
            item = CalculatorSwarmPass(role_id, ok, payload if ok else {}, problems)
            passes.append(item)
            record_invocation(
                private_root,
                permit,
                status="accepted" if ok else "schema_rejected",
                output_ref=f"{packet.feature_packet_id}:{role_id}",
                problems=problems,
                usage=usage,
            )
            if not ok:
                break
        except (LLMProviderError, json.JSONDecodeError, ValueError) as exc:
            item = CalculatorSwarmPass(role_id, False, {}, [str(exc).strip() or type(exc).__name__])
            passes.append(item)
            record_invocation(private_root, permit, status="provider_error", problems=item.problems)
            break

    merged: dict[str, Any] = {}
    for item in passes:
        if item.accepted:
            merged.update(item.payload)
    critic = next((item for item in passes if item.role_id == "calculator_hypothesis_critic"), None)
    critic_rejected = bool(critic and critic.payload.get("proposal_quality") == "reject")
    merged.pop("proposal_quality", None)
    merged = normalize_advice_payload(merged)
    ok, problems = validate_advice_payload(merged)
    all_passes = len(passes) == len(PASS_SPECS) and all(item.accepted for item in passes)
    accepted = bool(all_passes and ok and not critic_rejected)
    if critic_rejected:
        problems = [*problems, "critic_rejected"]
    for item in passes:
        problems.extend(item.problems)
    advisor_ref = stable_id(
        "calculatorswarm",
        {"feature_packet_id": packet.feature_packet_id, "passes": [asdict(item) for item in passes]},
        length=24,
    )
    advice = CalculatorAdvice(
        advisor_ref=advisor_ref,
        feature_packet_id=packet.feature_packet_id,
        provider=provider_name,
        model=model_name,
        advice=merged if accepted else {},
        problems=problems,
        accepted=accepted,
    )
    if "duplicate_completed" not in problems:
        append_jsonl(
            swarm_path(private_root),
            {
                "schema": SCHEMA,
                "advisor_ref": advisor_ref,
                "feature_packet_id": packet.feature_packet_id,
                "provider": provider_name,
                "model": model_name,
                "passes": [asdict(item) for item in passes],
                "evals": [
                    evaluate_role_output(item.role_id, item.payload)
                    for item in passes
                    if item.payload
                ],
                "accepted": accepted,
                "problems": problems,
                "paper_only": True,
                "execution_allowed": False,
                "created_at": utc_now(),
            },
        )
        append_jsonl(advice_path(private_root), advice.to_dict())
    return advice
