"""Hash-bound recovery for a compute materialization committed before task ACK."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.research_lab.farm_tasks_db import FarmTasksDB

SCHEMA = "ExpiredMaterializationRecoveryPlan.v1"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _identity(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": plan.get("schema"),
        "farm_database": plan.get("farm_database"),
        "compute_database": plan.get("compute_database"),
        "entry": plan.get("entry"),
    }


def plan_digest(plan: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(_identity(plan))).hexdigest()


def _connect_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _queue_binding(
    conn: sqlite3.Connection, materialization_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT qm.materialization_id, qm.job_id, qm.spec_path, qm.spec_digest,
                  q.status, q.materialization_id AS queue_materialization_id,
                  q.materialization_digest AS queue_digest
           FROM queue_materializations qm
           JOIN queue q ON q.job_id=qm.job_id
           WHERE qm.materialization_id=?""",
        (materialization_id,),
    ).fetchone()


def build_plan(
    farm_db_path: Path,
    compute_db_path: Path,
    *,
    task_id: int,
    now: float | None = None,
) -> dict[str, Any]:
    """Build one read-only recovery plan; ambiguity is rejected."""

    observed_at = time.time() if now is None else float(now)
    farm = _connect_read_only(Path(farm_db_path))
    compute = _connect_read_only(Path(compute_db_path))
    try:
        task = farm.execute(
            """SELECT task_id,task_type,state,claim_owner,claim_expires_at,
                      fencing_token,mutation_protocol,mutation_seq,
                      materialized_queue_job_id
               FROM tasks WHERE task_id=?""",
            (int(task_id),),
        ).fetchone()
        if task is None:
            raise ValueError("recovery task is missing")
        outboxes = farm.execute(
            """SELECT materialization_id,task_id,task_fencing_token,spec_path,
                      spec_digest,state,queue_job_id
               FROM materialization_outbox
               WHERE task_id=? AND state IN ('pending','dispatched','acknowledged')
               ORDER BY materialization_id""",
            (int(task_id),),
        ).fetchall()
        if len(outboxes) != 1:
            raise ValueError("recovery requires exactly one materialization outbox")
        outbox = outboxes[0]
        binding = _queue_binding(compute, str(outbox["materialization_id"]))
        if binding is None:
            raise ValueError("recovery compute binding is missing")
        if (
            str(binding["spec_path"]) != str(outbox["spec_path"])
            or str(binding["spec_digest"]) != str(outbox["spec_digest"])
            or str(binding["queue_materialization_id"] or "")
            != str(outbox["materialization_id"])
            or str(binding["queue_digest"] or "") != str(outbox["spec_digest"])
        ):
            raise ValueError("recovery compute binding disagrees with outbox")
        already_adopted = (
            task["task_type"] == "run_sweep"
            and task["state"] == "deferred"
            and str(task["mutation_protocol"] or "") == "fenced.v2"
            and task["claim_owner"] is None
            and task["claim_expires_at"] is None
            and int(task["materialized_queue_job_id"] or 0) == int(binding["job_id"])
            and str(outbox["state"]) == "acknowledged"
            and int(outbox["queue_job_id"] or 0) == int(binding["job_id"])
        )
        if not already_adopted and (
            task["task_type"] != "run_sweep"
            or task["state"] != "running"
            or task["claim_owner"] is None
            or float(task["claim_expires_at"] or 0) > observed_at
            or str(task["mutation_protocol"] or "") != "fenced.v2"
            or task["materialized_queue_job_id"] is not None
            or int(outbox["task_fencing_token"] or 0)
            != int(task["fencing_token"] or 0)
            or str(outbox["state"]) not in {"pending", "dispatched"}
            or (
                outbox["queue_job_id"] is not None
                and int(outbox["queue_job_id"]) != int(binding["job_id"])
            )
        ):
            raise ValueError("recovery task or outbox is not eligible")
        entry = {
            "task_id": int(task["task_id"]),
            "task_type": str(task["task_type"]),
            "state": str(task["state"]),
            "fencing_token": int(task["fencing_token"] or 0),
            "mutation_seq": int(task["mutation_seq"] or 0)
            - (1 if already_adopted else 0),
            "materialization_id": str(outbox["materialization_id"]),
            "outbox_state": str(outbox["state"]),
            "queue_job_id": int(binding["job_id"]),
            "queue_status": str(binding["status"]),
            "spec_path": str(outbox["spec_path"]),
            "spec_digest": str(outbox["spec_digest"]),
            "already_adopted": already_adopted,
        }
        plan: dict[str, Any] = {
            "schema": SCHEMA,
            "observed_at": observed_at,
            "farm_database": str(Path(farm_db_path).resolve()),
            "compute_database": str(Path(compute_db_path).resolve()),
            "entry": entry,
        }
        plan["plan_digest"] = plan_digest(plan)
        return plan
    finally:
        compute.close()
        farm.close()


def apply_plan(
    farm_db_path: Path,
    compute_db_path: Path,
    plan: dict[str, Any],
    *,
    expected_plan_digest: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Adopt the exact existing queue effect and change no compute rows."""

    if plan.get("schema") != SCHEMA:
        raise ValueError("unsupported materialization recovery plan schema")
    actual_digest = plan_digest(plan)
    if actual_digest != str(plan.get("plan_digest") or ""):
        raise ValueError("materialization recovery plan self-digest mismatch")
    if actual_digest != str(expected_plan_digest):
        raise ValueError("materialization recovery expected digest mismatch")
    if Path(str(plan.get("farm_database") or "")).resolve() != Path(
        farm_db_path
    ).resolve() or Path(str(plan.get("compute_database") or "")).resolve() != Path(
        compute_db_path
    ).resolve():
        raise ValueError("materialization recovery database capability mismatch")
    entry = dict(plan.get("entry") or {})
    current = time.time() if now is None else float(now)
    store = FarmTasksDB(Path(farm_db_path), owner_id="materialization-recovery")
    try:
        changed = store.adopt_expired_materialization(
            task_id=int(entry.get("task_id") or 0),
            expected_fence=int(entry.get("fencing_token") or 0),
            expected_mutation_seq=int(entry.get("mutation_seq") or 0),
            materialization_id=str(entry.get("materialization_id") or ""),
            queue_job_id=int(entry.get("queue_job_id") or 0),
            spec_path=str(entry.get("spec_path") or ""),
            spec_digest=str(entry.get("spec_digest") or ""),
            compute_db_path=Path(compute_db_path),
            expected_queue_status=str(entry.get("queue_status") or ""),
            now=current,
        )
    finally:
        store.close()
    return {
        "schema": "ExpiredMaterializationRecoveryResult.v1",
        "plan_digest": actual_digest,
        "task_id": int(entry.get("task_id") or 0),
        "queue_job_id": int(entry.get("queue_job_id") or 0),
        "changed": changed,
        "idempotent_reapply": changed == 0,
    }
