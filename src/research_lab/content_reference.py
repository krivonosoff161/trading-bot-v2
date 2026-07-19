"""Content-bound reference helpers for adaptive research records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Mapping

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class ContentReference:
    ref: str
    content_sha256: str
    producer_completion_id: str
    producer_schema: str = ""
    generation: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_content_sha256(value: Any, *, label: str = "content digest") -> str:
    text = str(value or "").strip().lower()
    if not _SHA256.fullmatch(text):
        raise ValueError(f"{label} must be a sha256 content digest")
    return text


def content_reference_from_mapping(value: Mapping[str, Any]) -> ContentReference:
    ref = str(value.get("ref") or value.get("source_ref") or value.get("result_ref") or "").strip()
    digest = validate_content_sha256(value.get("content_sha256"), label="evidence content digest")
    completion = str(value.get("producer_completion_id") or "").strip()
    if not ref or not completion:
        raise ValueError("content reference requires ref and producer_completion_id")
    return ContentReference(
        ref=ref,
        content_sha256=digest,
        producer_completion_id=completion,
        producer_schema=str(value.get("producer_schema") or "").strip(),
        generation=int(value.get("generation") or 0),
    )


def content_reference_hash(ref: ContentReference) -> str:
    raw = json.dumps(ref.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
