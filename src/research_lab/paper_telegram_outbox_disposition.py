"""Hash-bound, forward-only operator disposition for unsafe Telegram outbox debt."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from src.research_lab import paper_telegram_sender as sender

PLAN_SCHEMA = "PaperTelegramOutboxDispositionPlan.v1"
RESULT_SCHEMA = "PaperTelegramOutboxDispositionResult.v1"
ACTION = "operator_suppressed_no_replay"
ELIGIBLE_STATUSES = frozenset({"external_ack_ambiguous", "pending"})


class OutboxDispositionError(RuntimeError):
    """The requested disposition is not exactly bound to current outbox state."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _record_digest(record: dict[str, Any]) -> str:
    return _sha256(_canonical(record))


def _plan_identity(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if key != "plan_digest"}


def plan_digest(plan: dict[str, Any]) -> str:
    return _sha256(_canonical(_plan_identity(plan)))


def _read_outbox_bytes(private_root: Path) -> tuple[Path, bytes]:
    path = sender._outbox_path(private_root)
    try:
        return path, path.read_bytes()
    except OSError as exc:
        raise OutboxDispositionError("outbox is unavailable") from exc


def _decode_outbox(encoded: bytes) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OutboxDispositionError("outbox is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "paper_telegram_delivery_outbox.v1"
        or not isinstance(payload.get("items"), list)
    ):
        raise OutboxDispositionError("outbox is invalid")
    rows: dict[str, dict[str, Any]] = {}
    for raw in payload["items"]:
        if not isinstance(raw, dict):
            raise OutboxDispositionError("outbox is invalid")
        row = dict(raw)
        key = str(row.get("delivery_key") or "")
        if (
            not key
            or key in rows
            or row.get("schema") != "paper_telegram_delivery_outbox_item.v1"
            or row.get("paper_only") is not True
            or row.get("execution_allowed") is not False
        ):
            raise OutboxDispositionError("outbox is invalid")
        rows[key] = row
    return rows


def _sent_keys_digest(private_root: Path) -> str | None:
    path = sender._sent_keys_path(private_root)
    if not path.exists():
        return None
    try:
        encoded = path.read_bytes()
        sender._load_sent_keys(private_root)
    except (OSError, sender.DeliveryOutboxUnavailable) as exc:
        raise OutboxDispositionError("sent-key index is unavailable") from exc
    return _sha256(encoded)


def build_disposition_plan(
    private_root: Path, *, now: float | None = None
) -> dict[str, Any]:
    """Build a read-only plan for every currently ambiguous or pending record."""

    root = Path(private_root).resolve()
    _, encoded = _read_outbox_bytes(root)
    rows = _decode_outbox(encoded)
    items = [
        {
            "delivery_key": key,
            "prior_status": str(row.get("status") or ""),
            "record_digest": _record_digest(row),
        }
        for key, row in sorted(rows.items())
        if str(row.get("status") or "") in ELIGIBLE_STATUSES
    ]
    plan: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "action": ACTION,
        "private_root": str(root),
        "observed_at": time.time() if now is None else float(now),
        "outbox_digest": _sha256(encoded),
        "sent_keys_digest": _sent_keys_digest(root),
        "target_count": len(items),
        "items": items,
    }
    plan["plan_digest"] = plan_digest(plan)
    return plan


def _validate_plan(
    private_root: Path, plan: dict[str, Any], expected_plan_digest: str
) -> str:
    if plan.get("schema") != PLAN_SCHEMA or plan.get("action") != ACTION:
        raise OutboxDispositionError("unsupported outbox disposition plan")
    actual = plan_digest(plan)
    if actual != str(plan.get("plan_digest") or ""):
        raise OutboxDispositionError("plan self-digest mismatch")
    if actual != str(expected_plan_digest):
        raise OutboxDispositionError("expected plan digest mismatch")
    if Path(str(plan.get("private_root") or "")).resolve() != private_root.resolve():
        raise OutboxDispositionError("private-root capability mismatch")
    items = plan.get("items")
    if not isinstance(items, list) or int(plan.get("target_count") or 0) != len(items):
        raise OutboxDispositionError("invalid target inventory")
    keys: set[str] = set()
    for item in items:
        if (
            not isinstance(item, dict)
            or str(item.get("prior_status") or "") not in ELIGIBLE_STATUSES
            or not str(item.get("delivery_key") or "")
            or not str(item.get("record_digest") or "").startswith("sha256:")
        ):
            raise OutboxDispositionError("invalid target inventory")
        key = str(item["delivery_key"])
        if key in keys:
            raise OutboxDispositionError("duplicate target inventory")
        keys.add(key)
    return actual


def _write_backup(path: Path, encoded: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise OutboxDispositionError("backup path already contains different bytes")
        return
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise OutboxDispositionError("backup path appeared concurrently") from exc
    with os.fdopen(fd, "wb", closefd=True) as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def apply_disposition_plan(
    private_root: Path,
    plan: dict[str, Any],
    *,
    expected_plan_digest: str,
    backup_path: Path,
    confirm_permanent_no_replay: bool,
    now: float | None = None,
) -> dict[str, Any]:
    """Suppress exact planned debt without claiming Telegram acknowledgement."""

    if confirm_permanent_no_replay is not True:
        raise OutboxDispositionError("permanent no-replay confirmation is required")
    root = Path(private_root).resolve()
    digest = _validate_plan(root, plan, expected_plan_digest)
    resolved_backup = Path(backup_path).resolve()
    if resolved_backup == sender._outbox_path(root).resolve():
        raise OutboxDispositionError("backup path must differ from the live outbox")
    applied_at = time.time() if now is None else float(now)
    with sender._delivery_claim(root):
        _, encoded = _read_outbox_bytes(root)
        rows = _decode_outbox(encoded)
        items = list(plan["items"])
        already_applied = 0
        pending_apply: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for item in items:
            row = rows.get(str(item["delivery_key"]))
            if row is None:
                raise OutboxDispositionError("outbox drift: planned record is missing")
            disposition = row.get("operator_disposition")
            if (
                row.get("status") == ACTION
                and isinstance(disposition, dict)
                and disposition.get("plan_digest") == digest
                and disposition.get("prior_status") == item.get("prior_status")
            ):
                already_applied += 1
                continue
            if (
                row.get("status") != item.get("prior_status")
                or _record_digest(row) != item.get("record_digest")
            ):
                raise OutboxDispositionError("outbox drift: planned record changed")
            pending_apply.append((row, item))
        if pending_apply:
            if _sha256(encoded) != str(plan.get("outbox_digest") or ""):
                raise OutboxDispositionError("outbox drift: file digest changed")
            if _sent_keys_digest(root) != plan.get("sent_keys_digest"):
                raise OutboxDispositionError("outbox drift: sent-key index changed")
            _write_backup(resolved_backup, encoded)
            for row, item in pending_apply:
                prior_problem = str(row.get("problem") or "")
                row["status"] = ACTION
                row["problem"] = ACTION
                row["operator_disposition"] = {
                    "schema": "PaperTelegramOutboxOperatorDisposition.v1",
                    "action": ACTION,
                    "prior_status": str(item["prior_status"]),
                    "prior_problem": prior_problem,
                    "plan_digest": digest,
                    "applied_at": applied_at,
                }
            sender._save_outbox(root, rows)
        return {
            "schema": RESULT_SCHEMA,
            "action": ACTION,
            "plan_digest": digest,
            "target_count": len(items),
            "applied": len(pending_apply),
            "already_applied": already_applied,
            "backup_digest": _sha256(resolved_backup.read_bytes())
            if resolved_backup.exists()
            else None,
            "paper_only": True,
            "execution_allowed": False,
        }
