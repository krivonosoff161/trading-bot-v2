"""
Telegram notifications — send trade alerts via Bot API.
Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from .env.
TELEGRAM_CHAT_ID supports multiple IDs separated by commas.
Silently skips if not configured.
"""

import hashlib
import os
from pathlib import Path

import aiohttp
from loguru import logger


def _log_transport_ack(message: str, *args: object) -> None:
    """Keep a successful external ACK independent from a fallible log sink.

    Telegram's response is the side-effect authority.  Runtime stdout rotation
    is useful evidence, but a storage/tee failure after ``ok=true`` must not
    turn the already completed external send into an unknown result for the
    delivery outbox.  The caller persists the returned message id through its
    own transactional boundary.
    """

    try:
        logger.info(message, *args)
    except Exception:  # noqa: BLE001 - an audit sink cannot revoke an external ACK
        return


def recipient_ref(chat_id: str) -> str:
    """Return a non-reversible local log label for a Telegram recipient."""
    return hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:12]


def _clean_env(value: str | None) -> str:
    return (value or "").strip().strip("'\"")


def bot_token() -> str:
    """Return the current bot token without caching it at import time."""
    return _clean_env(os.getenv("TELEGRAM_BOT_TOKEN"))


def chat_ids(env_name: str = "TELEGRAM_CHAT_ID") -> list[str]:
    """Return configured chat IDs from the current environment."""
    return [cid.strip() for cid in _clean_env(os.getenv(env_name)).split(",") if cid.strip()]


def telegram_status(*, chat_env: str = "TELEGRAM_CHAT_ID") -> dict[str, object]:
    """Public-safe Telegram config status. Never exposes token or chat IDs."""
    ids = chat_ids(chat_env)
    token_set = bool(bot_token())
    return {
        "token_set": token_set,
        "chat_env": chat_env,
        "chat_ids_count": len(ids),
        "configured": bool(token_set and ids),
    }


async def send_message(text: str) -> None:
    """Send a Telegram message to all configured chat IDs. No-op if not configured."""
    token = bot_token()
    ids = chat_ids()
    if not token or not ids:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            for chat_id in ids:
                resp = await session.post(
                    url,
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                    timeout=aiohttp.ClientTimeout(total=5),
                )
                if resp.status != 200:
                    logger.warning(
                        "Telegram error | recipient_ref={} status={}",
                        recipient_ref(chat_id),
                        resp.status,
                    )
    except Exception as e:
        logger.warning("Telegram send failed | {}", e)


async def send_message_to(chat_id: str, text: str) -> int | None:
    """Send a Telegram message to a specific chat_id (not broadcast).

    Returns ``message_id`` on success (or None if no token configured).
    Raises ``RuntimeError`` on HTTP errors, 429 retries, or ``ok: false``.

    Logs delivery via loguru so silent drops can be debugged: if Telegram
    returns ``ok: true`` without sequential message_id, that indicates a
    server-side silent drop (anti-spam shadow ban).
    """
    token = bot_token()
    if not token:
        return None
    import asyncio
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

    async with aiohttp.ClientSession() as session:
        for attempt in range(2):
            resp = await session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10))
            status = resp.status
            body_text = await resp.text()
            try:
                import json as _json
                body_json = _json.loads(body_text)
            except Exception:
                body_json = {}

            if status == 429:
                retry_after = int(body_json.get("parameters", {}).get("retry_after", 2))
                if attempt == 0:
                    await asyncio.sleep(min(retry_after, 5))
                    continue
                raise RuntimeError(f"Telegram 429 (retry_after={retry_after}s): {body_text[:120]}")

            if status != 200:
                raise RuntimeError(f"Telegram HTTP {status}: {body_text[:200]}")

            if body_json and not body_json.get("ok", True):
                raise RuntimeError(f"Telegram ok=false: {body_text[:200]}")

            msg_id = body_json.get("result", {}).get("message_id")
            _log_transport_ack(
                "Telegram sent | recipient_ref={} msg_id={}",
                recipient_ref(chat_id),
                msg_id,
            )
            return msg_id


async def send_photo_to(chat_id: str, file_path: str, caption: str = "",
                        parse_mode: str | None = None) -> int | None:
    """Send a photo file to a specific chat_id and return Telegram message_id."""
    token = bot_token()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    async with aiohttp.ClientSession() as session:
        data = aiohttp.FormData()
        data.add_field("chat_id", chat_id)
        data.add_field("caption", caption)
        if parse_mode:
            data.add_field("parse_mode", parse_mode)
        with open(file_path, "rb") as f:
            data.add_field("photo", f, filename=Path(file_path).name)
            resp = await session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=30))
        body_text = await resp.text()
        try:
            import json as _json
            body_json = _json.loads(body_text)
        except Exception:
            body_json = {}

        if resp.status != 200:
            raise RuntimeError(f"Telegram photo HTTP {resp.status}: {body_text[:200]}")
        if body_json and not body_json.get("ok", True):
            raise RuntimeError(f"Telegram photo ok=false: {body_text[:200]}")

        msg_id = body_json.get("result", {}).get("message_id")
        _log_transport_ack(
            "Telegram photo sent | recipient_ref={} msg_id={}",
            recipient_ref(chat_id),
            msg_id,
        )
        return msg_id


async def send_photo_bytes_to(chat_id: str, payload: bytes, caption: str = "",
                              parse_mode: str | None = None) -> int | None:
    """Send already captured PNG bytes without reopening a mutable source path."""
    token = bot_token()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    async with aiohttp.ClientSession() as session:
        data = aiohttp.FormData()
        data.add_field("chat_id", chat_id)
        data.add_field("caption", caption)
        if parse_mode:
            data.add_field("parse_mode", parse_mode)
        data.add_field(
            "photo",
            payload,
            filename="paper_chart.png",
            content_type="image/png",
        )
        resp = await session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=30))
        body_text = await resp.text()
        try:
            import json as _json
            body_json = _json.loads(body_text)
        except Exception:
            body_json = {}

        if resp.status != 200:
            raise RuntimeError(f"Telegram photo HTTP {resp.status}: {body_text[:200]}")
        if body_json and not body_json.get("ok", True):
            raise RuntimeError(f"Telegram photo ok=false: {body_text[:200]}")

        msg_id = body_json.get("result", {}).get("message_id")
        _log_transport_ack(
            "Telegram photo sent | recipient_ref={} msg_id={}",
            recipient_ref(chat_id),
            msg_id,
        )
        return msg_id
