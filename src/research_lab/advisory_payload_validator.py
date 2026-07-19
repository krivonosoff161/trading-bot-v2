"""Recursive advisory payload containment for LLM/calculator outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping
import unicodedata

VALIDATOR_VERSION = "AdvisoryPayloadValidator.v1"

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEPARATOR = re.compile(r"[^0-9A-Za-z]+")
_CONFUSABLES = str.maketrans(
    {
        # Cyrillic homoglyphs relevant to ASCII advisory identifiers.
        "\u0410": "A", "\u0430": "a", "\u0412": "B", "\u0432": "b",
        "\u0415": "E", "\u0435": "e", "\u041a": "K", "\u043a": "k",
        "\u041c": "M", "\u043c": "m", "\u041d": "H", "\u043d": "h",
        "\u041e": "O", "\u043e": "o", "\u0420": "P", "\u0440": "p",
        "\u0421": "C", "\u0441": "c", "\u0422": "T", "\u0442": "t",
        "\u0425": "X", "\u0445": "x", "\u0423": "Y", "\u0443": "y",
        "\u0406": "I", "\u0456": "i", "\u0408": "J", "\u0458": "j",
        "\u0405": "S", "\u0455": "s",
        # Greek homoglyphs that can hide the same authority-bearing words.
        "\u0391": "A", "\u03b1": "a", "\u0392": "B", "\u03b2": "b",
        "\u0395": "E", "\u03b5": "e", "\u0399": "I", "\u03b9": "i",
        "\u039a": "K", "\u03ba": "k", "\u039c": "M", "\u03bc": "m",
        "\u039d": "N", "\u03bd": "v", "\u039f": "O", "\u03bf": "o",
        "\u03a1": "P", "\u03c1": "p", "\u03a4": "T", "\u03c4": "t",
        "\u03a5": "Y", "\u03c5": "y", "\u03a7": "X", "\u03c7": "x",
    }
)
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
    text = unicodedata.normalize("NFKC", str(key or "")).translate(_CONFUSABLES).strip()
    text = _CAMEL_BOUNDARY.sub("_", text)
    text = _SEPARATOR.sub("_", text).strip("_").lower()
    squashed = text.replace("_", "")
    return _ALIASES.get(text, _ALIASES.get(squashed, text))


def _has_unmapped_non_ascii_key(key: Any) -> bool:
    skeleton = unicodedata.normalize("NFKC", str(key or "")).translate(_CONFUSABLES)
    return not skeleton.isascii()


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
            if _has_unmapped_non_ascii_key(key):
                problems.append(f"non-ASCII advisory key at {child_path}")
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
        if _has_unmapped_non_ascii_key(key):
            problems.append(f"non-ASCII advisory key: {key}")
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
