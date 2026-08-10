"""Opt-in Telegram sender for validated paper-watch previews.

This module is deliberately downstream of ``paper_telegram_preview``. It never
builds trading decisions and never falls back to public scanner/default chats.
Paper setup delivery is a subscriber product surface: it sends only to active
bot subscribers/superadmins supplied by the caller, and only after an explicit
``apply=True`` call from the CLI/operator.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.research_lab.storage_os_lock import StorageLockConflict, storage_root_lock

SCHEMA = "PaperTelegramDelivery.v1"
SUMMARY_SCHEMA = "paper_telegram_delivery.v1"
DEFAULT_DELIVERY_TARGET = "SUBSCRIPTION_USERS"
MAX_CHART_PAYLOAD_BYTES = 10 * 1024 * 1024
REQUIRED_DISCLAIMER = "Бумажный режим: это не ордер."
_OUTBOX_STATUSES = {
    "completed",
    "error",
    "external_ack_ambiguous",
    "pending",
    "skipped_no_token",
}


class DeliveryClaimConflict(RuntimeError):
    """Another process owns the public delivery side-effect boundary."""


class DeliveryOutboxUnavailable(RuntimeError):
    """The existing recovery source cannot be proved readable and valid."""

    def __init__(self, problem: str):
        super().__init__(problem)
        self.problem = problem


@dataclass(frozen=True)
class PaperTelegramDelivery:
    preview_id: str
    instruction_id: str
    source_signal_id: str
    pair: str
    timeframe: str
    side: str
    setup_family: str
    status: str
    message_id: int | None = None
    problem: str = ""
    destination: str = "personal_bot"
    recipient_hash: str = ""
    delivery_key: str = ""
    transport_kind: str = ""
    chart_available: bool = False
    chart_sent: bool = False
    chart_problem: str = ""
    paper_only: bool = True
    execution_allowed: bool = False
    chat_env: str = DEFAULT_DELIVERY_TARGET
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError(f"schema must be {SCHEMA}")
        if self.execution_allowed:
            raise ValueError("paper Telegram delivery must never allow execution")
        if not self.paper_only:
            raise ValueError("paper Telegram delivery must be paper_only")
        if self.destination != "personal_bot":
            raise ValueError("paper Telegram delivery must use personal bot chats")
        if self.chat_env != DEFAULT_DELIVERY_TARGET:
            raise ValueError("paper Telegram delivery must use subscription users")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _preview_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_preview.json"


def _delivery_jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_delivery.jsonl"


def _delivery_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_delivery.json"


def _sent_keys_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_sent_keys.json"


def _outbox_path(private_root: Path) -> Path:
    return (
        Path(private_root) / "state" / "derived" / "paper_telegram_delivery_outbox.json"
    )


def _delivery_lock_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_delivery.lock"


@contextmanager
def _delivery_claim(private_root: Path):
    """Acquire a process-local and OS-level claim for the whole send boundary."""
    path = _delivery_lock_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
    try:
        with storage_root_lock(path):
            yield
    except StorageLockConflict as exc:
        raise DeliveryClaimConflict(
            "delivery claim is held by another process"
        ) from exc


def _quality_report_path(private_root: Path) -> Path:
    return (
        Path(private_root) / "state" / "derived" / "paper_product_quality_report.json"
    )


def _load_sent_keys(private_root: Path) -> set[str]:
    path = _sent_keys_path(private_root)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DeliveryOutboxUnavailable("delivery_sent_keys_unreadable") from exc
    except json.JSONDecodeError as exc:
        raise DeliveryOutboxUnavailable("delivery_sent_keys_invalid") from exc
    if (
        not isinstance(data, dict)
        or data.get("schema") != "paper_telegram_sent_keys.v1"
    ):
        raise DeliveryOutboxUnavailable("delivery_sent_keys_invalid")
    items = data.get("sent_keys")
    if not isinstance(items, list) or any(
        not isinstance(item, str) or not item for item in items
    ):
        raise DeliveryOutboxUnavailable("delivery_sent_keys_invalid")
    return set(items)


def _atomic_write_bytes(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(path)
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, encoded)


def _save_sent_keys(private_root: Path, sent_keys: set[str]) -> None:
    path = _sent_keys_path(private_root)
    _atomic_write_json(
        path,
        {"schema": "paper_telegram_sent_keys.v1", "sent_keys": sorted(sent_keys)},
    )


def _load_outbox(private_root: Path) -> dict[str, dict[str, Any]]:
    path = _outbox_path(private_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DeliveryOutboxUnavailable("delivery_outbox_unreadable") from exc
    except json.JSONDecodeError as exc:
        raise DeliveryOutboxUnavailable("delivery_outbox_invalid") from exc
    if (
        not isinstance(data, dict)
        or data.get("schema") != "paper_telegram_delivery_outbox.v1"
    ):
        raise DeliveryOutboxUnavailable("delivery_outbox_invalid")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise DeliveryOutboxUnavailable("delivery_outbox_invalid")
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        if (
            not isinstance(item, dict)
            or item.get("schema") != "paper_telegram_delivery_outbox_item.v1"
            or item.get("paper_only") is not True
            or item.get("execution_allowed") is not False
            or str(item.get("status") or "") not in _OUTBOX_STATUSES
        ):
            raise DeliveryOutboxUnavailable("delivery_outbox_invalid")
        key = str(item.get("delivery_key") or "")
        if not key or key in out:
            raise DeliveryOutboxUnavailable("delivery_outbox_invalid")
        out[key] = item
    return out


def _save_outbox(private_root: Path, outbox: dict[str, dict[str, Any]]) -> None:
    path = _outbox_path(private_root)
    _atomic_write_json(
        path,
        {
            "schema": "paper_telegram_delivery_outbox.v1",
            "items": [outbox[key] for key in sorted(outbox)],
        },
    )


def _outbox_record(
    item: dict[str, Any],
    *,
    delivery_key: str,
    recipient_hash: str,
    status: str,
    transport_kind: str,
    message_id: int | None = None,
    photo_message_id: int | None = None,
    photo_status: str = "not_applicable",
    text_status: str = "pending",
    problem: str = "",
) -> dict[str, Any]:
    return {
        "schema": "paper_telegram_delivery_outbox_item.v1",
        "delivery_key": delivery_key,
        "preview_id": str(item.get("preview_id") or ""),
        "instruction_id": str(item.get("instruction_id") or ""),
        "source_signal_id": str(item.get("source_signal_id") or ""),
        "telegram_card_id": str(item.get("telegram_card_id") or ""),
        "recipient_hash": recipient_hash,
        "status": status,
        "transport_kind": transport_kind,
        "message_id": message_id,
        "photo_message_id": photo_message_id,
        "photo_status": photo_status,
        "text_status": text_status,
        "problem": problem,
        "paper_only": True,
        "execution_allowed": False,
    }


def _upsert_outbox_record(
    private_root: Path, outbox: dict[str, dict[str, Any]], record: dict[str, Any]
) -> None:
    outbox[str(record["delivery_key"])] = record
    _save_outbox(private_root, outbox)


def _load_preview_items(
    private_root: Path,
) -> tuple[list[dict[str, Any]], Path | None, dict[str, Any]]:
    path = _preview_snapshot_path(private_root)
    if not path.exists():
        return [], None, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("items") or []), path, data


def _valid_preview(item: dict[str, Any]) -> tuple[bool, str]:
    if item.get("schema") != "PaperTelegramPreview.v1":
        return False, "bad_preview_schema"
    if item.get("paper_only") is not True:
        return False, "paper_only_not_true"
    if item.get("execution_allowed") is not False:
        return False, "execution_allowed_not_false"
    if item.get("problems"):
        return False, "preview_has_problems"
    if (
        str(item.get("source_signal_id") or "") != "paper_status_digest"
        and not str(item.get("telegram_card_id") or "").strip()
    ):
        return False, "missing_immutable_content_identity"
    if not str(item.get("text") or "").strip():
        return False, "missing_text"
    if REQUIRED_DISCLAIMER not in str(item.get("text") or ""):
        return False, "missing_research_disclaimer"
    return True, ""


def _load_quality_report(private_root: Path) -> dict[str, Any]:
    path = _quality_report_path(private_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


async def _send_items(
    items: list[dict[str, Any]],
    recipient_ids: list[str],
    send_text: Callable[[str, str], Awaitable[int | None]],
    send_photo: Callable[[str, bytes], Awaitable[int | None]] | None,
    sent_keys: set[str],
    private_root: Path,
) -> list[PaperTelegramDelivery]:
    deliveries: list[PaperTelegramDelivery] = []
    try:
        outbox = _load_outbox(private_root)
    except DeliveryOutboxUnavailable as exc:
        return [
            _delivery_from_preview(
                item,
                status="outbox_unavailable",
                problem=exc.problem,
                recipient_id=recipient_id,
                delivery_key=_delivery_key(item, recipient_id),
                transport_kind="telegram_text",
                chart_available=bool(_safe_chart_path(item, private_root)[0]),
                chart_problem=_safe_chart_path(item, private_root)[1],
            )
            for item in items
            for recipient_id in recipient_ids
        ]
    for item in items:
        for recipient_id in recipient_ids:
            chart_path, chart_problem = _safe_chart_path(item, private_root)
            if str(item.get("chart_path") or "").strip() and chart_path is None:
                deliveries.append(
                    _delivery_from_preview(
                        item,
                        status="invalid_preview",
                        problem=chart_problem or "invalid_chart_path",
                        recipient_id=recipient_id,
                        transport_kind="telegram_photo+text",
                        chart_available=False,
                        chart_problem=chart_problem or "invalid_chart_path",
                    )
                )
                continue
            if chart_path is not None and send_photo is None:
                deliveries.append(
                    _delivery_from_preview(
                        item,
                        status="invalid_preview",
                        problem="photo_transport_not_configured",
                        recipient_id=recipient_id,
                        transport_kind="telegram_photo+text",
                        chart_available=True,
                        chart_problem="photo_transport_not_configured",
                    )
                )
                continue
            transport_kind = (
                "telegram_photo+text"
                if chart_path and send_photo is not None
                else "telegram_text"
            )
            identity_chart_path = chart_path if send_photo is not None else None
            try:
                if identity_chart_path is not None:
                    transport_chart_payload, chart_sha256 = _capture_chart_payload(
                        identity_chart_path,
                        private_root,
                    )
                else:
                    transport_chart_payload, chart_sha256 = None, ""
            except OSError:
                deliveries.append(
                    _delivery_from_preview(
                        item,
                        status="invalid_preview",
                        problem="chart_content_unreadable",
                        recipient_id=recipient_id,
                        transport_kind=transport_kind,
                        chart_available=bool(chart_path),
                        chart_problem="chart_content_unreadable",
                    )
                )
                continue
            delivery_keys = _delivery_keys(
                item,
                recipient_id,
                chart_path=identity_chart_path,
                chart_sha256=chart_sha256,
            )
            delivery_key = delivery_keys[0]
            recipient_hash = _recipient_hash(recipient_id)
            blocking_record = next(
                (
                    outbox.get(key)
                    for key in delivery_keys
                    if outbox.get(key, {}).get("status") == "external_ack_ambiguous"
                ),
                None,
            )
            if blocking_record is not None:
                deliveries.append(
                    _delivery_from_preview(
                        item,
                        status="external_ack_ambiguous",
                        message_id=_int_or_none(blocking_record.get("message_id")),
                        problem="external_ack_requires_operator_recovery",
                        recipient_id=recipient_id,
                        delivery_key=str(
                            blocking_record.get("delivery_key") or delivery_key
                        ),
                        transport_kind=str(
                            blocking_record.get("transport_kind") or transport_kind
                        ),
                        chart_available=bool(chart_path),
                        chart_sent=_int_or_none(blocking_record.get("photo_message_id"))
                        is not None,
                        chart_problem=chart_problem,
                    )
                )
                continue
            pending_record = next(
                (
                    outbox.get(key)
                    for key in delivery_keys
                    if outbox.get(key, {}).get("status") == "pending"
                ),
                None,
            )
            if pending_record is not None:
                deliveries.append(
                    _delivery_from_preview(
                        item,
                        status="pending_delivery_claim",
                        message_id=_int_or_none(pending_record.get("message_id")),
                        problem="delivery_owned_by_existing_attempt",
                        recipient_id=recipient_id,
                        delivery_key=str(
                            pending_record.get("delivery_key") or delivery_key
                        ),
                        transport_kind=str(
                            pending_record.get("transport_kind") or transport_kind
                        ),
                        chart_available=bool(chart_path),
                        chart_sent=_int_or_none(pending_record.get("photo_message_id"))
                        is not None,
                        chart_problem=chart_problem,
                    )
                )
                continue
            completed_record = next(
                (
                    outbox.get(key)
                    for key in delivery_keys
                    if outbox.get(key, {}).get("status") == "completed"
                ),
                None,
            )
            if completed_record is not None:
                sent_keys.add(delivery_key)
                _save_sent_keys(private_root, sent_keys)
                deliveries.append(
                    _delivery_from_preview(
                        item,
                        status="skipped_duplicate",
                        message_id=_int_or_none(completed_record.get("message_id")),
                        problem="already_sent_to_recipient",
                        recipient_id=recipient_id,
                        delivery_key=str(
                            completed_record.get("delivery_key") or delivery_key
                        ),
                        transport_kind=str(
                            completed_record.get("transport_kind") or transport_kind
                        ),
                        chart_available=bool(chart_path),
                        chart_sent=_int_or_none(
                            completed_record.get("photo_message_id")
                        )
                        is not None,
                        chart_problem=chart_problem,
                    )
                )
                continue
            if any(key in sent_keys for key in delivery_keys):
                sent_keys.add(delivery_key)
                _save_sent_keys(private_root, sent_keys)
                deliveries.append(
                    _delivery_from_preview(
                        item,
                        status="skipped_duplicate",
                        problem="already_sent_to_recipient",
                        recipient_id=recipient_id,
                        delivery_key=delivery_key,
                        transport_kind=transport_kind,
                        chart_available=bool(chart_path),
                        chart_problem=chart_problem,
                    )
                )
                continue
            message_id: int | None = None
            problem = ""
            status = "sent"
            chart_sent = False
            photo_message_id: int | None = None
            photo_status = (
                "pending" if chart_path and send_photo is not None else "not_applicable"
            )
            text_status = "pending"
            try:
                _upsert_outbox_record(
                    private_root,
                    outbox,
                    _outbox_record(
                        item,
                        delivery_key=delivery_key,
                        recipient_hash=recipient_hash,
                        status="pending",
                        transport_kind=transport_kind,
                        photo_status=photo_status,
                        text_status=text_status,
                    ),
                )
                if transport_chart_payload is not None and send_photo is not None:
                    photo_message_id = await send_photo(
                        recipient_id, transport_chart_payload
                    )
                    if photo_message_id is None:
                        photo_status = "unacknowledged"
                        chart_problem = "photo_message_id_missing"
                    else:
                        chart_sent = True
                        photo_status = "acknowledged"
                        _upsert_outbox_record(
                            private_root,
                            outbox,
                            _outbox_record(
                                item,
                                delivery_key=delivery_key,
                                recipient_hash=recipient_hash,
                                status="external_ack_ambiguous",
                                transport_kind=transport_kind,
                                photo_message_id=photo_message_id,
                                photo_status=photo_status,
                                text_status="pending",
                                problem="photo_ack_text_pending",
                            ),
                        )
                elif chart_path and send_photo is None:
                    photo_status = "transport_unavailable"
                    chart_problem = "photo_transport_not_configured"
                message_id = await send_text(recipient_id, str(item.get("text") or ""))
                if message_id is None:
                    text_status = "unacknowledged"
                    if chart_sent:
                        status = "external_ack_ambiguous"
                        problem = "photo_ack_text_unacknowledged"
                    else:
                        status = "skipped_no_token"
                        problem = "telegram_token_not_configured"
                else:
                    text_status = "acknowledged"
            except Exception as exc:  # noqa: BLE001 - delivery errors must be recorded, not crash the farm.
                status = "external_ack_ambiguous"
                text_status = "failed"
                if chart_sent:
                    problem = f"photo_ack_text_failed:{type(exc).__name__}"
                else:
                    problem = f"text_ack_ambiguous:{type(exc).__name__}"
            if status == "sent":
                completed_keys = sent_keys | {delivery_key}
                try:
                    _save_sent_keys(private_root, completed_keys)
                except OSError:
                    _upsert_outbox_record(
                        private_root,
                        outbox,
                        _outbox_record(
                            item,
                            delivery_key=delivery_key,
                            recipient_hash=recipient_hash,
                            status="external_ack_ambiguous",
                            transport_kind=transport_kind,
                            message_id=message_id,
                            photo_message_id=photo_message_id,
                            photo_status=photo_status,
                            text_status=text_status,
                            problem="sent_key_write_failed",
                        ),
                    )
                    deliveries.append(
                        _delivery_from_preview(
                            item,
                            status="external_ack_ambiguous",
                            message_id=message_id,
                            problem="sent_key_write_failed",
                            recipient_id=recipient_id,
                            delivery_key=delivery_key,
                            transport_kind=transport_kind,
                            chart_available=bool(chart_path),
                            chart_sent=chart_sent,
                            chart_problem=chart_problem,
                        )
                    )
                    continue
                sent_keys.add(delivery_key)
                _upsert_outbox_record(
                    private_root,
                    outbox,
                    _outbox_record(
                        item,
                        delivery_key=delivery_key,
                        recipient_hash=recipient_hash,
                        status="completed",
                        transport_kind=transport_kind,
                        message_id=message_id,
                        photo_message_id=photo_message_id,
                        photo_status=photo_status,
                        text_status=text_status,
                    ),
                )
            elif status in {"error", "skipped_no_token", "external_ack_ambiguous"}:
                _upsert_outbox_record(
                    private_root,
                    outbox,
                    _outbox_record(
                        item,
                        delivery_key=delivery_key,
                        recipient_hash=recipient_hash,
                        status=status,
                        transport_kind=transport_kind,
                        message_id=message_id,
                        photo_message_id=photo_message_id,
                        photo_status=photo_status,
                        text_status=text_status,
                        problem=problem,
                    ),
                )
            deliveries.append(
                _delivery_from_preview(
                    item,
                    status=status,
                    message_id=message_id,
                    problem=problem,
                    recipient_id=recipient_id,
                    delivery_key=delivery_key,
                    transport_kind=transport_kind,
                    chart_available=bool(chart_path),
                    chart_sent=chart_sent,
                    chart_problem=chart_problem,
                )
            )
    return deliveries


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _recipient_hash(recipient_id: str) -> str:
    return (
        hashlib.sha256(recipient_id.encode("utf-8")).hexdigest()[:16]
        if recipient_id
        else ""
    )


def _chart_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _allowed_chart_roots(private_root: Path) -> tuple[Path, ...]:
    return (
        (Path(private_root) / "state" / "derived" / "paper_reviews").resolve(),
        (
            Path(private_root) / "state" / "derived" / "paper_telegram_base_charts"
        ).resolve(),
        (Path(private_root) / "state" / "derived" / "paper_telegram_cards").resolve(),
    )


def _capture_chart_payload(source_path: Path, private_root: Path) -> tuple[bytes, str]:
    """Pin one validated file handle so identity and transport use identical bytes."""
    allowed_roots = _allowed_chart_roots(private_root)
    candidate = source_path.resolve(strict=True)
    if not any(_is_relative_to(candidate, root) for root in allowed_roots):
        raise OSError("chart source escaped allowed roots")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(candidate, flags)
    try:
        opened_stat = os.fstat(fd)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise OSError("chart source is not a regular file")
        if opened_stat.st_size <= 0 or opened_stat.st_size > MAX_CHART_PAYLOAD_BYTES:
            raise OSError("chart source size is outside the allowed bound")
        current = candidate.resolve(strict=True)
        current_stat = os.stat(current, follow_symlinks=True)
        if not any(
            _is_relative_to(current, root) for root in allowed_roots
        ) or not os.path.samestat(opened_stat, current_stat):
            raise OSError("chart source changed during secure open")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            chunks: list[bytes] = []
            total = 0
            while total <= MAX_CHART_PAYLOAD_BYTES:
                chunk = handle.read(
                    min(1024 * 1024, MAX_CHART_PAYLOAD_BYTES + 1 - total)
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total != opened_stat.st_size or total > MAX_CHART_PAYLOAD_BYTES:
                raise OSError("chart source changed size during capture")
            encoded = b"".join(chunks)
    finally:
        os.close(fd)
    digest = hashlib.sha256(encoded).hexdigest()
    return encoded, digest


def _delivery_content_identity(
    item: dict[str, Any],
    *,
    chart_path: Path | None = None,
    chart_sha256: str = "",
) -> str:
    payload = {
        key: item.get(key)
        for key in (
            "schema",
            "preview_id",
            "instruction_id",
            "source_signal_id",
            "telegram_card_id",
            "pair",
            "timeframe",
            "side",
            "setup_family",
            "text",
        )
    }
    payload["chart"] = (
        {
            "resolved_path": str(chart_path),
            "sha256": chart_sha256 or _chart_sha256(chart_path),
        }
        if chart_path is not None
        else None
    )
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _legacy_delivery_content_identity_v1(item: dict[str, Any]) -> str:
    """Reproduce the da49350 content key so completed sends are never replayed."""
    payload = {
        key: item.get(key)
        for key in (
            "schema",
            "preview_id",
            "instruction_id",
            "source_signal_id",
            "telegram_card_id",
            "pair",
            "timeframe",
            "side",
            "setup_family",
            "text",
            "chart_path",
        )
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _delivery_key(
    item: dict[str, Any],
    recipient_id: str,
    *,
    private_root: Path | None = None,
) -> str:
    return _delivery_keys(item, recipient_id, private_root=private_root)[0]


def _delivery_keys(
    item: dict[str, Any],
    recipient_id: str,
    *,
    private_root: Path | None = None,
    chart_path: Path | None = None,
    chart_sha256: str = "",
) -> list[str]:
    """Return primary + legacy sent keys for one recipient.

    The primary key binds immutable rendered content identity to a pseudonymous
    recipient. Legacy signal/preview keys remain candidates so already completed
    deliveries keep their no-resend behavior after the migration.
    """
    rh = _recipient_hash(recipient_id)
    source_signal_id = str(item.get("source_signal_id") or "").strip()
    preview_id = str(item.get("preview_id") or "").strip()
    telegram_card_id = str(item.get("telegram_card_id") or "").strip()
    if chart_path is None and private_root is not None:
        chart_path, _problem = _safe_chart_path(item, Path(private_root))
    content_identity = _delivery_content_identity(
        item,
        chart_path=chart_path,
        chart_sha256=chart_sha256,
    )
    legacy_content_identity = _legacy_delivery_content_identity_v1(item)

    if source_signal_id and source_signal_id != "paper_status_digest":
        content_key = telegram_card_id or preview_id
        candidates = [
            f"signal:{source_signal_id}:content-sha256:{content_identity}:{rh}"
        ]
        candidates.append(
            f"signal:{source_signal_id}:content-sha256:{legacy_content_identity}:{rh}"
        )
        if content_key:
            candidates.append(f"signal:{source_signal_id}:content:{content_key}:{rh}")
        candidates.append(f"signal:{source_signal_id}:{rh}")
    else:
        digest_key = preview_id or source_signal_id or telegram_card_id
        candidates = [f"digest:content-sha256:{content_identity}:{rh}"]
        candidates.append(f"digest:content-sha256:{legacy_content_identity}:{rh}")
        if digest_key:
            candidates.append(f"digest:{digest_key}:{rh}")

    for legacy in (telegram_card_id, preview_id):
        if legacy:
            candidates.append(f"{legacy}:{rh}")

    out: list[str] = []
    for key in candidates:
        if key and key not in out:
            out.append(key)
    return out or [f"missing_preview_identity:{rh}"]


def _safe_chart_path(
    item: dict[str, Any], private_root: Path
) -> tuple[Path | None, str]:
    raw = str(item.get("chart_path") or "").strip()
    if not raw:
        return None, ""
    try:
        path = Path(raw).resolve()
        allowed_roots = _allowed_chart_roots(private_root)
    except OSError:
        return None, "invalid_chart_path"
    if path.suffix.lower() != ".png":
        return None, "chart_not_png"
    if not path.exists():
        return None, "chart_missing"
    if not any(_is_relative_to(path, allowed_root) for allowed_root in allowed_roots):
        return None, "chart_outside_private_reviews"
    return path, ""


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _status_digest_reason(
    *,
    source: dict[str, Any],
    accepted: list[dict[str, Any]],
    deliveries: list[PaperTelegramDelivery],
    recipient_count: int,
) -> str:
    if not source:
        return ""
    sent = sum(1 for delivery in deliveries if delivery.status == "sent")
    duplicates = sum(
        1 for delivery in deliveries if delivery.status == "skipped_duplicate"
    )
    if (
        accepted
        and sent == 0
        and recipient_count > 0
        and duplicates >= len(accepted) * recipient_count
    ):
        return "all_cards_duplicate"
    if not accepted and int(source.get("records_read") or 0) > 0:
        if int(source.get("skipped_quality_gate") or 0) > 0:
            return "quality_gate_no_cards"
        if int(source.get("skipped_non_actionable") or 0) > 0:
            return "non_actionable_no_cards"
    return ""


def _status_digest_state(
    *,
    source: dict[str, Any],
    quality: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    pfr_state = (
        raw_pfr_state
        if isinstance(raw_pfr_state := quality.get("pfr_trigger_state"), dict)
        else {}
    )
    pfr_funnel = (
        raw_pfr_funnel
        if isinstance(raw_pfr_funnel := quality.get("pfr_funnel"), dict)
        else {}
    )
    raw_lifecycle = quality.get("active_signal_lifecycle")
    lifecycle = raw_lifecycle if isinstance(raw_lifecycle, dict) else {}
    return {
        "reason": reason,
        "source": {
            "records_read": int(source.get("records_read") or 0),
            "rendered": int(source.get("rendered") or 0),
            "skipped_quality_gate": int(source.get("skipped_quality_gate") or 0),
            "skipped_non_actionable": int(source.get("skipped_non_actionable") or 0),
        },
        "quality": {
            "operator_action": str(quality.get("operator_action") or ""),
            "active_trades": int(quality.get("active_trades") or 0),
            "active_live_ready": int(quality.get("active_live_ready") or 0),
            "quality_labels": quality.get("quality_labels") or {},
            "training_rows": int(quality.get("training_rows") or 0),
            "training_by_result": quality.get("training_by_result") or {},
        },
        "lifecycle": {
            "active": int(lifecycle.get("active") or 0),
            "by_status": lifecycle.get("by_status") or {},
            "by_outcome_result": lifecycle.get("by_outcome_result") or {},
            "pending_outcomes": int(lifecycle.get("pending_outcomes") or 0),
            "overdue_expiry": int(lifecycle.get("overdue_expiry") or 0),
            "terminal_training_backlog": int(
                lifecycle.get("terminal_training_backlog") or 0
            ),
        },
        "pfr": {
            "state": str(pfr_state.get("state") or ""),
            "catalog_ready": int(pfr_state.get("catalog_ready") or 0),
            "last_cycle_generated": int(pfr_state.get("last_cycle_generated") or 0),
            "top_reasons": pfr_state.get("top_reasons") or {},
            "near_trigger_counts": pfr_funnel.get("near_trigger_counts") or {},
            "cycle_resource_reasons": pfr_funnel.get("cycle_resource_reasons") or {},
        },
    }


def _status_digest_fingerprint(state: dict[str, Any]) -> str:
    encoded = json.dumps(
        state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _status_digest_preview(
    private_root: Path,
    *,
    source: dict[str, Any],
    reason: str,
    now: float,
    interval_hours: int,
) -> dict[str, Any]:
    quality = _load_quality_report(private_root)
    pfr_state = (
        raw_pfr_state
        if isinstance(raw_pfr_state := quality.get("pfr_trigger_state"), dict)
        else {}
    )
    pfr_reasons = (
        raw_pfr_reasons
        if isinstance(raw_pfr_reasons := pfr_state.get("top_reasons"), dict)
        else {}
    )
    pfr_funnel = (
        raw_pfr_funnel
        if isinstance(raw_pfr_funnel := quality.get("pfr_funnel"), dict)
        else {}
    )
    raw_cycle_reasons = pfr_funnel.get("cycle_resource_reasons")
    cycle_reasons = raw_cycle_reasons if isinstance(raw_cycle_reasons, dict) else {}
    raw_near_reasons = pfr_funnel.get("near_trigger_counts")
    near_reasons = raw_near_reasons if isinstance(raw_near_reasons, dict) else {}
    raw_lifecycle = quality.get("active_signal_lifecycle")
    lifecycle = raw_lifecycle if isinstance(raw_lifecycle, dict) else {}
    bucket_seconds = max(1, int(interval_hours)) * 3600
    bucket = int(now // bucket_seconds)
    state = _status_digest_state(source=source, quality=quality, reason=reason)
    state_hash = _status_digest_fingerprint(state)
    quality_labels = quality.get("quality_labels") or {}
    outcomes = quality.get("training_by_result") or {}
    text = "\n".join(
        [
            "<b>Статус paper-бота</b>",
            REQUIRED_DISCLAIMER,
            "Автоисполнение выключено.",
            "",
            f"<b>Причина:</b> <code>{reason}</code>",
            f"<b>Превью:</b> rendered=<code>{source.get('rendered', 0)}</code> "
            f"quality_skip=<code>{source.get('skipped_quality_gate', 0)}</code>",
            f"<b>Действие:</b> <code>{quality.get('operator_action') or 'monitor'}</code>",
            f"<b>Активные paper-наблюдения:</b> <code>{quality.get('active_trades', 0)}</code> "
            f"live_ready=<code>{quality.get('active_live_ready', 0)}</code>",
            f"<b>Жизненный цикл:</b> pending=<code>{lifecycle.get('pending_outcomes', 0)}</code> "
            f"states=<code>{json.dumps(lifecycle.get('by_outcome_result') or {}, ensure_ascii=False, sort_keys=True)}</code>",
            f"<b>Время:</b> oldest_h=<code>{lifecycle.get('oldest_age_hours', 0)}</code> "
            f"next_expiry_h=<code>{lifecycle.get('next_expiry_hours')}</code> "
            f"overdue=<code>{lifecycle.get('overdue_expiry', 0)}</code>",
            f"<b>PFR:</b> state=<code>{pfr_state.get('state') or 'unknown'}</code> "
            f"catalog_ready=<code>{pfr_state.get('catalog_ready', 0)}</code> "
            f"generated=<code>{pfr_state.get('last_cycle_generated', 0)}</code>",
            f"<b>Причины PFR:</b> <code>{json.dumps(pfr_reasons, ensure_ascii=False, sort_keys=True)}</code>",
            f"<b>PFR рядом со входом:</b> <code>{json.dumps(near_reasons, ensure_ascii=False, sort_keys=True)}</code>",
            f"<b>Блокеры цикла:</b> <code>{json.dumps(cycle_reasons, ensure_ascii=False, sort_keys=True)}</code>",
            f"<b>Качество:</b> <code>{json.dumps(quality_labels, ensure_ascii=False, sort_keys=True)}</code>",
            f"<b>Исходы:</b> <code>{json.dumps(outcomes, ensure_ascii=False, sort_keys=True)}</code>",
            "",
            "<i>В этом цикле не было новой карточки для подписчиков; paper-контур продолжает работать.</i>",
        ]
    )
    return {
        "schema": "PaperTelegramPreview.v1",
        "preview_id": f"paper_status_digest_{bucket}_{state_hash}",
        "instruction_id": "",
        "source_signal_id": "paper_status_digest",
        "pair": "PAPER-STATUS",
        "timeframe": "digest",
        "side": "none",
        "setup_family": "paper_status",
        "consumer_status": "status_digest",
        "text": text,
        "problems": [],
        "paper_only": True,
        "execution_allowed": False,
    }


def _delivery_from_preview(
    item: dict[str, Any],
    *,
    status: str,
    message_id: int | None = None,
    problem: str = "",
    recipient_id: str = "",
    delivery_key: str = "",
    transport_kind: str = "",
    chart_available: bool = False,
    chart_sent: bool = False,
    chart_problem: str = "",
) -> PaperTelegramDelivery:
    return PaperTelegramDelivery(
        preview_id=str(item.get("preview_id") or ""),
        instruction_id=str(item.get("instruction_id") or ""),
        source_signal_id=str(item.get("source_signal_id") or ""),
        pair=str(item.get("pair") or ""),
        timeframe=str(item.get("timeframe") or ""),
        side=str(item.get("side") or ""),
        setup_family=str(item.get("setup_family") or ""),
        status=status,
        message_id=message_id,
        problem=problem,
        recipient_hash=_recipient_hash(recipient_id),
        delivery_key=delivery_key,
        transport_kind=transport_kind,
        chart_available=chart_available,
        chart_sent=chart_sent,
        chart_problem=chart_problem,
    )


def _unique_preview_count(deliveries: list[PaperTelegramDelivery], status: str) -> int:
    return len(
        {
            delivery.preview_id
            for delivery in deliveries
            if delivery.status == status and delivery.preview_id
        }
    )


def send_paper_telegram_previews(
    private_root: Path,
    *,
    limit: int = 20,
    apply: bool = False,
    paper_chat_configured: bool = False,
    paper_chat_ids_count: int = 0,
    recipient_ids: list[str] | None = None,
    send_text: Callable[[str, str], Awaitable[int | None]] | None = None,
    send_photo: Callable[[str, bytes], Awaitable[int | None]] | None = None,
    status_digest: bool = False,
    status_digest_interval_hours: int = 12,
    now: float | None = None,
    expected_generation_run_id: str | None = None,
) -> dict[str, Any]:
    """Dry-run or send validated paper previews to active subscriber bot chats.

    ``apply=False`` is a pure dry-run and never calls Telegram. ``apply=True``
    still sends nothing unless the caller provides a configured ``send_text``
    transport. Keeping transport injection here prevents the research/farm core
    from importing Telegram or credential-aware modules.
    """
    items, source_path, source = _load_preview_items(private_root)
    expected_run_id = str(expected_generation_run_id or "")
    generation_block_reason = ""
    if expected_run_id:
        source_run_id = str(source.get("paper_generation_run_id") or "")
        source_research_generation_id = str(
            source.get("research_observation_generation_id") or ""
        )
        if source.get("current_generation_compatible") is not True:
            generation_block_reason = "preview_generation_not_current"
        elif source_run_id != expected_run_id:
            generation_block_reason = "preview_generation_run_mismatch"
        elif source_research_generation_id:
            from src.research_lab.paper_telegram_preview import (
                research_observation_generation_id,
            )

            if (
                research_observation_generation_id(Path(private_root))
                != source_research_generation_id
            ):
                generation_block_reason = "research_preview_generation_stale"
        if not generation_block_reason and any(
            not (
                str(item.get("paper_generation_run_id") or "") == expected_run_id
                or (
                    not str(item.get("paper_generation_run_id") or "")
                    and str(item.get("validation_tier") or "") == "farm_calculated"
                    and bool(source_research_generation_id)
                    and str(item.get("research_observation_generation_id") or "")
                    == source_research_generation_id
                )
            )
            for item in items
        ):
            generation_block_reason = "preview_item_generation_run_mismatch"
        if generation_block_reason:
            items = []
            status_digest = False
    recipient_ids = [str(r).strip() for r in (recipient_ids or []) if str(r).strip()]
    sent_keys: set[str] = set()
    accepted: list[dict[str, Any]] = []
    deliveries: list[PaperTelegramDelivery] = []
    status_digest_reason = ""
    invalid = 0
    for item in items:
        ok, problem = _valid_preview(item)
        if not ok:
            invalid += 1
            deliveries.append(
                _delivery_from_preview(item, status="invalid_preview", problem=problem)
            )
            continue
        if len(accepted) < limit:
            accepted.append(item)

    if not apply:
        deliveries.extend(
            _delivery_from_preview(
                item,
                status="dry_run",
                chart_available=bool(_safe_chart_path(item, Path(private_root))[0]),
                chart_problem=_safe_chart_path(item, Path(private_root))[1],
            )
            for item in accepted
        )
    elif not paper_chat_configured or send_text is None or not recipient_ids:
        deliveries.extend(
            _delivery_from_preview(
                item,
                status="skipped_no_subscribers",
                problem="paper_subscribers_not_configured",
            )
            for item in accepted
        )
    else:
        try:
            with _delivery_claim(Path(private_root)):
                sent_keys = _load_sent_keys(private_root)
                deliveries.extend(
                    asyncio.run(
                        _send_items(
                            accepted,
                            recipient_ids,
                            send_text,
                            send_photo,
                            sent_keys,
                            Path(private_root),
                        )
                    )
                )
                status_digest_reason = _status_digest_reason(
                    source=source,
                    accepted=accepted,
                    deliveries=deliveries,
                    recipient_count=len(recipient_ids),
                )
                if status_digest and status_digest_reason:
                    digest_item = _status_digest_preview(
                        Path(private_root),
                        source=source,
                        reason=status_digest_reason,
                        now=time.time() if now is None else now,
                        interval_hours=status_digest_interval_hours,
                    )
                    deliveries.extend(
                        asyncio.run(
                            _send_items(
                                [digest_item],
                                recipient_ids,
                                send_text,
                                send_photo,
                                sent_keys,
                                Path(private_root),
                            )
                        )
                    )
        except DeliveryOutboxUnavailable as exc:
            deliveries.extend(
                _delivery_from_preview(
                    item,
                    status="outbox_unavailable",
                    problem=exc.problem,
                    recipient_id=recipient_id,
                    delivery_key=_delivery_key(item, recipient_id),
                    transport_kind="telegram_text",
                    chart_available=bool(_safe_chart_path(item, Path(private_root))[0]),
                    chart_problem=_safe_chart_path(item, Path(private_root))[1],
                )
                for item in accepted
                for recipient_id in recipient_ids
            )
        except (DeliveryClaimConflict, OSError) as exc:
            claim_problem = (
                "delivery_claim_held_by_other_process"
                if isinstance(exc, DeliveryClaimConflict)
                else "delivery_claim_unavailable"
            )
            deliveries.extend(
                _delivery_from_preview(
                    item,
                    status="pending_delivery_claim",
                    problem=claim_problem,
                    recipient_id=recipient_id,
                    delivery_key=_delivery_key(item, recipient_id),
                    transport_kind="telegram_text",
                    chart_available=bool(_safe_chart_path(item, Path(private_root))[0]),
                    chart_problem=_safe_chart_path(item, Path(private_root))[1],
                )
                for item in accepted
                for recipient_id in recipient_ids
            )

    out_jsonl = _delivery_jsonl_path(private_root)
    out_snapshot = _delivery_snapshot_path(private_root)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for delivery in deliveries:
            fh.write(
                json.dumps(delivery.to_dict(), ensure_ascii=False, sort_keys=True)
                + "\n"
            )

    sent_messages = sum(1 for delivery in deliveries if delivery.status == "sent")
    duplicate_messages = sum(
        1 for delivery in deliveries if delivery.status == "skipped_duplicate"
    )
    skipped_messages = sum(
        1 for delivery in deliveries if delivery.status.startswith("skipped")
    )
    error_messages = sum(1 for delivery in deliveries if delivery.status == "error")
    ambiguous_messages = sum(
        1 for delivery in deliveries if delivery.status == "external_ack_ambiguous"
    )
    carried_ambiguous_messages = sum(
        1
        for delivery in deliveries
        if delivery.status == "external_ack_ambiguous"
        and delivery.problem == "external_ack_requires_operator_recovery"
    )
    current_ambiguous_messages = max(
        0, ambiguous_messages - carried_ambiguous_messages
    )
    pending_claim_messages = sum(
        1 for delivery in deliveries if delivery.status == "pending_delivery_claim"
    )
    outbox_unavailable_messages = sum(
        1 for delivery in deliveries if delivery.status == "outbox_unavailable"
    )
    chart_available_messages = sum(
        1 for delivery in deliveries if delivery.chart_available
    )
    chart_sent_messages = sum(1 for delivery in deliveries if delivery.chart_sent)
    digest_messages = sum(
        1
        for delivery in deliveries
        if delivery.source_signal_id == "paper_status_digest"
        and delivery.status == "sent"
    )
    digest_duplicates = sum(
        1
        for delivery in deliveries
        if delivery.source_signal_id == "paper_status_digest"
        and delivery.status == "skipped_duplicate"
    )
    summary = {
        "schema": SUMMARY_SCHEMA,
        "source_schema": source.get("schema", ""),
        "source_exists": source_path is not None,
        "source_path": str(source_path) if source_path else "",
        "paper_generation_run_id": str(source.get("paper_generation_run_id") or ""),
        "expected_generation_run_id": expected_run_id,
        "generation_block_reason": generation_block_reason,
        "current_generation_compatible": bool(
            source.get("current_generation_compatible")
        )
        and not generation_block_reason,
        "records_read": len(items),
        "eligible": len(accepted),
        "eligible_cards": len(accepted),
        "invalid_preview": invalid,
        "dry_run": not apply,
        "configured": bool(paper_chat_configured),
        "chat_env": DEFAULT_DELIVERY_TARGET,
        "chat_ids_count": int(paper_chat_ids_count),
        "targets": len(recipient_ids),
        "target_recipients": len(recipient_ids),
        "potential_messages": len(accepted) * len(recipient_ids),
        "delivery_target": "active_subscription_users",
        "sent": sent_messages,
        "sent_messages": sent_messages,
        "sent_cards": _unique_preview_count(deliveries, "sent"),
        "chart_available_messages": chart_available_messages,
        "chart_sent_messages": chart_sent_messages,
        "duplicates": duplicate_messages,
        "duplicate_messages": duplicate_messages,
        "duplicate_cards": _unique_preview_count(deliveries, "skipped_duplicate"),
        "skipped": skipped_messages,
        "skipped_messages": skipped_messages,
        "errors": error_messages,
        "error_messages": error_messages,
        "error_cards": _unique_preview_count(deliveries, "error"),
        "external_ack_ambiguous": ambiguous_messages,
        "external_ack_ambiguous_messages": ambiguous_messages,
        "external_ack_ambiguous_current_attempts": current_ambiguous_messages,
        "external_ack_ambiguous_carried": carried_ambiguous_messages,
        "external_ack_ambiguous_cards": _unique_preview_count(
            deliveries, "external_ack_ambiguous"
        ),
        "pending_delivery_claim": pending_claim_messages,
        "pending_delivery_claim_messages": pending_claim_messages,
        "pending_delivery_claim_cards": _unique_preview_count(
            deliveries, "pending_delivery_claim"
        ),
        "outbox_unavailable": outbox_unavailable_messages,
        "outbox_unavailable_messages": outbox_unavailable_messages,
        "outbox_unavailable_cards": _unique_preview_count(
            deliveries, "outbox_unavailable"
        ),
        "status_digest_enabled": bool(status_digest),
        "status_digest_reason": status_digest_reason,
        "status_digest_sent_messages": digest_messages,
        "status_digest_duplicate_messages": digest_duplicates,
        "paper_only": True,
        "execution_allowed": False,
        "sends_network": bool(
            apply and paper_chat_configured and send_text is not None and recipient_ids
        ),
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
    }
    out_snapshot.write_text(
        json.dumps(
            {**summary, "items": [delivery.to_dict() for delivery in deliveries]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary
