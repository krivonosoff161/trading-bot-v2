"""Advisory-only LLM review records for outcomes, validator rejects, and sources."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from src.research_lab.agent_role_registry import role_by_id, validate_role_payload
from src.research_lab.lineage_contract import append_jsonl, stable_id, utc_now
from src.research_lab.llm_provider import LLMProviderError, ProposalProvider, record_usage

OUTCOME_SCHEMA = "OutcomeReview.v1"
VALIDATOR_SCHEMA = "ValidatorReview.v1"
SOURCE_TRUST_SCHEMA = "SourceTrustEvent.v1"

ROLE_TO_SCHEMA = {
    "outcome_reviewer": OUTCOME_SCHEMA,
    "validator_reviewer": VALIDATOR_SCHEMA,
    "source_trust_reviewer": SOURCE_TRUST_SCHEMA,
}

ROLE_TO_PATH = {
    "outcome_reviewer": ("state", "llm_advice", "outcome_reviews.jsonl"),
    "validator_reviewer": ("state", "llm_advice", "validator_reviews.jsonl"),
    "source_trust_reviewer": ("state", "llm_advice", "source_trust_events.jsonl"),
}

ROLE_SYSTEM_PROMPTS = {
    "outcome_reviewer": (
        "You are an advisory paper-trading outcome reviewer. Return JSON only. "
        "Classify why a completed paper setup won, lost, expired, missed entry, "
        "or gave back. Use the supplied OutcomeLearningCase review_kind and "
        "outcome_bucket as hard context. You may suggest bounded next-test "
        "dimensions and counterfactual hypotheses, but deterministic code must "
        "test them later. You must not change entry, "
        "stop, take profit, side, validator verdict, paper_ready, order, size, or execution."
        " Keep the object compact: summary, review_kind, outcome_bucket, diagnosis, "
        "confidence, evidence, warnings, next_test_dimensions, learning_tags, "
        "actionability. Confidence must be a number from 0 to 1."
    ),
    "validator_reviewer": (
        "You are an advisory validator reviewer. Return JSON only. Explain why a "
        "candidate failed, was underpowered, needed data, or looked regime-only. "
        "You may suggest bounded next tests. You must not change hard validator status, "
        "paper_ready, trade levels, order, size, or execution."
        " Keep the object compact: summary, validator_class, confidence, evidence, warnings, "
        "next_test_dimensions. Confidence must be a number from 0 to 1."
    ),
    "source_trust_reviewer": (
        "You are an advisory source-trust reviewer. Return JSON only. Classify a "
        "scanner/news/source event and whether later outcomes should increase or "
        "decrease trust in similar sources. You must not create trades, paper_ready, "
        "validator verdicts, orders, sizes, or execution."
        " Keep the object compact: summary, source_class, trust_delta, confidence, evidence, "
        "warnings, followup_window. Confidence must be a number from 0 to 1."
    ),
}


@dataclass(frozen=True)
class LLMRoleReview:
    review_id: str
    role_id: str
    source_ref: str
    provider: str
    model: str
    payload: dict[str, Any]
    accepted: bool
    problems: list[str] = field(default_factory=list)
    schema: str = ""
    paper_only: bool = True
    execution_allowed: bool = False
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def review_path(private_root: Path, role_id: str) -> Path:
    parts = ROLE_TO_PATH[role_id]
    return Path(private_root).joinpath(*parts)


def validate_review_payload(role_id: str, payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    return validate_role_payload(role_id, payload)


def normalize_review_payload(role_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Map common provider synonyms into the canonical role contract.

    This is not a safety bypass: forbidden trade/execution fields remain present
    and will still be rejected by validate_role_payload().
    """
    normalized = dict(payload)
    for wrapper in ("review", "result", "output", "analysis"):
        nested = normalized.get(wrapper)
        if isinstance(nested, Mapping):
            normalized = dict(nested)
            break
    if "reason" in normalized and "summary" not in normalized:
        normalized["summary"] = normalized.pop("reason")
    if "explanation" in normalized and "summary" not in normalized:
        normalized["summary"] = normalized.pop("explanation")
    if "suggested_next_test_dimensions" in normalized and "next_test_dimensions" not in normalized:
        normalized["next_test_dimensions"] = normalized.pop("suggested_next_test_dimensions")
    if "suggested_next_tests" in normalized and "next_test_dimensions" not in normalized:
        normalized["next_test_dimensions"] = normalized.pop("suggested_next_tests")
    if "suggestion" in normalized and "next_test_dimensions" not in normalized:
        normalized["next_test_dimensions"] = normalized.pop("suggestion")
    if "next_test_dimensions" in normalized and isinstance(normalized["next_test_dimensions"], str):
        normalized["next_test_dimensions"] = [normalized["next_test_dimensions"]]
    if "evidence" in normalized and isinstance(normalized["evidence"], str):
        normalized["evidence"] = [normalized["evidence"]]
    if "evidence_refs" in normalized and isinstance(normalized["evidence_refs"], str):
        normalized["evidence_refs"] = [normalized["evidence_refs"]]
    if "warnings" in normalized and isinstance(normalized["warnings"], str):
        normalized["warnings"] = [normalized["warnings"]]
    if "learning_tags" in normalized and isinstance(normalized["learning_tags"], str):
        normalized["learning_tags"] = [normalized["learning_tags"]]
    if "memory_tags" in normalized and isinstance(normalized["memory_tags"], str):
        normalized["memory_tags"] = [normalized["memory_tags"]]
    confidence = normalized.get("confidence")
    if isinstance(confidence, str):
        try:
            normalized["confidence"] = float(confidence.strip().rstrip("%")) / (
                100.0 if confidence.strip().endswith("%") else 1.0
            )
        except ValueError:
            pass

    if role_id == "outcome_reviewer":
        for key in ("classification", "outcome", "outcome_classification"):
            if key in normalized and "diagnosis" not in normalized:
                normalized["diagnosis"] = normalized.pop(key)
        if "case_type" in normalized and "review_kind" not in normalized:
            normalized["review_kind"] = normalized.pop("case_type")
        if "bucket" in normalized and "outcome_bucket" not in normalized:
            normalized["outcome_bucket"] = normalized.pop("bucket")
        if "tags" in normalized and "learning_tags" not in normalized:
            normalized["learning_tags"] = normalized.pop("tags")
    elif role_id == "validator_reviewer":
        if "classification" in normalized and "validator_class" not in normalized:
            normalized["validator_class"] = normalized.pop("classification")
        if normalized.pop("regime_only", False) and "validator_class" not in normalized:
            normalized["validator_class"] = "regime_only"
        if "required_data" in normalized and "data_gap" not in normalized:
            normalized["data_gap"] = normalized.pop("required_data")
    elif role_id == "source_trust_reviewer":
        for key in ("classification", "source_classification", "source_trust_classification", "trust_outcome"):
            if key in normalized and "source_class" not in normalized:
                normalized["source_class"] = normalized.pop(key)
        if "trust_adjustment" in normalized and "trust_delta" not in normalized:
            normalized["trust_delta"] = normalized.pop("trust_adjustment")
    return normalized


def build_review_input(role_id: str, source_payload: Mapping[str, Any]) -> str:
    role = role_by_id(role_id)
    return json.dumps(
        {
            "schema": "LLMRoleReviewInput.v1",
            "role_id": role_id,
            "output_contract": role.output_contract,
            "allowed_output_keys": list(role.allowed_fields),
            "forbidden_output_keys": list(role.forbidden_fields),
            "source_payload": source_payload,
            "hard_rules": {
                "paper_only": True,
                "execution_allowed": False,
                "llm_may_change_trade_numbers": False,
                "llm_may_set_validator_status": False,
                "llm_may_set_paper_ready": False,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def request_role_review(
    private_root: Path,
    *,
    role_id: str,
    source_ref: str,
    source_payload: Mapping[str, Any],
    provider: ProposalProvider,
    allow_public_output: bool = False,
) -> LLMRoleReview:
    if role_id not in ROLE_TO_SCHEMA:
        raise KeyError(f"unsupported review role: {role_id}")
    if not provider.configured:
        review = LLMRoleReview(
            review_id=stable_id("llmr", {"role_id": role_id, "source_ref": source_ref, "status": "disabled"}),
            role_id=role_id,
            source_ref=source_ref,
            provider=getattr(provider, "name", "null"),
            model="",
            payload={},
            accepted=False,
            problems=["provider_not_configured"],
            schema=ROLE_TO_SCHEMA[role_id],
        )
        append_jsonl(review_path(private_root, role_id), review.to_dict())
        return review
    try:
        text, usage = provider.generate(ROLE_SYSTEM_PROMPTS[role_id], build_review_input(role_id, source_payload))
        record_usage(private_root, usage, allow_public_output=allow_public_output)
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("review must be a JSON object")
        payload = normalize_review_payload(role_id, payload)
        accepted, problems = validate_review_payload(role_id, payload)
        review = LLMRoleReview(
            review_id=stable_id("llmr", {"role_id": role_id, "source_ref": source_ref, "payload": payload}),
            role_id=role_id,
            source_ref=source_ref,
            provider=usage.provider,
            model=usage.model,
            payload=payload if accepted else {},
            accepted=accepted,
            problems=problems,
            schema=ROLE_TO_SCHEMA[role_id],
        )
    except (LLMProviderError, json.JSONDecodeError, ValueError) as exc:
        review = LLMRoleReview(
            review_id=stable_id("llmr", {"role_id": role_id, "source_ref": source_ref, "error": type(exc).__name__}),
            role_id=role_id,
            source_ref=source_ref,
            provider=getattr(provider, "name", "unknown"),
            model="",
            payload={},
            accepted=False,
            problems=[str(exc).strip() or type(exc).__name__],
            schema=ROLE_TO_SCHEMA[role_id],
        )
    append_jsonl(review_path(private_root, role_id), review.to_dict())
    return review


def review_summary(private_root: Path) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for role_id, parts in ROLE_TO_PATH.items():
        path = Path(private_root).joinpath(*parts)
        total = 0
        accepted = 0
        by_problem: dict[str, int] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    by_problem["invalid_json"] = by_problem.get("invalid_json", 0) + 1
                    continue
                total += 1
                if bool(row.get("accepted")):
                    accepted += 1
                for problem in row.get("problems") or []:
                    key = str(problem)
                    by_problem[key] = by_problem.get(key, 0) + 1
        rows[role_id] = {
            "path_label": "/".join(("strategy-lab", *parts)),
            "rows": total,
            "accepted": accepted,
            "rejected": max(0, total - accepted),
            "by_problem": by_problem,
        }
    return {
        "schema": "LLMRoleReviewSummary.v1",
        "roles": rows,
        "paper_only": True,
        "execution_allowed": False,
        "secrets_exposed": False,
    }
