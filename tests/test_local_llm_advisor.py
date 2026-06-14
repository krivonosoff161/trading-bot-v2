# -*- coding: utf-8 -*-
"""Tests for local_llm_advisor.py — Phase 7."""
from __future__ import annotations

import os
from unittest.mock import patch

from src.research_lab.local_llm_advisor import (
    build_prompt,
    generate_suggestions,
    get_provider_config,
    is_enabled,
    parse_llm_response,
    validate_proposal,
)


class TestIsEnabled:
    def test_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert is_enabled() is False

    def test_enabled_with_env(self) -> None:
        with patch.dict(os.environ, {"STRATEGY_LAB_LOCAL_LLM_ENABLED": "1"}):
            assert is_enabled() is True


class TestProviderConfig:
    def test_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = get_provider_config()
            assert config["enabled"] is False
            assert "localhost" in config["base_url"]
            assert config["daily_cap_rub"] > 0


class TestBuildPrompt:
    def test_includes_feedback(self) -> None:
        feedback = [{
            "strategy_id": "trend",
            "symbol": "BTC-USDT-SWAP",
            "timeframe": "15m",
            "hard_status": "FAILED_COSTS",
            "failed_checks": ["costs"],
            "suggested_next_test_constraints": ["wider_move"],
        }]
        prompt = build_prompt(feedback)
        assert "FAILED_COSTS" in prompt
        assert "trend" in prompt
        assert "BTC-USDT-SWAP" in prompt

    def test_caps_proposals(self) -> None:
        feedback = [
            {"strategy_id": f"s{i}", "symbol": "X", "timeframe": "15m",
             "hard_status": "FAILED_COSTS", "failed_checks": [],
             "suggested_next_test_constraints": []}
            for i in range(10)
        ]
        prompt = build_prompt(feedback)
        assert "10" not in prompt.split("Return")[0]


class TestValidateProposal:
    def test_valid(self) -> None:
        errors = validate_proposal({
            "symbol": "BTC-USDT-SWAP",
            "timeframe": "15m",
            "strategy_id": "trend",
            "params": {"ma": 20},
        })
        assert errors == []

    def test_missing_symbol(self) -> None:
        errors = validate_proposal({
            "timeframe": "15m",
            "strategy_id": "trend",
            "params": {},
        })
        assert any("symbol" in e for e in errors)

    def test_unsafe_1m(self) -> None:
        errors = validate_proposal({
            "symbol": "BTC-USDT-SWAP",
            "timeframe": "15m",
            "strategy_id": "1m_full_universe",
            "params": {},
        })
        assert any("1m" in e for e in errors)

    def test_too_many_params(self) -> None:
        errors = validate_proposal({
            "symbol": "BTC-USDT-SWAP",
            "timeframe": "15m",
            "strategy_id": "trend",
            "params": {f"p{i}": i for i in range(25)},
        })
        assert any("too_many" in e for e in errors)


class TestParseLLMResponse:
    def test_valid_json(self) -> None:
        text = '{"proposals": [{"symbol": "BTC-USDT-SWAP"}]}'
        result = parse_llm_response(text)
        assert result is not None
        assert len(result["proposals"]) == 1

    def test_json_with_markdown_wrapper(self) -> None:
        text = '```json\n{"proposals": []}\n```'
        result = parse_llm_response(text)
        assert result is not None

    def test_json_with_think_prefix(self) -> None:
        text = '<think>reasoning</think>{"proposals": []}'
        result = parse_llm_response(text)
        assert result is not None

    def test_invalid_json(self) -> None:
        result = parse_llm_response("not json at all")
        assert result is None

    def test_wrong_shape(self) -> None:
        result = parse_llm_response('{"not_proposals": []}')
        assert result is None


class TestGenerateSuggestions:
    def test_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = generate_suggestions([])
            assert result["enabled"] is False
            assert result["suggestions"] == []

    def test_dry_run(self) -> None:
        with patch.dict(os.environ, {"STRATEGY_LAB_LOCAL_LLM_ENABLED": "1"}):
            result = generate_suggestions([], dry_run=True)
            assert result["dry_run"] is True
            assert result["suggestions"] == []

    def test_no_live_trading_fields(self) -> None:
        with patch.dict(os.environ, {"STRATEGY_LAB_LOCAL_LLM_ENABLED": "1"}):
            result = generate_suggestions([], dry_run=True)
            raw = str(result)
            assert "AUTO_TRADE" not in raw
            assert "live_order" not in raw
