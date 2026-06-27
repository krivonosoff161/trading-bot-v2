"""Opt-in Telegram sender for validated paper-watch previews.

This module is deliberately downstream of ``paper_telegram_preview``. It never
builds trading decisions and never falls back to the default Telegram chat. The
only delivery target is ``PAPER_CHAT_ID`` and sending requires an explicit
``apply=True`` call from the CLI/operator.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

SCHEMA = "PaperTelegramDelivery.v1"
SUMMARY_SCHEMA = "paper_telegram_delivery.v1"
DEFAULT_CHAT_ENV = "PAPER_CHAT_ID"


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
    paper_only: bool = True
    execution_allowed: bool = False
    chat_env: str = DEFAULT_CHAT_ENV
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError(f"schema must be {SCHEMA}")
        if self.execution_allowed:
            raise ValueError("paper Telegram delivery must never allow execution")
        if not self.paper_only:
            raise ValueError("paper Telegram delivery must be paper_only")
        if self.chat_env != DEFAULT_CHAT_ENV:
            raise ValueError("paper Telegram delivery must use PAPER_CHAT_ID")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _preview_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_preview.json"


def _delivery_jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_delivery.jsonl"


def _delivery_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_telegram_delivery.json"


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
    if "research-only, not an order" not in str(item.get("text") or ""):
        return False, "missing_research_disclaimer"
    return True, ""


async def _send_items(
    items: list[dict[str, Any]],
    send_text: Callable[[str], Awaitable[int | None]],
) -> list[PaperTelegramDelivery]:
    deliveries: list[PaperTelegramDelivery] = []
    for item in items:
        message_id: int | None = None
        problem = ""
        status = "sent"
        try:
            message_id = await send_text(str(item.get("text") or ""))
            if message_id is None:
                status = "skipped_no_token"
                problem = "telegram_token_not_configured"
        except Exception as exc:  # noqa: BLE001 - delivery errors must be recorded, not crash the farm.
            status = "error"
            problem = type(exc).__name__
        deliveries.append(_delivery_from_preview(item, status=status, message_id=message_id, problem=problem))
    return deliveries


def _delivery_from_preview(
    item: dict[str, Any],
    *,
    status: str,
    message_id: int | None = None,
    problem: str = "",
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
    )


def send_paper_telegram_previews(
    private_root: Path,
    *,
    limit: int = 20,
    apply: bool = False,
    paper_chat_configured: bool = False,
    paper_chat_ids_count: int = 0,
    send_text: Callable[[str], Awaitable[int | None]] | None = None,
) -> dict[str, Any]:
    """Dry-run or send validated paper previews to ``PAPER_CHAT_ID``.

    ``apply=False`` is a pure dry-run and never calls Telegram. ``apply=True``
    still sends nothing unless the caller provides a configured ``send_text``
    transport. Keeping transport injection here prevents the research/farm core
    from importing Telegram or credential-aware modules.
    """
    items, source_path, source = _load_preview_items(private_root)
    accepted: list[dict[str, Any]] = []
    deliveries: list[PaperTelegramDelivery] = []
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
        deliveries.extend(_delivery_from_preview(item, status="dry_run") for item in accepted)
    elif not paper_chat_configured or send_text is None:
        deliveries.extend(
            _delivery_from_preview(item, status="skipped_no_paper_chat", problem="paper_telegram_not_configured")
            for item in accepted
        )
    else:
        deliveries.extend(asyncio.run(_send_items(accepted, send_text)))

    out_jsonl = _delivery_jsonl_path(private_root)
    out_snapshot = _delivery_snapshot_path(private_root)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for delivery in deliveries:
            fh.write(json.dumps(delivery.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    summary = {
        "schema": SUMMARY_SCHEMA,
        "source_schema": source.get("schema", ""),
        "source_exists": source_path is not None,
        "source_path": str(source_path) if source_path else "",
        "records_read": len(items),
        "eligible": len(accepted),
        "invalid_preview": invalid,
        "dry_run": not apply,
        "configured": bool(paper_chat_configured),
        "chat_env": DEFAULT_CHAT_ENV,
        "chat_ids_count": int(paper_chat_ids_count),
        "sent": sum(1 for delivery in deliveries if delivery.status == "sent"),
        "skipped": sum(1 for delivery in deliveries if delivery.status.startswith("skipped")),
        "errors": sum(1 for delivery in deliveries if delivery.status == "error"),
        "paper_only": True,
        "execution_allowed": False,
        "sends_network": bool(apply and paper_chat_configured and send_text is not None),
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
    }
    out_snapshot.write_text(
        json.dumps({**summary, "items": [delivery.to_dict() for delivery in deliveries]},
                   ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
