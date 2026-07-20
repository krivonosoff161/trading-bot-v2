"""Content-bound metadata for staged paper projections.

Legacy JSON/JSONL files remain readable, but only a verified v2 stage envelope can
participate in a ``PaperGenerationRun.v2``.  This module is pure and has no provider,
process-control, credential, or execution imports.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

SCHEMA = "PaperStageEnvelope.v2"
LEGACY_STATUS = "legacy_unversioned_projection"


class PaperGenerationMismatch(ValueError):
    """A staged projection cannot prove the expected generation or bytes."""


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PaperGenerationContext:
    run_id: str
    producer_generation_id: str
    input_digest: str

    def __post_init__(self) -> None:
        if not self.run_id or not self.producer_generation_id or not self.input_digest:
            raise ValueError("complete paper generation context required")


def stage_envelope(
    stage: str,
    context: PaperGenerationContext | None,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a self-verifiable stage envelope or an explicit legacy label."""
    items_digest = canonical_digest(items)
    if context is None:
        return {
            "paper_stage_schema": "",
            "paper_generation_run_id": "",
            "source_producer_generation_id": "",
            "stage_name": stage,
            "stage_input_digest": "",
            "stage_items_digest": items_digest,
            "stage_output_digest": "",
            "generation_status": LEGACY_STATUS,
            "current_generation_compatible": False,
            "display_only": True,
        }
    identity = {
        "schema": SCHEMA,
        "paper_generation_run_id": context.run_id,
        "source_producer_generation_id": context.producer_generation_id,
        "stage_name": stage,
        "stage_input_digest": context.input_digest,
        "stage_items_digest": items_digest,
        "paper_only": True,
        "execution_allowed": False,
    }
    return {
        "paper_stage_schema": SCHEMA,
        "paper_generation_run_id": context.run_id,
        "source_producer_generation_id": context.producer_generation_id,
        "stage_name": stage,
        "stage_input_digest": context.input_digest,
        "stage_items_digest": items_digest,
        "stage_output_digest": canonical_digest(identity),
        "generation_status": "stage_completed",
        "current_generation_compatible": False,
        "display_only": False,
    }


def verify_stage_envelope(
    payload: dict[str, Any],
    *,
    stage: str,
    expected_run_id: str = "",
    expected_input_digest: str = "",
) -> PaperGenerationContext:
    """Recompute a stage digest and return context for the next stage."""
    if payload.get("paper_stage_schema") != SCHEMA:
        raise PaperGenerationMismatch(LEGACY_STATUS)
    items = payload.get("items")
    if not isinstance(items, list):
        raise PaperGenerationMismatch("stage items are missing")
    run_id = str(payload.get("paper_generation_run_id") or "")
    producer_generation_id = str(payload.get("source_producer_generation_id") or "")
    input_digest = str(payload.get("stage_input_digest") or "")
    items_digest = canonical_digest(items)
    identity = {
        "schema": SCHEMA,
        "paper_generation_run_id": run_id,
        "source_producer_generation_id": producer_generation_id,
        "stage_name": stage,
        "stage_input_digest": input_digest,
        "stage_items_digest": items_digest,
        "paper_only": True,
        "execution_allowed": False,
    }
    if (
        payload.get("stage_name") != stage
        or not run_id
        or not producer_generation_id
        or not input_digest
        or payload.get("stage_items_digest") != items_digest
        or payload.get("stage_output_digest") != canonical_digest(identity)
        or payload.get("paper_only") is not True
        or payload.get("execution_allowed") is not False
        or (expected_run_id and run_id != expected_run_id)
        or (expected_input_digest and input_digest != expected_input_digest)
    ):
        raise PaperGenerationMismatch("paper stage generation/digest mismatch")
    return PaperGenerationContext(
        run_id=run_id,
        producer_generation_id=producer_generation_id,
        input_digest=str(payload["stage_output_digest"]),
    )
