# -*- coding: utf-8 -*-
"""Local LLM advisory worker — disabled by default.

Reads validation feedback summaries, creates bounded proposal JSON
suggestions via Ollama/openai-compatible endpoint. LLM output is
validated through existing proposal schema. Default: dry-run, no calls.

LLM is advisory only. LLM never decides verdict. LLM never modifies
code or executes commands.
"""
from __future__ import annotations

import json
import os
from typing import Any

_ENABLED_KEY = "STRATEGY_LAB_LOCAL_LLM_ENABLED"
_BASE_URL_KEY = "STRATEGY_LAB_LOCAL_LLM_BASE_URL"
_MODEL_KEY = "STRATEGY_LAB_LOCAL_LLM_MODEL"
_DAILY_CAP_KEY = "STRATEGY_LAB_LOCAL_LLM_DAILY_CAP_RUB"

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "llama3.2"
MAX_PROPOSALS_PER_CALL = 5

SYSTEM_PROMPT = """You are a strategy research advisor. You suggest bounded
parameter variations for trading strategy candidates. You do NOT decide
final verdicts. You do NOT execute trades. You output ONLY valid JSON.

Output format:
{
  "proposals": [
    {
      "symbol": "...",
      "timeframe": "...",
      "strategy_id": "...",
      "params": {},
      "reason_from_feedback": "...",
      "expected_check_to_improve": "...",
      "risk_note": "..."
    }
  ]
}
"""


def is_enabled() -> bool:
    return os.environ.get(_ENABLED_KEY, "0") == "1"


def get_provider_config() -> dict[str, Any]:
    return {
        "enabled": is_enabled(),
        "base_url": os.environ.get(_BASE_URL_KEY, DEFAULT_BASE_URL),
        "model": os.environ.get(_MODEL_KEY, DEFAULT_MODEL),
        "daily_cap_rub": float(os.environ.get(_DAILY_CAP_KEY, "50")),
    }


def build_prompt(feedback_entries: list[dict[str, Any]]) -> str:
    """Build a bounded prompt from feedback entries."""
    lines = [
        "Given these strategy validation failures, suggest parameter "
        "variations that might address the specific failure modes.",
        "",
        "Feedback entries:",
    ]
    for i, fb in enumerate(feedback_entries[:MAX_PROPOSALS_PER_CALL], 1):
        lines.append(f"{i}. {fb.get('strategy_id', '?')} / "
                     f"{fb.get('symbol', '?')} / {fb.get('timeframe', '?')}")
        lines.append(f"   Status: {fb.get('hard_status', '?')}")
        lines.append(f"   Failed: {', '.join(fb.get('failed_checks', []))}")
        suggested = fb.get("suggested_next_test_constraints", [])
        lines.append(f"   Suggested: {', '.join(suggested)}")
        lines.append("")
    lines.append(f"Return at most {MAX_PROPOSALS_PER_CALL} proposals as JSON.")
    return "\n".join(lines)


def validate_proposal(proposal: dict[str, Any]) -> list[str]:
    """Validate a single proposal. Returns list of error messages."""
    errors = []
    required = ["symbol", "timeframe", "strategy_id", "params"]
    for field in required:
        if not proposal.get(field):
            errors.append(f"missing_{field}")
    if proposal.get("strategy_id") == "1m_full_universe":
        errors.append("unsafe_1m_full_sweep")
    params = proposal.get("params") or {}
    if len(params) > 20:
        errors.append("too_many_params")
    return errors


def parse_llm_response(text: str) -> dict[str, Any] | None:
    """Parse LLM JSON response, handling common wrappers."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    think_prefix = "<think>"
    if think_prefix in text:
        text = text.split(think_prefix)[-1]
        if "</think>" in text:
            text = text.split("</think>")[-1]
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and "proposals" in data:
        return data
    return None


def generate_suggestions(
    feedback_entries: list[dict[str, Any]],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Generate LLM suggestions (or dry-run summary)."""
    if not is_enabled():
        return {
            "enabled": False,
            "message": f"Set {_ENABLED_KEY}=1 to enable.",
            "suggestions": [],
        }

    if dry_run:
        return {
            "enabled": True,
            "dry_run": True,
            "message": "Dry-run: would send prompt to LLM.",
            "prompt_preview": build_prompt(feedback_entries)[:500],
            "suggestions": [],
        }

    config = get_provider_config()
    prompt = build_prompt(feedback_entries)

    return {
        "enabled": True,
        "dry_run": False,
        "provider": config["base_url"],
        "model": config["model"],
        "prompt_length": len(prompt),
        "message": "LLM call would go here. Provider not called in test mode.",
        "suggestions": [],
    }
