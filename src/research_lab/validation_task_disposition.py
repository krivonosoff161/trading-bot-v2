"""Hash-bound planning and canonical disposition for stale validation tasks."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from src.research_lab.farm_tasks_db import FarmTasksDB

SCHEMA = "ValidationTaskDispositionPlan.v1"
ELIGIBLE_STATUSES = {"FORWARD_PAPER", "REGIME_SPECIFIC"}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _identity(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": plan.get("schema"),
        "database": plan.get("database"),
        "entries": plan.get("entries") or [],
    }


def plan_digest(plan: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(_identity(plan))).hexdigest()


def build_plan(
    db_path: Path,
    *,
    now: float | None = None,
    missing_grace_seconds: float = 600.0,
) -> dict[str, Any]:
    """Build a read-only exact plan for queued/deferred terminal orphans."""
    observed_at = time.time() if now is None else float(now)
    store = FarmTasksDB(Path(db_path), read_only=True)
    try:
        conn = store.raw_connection
        candidates = {
            str(row["uc_key"]): str(row["validation_status"] or "")
            for row in conn.execute("SELECT uc_key,validation_status FROM unique_candidates")
        }
        rows = conn.execute(
            """SELECT task_id,state,created_at,fencing_token,mutation_seq,payload_json
               FROM tasks
               WHERE task_type='export_validation'
                 AND state IN ('queued','deferred')
                 AND claim_owner IS NULL
               ORDER BY task_id"""
        ).fetchall()
        entries: list[dict[str, Any]] = []
        reasons: dict[str, int] = {}
        for row in rows:
            payload_text = str(row["payload_json"] or "")
            try:
                payload = json.loads(payload_text or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            uc_key = (
                str(payload.get("uc_key") or "") if isinstance(payload, dict) else ""
            )
            reason = ""
            if not uc_key:
                reason = "validation_task_missing_uc_key"
            elif uc_key not in candidates:
                age = max(0.0, observed_at - float(row["created_at"] or observed_at))
                if age >= max(0.0, float(missing_grace_seconds)):
                    reason = "validation_orphan_missing_unique_candidate"
            elif candidates[uc_key] not in ELIGIBLE_STATUSES:
                reason = "validation_candidate_no_longer_eligible"
            if not reason:
                continue
            reasons[reason] = reasons.get(reason, 0) + 1
            entries.append(
                {
                    "task_id": int(row["task_id"]),
                    "state": str(row["state"]),
                    "fencing_token": int(row["fencing_token"] or 0),
                    "mutation_seq": int(row["mutation_seq"] or 0),
                    "payload_sha256": hashlib.sha256(
                        payload_text.encode("utf-8")
                    ).hexdigest(),
                    "reason": reason,
                }
            )
        plan: dict[str, Any] = {
            "schema": SCHEMA,
            "observed_at": observed_at,
            "database": str(Path(db_path).resolve()),
            "entries": entries,
            "counts": {
                "examined": len(rows),
                "dispositioned": len(entries),
                "retained": len(rows) - len(entries),
                "reasons": dict(sorted(reasons.items())),
            },
        }
        plan["plan_digest"] = plan_digest(plan)
        return plan
    finally:
        store.close()


def apply_plan(
    db_path: Path,
    plan: dict[str, Any],
    *,
    expected_plan_digest: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Apply one exact plan through the canonical fenced task store."""
    if plan.get("schema") != SCHEMA:
        raise ValueError("unsupported validation disposition plan schema")
    actual_digest = plan_digest(plan)
    if actual_digest != str(plan.get("plan_digest") or ""):
        raise ValueError("validation disposition plan self-digest mismatch")
    if actual_digest != str(expected_plan_digest):
        raise ValueError("validation disposition expected digest mismatch")
    if Path(str(plan.get("database") or "")).resolve() != Path(db_path).resolve():
        raise ValueError("validation disposition database capability mismatch")
    store = FarmTasksDB(Path(db_path), owner_id="validation-orphan-disposition")
    try:
        changed = store.apply_export_validation_disposition_plan(
            list(plan.get("entries") or []), now=now
        )
    finally:
        store.close()
    return {
        "schema": "ValidationTaskDispositionResult.v1",
        "plan_digest": actual_digest,
        "planned": len(plan.get("entries") or []),
        "changed": changed,
        "idempotent_reapply": changed == 0,
    }
