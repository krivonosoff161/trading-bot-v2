"""Deterministic offline scoring for local-advisor outputs."""

from __future__ import annotations

from typing import Any

from src.research_lab.agent_role_registry import validate_role_payload

SCHEMA = "LocalModelEvalResult.v1"


def evaluate_role_output(role_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    valid, problems = validate_role_payload(role_id, payload)
    confidence = payload.get("confidence")
    has_confidence = isinstance(confidence, (int, float)) and 0 <= float(confidence) <= 1
    nonempty = any(
        value not in (None, "", [], {})
        for key, value in payload.items()
        if key not in {"confidence", "warnings"}
    )
    checks = {
        "schema_safe": bool(valid),
        "confidence_bounded": bool(has_confidence),
        "answer_nonempty": bool(nonempty),
    }
    return {
        "schema": SCHEMA,
        "role_id": role_id,
        "checks": checks,
        "score": round(sum(checks.values()) / len(checks), 4),
        "passed": all(checks.values()),
        "problems": list(problems),
        "advisory_only": True,
        "paper_only": True,
        "execution_allowed": False,
    }
