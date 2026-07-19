"""Bounded calculator advisor over FeaturePacket JSON.

The calculator may classify or explain a setup, but it cannot mint paper signals,
change trading numbers, set validator verdicts, or request execution.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.feature_packet import FeaturePacket
from src.research_lab.advisory_payload_validator import validate_advisory_payload
from src.research_lab.lineage_contract import append_jsonl, stable_id, utc_now
from src.research_lab.llm_provider import LLMProviderError, ProposalProvider, record_usage
from src.research_lab.llm_invocation_ledger import preflight_invocation, record_invocation

SCHEMA = "CalculatorAdvice.v1"
PROMPT_VERSION = "calculator_advisor_v2_feature_packet_json"

ALLOWED_KEYS = {
    "situation_class",
    "advisory_reason",
    "rejection_reason",
    "missing_data",
    "sweep_suggestions",
    "confidence",
    "warnings",
}
FORBIDDEN_KEYS = {
    "entry",
    "entry_zone",
    "stop",
    "stop_loss",
    "take_profit",
    "take_profit_plan",
    "side",
    "paper_ready",
    "execution_allowed",
    "auto_trade",
    "validator_verdict",
    "order",
    "size",
    "execute",
}
ALIASES = {
    "classification": "situation_class",
    "suggested_dimensions": "sweep_suggestions",
    "additional_suggestions": "warnings",
    "suggestions": "sweep_suggestions",
    "reason": "advisory_reason",
    "missing": "missing_data",
}

SYSTEM_PROMPT = (
    "You are Strategy Lab Calculator, a bounded research advisor. "
    "Return JSON only. You may classify the feature packet, explain missing data, "
    "and suggest bounded sweep dimensions. Use only these sweep_suggestions "
    "dimensions: entry_timing, stop, take_profit, hold, trailing, timeframe, "
    "family, regime_filter. Do not output indicator names such as RSI_14, "
    "ATR_14, volume_spike, or concrete numeric thresholds as sweep dimensions; "
    "map them to the allowed dimension they would test, usually regime_filter, "
    "stop, hold, or trailing. You must not set entry, stop, side, "
    "take profit, validator verdict, paper_ready, order, size, or execution fields."
)


@dataclass(frozen=True)
class CalculatorAdvice:
    advisor_ref: str
    feature_packet_id: str
    provider: str
    model: str
    advice: dict[str, Any]
    problems: list[str] = field(default_factory=list)
    accepted: bool = False
    paper_only: bool = True
    execution_allowed: bool = False
    created_at: str = field(default_factory=utc_now)
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["calculator_advice_id"] = self.advisor_ref
        payload["prompt_version"] = PROMPT_VERSION
        payload["prompt_hash"] = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:16]
        return payload


def advice_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "llm_advice" / "calculator_advice.jsonl"


def normalize_advice_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        return [value]

    normalized: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in payload.items():
        target_key = ALIASES.get(key, key)
        if target_key in FORBIDDEN_KEYS:
            normalized[target_key] = value
        elif target_key not in ALLOWED_KEYS:
            dropped.append(str(key))
        elif target_key in {"sweep_suggestions", "warnings", "missing_data"}:
            normalized[target_key] = as_list(value)
        else:
            normalized[target_key] = value
    if dropped:
        warnings = normalized.get("warnings")
        warnings = as_list(warnings)
        warnings.extend(f"dropped_unknown_field:{key}" for key in dropped)
        normalized["warnings"] = warnings
    return normalized


def validate_advice_payload(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    payload = normalize_advice_payload(payload)
    result = validate_advisory_payload(
        "farm_calculator_advisor",
        payload,
        allowed_fields=ALLOWED_KEYS,
        forbidden_fields=FORBIDDEN_KEYS,
        container_fields={"sweep_suggestions", "warnings", "missing_data"},
    )
    problems: list[str] = list(result.problems)
    suggestions = payload.get("sweep_suggestions")
    if suggestions is not None and not isinstance(suggestions, list):
        problems.append("sweep_suggestions must be a list")
    missing = payload.get("missing_data")
    if missing is not None and not isinstance(missing, list):
        problems.append("missing_data must be a list")
    warnings = payload.get("warnings")
    if warnings is not None and not isinstance(warnings, list):
        problems.append("warnings must be a list")
    return (not problems, list(dict.fromkeys(problems)))


def _user_payload(packet: FeaturePacket) -> str:
    return json.dumps(
        {
            "schema": "CalculatorAdvisorInput.v1",
            "feature_packet": packet.to_dict(),
            "hard_rules": {
                "paper_only": True,
                "execution_allowed": False,
                "llm_may_change_numbers": False,
                "llm_may_set_trade_decision": False,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def request_calculator_advice(
    private_root: Path,
    packet: FeaturePacket,
    provider: ProposalProvider,
    *,
    allow_public_output: bool = False,
) -> CalculatorAdvice:
    permit = preflight_invocation(
        private_root,
        role_id="farm_calculator_advisor",
        source_ref=packet.feature_packet_id,
        input_payload={
            "feature_packet": packet.to_dict(),
            "prompt_version": PROMPT_VERSION,
            "prompt_hash": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:16],
        },
        provider=provider,
        local_only=True,
    )
    if not permit.allowed:
        advice = CalculatorAdvice(
            advisor_ref=stable_id(
                "advisor",
                {"feature_packet_id": packet.feature_packet_id, "status": permit.reason},
            ),
            feature_packet_id=packet.feature_packet_id,
            provider=getattr(provider, "name", "null"),
            model="",
            advice={},
            problems=[permit.reason],
            accepted=False,
        )
        if permit.reason != "duplicate_completed":
            record_invocation(
                private_root,
                permit,
                status="blocked",
                output_ref=advice.advisor_ref,
                problems=advice.problems,
            )
            append_jsonl(advice_path(private_root), advice.to_dict())
        return advice
    try:
        text, usage = provider.generate(SYSTEM_PROMPT, _user_payload(packet))
        record_usage(private_root, usage, allow_public_output=allow_public_output)
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("advice must be a JSON object")
        payload = normalize_advice_payload(payload)
        ok, problems = validate_advice_payload(payload)
        advice = CalculatorAdvice(
            advisor_ref=stable_id("advisor", {"feature_packet_id": packet.feature_packet_id, "advice": payload}),
            feature_packet_id=packet.feature_packet_id,
            provider=usage.provider,
            model=usage.model,
            advice=payload if ok else {},
            problems=problems,
            accepted=ok,
        )
        record_invocation(
            private_root,
            permit,
            status="accepted" if ok else "schema_rejected",
            output_ref=advice.advisor_ref,
            problems=problems,
            usage=usage,
        )
    except (LLMProviderError, json.JSONDecodeError, ValueError) as exc:
        problem = str(exc).strip() or type(exc).__name__
        advice = CalculatorAdvice(
            advisor_ref=stable_id("advisor", {"feature_packet_id": packet.feature_packet_id, "error": type(exc).__name__}),
            feature_packet_id=packet.feature_packet_id,
            provider=getattr(provider, "name", "unknown"),
            model="",
            advice={},
            problems=[problem],
            accepted=False,
        )
        record_invocation(
            private_root,
            permit,
            status="provider_error",
            output_ref=advice.advisor_ref,
            problems=advice.problems,
        )
    append_jsonl(advice_path(private_root), advice.to_dict())
    return advice
