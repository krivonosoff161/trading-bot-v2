"""Opt-in Telegram transport adapter for Strategy Lab paper previews.

This module is intentionally outside the research core. The farm loop imports it
only when the operator explicitly enables paper Telegram delivery.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import aiohttp


def build_subscription_delivery_config(root: Path) -> dict[str, Any]:
    """Resolve active bot subscribers and Telegram send function.

    Chat IDs are returned to the sender, which stores only recipient hashes in
    delivery artifacts. Importing credential-aware Telegram helpers is isolated
    here so the farm/research modules stay free of direct money/product imports.
    """
    from dotenv import load_dotenv
    from src.utils.runtime_root import runtime_env_file

    load_dotenv(runtime_env_file(Path(root)))

    from scripts.subscriptions import list_delivery_users
    from src.utils.telegram import bot_token, send_photo_bytes_to

    ids = [
        str(user.get("chat_id") or "").strip()
        for user in list_delivery_users()
        if str(user.get("status") or "").lower() in {"active", "superadmin"}
    ]
    ids = [chat_id for chat_id in ids if chat_id]

    async def send_text(chat_id: str, text: str) -> int | None:
        token = bot_token()
        if not token:
            return None
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        async with aiohttp.ClientSession() as session:
            resp = await session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10))
            body_text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Telegram HTTP {resp.status}: {body_text[:160]}")
            try:
                body = await resp.json()
            except Exception as exc:  # noqa: BLE001 - malformed Telegram body is a delivery error
                raise RuntimeError("Telegram invalid_json") from exc
            if not body.get("ok", True):
                raise RuntimeError("Telegram ok=false")
            return body.get("result", {}).get("message_id")

    async def send_photo(chat_id: str, payload: bytes) -> int | None:
        return await send_photo_bytes_to(chat_id, payload)

    configured = bool(bot_token() and ids)
    return {
        "configured": configured,
        "ids": ids,
        "send_text": send_text if configured else None,
        "send_photo": send_photo if configured else None,
    }
