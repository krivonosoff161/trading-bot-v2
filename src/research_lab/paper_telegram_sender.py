"""Opt-in Telegram sender for validated paper-watch previews.

This module is deliberately downstream of ``paper_telegram_preview``. It never
builds trading decisions and never falls back to public scanner/default chats.
Paper setup delivery is a subscriber product surface: it sends only to active
bot subscribers/superadmins supplied by the caller, and only after an explicit
``apply=True`` call from the CLI/operator.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

SCHEMA = "PaperTelegramDelivery.v1"
SUMMARY_SCHEMA = "paper_telegram_delivery.v1"
DEFAULT_DELIVERY_TARGET = "SUBSCRIPTION_USERS"
REQUIRED_DISCLAIMER = "Бумажный режим: это не ордер."


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
    return Path(private_root) / "state" / "derived" / "paper_telegram_delivery_outbox.json"


def _quality_report_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_product_quality_report.json"


def _load_sent_keys(private_root: Path) -> set[str]:
    path = _sent_keys_path(private_root)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(item) for item in data.get("sent_keys", []) if str(item)}


def _save_sent_keys(private_root: Path, sent_keys: set[str]) -> None:
    path = _sent_keys_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(
            {"schema": "paper_telegram_sent_keys.v1", "sent_keys": sorted(sent_keys)},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _load_outbox(private_root: Path) -> dict[str, dict[str, Any]]:
    path = _outbox_path(private_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("delivery_key") or "")
        if key:
            out[key] = item
    return out


def _save_outbox(private_root: Path, outbox: dict[str, dict[str, Any]]) -> None:
    path = _outbox_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(
            {
                "schema": "paper_telegram_delivery_outbox.v1",
                "items": [outbox[key] for key in sorted(outbox)],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _outbox_record(
    item: dict[str, Any],
    *,
    delivery_key: str,
    recipient_hash: str,
    status: str,
    transport_kind: str,
    message_id: int | None = None,
    photo_message_id: int | None = None,
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
        "problem": problem,
        "paper_only": True,
        "execution_allowed": False,
    }


def _upsert_outbox_record(private_root: Path, outbox: dict[str, dict[str, Any]], record: dict[str, Any]) -> None:
    outbox[str(record["delivery_key"])] = record
    _save_outbox(private_root, outbox)


def _load_preview_items(private_root: Path) -> tuple[list[dict[str, Any]], Path | None, dict[str, Any]]:
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
    send_photo: Callable[[str, str], Awaitable[int | None]] | None,
    sent_keys: set[str],
    private_root: Path,
) -> list[PaperTelegramDelivery]:
    deliveries: list[PaperTelegramDelivery] = []
    outbox = _load_outbox(private_root)
    for item in items:
        for recipient_id in recipient_ids:
            delivery_keys = _delivery_keys(item, recipient_id)
            delivery_key = delivery_keys[0]
            recipient_hash = _recipient_hash(recipient_id)
            chart_path, chart_problem = _safe_chart_path(item, private_root)
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
                        delivery_key=str(blocking_record.get("delivery_key") or delivery_key),
                        transport_kind=str(blocking_record.get("transport_kind") or "telegram_text"),
                        chart_available=bool(chart_path),
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
                        delivery_key=str(pending_record.get("delivery_key") or delivery_key),
                        transport_kind=str(pending_record.get("transport_kind") or "telegram_text"),
                        chart_available=bool(chart_path),
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
                sent_keys.update(delivery_keys)
                _save_sent_keys(private_root, sent_keys)
                deliveries.append(
                    _delivery_from_preview(
                        item,
                        status="skipped_duplicate",
                        message_id=_int_or_none(completed_record.get("message_id")),
                        problem="already_sent_to_recipient",
                        recipient_id=recipient_id,
                        delivery_key=str(completed_record.get("delivery_key") or delivery_key),
                        transport_kind=str(completed_record.get("transport_kind") or "telegram_text"),
                        chart_available=bool(chart_path),
                        chart_problem=chart_problem,
                    )
                )
                continue
            if any(key in sent_keys for key in delivery_keys):
                sent_keys.update(delivery_keys)
                _save_sent_keys(private_root, sent_keys)
                deliveries.append(
                    _delivery_from_preview(
                        item,
                        status="skipped_duplicate",
                        problem="already_sent_to_recipient",
                        recipient_id=recipient_id,
                        delivery_key=delivery_key,
                        transport_kind="telegram_text",
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
            try:
                _upsert_outbox_record(
                    private_root,
                    outbox,
                    _outbox_record(
                        item,
                        delivery_key=delivery_key,
                        recipient_hash=recipient_hash,
                        status="pending",
                        transport_kind="telegram_text",
                    ),
                )
                if chart_path and send_photo is not None:
                    photo_message_id = await send_photo(recipient_id, str(chart_path))
                    if photo_message_id is None:
                        chart_problem = "photo_message_id_missing"
                    else:
                        chart_sent = True
                elif chart_path and send_photo is None:
                    chart_problem = "photo_transport_not_configured"
                message_id = await send_text(recipient_id, str(item.get("text") or ""))
                if message_id is None:
                    status = "skipped_no_token"
                    problem = "telegram_token_not_configured"
            except Exception as exc:  # noqa: BLE001 - delivery errors must be recorded, not crash the farm.
                status = "error"
                problem = type(exc).__name__
            if status == "sent":
                sent_keys.update(delivery_keys)
                try:
                    _save_sent_keys(private_root, sent_keys)
                except OSError:
                    _upsert_outbox_record(
                        private_root,
                        outbox,
                        _outbox_record(
                            item,
                            delivery_key=delivery_key,
                            recipient_hash=recipient_hash,
                            status="external_ack_ambiguous",
                            transport_kind="telegram_text",
                            message_id=message_id,
                            photo_message_id=photo_message_id,
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
                            transport_kind="telegram_text",
                            chart_available=bool(chart_path),
                            chart_sent=chart_sent,
                            chart_problem=chart_problem,
                        )
                    )
                    continue
                _upsert_outbox_record(
                    private_root,
                    outbox,
                    _outbox_record(
                        item,
                        delivery_key=delivery_key,
                        recipient_hash=recipient_hash,
                        status="completed",
                        transport_kind="telegram_text",
                        message_id=message_id,
                        photo_message_id=photo_message_id,
                    ),
                )
            elif status in {"error", "skipped_no_token"}:
                _upsert_outbox_record(
                    private_root,
                    outbox,
                    _outbox_record(
                        item,
                        delivery_key=delivery_key,
                        recipient_hash=recipient_hash,
                        status=status,
                        transport_kind="telegram_text",
                        message_id=message_id,
                        photo_message_id=photo_message_id,
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
                    transport_kind="telegram_text",
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
    return hashlib.sha256(recipient_id.encode("utf-8")).hexdigest()[:16] if recipient_id else ""


def _delivery_key(item: dict[str, Any], recipient_id: str) -> str:
    return _delivery_keys(item, recipient_id)[0]


def _delivery_keys(item: dict[str, Any], recipient_id: str) -> list[str]:
    """Return primary + legacy sent keys for one recipient.

    Signal cards deduplicate on stable signal identity. ``telegram_card_id`` is a
    rendered-content hash and changes when wording/templates change, which caused
    duplicate Telegram sends for the same trade idea. Status digests are different:
    their preview_id includes a bucket/state hash, so they intentionally resend
    only when the operator digest materially changes.
    """
    rh = _recipient_hash(recipient_id)
    source_signal_id = str(item.get("source_signal_id") or "").strip()
    preview_id = str(item.get("preview_id") or "").strip()
    telegram_card_id = str(item.get("telegram_card_id") or "").strip()

    if source_signal_id and source_signal_id != "paper_status_digest":
        candidates = [f"signal:{source_signal_id}:{rh}"]
    else:
        digest_key = preview_id or source_signal_id or telegram_card_id
        candidates = [f"digest:{digest_key}:{rh}"] if digest_key else []

    for legacy in (telegram_card_id, preview_id):
        if legacy:
            candidates.append(f"{legacy}:{rh}")

    out: list[str] = []
    for key in candidates:
        if key and key not in out:
            out.append(key)
    return out or [f"missing_preview_identity:{rh}"]


def _safe_chart_path(item: dict[str, Any], private_root: Path) -> tuple[Path | None, str]:
    raw = str(item.get("chart_path") or "").strip()
    if not raw:
        return None, ""
    try:
        path = Path(raw).resolve()
        allowed_roots = (
            (Path(private_root) / "state" / "derived" / "paper_reviews").resolve(),
            (Path(private_root) / "state" / "derived" / "paper_telegram_base_charts").resolve(),
            (Path(private_root) / "state" / "derived" / "paper_telegram_cards").resolve(),
        )
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
    duplicates = sum(1 for delivery in deliveries if delivery.status == "skipped_duplicate")
    if accepted and sent == 0 and recipient_count > 0 and duplicates >= len(accepted) * recipient_count:
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
    pfr_state = quality.get("pfr_trigger_state") if isinstance(quality.get("pfr_trigger_state"), dict) else {}
    pfr_funnel = quality.get("pfr_funnel") if isinstance(quality.get("pfr_funnel"), dict) else {}
    lifecycle = quality.get("active_signal_lifecycle") if isinstance(
        quality.get("active_signal_lifecycle"),
        dict,
    ) else {}
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
            "terminal_training_backlog": int(lifecycle.get("terminal_training_backlog") or 0),
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
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
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
    pfr_state = quality.get("pfr_trigger_state") if isinstance(quality.get("pfr_trigger_state"), dict) else {}
    pfr_reasons = pfr_state.get("top_reasons") if isinstance(pfr_state.get("top_reasons"), dict) else {}
    pfr_funnel = quality.get("pfr_funnel") if isinstance(quality.get("pfr_funnel"), dict) else {}
    cycle_reasons = pfr_funnel.get("cycle_resource_reasons") if isinstance(
        pfr_funnel.get("cycle_resource_reasons"),
        dict,
    ) else {}
    near_reasons = pfr_funnel.get("near_trigger_counts") if isinstance(
        pfr_funnel.get("near_trigger_counts"),
        dict,
    ) else {}
    lifecycle = quality.get("active_signal_lifecycle") if isinstance(
        quality.get("active_signal_lifecycle"),
        dict,
    ) else {}
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
    return len({delivery.preview_id for delivery in deliveries if delivery.status == status and delivery.preview_id})


def send_paper_telegram_previews(
    private_root: Path,
    *,
    limit: int = 20,
    apply: bool = False,
    paper_chat_configured: bool = False,
    paper_chat_ids_count: int = 0,
    recipient_ids: list[str] | None = None,
    send_text: Callable[[str, str], Awaitable[int | None]] | None = None,
    send_photo: Callable[[str, str], Awaitable[int | None]] | None = None,
    status_digest: bool = False,
    status_digest_interval_hours: int = 12,
    now: float | None = None,
) -> dict[str, Any]:
    """Dry-run or send validated paper previews to active subscriber bot chats.

    ``apply=False`` is a pure dry-run and never calls Telegram. ``apply=True``
    still sends nothing unless the caller provides a configured ``send_text``
    transport. Keeping transport injection here prevents the research/farm core
    from importing Telegram or credential-aware modules.
    """
    items, source_path, source = _load_preview_items(private_root)
    recipient_ids = [str(r).strip() for r in (recipient_ids or []) if str(r).strip()]
    sent_keys = _load_sent_keys(private_root)
    accepted: list[dict[str, Any]] = []
    deliveries: list[PaperTelegramDelivery] = []
    status_digest_reason = ""
    invalid = 0
    for item in items:
        ok, problem = _valid_preview(item)
        if not ok:
            invalid += 1
            deliveries.append(_delivery_from_preview(item, status="invalid_preview", problem=problem))
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
            _delivery_from_preview(item, status="skipped_no_subscribers", problem="paper_subscribers_not_configured")
            for item in accepted
        )
    else:
        deliveries.extend(asyncio.run(_send_items(accepted, recipient_ids, send_text, send_photo, sent_keys, Path(private_root))))
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
                asyncio.run(_send_items([digest_item], recipient_ids, send_text, send_photo, sent_keys, Path(private_root)))
            )

    out_jsonl = _delivery_jsonl_path(private_root)
    out_snapshot = _delivery_snapshot_path(private_root)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for delivery in deliveries:
            fh.write(json.dumps(delivery.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    sent_messages = sum(1 for delivery in deliveries if delivery.status == "sent")
    duplicate_messages = sum(1 for delivery in deliveries if delivery.status == "skipped_duplicate")
    skipped_messages = sum(1 for delivery in deliveries if delivery.status.startswith("skipped"))
    error_messages = sum(1 for delivery in deliveries if delivery.status == "error")
    ambiguous_messages = sum(1 for delivery in deliveries if delivery.status == "external_ack_ambiguous")
    pending_claim_messages = sum(1 for delivery in deliveries if delivery.status == "pending_delivery_claim")
    chart_available_messages = sum(1 for delivery in deliveries if delivery.chart_available)
    chart_sent_messages = sum(1 for delivery in deliveries if delivery.chart_sent)
    digest_messages = sum(
        1
        for delivery in deliveries
        if delivery.source_signal_id == "paper_status_digest" and delivery.status == "sent"
    )
    digest_duplicates = sum(
        1
        for delivery in deliveries
        if delivery.source_signal_id == "paper_status_digest" and delivery.status == "skipped_duplicate"
    )
    summary = {
        "schema": SUMMARY_SCHEMA,
        "source_schema": source.get("schema", ""),
        "source_exists": source_path is not None,
        "source_path": str(source_path) if source_path else "",
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
        "external_ack_ambiguous_cards": _unique_preview_count(deliveries, "external_ack_ambiguous"),
        "pending_delivery_claim": pending_claim_messages,
        "pending_delivery_claim_messages": pending_claim_messages,
        "pending_delivery_claim_cards": _unique_preview_count(deliveries, "pending_delivery_claim"),
        "status_digest_enabled": bool(status_digest),
        "status_digest_reason": status_digest_reason,
        "status_digest_sent_messages": digest_messages,
        "status_digest_duplicate_messages": digest_duplicates,
        "paper_only": True,
        "execution_allowed": False,
        "sends_network": bool(apply and paper_chat_configured and send_text is not None and recipient_ids),
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
    }
    out_snapshot.write_text(
        json.dumps({**summary, "items": [delivery.to_dict() for delivery in deliveries]},
                   ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
