# -*- coding: utf-8 -*-
"""Tests for the LLM tiny test harness (Phase 6).

Verifies:
- Refusal when env missing.
- Command construction / docs only (no bat test infra needed).
- Provider not configured -> clean no-op.
"""

from pathlib import Path


def test_bat_file_exists():
    bat = Path("bat/strategy_lab_llm_tiny_test.bat")
    assert bat.exists(), "tiny test bat must exist"


def test_bat_refuses_when_llm_disabled():
    bat = Path("bat/strategy_lab_llm_tiny_test.bat").read_text(encoding="utf-8")
    assert "STRATEGY_LAB_LLM_ENABLED" in bat
    assert 'exit /b 2' in bat


def test_bat_requires_daily_cap():
    bat = Path("bat/strategy_lab_llm_tiny_test.bat").read_text(encoding="utf-8")
    assert "STRATEGY_LAB_LLM_DAILY_CAP" in bat


def test_bat_requires_provider():
    bat = Path("bat/strategy_lab_llm_tiny_test.bat").read_text(encoding="utf-8")
    assert "STRATEGY_LAB_LLM_PROVIDER" in bat


def test_bat_has_cost_warning():
    bat = Path("bat/strategy_lab_llm_tiny_test.bat").read_text(encoding="utf-8")
    assert "costs money" in bat.lower() or "WARNING" in bat


def test_bat_no_live_trading():
    bat = Path("bat/strategy_lab_llm_tiny_test.bat").read_text(encoding="utf-8")
    assert "no live trading" in bat.lower() or "No live trading" in bat
    assert "no order engine" in bat.lower() or "No order engine" in bat


def test_bat_uses_tiny_caps():
    bat = Path("bat/strategy_lab_llm_tiny_test.bat").read_text(encoding="utf-8")
    assert "--max-candidates 2" in bat
    assert "--max-queued 3" in bat
    assert "--duration-minutes 5" in bat


def test_bat_uses_llm_propose():
    bat = Path("bat/strategy_lab_llm_tiny_test.bat").read_text(encoding="utf-8")
    assert "--llm-propose" in bat
