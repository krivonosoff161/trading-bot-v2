"""Immutable cross-role identity and stage records for adaptive research."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.research_lab.content_reference import content_reference_from_mapping, validate_content_sha256
from src.research_lab.lineage_contract import utc_now

SCHEMA = "AdaptiveTrial.v1"
RECORD_SCHEMA = "AdaptiveTrialRecord.v1"


def _sha256(payload: Any) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def adaptive_trial_id(task_spec: dict[str, Any]) -> str:
    if task_spec.get("schema") != "RoleTaskSpec.v1":
        raise ValueError("adaptive trial requires RoleTaskSpec.v1")
    source_content_sha256 = validate_content_sha256(
        task_spec.get("source_content_sha256"),
        label="source content digest",
    )
    producer_completion_id = str(task_spec.get("producer_completion_id") or "").strip()
    if not producer_completion_id:
        raise ValueError("adaptive trial requires source producer_completion_id")
    identity = {
        "subject": dict(task_spec.get("subject") or {}),
        "source_ref": str(task_spec.get("source_ref") or ""),
        "source_content_sha256": source_content_sha256,
        "producer_completion_id": producer_completion_id,
        "generation": int(task_spec.get("generation") or 0),
        "dimensions": list(task_spec.get("dimensions") or []),
        "tests": list(task_spec.get("tests") or []),
        "hypotheses": list(task_spec.get("hypotheses") or []),
        "parent_family_id": str(task_spec.get("parent_family_id") or ""),
        "parent_trial_id": str(task_spec.get("parent_trial_id") or ""),
        "parent_effective_n_trials": int(
            task_spec.get("parent_effective_n_trials") or 0
        ),
        "cumulative_family_policy": str(
            task_spec.get("cumulative_family_policy") or "independent"
        ),
    }
    if not identity["subject"] or not identity["source_ref"]:
        raise ValueError("adaptive trial subject and source_ref are required")
    if identity["cumulative_family_policy"] != "independent" and not (
        identity["parent_family_id"]
        and identity["parent_trial_id"]
        and identity["parent_effective_n_trials"] > 0
    ):
        raise ValueError("adaptive follow-up requires parent search-family accounting")
    if identity["cumulative_family_policy"] == "independent" and (
        identity["parent_family_id"]
        or identity["parent_trial_id"]
        or identity["parent_effective_n_trials"]
    ):
        raise ValueError("independent adaptive trial cannot inherit parent accounting")
    return f"atrial_{_sha256(identity)}"


def write_adaptive_trial_record(
    private_root: Path,
    *,
    trial_id: str,
    stage: str,
    role: str,
    artifact_id: str,
    evidence_refs: list[Any] | tuple[Any, ...] = (),
) -> Path:
    if not trial_id.startswith("atrial_") or len(trial_id) != 71:
        raise ValueError("invalid adaptive trial id")
    content_refs: list[dict[str, Any]] = []
    for item in evidence_refs:
        if not isinstance(item, dict):
            raise ValueError("evidence refs require content digest and producer completion evidence")
        content_refs.append(content_reference_from_mapping(item).to_dict())
    identity = {
        "adaptive_trial_id": trial_id,
        "stage": str(stage),
        "role": str(role),
        "artifact_id": str(artifact_id),
        "evidence_refs": content_refs,
    }
    record_id = f"atr_{_sha256(identity)}"
    payload = {
        "schema": RECORD_SCHEMA,
        "record_id": record_id,
        **identity,
        "recorded_at": utc_now(),
        "paper_only": True,
        "execution_allowed": False,
    }
    # Keep the full content-addressed record name, but avoid nesting the equally
    # long trial id: Windows temp/private roots can otherwise exceed MAX_PATH.
    directory = Path(private_root) / "state" / "adaptive_trials"
    path = directory / f"{record_id}.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        comparable = {key: existing.get(key) for key in identity}
        if comparable != identity:
            raise ValueError("immutable adaptive trial record collision")
        return path
    directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
