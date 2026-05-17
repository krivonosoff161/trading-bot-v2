"""
Telegram notifications — send trade alerts via Bot API.
Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from .env.
TELEGRAM_CHAT_ID supports multiple IDs separated by commas.
Silently skips if not configured.
"""

import os
from pathlib import Path

import aiohttp
from loguru import logger

_BOT_TOKEN: str       = os.getenv("TELEGRAM_BOT_TOKEN", "").strip("'\"")
_CHAT_IDS:  list[str] = [
    cid.strip() for cid in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if cid.strip()
]


async def send_message(text: str) -> None:
    """Send a Telegram message to all configured chat IDs. No-op if not configured."""
    if not _BOT_TOKEN or not _CHAT_IDS:
        return
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            for chat_id in _CHAT_IDS:
                resp = await session.post(
                    url,
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                    timeout=aiohttp.ClientTimeout(total=5),
                )
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("Telegram error | chat_id={} status={} body={}", chat_id, resp.status, body)
    except Exception as e:
        logger.warning("Telegram send failed | {}", e)


async def send_message_to(chat_id: str, text: str) -> None:
    """Send a Telegram message to a specific chat_id (not broadcast).

    Handles HTTP 429 (rate limit) with retry honoring retry_after.
    Checks ``ok`` field in response body — Telegram can return HTTP 200 with
    ``ok: false`` for some errors; raise on these too.
    """
    if not _BOT_TOKEN:
        return
    import asyncio
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
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
            return


async def send_photo_to(chat_id: str, file_path: str, caption: str = "") -> None:
    """Send a photo file to a specific chat_id."""
    if not _BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendPhoto"
    try:
        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field("chat_id", chat_id)
            data.add_field("caption", caption)
            with open(file_path, "rb") as f:
                data.add_field("photo", f, filename=Path(file_path).name)
                resp = await session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=30))
            if resp.status != 200:
                body = await resp.text()
                logger.warning("Telegram send_photo_to error | chat_id={} status={} body={}", chat_id, resp.status, body)
    except Exception as e:
        logger.warning("Telegram send_photo_to failed | {}", e)
