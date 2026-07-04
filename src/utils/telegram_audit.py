"""Append-only Telegram product audit.

The audit is intentionally separate from Telegram delivery. It records enough
metadata to debug billing, routing, and product behavior without becoming the
source of truth for trading decisions.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT_PATH = ROOT / "logs" / "telegram" / "message_audit.jsonl"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _preview(text: str, limit: int = 240) -> str:
    normalized = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())
    return normalized[:limit]


def record_message_audit(
    *,
    chat_id: str,
    direction: str,
    mode: str,
    event: str,
    text: str = "",
    symbol: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    usage: dict[str, Any] | None = None,
    status: str = "ok",
    delivery_status: str | None = None,
    message_id: int | None = None,
    extra: dict[str, Any] | None = None,
    path: Path | None = None,
) -> Path:
    """Write one sanitized audit row and return the audit path."""
    audit_path = path or DEFAULT_AUDIT_PATH
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema": "telegram_message_audit.v1",
        "ts_ms": _now_ms(),
        "chat_id": str(chat_id),
        "direction": str(direction),
        "mode": str(mode),
        "event": str(event),
        "symbol": symbol,
        "text_hash": _hash_text(text) if text else "",
        "text_preview": _preview(text) if text else "",
        "provider": provider,
        "model": model,
        "usage": usage or {},
        "status": status,
        "delivery_status": delivery_status,
        "message_id": message_id,
        "extra": extra or {},
    }
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return audit_path
