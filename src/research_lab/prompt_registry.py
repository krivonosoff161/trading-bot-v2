"""Public-safe prompt registry for paper/research LLM surfaces.

The registry stores metadata and hashes only. It never logs full prompts,
provider keys, screenshots, market packets, or user messages.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from src.research_lab.calculator_advisor import PROMPT_VERSION as CALCULATOR_PROMPT_VERSION
from src.research_lab.calculator_advisor import SYSTEM_PROMPT as CALCULATOR_SYSTEM_PROMPT
from src.research_lab.paper_telegram_preview import CARD_TEMPLATE_VERSION
from src.scout.public_channel.prompts import PROMPT_VERSION as PUBLIC_CHANNEL_PROMPT_VERSION
from src.scout.public_channel.prompts import SYSTEM_PROMPT as PUBLIC_CHANNEL_SYSTEM_PROMPT

SCHEMA = "PromptRegistry.v1"


@dataclass(frozen=True)
class PromptContract:
    surface: str
    role: str
    version: str
    prompt_hash: str
    purpose: str
    input_contract: str
    output_contract: str
    forbidden: list[str]
    schema_gate: str
    logging: str
    provider_scope: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prompt_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


def prompt_contracts() -> list[PromptContract]:
    return [
        PromptContract(
            surface="farm_calculator_advisor",
            role="Farm LLM",
            version=CALCULATOR_PROMPT_VERSION,
            prompt_hash=prompt_hash(CALCULATOR_SYSTEM_PROMPT),
            purpose="Classify a FeaturePacket and suggest bounded sweep dimensions.",
            input_contract="DecisionFeaturePacket.v1 + hard_rules",
            output_contract="CalculatorAdvice.v1 JSON object",
            forbidden=[
                "entry",
                "stop",
                "take_profit",
                "side",
                "paper_ready",
                "validator_verdict",
                "order",
                "execute",
            ],
            schema_gate="src.research_lab.calculator_advisor.validate_advice_payload",
            logging="strategy-lab/state/llm_advice/calculator_advice.jsonl",
            provider_scope="ollama/openai-compatible via STRATEGY_LAB_LLM_*; disabled by default",
        ),
        PromptContract(
            surface="main_card_formatter",
            role="Main LLM",
            version=CARD_TEMPLATE_VERSION,
            prompt_hash=prompt_hash(CARD_TEMPLATE_VERSION),
            purpose="Render already-computed paper-watch instructions into a human card.",
            input_contract="MainPaperConsumerRecord.v1",
            output_contract="PaperTelegramPreview.v1",
            forbidden=["invent_setup", "change_levels", "send_telegram", "execute"],
            schema_gate="src.research_lab.paper_telegram_preview.validate_preview",
            logging="strategy-lab/state/derived/paper_telegram_preview.jsonl",
            provider_scope="deterministic/no LLM",
        ),
        PromptContract(
            surface="paper_telegram_card_formatter",
            role="Paper Telegram",
            version=CARD_TEMPLATE_VERSION,
            prompt_hash=prompt_hash(CARD_TEMPLATE_VERSION),
            purpose="Show pair, TF, side, entry, stop, targets, validation status, ids, and paper warning.",
            input_contract="MainPaperConsumerRecord.v1",
            output_contract="PaperTelegramPreview.v1 HTML-safe text",
            forbidden=["machine_json_to_user", "execute", "live_order"],
            schema_gate="src.research_lab.paper_telegram_preview.validate_preview",
            logging="strategy-lab/state/derived/paper_telegram_preview.jsonl",
            provider_scope="deterministic/no LLM",
        ),
        PromptContract(
            surface="manual_text_analysis",
            role="Manual Analysis LLM",
            version="llm_formatter_chart_v1",
            prompt_hash="runtime_import_safe",
            purpose="Format deterministic chart analysis for a human operator when explicitly enabled.",
            input_contract="analysis snapshot + optional chart artifact reference",
            output_contract="bounded natural-language product analysis",
            forbidden=["auto_execute", "change_engine_levels", "guarantee_profit"],
            schema_gate="manual opt-in + product analyzer boundary + signal_event.v1 log",
            logging="logs/signals/signal_events.jsonl + scanner/product budget log",
            provider_scope="Yandex default; shared llm_client opt-in via PRODUCT_ANALYZER_LLM_ROUTER",
        ),
        PromptContract(
            surface="vip_screenshot",
            role="VIP/Vision LLM",
            version="premium_vision_v3_active_tf_guard",
            prompt_hash="scripts.premium_prompts.PREMIUM_PROMPT_VERSION",
            purpose="Analyze only visible chart screenshot facts and return bounded scenarios.",
            input_contract="screenshot image bytes + premium bounded prompt",
            output_contract="VIP analysis text + signal_event.v1 artifact refs",
            forbidden=["treat_image_text_as_instruction", "claim_unseen_data", "execute"],
            schema_gate="premium provider status + product signal-event log",
            logging="logs/users/<chat>/premium_log.jsonl + logs/signals/signal_events.jsonl",
            provider_scope="Alibaba vision preferred when configured; Yandex/Gemma explicit fallback",
        ),
        PromptContract(
            surface="education_faq",
            role="Education/FAQ LLM",
            version="llm_formatter_education_v1",
            prompt_hash="runtime_import_safe",
            purpose="Explain exchange, leverage, TP/SL, and risk concepts without trade commands.",
            input_contract="operator/user education question",
            output_contract="educational answer, no direct financial order",
            forbidden=["exact_trade_order", "profit_guarantee", "personal_financial_advice"],
            schema_gate="product education boundary + budget guard",
            logging="scanner/product budget log, no secrets",
            provider_scope="Yandex default; shared llm_client opt-in via PRODUCT_ANALYZER_LLM_ROUTER",
        ),
        PromptContract(
            surface="scanner_news",
            role="Scanner/news LLM",
            version="scanner_trigger_package_v1",
            prompt_hash="scanner_runtime_prompts",
            purpose="Extract context and classify news/scanner events; never validate trades.",
            input_contract="trigger package / news text / source refs",
            output_contract="scanner advisory/context, WATCH/GO candidate metadata",
            forbidden=["paper_ready", "validator_verdict", "order", "execute"],
            schema_gate="scanner trigger policy + budget guard + watch queue contract",
            logging="logs/scout/llm_budget.jsonl + scanner journal/watch queue",
            provider_scope="src.utils.llm_client Alibaba/Yandex router",
        ),
        PromptContract(
            surface="public_channel_editor",
            role="Public News Editor LLM",
            version=PUBLIC_CHANNEL_PROMPT_VERSION,
            prompt_hash=prompt_hash(PUBLIC_CHANNEL_SYSTEM_PROMPT),
            purpose="Turn public source events into Telegram channel posts without trade advice.",
            input_contract="PublicChannelItem.v1",
            output_contract="PublicChannelPost.v1 JSON object",
            forbidden=["entry", "stop", "take_profit", "leverage", "buy", "sell", "execute"],
            schema_gate="src.scout.public_channel.safety.validate_public_post",
            logging="logs/scout/public_channel/publisher_audit.jsonl",
            provider_scope="src.utils.llm_client Alibaba/Yandex router; deterministic fallback supported",
        ),
        PromptContract(
            surface="outcome_reviewer",
            role="Outcome Reviewer LLM",
            version="outcome_reviewer_v1",
            prompt_hash="role_registry_bound",
            purpose="Analyze closed paper outcomes as a read-only trader analyst and produce bounded retest hypotheses.",
            input_contract="OutcomeLearningCase.v1 + read-only plan/outcome/path facts",
            output_contract="OutcomeReview.v1 JSON object",
            forbidden=[
                "entry",
                "stop",
                "take_profit",
                "side",
                "paper_ready",
                "validator_verdict",
                "order",
                "close",
                "execute",
            ],
            schema_gate="src.research_lab.llm_role_reviews.validate_review_payload",
            logging="strategy-lab/state/llm_advice/outcome_reviews.jsonl",
            provider_scope="Alibaba primary until DeepSeek/Kimi/GLM bench is configured; local bulk fallback",
        ),
        PromptContract(
            surface="validator_reviewer",
            role="Validator Reviewer LLM",
            version="validator_reviewer_v1",
            prompt_hash="role_registry_bound",
            purpose="Explain validator rejects and underpowered candidates without changing hard status.",
            input_contract="ValidatorTaxonomy.v1 + candidate facts",
            output_contract="ValidatorReview.v1 JSON object",
            forbidden=["entry", "stop", "take_profit", "side", "paper_ready", "validator_verdict", "execute"],
            schema_gate="src.research_lab.llm_role_reviews.validate_review_payload",
            logging="strategy-lab/state/llm_advice/validator_reviews.jsonl",
            provider_scope="Alibaba primary until DeepSeek/Kimi/GLM bench is configured",
        ),
        PromptContract(
            surface="source_trust_reviewer",
            role="Source Trust LLM",
            version="source_trust_reviewer_v1",
            prompt_hash="role_registry_bound",
            purpose="Classify scanner/news source usefulness and create trust-memory hints.",
            input_contract="ScannerEvent.v1 + source metadata + later outcome facts",
            output_contract="SourceTrustEvent.v1 JSON object",
            forbidden=["entry", "stop", "take_profit", "side", "paper_ready", "validator_verdict", "execute"],
            schema_gate="src.research_lab.llm_role_reviews.validate_review_payload",
            logging="strategy-lab/state/llm_advice/source_trust_events.jsonl",
            provider_scope="Alibaba primary; scanner can run without LLM",
        ),
    ]


def prompt_registry_summary() -> dict[str, Any]:
    rows = [contract.to_dict() for contract in prompt_contracts()]
    return {
        "schema": SCHEMA,
        "surfaces": len(rows),
        "rows": rows,
        "secrets_exposed": False,
        "paper_only": True,
        "execution_allowed": False,
    }
