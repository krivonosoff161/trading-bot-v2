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
    """Send a Telegram message to a specific chat_id (not broadcast)."""
    if not _BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=aiohttp.ClientTimeout(total=10),
            )
            if resp.status != 200:
                body = await resp.text()
                logger.warning("Telegram send_message_to error | chat_id={} status={} body={}", chat_id, resp.status, body)
    except Exception as e:
        logger.warning("Telegram send_message_to failed | {}", e)


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
