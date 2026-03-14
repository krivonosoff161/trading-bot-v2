"""
Telegram intake bot — trusted-user automatic flow.

Receives chart screenshots from whitelisted chat_ids, asks for symbol
via inline keyboard (or manual input), runs analyze_chart.run(), and
returns annotated.png + client summary back to the same chat.

This is NOT operator-assisted: after symbol selection, analysis runs
automatically with no human in the loop.

Whitelist: TELEGRAM_CHAT_ID from .env (same comma-separated list used
for delivery broadcast — no new env vars needed).

Usage:
    python scripts/telegram_bot.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv()

from scripts.analyze_chart import build_client_summary, _format_telegram, run as analyze_run  # noqa: E402
from src.utils.telegram import send_message_to, send_photo_to  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip("'\"")
WHITELIST: set[str] = {
    cid.strip() for cid in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if cid.strip()
}

TEMP_DIR    = Path(__file__).parent / "tg_temp"
OUTPUT_ROOT = Path(__file__).parent / "analysis_output"

SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "DOGE-USDT", "XRP-USDT"]
IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
TIMEOUT_SEC = 120
STALE_THRESHOLD = 300   # seconds — warn if image message is older than this
MIN_IMAGE_BYTES = 5_000  # below this = suspicious (not a real chart)
MIN_IMAGE_WIDTH = 360
MIN_IMAGE_HEIGHT = 240
MIN_ASPECT_RATIO = 0.45
MAX_ASPECT_RATIO = 3.50

TRANSPARENCY_NOTE = (
    "ℹ️ Анализ строится по рыночным данным OKX.\n"
    "Изображение — визуальная подложка результата, не источник торгового решения."
)

WELCOME_TEXT = (
    "Анализ рынка по данным OKX. "
    "Выберите пару — получите разбор текущей ситуации, уровни и график."
)

# In-memory state per chat_id: {status, image_path, started_at, msg_date}
# status: idle | awaiting_symbol | processing
_state: dict[str, dict] = {}

# Persistent HTTP session — one per bot lifetime, not per request
_SESSION: aiohttp.ClientSession | None = None


# ── Telegram API helpers ───────────────────────────────────────────────────────

async def _get_session() -> aiohttp.ClientSession:
    global _SESSION
    if _SESSION is None or _SESSION.closed:
        _SESSION = aiohttp.ClientSession()
    return _SESSION


async def _tg(method: str, http_timeout: int = 10, **params) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    s = await _get_session()
    async with s.post(url, json=params, timeout=aiohttp.ClientTimeout(total=http_timeout)) as resp:
        return await resp.json()


async def _send(chat_id: str, text: str) -> None:
    await _tg("sendMessage", chat_id=chat_id, text=text)


async def _download(file_id: str, dest: Path) -> None:
    info = await _tg("getFile", file_id=file_id)
    remote_path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{remote_path}"
    s = await _get_session()
    async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        dest.write_bytes(await resp.read())


async def _send_pair_keyboard(chat_id: str, extra_note: str = "", welcome: bool = False) -> None:
    parts = []
    if welcome:
        parts.append(WELCOME_TEXT)
    if extra_note:
        parts.append(extra_note)
    parts.append(TRANSPARENCY_NOTE)
    parts.append("\nВыбери пару:")
    buttons = [[{"text": sym, "callback_data": sym}] for sym in SYMBOLS]
    buttons.append([{"text": "Другая пара", "callback_data": "__manual__"}])
    await _tg(
        "sendMessage",
        chat_id=chat_id,
        text="\n".join(parts),
        reply_markup={"inline_keyboard": buttons},
    )


# ── Image validation ──────────────────────────────────────────────────────────

def _validate_image(path: Path) -> str | None:
    """Returns error message if image is suspicious, None if OK."""
    size = path.stat().st_size
    if size < MIN_IMAGE_BYTES:
        return (
            f"Изображение подозрительно маленькое ({size} байт). "
            "Убедись, что отправляешь полный скрин графика."
        )
    # Check magic bytes for common image formats
    header = path.read_bytes()[:8]
    is_jpg  = header[:2] == b'\xff\xd8'
    is_png  = header[:8] == b'\x89PNG\r\n\x1a\n'
    is_webp = header[:4] == b'RIFF'  # RIFF....WEBP
    if not (is_jpg or is_png or is_webp):
        return "Не удалось распознать формат изображения. Отправь PNG или JPEG."
    try:
        with Image.open(path) as img:
            width, height = img.size
    except Exception:
        return "Не удалось открыть изображение. Отправь обычный PNG, JPEG или WebP."

    if width < MIN_IMAGE_WIDTH or height < MIN_IMAGE_HEIGHT:
        return (
            f"Изображение слишком маленькое ({width}x{height}). "
            "Отправь полный скрин графика, а не миниатюру."
        )

    aspect_ratio = width / height if height else 0.0
    if aspect_ratio < MIN_ASPECT_RATIO or aspect_ratio > MAX_ASPECT_RATIO:
        return (
            f"У изображения нетипичные пропорции ({width}x{height}). "
            "Проверь, что это действительно скрин графика."
        )
    return None


# ── State helpers ──────────────────────────────────────────────────────────────

def _timed_out(chat_id: str) -> bool:
    st = _state.get(chat_id, {})
    return st.get("status") == "awaiting_symbol" and time.time() - st.get("started_at", 0) > TIMEOUT_SEC


def _reset(chat_id: str) -> None:
    st = _state.pop(chat_id, {})
    img = st.get("image_path")
    if img:
        Path(img).unlink(missing_ok=True)


# ── Analysis runner ────────────────────────────────────────────────────────────

async def _run_and_deliver(chat_id: str, image_path: str, symbol: str, captured_at: str) -> None:
    try:
        await _send(chat_id, f"Анализирую {symbol}... ⏳")
        before = time.time()
        await analyze_run(symbol=symbol, captured_at_iso=captured_at, limit=100, image_path=image_path)

        # Locate the run_dir created by analyze_run (newest dir after `before`)
        candidates = [d for d in OUTPUT_ROOT.iterdir() if d.is_dir() and d.stat().st_mtime >= before]
        if not candidates:
            await _send(chat_id, "Анализ завершён, но результаты не найдены. Попробуй снова.")
            return

        run_dir   = max(candidates, key=lambda d: d.stat().st_mtime)
        png_path  = run_dir / f"{symbol}_annotated.png"
        snap_path = run_dir / f"{symbol}_snapshot.json"

        # Read LLM-generated summary if available, else reconstruct from snapshot
        summary_text = None
        summary_file = run_dir / f"{symbol}_client_summary.txt"
        if summary_file.exists():
            import html as _html
            summary_text = _html.escape(summary_file.read_text(encoding="utf-8"))
        elif snap_path.exists():
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
            r = {
                "1h":          snap["1h"],
                "15m":         snap["15m"],
                "5m":          snap["5m"],
                "signal":      snap["bot_decision"],
                "action":      snap["action"],
                "pending_plan": snap.get("pending_plan", {"available": False}),
            }
            summary_text = _format_telegram(build_client_summary(symbol, captured_at, r))

        if summary_text:
            await send_message_to(chat_id, summary_text)

        if png_path.exists():
            await send_photo_to(chat_id, str(png_path))
        else:
            await _send(chat_id, "Изображение не создано — возможно, скрин не был передан в engine.")

    except Exception:
        # Print traceback first — before any further network calls that may also fail
        tb = traceback.format_exc()
        print(f"ERROR _run_and_deliver | chat_id={chat_id} symbol={symbol}\n{tb}")
        try:
            await _send(chat_id, "Произошла ошибка при анализе. Попробуй позже.")
        except Exception:
            pass  # already logged above — don't mask the original error
    finally:
        _reset(chat_id)


async def _start_analysis(chat_id: str, symbol: str) -> None:
    st = _state.get(chat_id, {})
    captured_at = datetime.fromtimestamp(st["msg_date"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _state[chat_id]["status"] = "processing"
    asyncio.create_task(_run_and_deliver(chat_id, st["image_path"], symbol, captured_at))


# ── Update handlers ────────────────────────────────────────────────────────────

async def _handle_image(msg: dict, file_id: str) -> None:
    chat_id = str(msg["chat"]["id"])
    if chat_id not in WHITELIST:
        return

    st = _state.get(chat_id, {})
    if st.get("status") == "processing":
        await _send(chat_id, "Уже обрабатываю предыдущий запрос. Подожди немного.")
        return

    # Drop timed-out awaiting state before accepting new image
    if _timed_out(chat_id):
        _reset(chat_id)

    TEMP_DIR.mkdir(exist_ok=True)
    dest = TEMP_DIR / f"{chat_id}_{int(time.time())}.jpg"
    await _download(file_id, dest)

    # Validate image before proceeding
    err = _validate_image(dest)
    if err:
        dest.unlink(missing_ok=True)
        await _send(chat_id, f"⚠️ {err}")
        return

    msg_date = msg.get("date", int(time.time()))
    captured_at = datetime.fromtimestamp(msg_date, tz=timezone.utc).strftime("%H:%M UTC")
    _state[chat_id] = {
        "status":     "awaiting_symbol",
        "image_path": str(dest),
        "started_at": time.time(),
        "msg_date":   msg_date,
    }

    # Warn if image is stale (message sent long before bot received it)
    extra = f"🕒 Время анализа будет взято из времени сообщения: {captured_at}."
    age_sec = int(time.time()) - msg_date
    if age_sec > STALE_THRESHOLD:
        minutes = age_sec // 60
        extra += f"\n⏰ Сообщение отправлено {minutes} мин назад — результат может отличаться от старого скрина."

    await _send_pair_keyboard(chat_id, extra_note=extra)


async def _handle_callback(cbq: dict) -> None:
    chat_id = str(cbq["message"]["chat"]["id"])
    await _tg("answerCallbackQuery", callback_query_id=cbq["id"])

    if chat_id not in WHITELIST:
        return

    if _timed_out(chat_id):
        _reset(chat_id)
        await _send(chat_id, "Время вышло. Отправь скрин заново.")
        return

    st = _state.get(chat_id, {})
    if st.get("status") != "awaiting_symbol":
        return

    data = cbq["data"]
    if data == "__manual__":
        await _send(chat_id, "Напиши тикер пары (например: AVAX-USDT):")
    else:
        await _start_analysis(chat_id, data)


async def _handle_text(msg: dict) -> None:
    chat_id = str(msg["chat"]["id"])
    text = msg.get("text", "").strip()
    if not text or chat_id not in WHITELIST:
        return

    st = _state.get(chat_id, {})
    status = st.get("status", "idle")

    if status == "processing":
        await _send(chat_id, "Уже обрабатываю запрос. Подожди немного.")
        return

    if status == "awaiting_symbol":
        if _timed_out(chat_id):
            _reset(chat_id)
            await _send(chat_id, "Время вышло. Напиши «Анализ» чтобы начать заново.")
            return
        # Validate format: letters/digits, dash, letters/digits (e.g. BTC-USDT)
        symbol = text.upper()
        if re.match(r"^[A-Z0-9]+-[A-Z0-9]+$", symbol):
            await _start_analysis(chat_id, symbol)
        else:
            await _send(chat_id, "Не понял. Напиши в формате BTC-USDT и попробуй снова.")
        return

    # idle — trigger on "анализ", hint otherwise
    if "анализ" in text.lower():
        _state[chat_id] = {
            "status":     "awaiting_symbol",
            "image_path": None,
            "started_at": time.time(),
            "msg_date":   int(time.time()),
        }
        await _send_pair_keyboard(chat_id, welcome=True)
    else:
        await _send(chat_id, "Напиши «Анализ» чтобы начать.")


async def handle_update(update: dict) -> None:
    if cbq := update.get("callback_query"):
        await _handle_callback(cbq)
        return

    msg = update.get("message")
    if not msg:
        return

    # Photo (compressed by Telegram)
    if "photo" in msg:
        await _handle_image(msg, msg["photo"][-1]["file_id"])
        return

    # Document — accept image MIME types only
    if doc := msg.get("document"):
        if doc.get("mime_type", "") in IMAGE_MIMES:
            await _handle_image(msg, doc["file_id"])
        elif str(msg["chat"]["id"]) in WHITELIST:
            await _send(str(msg["chat"]["id"]), "Отправь, пожалуйста, фото или изображение графика.")
        return

    if "text" in msg:
        await _handle_text(msg)


# ── Polling loop ───────────────────────────────────────────────────────────────

async def main() -> None:
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        return
    if not WHITELIST:
        print("ERROR: TELEGRAM_CHAT_ID not set in .env")
        return

    TEMP_DIR.mkdir(exist_ok=True)
    OUTPUT_ROOT.mkdir(exist_ok=True)

    print(f"Telegram bot started. Whitelist: {WHITELIST}")

    offset = 0
    try:
        while True:
            try:
                result = await _tg(
                    "getUpdates",
                    http_timeout=45,
                    offset=offset,
                    timeout=30,
                    allowed_updates=["message", "callback_query"],
                )
                for update in result.get("result", []):
                    offset = update["update_id"] + 1
                    await handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception:
                print(f"Poll error:\n{traceback.format_exc()}")
                await asyncio.sleep(3)
    finally:
        global _SESSION
        if _SESSION and not _SESSION.closed:
            await _SESSION.close()


if __name__ == "__main__":
    asyncio.run(main())
