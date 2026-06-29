"""Public-safe provider routing table for product/farm LLM surfaces."""

from __future__ import annotations

import os
from typing import Any, Mapping

from src.research_lab.prompt_registry import prompt_registry_summary


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _has(env: Mapping[str, str], key: str) -> bool:
    return bool(str(env.get(key, "") or "").strip())


def provider_route_table(environ: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    env = environ if environ is not None else os.environ
    prompts = {row["surface"]: row for row in prompt_registry_summary()["rows"]}
    scanner_provider = str(env.get("LLM_PROVIDER") or "disabled").lower()
    product_router = _truthy(str(env.get("PRODUCT_ANALYZER_LLM_ROUTER") or ""))
    strategy_enabled = _truthy(str(env.get("STRATEGY_LAB_LLM_ENABLED") or ""))
    strategy_provider = str(env.get("STRATEGY_LAB_LLM_PROVIDER") or "disabled").lower()
    strategy_model = str(env.get("STRATEGY_LAB_LLM_MODEL_CHEAP") or "calculator")
    routes = [
        {
            "surface": "farm_calculator_advisor",
            "provider": strategy_provider if strategy_enabled else "disabled",
            "model": strategy_model if strategy_enabled else "",
            "input": "FeaturePacket.v1",
            "output": "CalculatorAdvice.v1",
            "cost_logging": "strategy-lab/reports/llm_usage/llm_usage.jsonl",
            "fallback": "deterministic sweep only; advice row records llm_disabled",
            "active": strategy_enabled,
            "prompt_version": prompts["farm_calculator_advisor"]["version"],
            "prompt_hash": prompts["farm_calculator_advisor"]["prompt_hash"],
        },
        {
            "surface": "vip_screenshot",
            "provider": "alibaba" if _has(env, "ALIBABA_API_KEY") else "disabled",
            "model": str(env.get("VISION_MODEL") or env.get("ALIBABA_VISION_MODEL") or ""),
            "input": "screenshot image + bounded prompt",
            "output": "manual/VIP analysis text",
            "cost_logging": "scanner/product budget log, no secrets",
            "fallback": "yandex if configured; otherwise manual_review_required",
            "active": _has(env, "ALIBABA_API_KEY"),
            "prompt_version": prompts["vip_screenshot"]["version"],
            "prompt_hash": prompts["vip_screenshot"]["prompt_hash"],
        },
        {
            "surface": "manual_text_analysis",
            "provider": scanner_provider if product_router else "disabled",
            "model": str(env.get("LLM_CHEAP_MODEL") or env.get("LLM_MODEL") or ""),
            "input": "operator text/chart context",
            "output": "bounded product analysis",
            "cost_logging": "scanner/product budget log, no secrets",
            "fallback": "deterministic/manual-only response",
            "active": product_router and scanner_provider not in {"", "disabled"},
            "prompt_version": prompts["manual_text_analysis"]["version"],
            "prompt_hash": prompts["manual_text_analysis"]["prompt_hash"],
        },
        {
            "surface": "education_faq",
            "provider": scanner_provider if product_router else "disabled",
            "model": str(env.get("LLM_CHEAP_MODEL") or env.get("LLM_MODEL") or ""),
            "input": "FAQ text",
            "output": "educational answer",
            "cost_logging": "scanner/product budget log, no secrets",
            "fallback": "static rules/manual response",
            "active": product_router and scanner_provider not in {"", "disabled"},
            "prompt_version": prompts["education_faq"]["version"],
            "prompt_hash": prompts["education_faq"]["prompt_hash"],
        },
        {
            "surface": "main_card_formatter",
            "provider": "none",
            "model": "",
            "input": "MainPaperConsumerRecord.v1",
            "output": "PaperTelegramPreview.v1",
            "cost_logging": "none",
            "fallback": "not needed; deterministic renderer",
            "active": True,
            "prompt_version": prompts["main_card_formatter"]["version"],
            "prompt_hash": prompts["main_card_formatter"]["prompt_hash"],
        },
        {
            "surface": "paper_telegram_card_formatter",
            "provider": "none",
            "model": "",
            "input": "MainPaperConsumerRecord.v1",
            "output": "PaperTelegramPreview.v1",
            "cost_logging": "none",
            "fallback": "not needed; deterministic renderer",
            "active": True,
            "prompt_version": prompts["paper_telegram_card_formatter"]["version"],
            "prompt_hash": prompts["paper_telegram_card_formatter"]["prompt_hash"],
        },
        {
            "surface": "scanner_news",
            "provider": scanner_provider,
            "model": str(env.get("LLM_CHEAP_MODEL") or env.get("LLM_MODEL") or ""),
            "input": "scanner/news text",
            "output": "scanner advisory/context",
            "cost_logging": "logs/scout/llm_budget.jsonl",
            "fallback": "scanner runs without LLM context",
            "active": scanner_provider not in {"", "disabled"},
            "prompt_version": prompts["scanner_news"]["version"],
            "prompt_hash": prompts["scanner_news"]["prompt_hash"],
        },
    ]
    return routes


def provider_route_summary(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    routes = provider_route_table(environ)
    return {
        "schema": "ProviderRoutingStatus.v1",
        "routes": routes,
        "active": sum(1 for row in routes if row["active"]),
        "disabled": sum(1 for row in routes if not row["active"]),
        "secrets_exposed": False,
    }
