"""LLM role registry for the paper/research swarm.

The registry is public-safe metadata. It defines which LLM role may read which
artifact, which schema it must return, where private logs go, and which fields
are forbidden. It does not call providers and does not grant execution authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from src.research_lab.advisory_payload_validator import validate_advisory_payload

SCHEMA = "AgentRoleRegistry.v1"
CONTAINER_FIELDS = (
    "evidence",
    "warnings",
    "missing_data",
    "sweep_suggestions",
    "next_test_dimensions",
    "counterfactual_tests",
    "parameter_hypotheses",
    "evidence_refs",
    "learning_tags",
    "memory_tags",
    "chart_facts",
    "scenario_notes",
    "risk_notes",
)

CRITICAL_FORBIDDEN_FIELDS = (
    "entry",
    "entry_zone",
    "stop",
    "stop_loss",
    "take_profit",
    "take_profit_plan",
    "side",
    "paper_ready",
    "validator_verdict",
    "hard_status",
    "order",
    "size",
    "close",
    "close_order",
    "execute",
    "execution_allowed",
    "auto_trade",
)


@dataclass(frozen=True)
class AgentRoleContract:
    role_id: str
    title: str
    provider_route: str
    model_hint: str
    input_contract: str
    output_contract: str
    allowed_fields: tuple[str, ...]
    forbidden_fields: tuple[str, ...]
    private_log_label: str
    max_calls_per_cycle: int
    fallback: str
    advisory_only: bool = True
    paper_only: bool = True
    execution_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def role_contracts() -> tuple[AgentRoleContract, ...]:
    common_review_fields = (
        "summary",
        "evidence",
        "diagnosis",
        "confidence",
        "warnings",
        "next_test_dimensions",
    )
    return (
        AgentRoleContract(
            role_id="farm_calculator_advisor",
            title="Farm calculator advisor",
            provider_route="ollama:calculator; alibaba fallback only after bench",
            model_hint="calculator",
            input_contract="DecisionFeaturePacket.v1",
            output_contract="CalculatorAdvice.v1",
            allowed_fields=(
                "situation_class",
                "advisory_reason",
                "user_facing_analysis",
                "rejection_reason",
                "missing_data",
                "sweep_suggestions",
                "confidence",
                "warnings",
            ),
            forbidden_fields=CRITICAL_FORBIDDEN_FIELDS,
            private_log_label="strategy-lab/state/llm_advice/calculator_advice.jsonl",
            max_calls_per_cycle=3,
            fallback="deterministic sweep only",
        ),
        AgentRoleContract(
            role_id="calculator_context_classifier",
            title="Local calculator context classifier",
            provider_route="ollama:calculator only",
            model_hint="calculator",
            input_contract="DecisionFeaturePacket.v1",
            output_contract="CalculatorContextPass.v1",
            allowed_fields=("situation_class", "missing_data", "confidence", "warnings"),
            forbidden_fields=CRITICAL_FORBIDDEN_FIELDS,
            private_log_label="strategy-lab/state/llm_advice/calculator_swarm.jsonl",
            max_calls_per_cycle=1,
            fallback="deterministic feature labels",
        ),
        AgentRoleContract(
            role_id="calculator_hypothesis_proposer",
            title="Local calculator hypothesis proposer",
            provider_route="ollama:calculator only",
            model_hint="calculator",
            input_contract="DecisionFeaturePacket.v1 + CalculatorContextPass.v1",
            output_contract="CalculatorHypothesisPass.v1",
            allowed_fields=(
                "advisory_reason",
                "user_facing_analysis",
                "sweep_suggestions",
                "confidence",
                "warnings",
            ),
            forbidden_fields=CRITICAL_FORBIDDEN_FIELDS,
            private_log_label="strategy-lab/state/llm_advice/calculator_swarm.jsonl",
            max_calls_per_cycle=1,
            fallback="no new hypothesis",
        ),
        AgentRoleContract(
            role_id="calculator_hypothesis_critic",
            title="Local calculator hypothesis critic",
            provider_route="ollama:calculator only",
            model_hint="calculator",
            input_contract="DecisionFeaturePacket.v1 + prior calculator passes",
            output_contract="CalculatorCriticPass.v1",
            allowed_fields=("proposal_quality", "rejection_reason", "confidence", "warnings"),
            forbidden_fields=CRITICAL_FORBIDDEN_FIELDS,
            private_log_label="strategy-lab/state/llm_advice/calculator_swarm.jsonl",
            max_calls_per_cycle=1,
            fallback="reject unreviewed hypothesis",
        ),
        AgentRoleContract(
            role_id="outcome_reviewer",
            title="Paper outcome trader analyst",
            provider_route="alibaba primary while DeepSeek/Kimi/GLM are not configured; local bulk fallback",
            model_hint="qwen-plus/qwen-max class",
            input_contract="OutcomeLearningCase.v1 + read-only planned/observed trade facts + candle path",
            output_contract="OutcomeReview.v1",
            allowed_fields=common_review_fields
            + (
                "review_kind",
                "outcome_class",
                "outcome_bucket",
                "root_cause",
                "missed_class",
                "path_diagnosis",
                "market_regime_notes",
                "counterfactual_summary",
                "counterfactual_delta_class",
                "counterfactual_tests",
                "parameter_hypotheses",
                "confidence_basis",
                "evidence_refs",
                "learning_tags",
                "actionability",
                "candidate_rule",
                "requires_retest",
                "risk_to_good_trades",
                "farm_memory_update",
                "positive_pattern_notes",
                "retest_priority",
                "memory_tags",
                "replay_needed",
            ),
            forbidden_fields=CRITICAL_FORBIDDEN_FIELDS,
            private_log_label="strategy-lab/state/llm_advice/outcome_reviews.jsonl",
            max_calls_per_cycle=25,
            fallback="deterministic paper_signals.review diagnosis",
        ),
        AgentRoleContract(
            role_id="validator_reviewer",
            title="Validator reviewer",
            provider_route="alibaba primary while DeepSeek/Kimi/GLM are not configured",
            model_hint="qwen-plus/qwen-max class",
            input_contract="ValidatorTaxonomy.v1 + setup memory summary + candidate facts",
            output_contract="ValidatorReview.v1",
            allowed_fields=common_review_fields
            + (
                "validator_class",
                "failure_mode",
                "underpowered_reason",
                "data_gap",
            ),
            forbidden_fields=CRITICAL_FORBIDDEN_FIELDS,
            private_log_label="strategy-lab/state/llm_advice/validator_reviews.jsonl",
            max_calls_per_cycle=15,
            fallback="deterministic validator taxonomy only",
        ),
        AgentRoleContract(
            role_id="trader_context_reviewer",
            title="Trader multimodal context reviewer",
            provider_route="bounded multimodal provider; deterministic fallback",
            model_hint="vision-capable advisory model",
            input_contract="MarketContextSnapshot.v1 + sanitized VisualEvidence.v1",
            output_contract="TraderContextAdvice.v1",
            allowed_fields=(
                "summary", "evidence", "confidence", "warnings",
                "market_regime_notes", "path_diagnosis", "learning_tags",
            ),
            forbidden_fields=CRITICAL_FORBIDDEN_FIELDS,
            private_log_label="strategy-lab/state/llm_advice/trader_context_reviews.jsonl",
            max_calls_per_cycle=10,
            fallback="deterministic TraderSupervisorFSM.v1 only",
        ),
        AgentRoleContract(
            role_id="system_analyst",
            title="System outcome and feedback analyst",
            provider_route="strong cloud reviewer over sanitized evidence",
            model_hint="reasoning reviewer class",
            input_contract="frozen outcome/validation/supervisor evidence pack",
            output_contract="SystemAnalystFeedbackDraft.v1",
            allowed_fields=(
                "summary", "evidence", "diagnosis", "confidence", "warnings",
                "next_test_dimensions", "counterfactual_summary", "learning_tags",
            ),
            forbidden_fields=CRITICAL_FORBIDDEN_FIELDS,
            private_log_label="strategy-lab/state/llm_advice/system_analyst_drafts.jsonl",
            max_calls_per_cycle=20,
            fallback="deterministic outcome taxonomy and no feedback promotion",
        ),
        AgentRoleContract(
            role_id="source_trust_reviewer",
            title="Source trust reviewer",
            provider_route="alibaba primary; scanner can run without LLM",
            model_hint="qwen-plus/qwen-turbo class",
            input_contract="ScannerEvent.v1 + source metadata + later outcome summary",
            output_contract="SourceTrustEvent.v1",
            allowed_fields=(
                "summary",
                "source_class",
                "trust_delta",
                "evidence",
                "confidence",
                "warnings",
                "followup_window",
                "memory_tags",
            ),
            forbidden_fields=CRITICAL_FORBIDDEN_FIELDS,
            private_log_label="strategy-lab/state/llm_advice/source_trust_events.jsonl",
            max_calls_per_cycle=25,
            fallback="source stored as unknown_trust",
        ),
        AgentRoleContract(
            role_id="vip_vision_reviewer",
            title="VIP chart vision reviewer",
            provider_route="alibaba vision primary; yandex fallback if configured",
            model_hint="qwen-vl-plus class",
            input_contract="screenshot artifact + product prompt",
            output_contract="VipVisionReview.v1",
            allowed_fields=(
                "summary",
                "visible_timeframe",
                "chart_facts",
                "scenario_notes",
                "risk_notes",
                "confidence",
                "warnings",
            ),
            forbidden_fields=CRITICAL_FORBIDDEN_FIELDS,
            private_log_label="logs/users/<chat>/premium_log.jsonl",
            max_calls_per_cycle=10,
            fallback="manual_review_required",
        ),
        AgentRoleContract(
            role_id="education_qa",
            title="Education Q&A",
            provider_route="alibaba/yandex shared product router",
            model_hint="cheap text model",
            input_contract="user education question",
            output_contract="EducationAnswer.v1",
            allowed_fields=("answer", "risk_note", "exchange_context", "confidence", "warnings"),
            forbidden_fields=CRITICAL_FORBIDDEN_FIELDS,
            private_log_label="scanner/product budget log",
            max_calls_per_cycle=50,
            fallback="static FAQ/manual response",
        ),
    )


def role_by_id(role_id: str) -> AgentRoleContract:
    for role in role_contracts():
        if role.role_id == role_id:
            return role
    raise KeyError(f"unknown role_id: {role_id}")


def validate_role_payload(role_id: str, payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    role = role_by_id(role_id)
    result = validate_advisory_payload(
        role_id,
        payload,
        allowed_fields=role.allowed_fields,
        forbidden_fields=role.forbidden_fields,
        container_fields=CONTAINER_FIELDS,
    )
    return (result.ok, result.problems)


def role_registry_summary() -> dict[str, Any]:
    rows = [role.to_dict() for role in role_contracts()]
    return {
        "schema": SCHEMA,
        "roles": len(rows),
        "rows": rows,
        "advisory_only": True,
        "paper_only": True,
        "execution_allowed": False,
        "secrets_exposed": False,
    }
