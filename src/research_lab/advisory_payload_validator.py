"""Recursive advisory payload containment for LLM/calculator outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping

VALIDATOR_VERSION = "AdvisoryPayloadValidator.v1"

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEPARATOR = re.compile(r"[^0-9A-Za-z]+")
_ALIASES = {
    "autotrade": "auto_trade",
    "auto_trade": "auto_trade",
    "executionallowed": "execution_allowed",
    "execution_allowed": "execution_allowed",
    "takeprofitplan": "take_profit_plan",
    "take_profit_plan": "take_profit_plan",
    "apikey": "api_key",
    "api_key": "api_key",
    "privatekey": "private_key",
    "private_key": "private_key",
    "stoploss": "stop_loss",
    "stop_loss": "stop_loss",
}


@dataclass(frozen=True)
class AdvisoryValidationResult:
    ok: bool
    problems: list[str] = field(default_factory=list)
    validator_version: str = VALIDATOR_VERSION
    recursive_validation: bool = True


def normalize_advisory_key(key: Any) -> str:
    text = str(key or "").strip()
    text = _CAMEL_BOUNDARY.sub("_", text)
    text = _SEPARATOR.sub("_", text).strip("_").lower()
    squashed = text.replace("_", "")
    return _ALIASES.get(text, _ALIASES.get(squashed, text))


def _path(parent: str, key: Any) -> str:
    piece = str(key)
    return f"{parent}.{piece}" if parent else piece


def _forbidden_at_any_depth(
    value: Any,
    forbidden: set[str],
    *,
    path: str = "",
) -> list[str]:
    problems: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = normalize_advisory_key(key)
            child_path = _path(path, key)
            if normalized in forbidden:
                problems.append(f"forbidden field: {normalized} at {child_path}")
            problems.extend(_forbidden_at_any_depth(child, forbidden, path=child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            problems.extend(_forbidden_at_any_depth(child, forbidden, path=f"{path}[{index}]"))
    return problems


def contains_forbidden_advisory_field(
    value: Any,
    forbidden_fields: Iterable[str],
) -> str | None:
    forbidden = {normalize_advisory_key(item) for item in forbidden_fields}
    problems = _forbidden_at_any_depth(value, forbidden)
    return problems[0] if problems else None


def validate_advisory_payload(
    role_id: str,
    payload: Mapping[str, Any],
    *,
    allowed_fields: Iterable[str],
    forbidden_fields: Iterable[str],
    container_fields: Iterable[str] = (),
) -> AdvisoryValidationResult:
    del role_id  # reserved for future role-specific diagnostics
    allowed = {normalize_advisory_key(item) for item in allowed_fields}
    forbidden = {normalize_advisory_key(item) for item in forbidden_fields}
    containers = {normalize_advisory_key(item) for item in container_fields}
    problems: list[str] = []
    for key, value in payload.items():
        normalized = normalize_advisory_key(key)
        if normalized in forbidden:
            problems.append(f"forbidden field: {normalized}")
        elif normalized not in allowed:
            problems.append(f"unknown field: {key}")
        elif isinstance(value, (Mapping, list, tuple)) and normalized not in containers:
            problems.append(f"container not allowed for field: {key}")
    problems.extend(_forbidden_at_any_depth(payload, forbidden))
    confidence = payload.get("confidence")
    if confidence is not None and not isinstance(confidence, (int, float)):
        problems.append("confidence must be numeric")
    if isinstance(confidence, (int, float)) and not 0 <= float(confidence) <= 1:
        problems.append("confidence must be in [0, 1]")
    deduped = list(dict.fromkeys(problems))
    return AdvisoryValidationResult(ok=not deduped, problems=deduped)
