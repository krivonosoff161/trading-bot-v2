# -*- coding: utf-8 -*-
"""Budget guard for LLM calls used by the scanner.

The guard is intentionally small and environment-driven:

* caps are disabled when set to 0 or omitted;
* if ``LLM_STOP_ON_BUDGET`` is false, calls are allowed but the report can still
  show projected spend;
* scanner-level caps are enforced within the current Python process;
* daily caps read the existing ``logs/scout/llm_budget.jsonl`` aggregate and add
  current-process spend that has not been flushed yet.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCOUT_LOG_DIR = ROOT / "logs" / "scout"
BUDGET_LOG = SCOUT_LOG_DIR / "llm_budget.jsonl"

_SESSION_CALLS: Counter[str] = Counter()
_SESSION_TOKENS = 0
_SESSION_COST_RUB = 0.0


def estimate_tokens(*parts: str, max_output_tokens: int = 0) -> int:
    """Conservative tokenizer-free estimate for pre-call budget checks."""
    text = "\n".join(p or "" for p in parts)
    input_estimate = max((len(text) + 2) // 3, len(text.split()), 1 if text else 0)
    return input_estimate + max(0, int(max_output_tokens or 0))


def env_float(name: str, default: float = 0.0) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(name: str, default: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def stop_on_budget() -> bool:
    return os.getenv("LLM_STOP_ON_BUDGET", "false").strip().lower() in {"1", "true", "yes", "on"}


def today_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def daily_spend_rub(day: str | None = None, path: Path = BUDGET_LOG) -> float:
    day = day or today_utc()
    total = 0.0
    if not path.exists():
        return 0.0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("ts") or "")[:10] != day:
            continue
        total += float(row.get("cost_rub") or 0.0)
    return round(total, 4)


def session_snapshot() -> dict[str, Any]:
    return {
        "calls": dict(_SESSION_CALLS),
        "tokens": _SESSION_TOKENS,
        "cost_rub": round(_SESSION_COST_RUB, 4),
    }


def reset_session() -> None:
    global _SESSION_TOKENS, _SESSION_COST_RUB
    _SESSION_CALLS.clear()
    _SESSION_TOKENS = 0
    _SESSION_COST_RUB = 0.0


def budget_caps() -> dict[str, float | int | bool]:
    return {
        "daily_rub_cap": env_float("LLM_DAILY_RUB_CAP", 0.0),
        "scan_rub_cap": env_float("LLM_SCAN_RUB_CAP", 0.0),
        "max_tokens_per_scan": env_int("LLM_MAX_TOKENS_PER_SCAN", 0),
        "max_chief_per_scan": env_int("LLM_MAX_CHIEF_PER_SCAN", 0),
        "stop_on_budget": stop_on_budget(),
    }


def should_block(role: str, estimated_tokens: int, estimated_cost_rub: float) -> tuple[bool, str, dict[str, Any]]:
    """Return (blocked, reason, context)."""
    caps = budget_caps()
    if not caps["stop_on_budget"]:
        return False, "", {"caps": caps, "session": session_snapshot(), "daily_spend_rub": daily_spend_rub()}

    daily_cap = float(caps["daily_rub_cap"] or 0.0)
    scan_cap = float(caps["scan_rub_cap"] or 0.0)
    token_cap = int(caps["max_tokens_per_scan"] or 0)
    chief_cap = int(caps["max_chief_per_scan"] or 0)
    daily = daily_spend_rub()
    projected_daily = daily + _SESSION_COST_RUB + estimated_cost_rub
    projected_scan = _SESSION_COST_RUB + estimated_cost_rub
    projected_tokens = _SESSION_TOKENS + estimated_tokens
    ctx = {
        "caps": caps,
        "daily_spend_rub": daily,
        "session": session_snapshot(),
        "estimated_tokens": estimated_tokens,
        "estimated_cost_rub": round(estimated_cost_rub, 4),
        "projected_daily_rub": round(projected_daily, 4),
        "projected_scan_rub": round(projected_scan, 4),
        "projected_scan_tokens": projected_tokens,
    }

    if daily_cap > 0 and projected_daily > daily_cap:
        return True, "LLM_DAILY_RUB_CAP", ctx
    if scan_cap > 0 and projected_scan > scan_cap:
        return True, "LLM_SCAN_RUB_CAP", ctx
    if token_cap > 0 and projected_tokens > token_cap:
        return True, "LLM_MAX_TOKENS_PER_SCAN", ctx
    if role == "chief" and chief_cap > 0 and _SESSION_CALLS["chief"] >= chief_cap:
        return True, "LLM_MAX_CHIEF_PER_SCAN", ctx
    return False, "", ctx


def record_usage(role: str, tokens: int, cost_rub: float) -> None:
    global _SESSION_TOKENS, _SESSION_COST_RUB
    _SESSION_CALLS[role] += 1
    _SESSION_TOKENS += max(0, int(tokens or 0))
    _SESSION_COST_RUB += max(0.0, float(cost_rub or 0.0))


def usage_for_block(provider: str, model: str, role: str, reason: str, ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "role": role,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "cost_rub": 0.0,
        "status": "budget_skipped",
        "error_type": reason,
        "budget": ctx,
    }
