# -*- coding: utf-8 -*-

import json
import urllib.error

import pytest

from src.research_lab.llm_provider import (
    LLMProviderError,
    NullProposalProvider,
    OllamaProposalProvider,
    OpenAICompatibleProvider,
    SyntheticProposalProvider,
    load_provider,
    record_usage,
    today_usage,
    usage_log_path,
)

_FULL_ENV = {
    "STRATEGY_LAB_LLM_ENABLED": "1",
    "STRATEGY_LAB_LLM_PROVIDER": "alibaba",
    "STRATEGY_LAB_LLM_BASE_URL": "https://example.invalid/compatible-mode/v1",
    "STRATEGY_LAB_LLM_API_KEY": "sk-SECRET-do-not-log",
    "STRATEGY_LAB_LLM_MODEL_CHEAP": "qwen-test",
}


def _envelope(content: str, total_tokens: int = 1000) -> dict:
    return {"choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 600, "total_tokens": total_tokens}}


# ── provider config gates ──────────────────────────────────────────────────────

def test_disabled_env_returns_null_provider():
    p = load_provider({})
    assert isinstance(p, NullProposalProvider) and p.configured is False


def test_null_provider_never_networks():
    with pytest.raises(LLMProviderError):
        NullProposalProvider().generate("s", "u")  # raises, no network


def test_enabled_without_key_falls_back_to_null():
    env = dict(_FULL_ENV)
    env.pop("STRATEGY_LAB_LLM_API_KEY")
    p = load_provider(env)
    assert isinstance(p, NullProposalProvider) and p.configured is False


def test_enabled_without_base_url_falls_back_to_null():
    env = dict(_FULL_ENV)
    env.pop("STRATEGY_LAB_LLM_BASE_URL")
    assert load_provider(env).configured is False


def test_synthetic_requires_allow_flag():
    env = {"STRATEGY_LAB_LLM_ENABLED": "1", "STRATEGY_LAB_LLM_PROVIDER": "synthetic"}
    assert load_provider(env, allow_synthetic=False).configured is False
    p = load_provider(env, allow_synthetic=True, synthetic_candidates=[{"x": 1}])
    assert isinstance(p, SyntheticProposalProvider) and p.configured is True


def test_openai_compatible_configured_with_full_env():
    p = load_provider(_FULL_ENV)
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.configured is True and p.name == "alibaba"


def test_ollama_provider_needs_no_api_key():
    env = {
        "STRATEGY_LAB_LLM_ENABLED": "1",
        "STRATEGY_LAB_LLM_PROVIDER": "ollama",
        "STRATEGY_LAB_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
        "STRATEGY_LAB_LLM_MODEL_CHEAP": "calculator",
    }
    p = load_provider(env)
    assert isinstance(p, OllamaProposalProvider)
    assert p.configured is True and p.name == "ollama"


def test_mocked_ollama_has_no_auth_header_and_zero_cost():
    seen = {}

    def fake_post(url, payload, headers, timeout):
        seen["url"] = url
        seen["headers"] = headers
        seen["payload"] = payload
        return _envelope('{"proposals": []}', total_tokens=200)

    p = load_provider(
        {
            "STRATEGY_LAB_LLM_ENABLED": "1",
            "STRATEGY_LAB_LLM_PROVIDER": "ollama",
            "STRATEGY_LAB_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "STRATEGY_LAB_LLM_MODEL_CHEAP": "calculator",
        },
        http_post=fake_post,
    )
    text, usage = p.generate("system", "user")
    assert text == '{"proposals": []}'
    assert usage.provider == "ollama" and usage.model == "calculator"
    assert usage.total_tokens == 200 and usage.cost_rub == 0.0
    assert seen["url"].endswith("/chat/completions")
    assert "Authorization" not in seen["headers"]
    assert seen["payload"]["temperature"] == 0


def test_scanner_style_alibaba_aliases_work_only_after_strategy_gate():
    env = {
        "STRATEGY_LAB_LLM_ENABLED": "1",
        "LLM_PROVIDER": "alibaba",
        "ALIBABA_BASE_URL": "https://example.invalid/compatible-mode/v1",
        "ALIBABA_API_KEY": "sk-SECRET-do-not-log",
        "LLM_CHEAP_MODEL": "qwen-test",
    }
    p = load_provider(env)
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.configured is True and p.name == "alibaba"


def test_scanner_style_aliases_do_not_enable_provider_without_lab_gate():
    env = {
        "LLM_PROVIDER": "alibaba",
        "ALIBABA_BASE_URL": "https://example.invalid/compatible-mode/v1",
        "ALIBABA_API_KEY": "sk-SECRET-do-not-log",
        "LLM_CHEAP_MODEL": "qwen-test",
    }
    assert load_provider(env).configured is False


# ── mocked HTTP transport (no real network) ────────────────────────────────────

def test_mocked_http_parses_content_and_computes_cost():
    seen = {}

    def fake_post(url, payload, headers, timeout):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization", "")
        return _envelope('{"proposals": []}', total_tokens=2000)

    p = load_provider(_FULL_ENV, http_post=fake_post)
    text, usage = p.generate("system", "user")
    assert text == '{"proposals": []}'
    assert usage.total_tokens == 2000 and usage.cost_rub > 0
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"].startswith("Bearer ")  # key only in the header


def test_structured_json_payload_does_not_set_max_tokens():
    seen = {}

    def fake_post(url, payload, headers, timeout):
        seen["payload"] = payload
        return _envelope('{"proposals": []}', total_tokens=100)

    p = load_provider(_FULL_ENV, http_post=fake_post)
    p.generate("system", "user")

    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert "max_tokens" not in seen["payload"]


def test_finish_reason_length_is_truncated_json_error():
    def fake_post(url, payload, headers, timeout):
        return {
            "choices": [{"finish_reason": "length", "message": {"content": '{"proposals": ['}}],
            "usage": {"total_tokens": 100},
        }

    p = load_provider(_FULL_ENV, http_post=fake_post)
    with pytest.raises(LLMProviderError, match="truncated_json"):
        p.generate("system", "user")


def test_mocked_http_error_raises_provider_error():
    def boom(url, payload, headers, timeout):
        raise urllib.error.URLError("offline")

    p = load_provider(_FULL_ENV, http_post=boom)
    with pytest.raises(LLMProviderError):
        p.generate("s", "u")


def test_mocked_http_missing_choices_raises():
    p = load_provider(_FULL_ENV, http_post=lambda *a: {"unexpected": True})
    with pytest.raises(LLMProviderError):
        p.generate("s", "u")


def test_synthetic_provider_is_offline_zero_cost():
    p = SyntheticProposalProvider([{"setup_family": "momentum_breakout"}])
    text, usage = p.generate("s", "u")
    assert json.loads(text) == [{"setup_family": "momentum_breakout"}]
    assert usage.cost_rub == 0.0 and usage.provider == "synthetic"


# ── private cost log ───────────────────────────────────────────────────────────

def test_usage_log_roundtrip_and_no_key_leak(tmp_path):
    def fake_post(url, payload, headers, timeout):
        return _envelope('{"proposals": []}', total_tokens=1500)

    p = load_provider(_FULL_ENV, http_post=fake_post)
    _, usage = p.generate("s", "u")
    record_usage(tmp_path, usage)
    summary = today_usage(tmp_path)
    assert summary["requests"] == 1 and summary["tokens"] == 1500 and summary["cost_rub"] > 0
    blob = usage_log_path(tmp_path).read_text(encoding="utf-8")
    assert "SECRET" not in blob and "sk-" not in blob  # key value never written


def test_today_usage_empty_is_zero(tmp_path):
    assert today_usage(tmp_path) == {"requests": 0, "tokens": 0, "cost_rub": 0.0}
