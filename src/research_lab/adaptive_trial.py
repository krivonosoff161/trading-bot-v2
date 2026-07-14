"""Immutable cross-role identity and stage records for adaptive research."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

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
    identity = {
        "subject": dict(task_spec.get("subject") or {}),
        "source_ref": str(task_spec.get("source_ref") or ""),
        "generation": int(task_spec.get("generation") or 0),
    }
    if not identity["subject"] or not identity["source_ref"]:
        raise ValueError("adaptive trial subject and source_ref are required")
    return f"atrial_{_sha256(identity)}"


def write_adaptive_trial_record(
    private_root: Path,
    *,
    trial_id: str,
    stage: str,
    role: str,
    artifact_id: str,
    evidence_refs: list[str] | tuple[str, ...] = (),
) -> Path:
    if not trial_id.startswith("atrial_") or len(trial_id) != 71:
        raise ValueError("invalid adaptive trial id")
    identity = {
        "adaptive_trial_id": trial_id,
        "stage": str(stage),
        "role": str(role),
        "artifact_id": str(artifact_id),
        "evidence_refs": list(evidence_refs),
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
    directory = Path(private_root) / "state" / "adaptive_trials" / trial_id
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
