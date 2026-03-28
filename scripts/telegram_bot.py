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
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv()

from scripts.analyze_chart import _format_telegram, run as analyze_run  # noqa: E402
from scripts.feedback import (  # noqa: E402
    save_entry, update_entry, pending_reminders, pending_for_chat, load_entries,
)
from scripts.subscriptions import is_subscribed, add_user, remove_user, list_users, get_status  # noqa: E402
from src.utils.telegram import send_message_to, send_photo_to  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip("'\"")

TEMP_DIR   = Path(__file__).parent / "tg_temp"
USERS_ROOT = ROOT / "logs" / "users"

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
    "Выберите пару — получите разбор текущей ситуации, уровни и график.\n\n"
    "⚠️ Это аналитика, не инвестиционная рекомендация. "
    "Торговля фьючерсами сопряжена с риском полной потери капитала. "
    "Решения принимаете вы сами."
)

CHAT_LINK  = "https://t.me/+B9T_L7VHdpkwZjZi"
ADMIN_LINK = "https://t.me/Krivonosoff"

START_TEXT = """\
Привет 👋

Я анализирую графики криптовалют и говорю человеческим языком:
что происходит, куда смотреть, где вход и где выход.

Работаю с BTC, ETH, SOL, DOGE, XRP и другими парами.
Данные беру с OKX в реальном времени.

Нажми кнопку ниже — и посмотри сам.

⚠️ Аналитика, не сигналы с гарантией.\
"""

# In-memory state per chat_id: {status, image_path, started_at, msg_date}
# status: idle | awaiting_symbol | processing
_state: dict[str, dict] = {}

# Persistent HTTP session — one per bot lifetime, not per request
_SESSION: aiohttp.ClientSession | None = None

# Max concurrent analyses — prevents OKX rate limit hits and RAM spikes from matplotlib
_ANALYSIS_SEM = asyncio.Semaphore(3)


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


# ── Feedback helpers ──────────────────────────────────────────────────────────

async def _send_feedback_entry_buttons(chat_id: str, entry_id: str, symbol: str, style: str) -> None:
    """Ask user if they entered the trade. Sent once after ENTRY signal."""
    label = f"{symbol} {style}" if style else symbol
    await _tg(
        "sendMessage",
        chat_id=chat_id,
        text=f"📊 Сигнал по {label} — вошёл в сделку?",
        reply_markup={"inline_keyboard": [
            [{"text": "☑️ SL выставил, плечо ≤5x", "callback_data": f"fb_ack:{entry_id}"}],
            [
                {"text": "✅ Вошёл",      "callback_data": f"fb_in:{entry_id}"},
                {"text": "⏭ Пропустил",  "callback_data": f"fb_skip:{entry_id}"},
            ],
        ]},
    )


async def _send_feedback_result_buttons(chat_id: str, entry_id: str, symbol: str) -> None:
    """Ask for trade result — shown when user re-requests analysis of the same pair."""
    await _tg(
        "sendMessage",
        chat_id=chat_id,
        text=f"📊 {symbol} — позиция ещё открыта?",
        reply_markup={"inline_keyboard": [[
            {"text": "✅ TP",             "callback_data": f"fb_tp:{entry_id}"},
            {"text": "❌ SL",             "callback_data": f"fb_sl:{entry_id}"},
            {"text": "🔓 Ещё держу",     "callback_data": f"fb_hold:{entry_id}"},
            {"text": "🔧 Вручную",       "callback_data": f"fb_man:{entry_id}"},
        ]]},
    )


async def _check_and_send_reminders() -> None:
    """Send 24h result reminders for all pending entries. Called on bot startup."""
    for e in pending_reminders():
        await _send_feedback_result_buttons(e["chat_id"], e["id"], e["symbol"])
        update_entry(e["id"], chat_id=e["chat_id"], reminded=True)
        await asyncio.sleep(0.3)  # avoid Telegram flood


# ── Analysis runner ────────────────────────────────────────────────────────────

async def _run_and_deliver(chat_id: str, image_path: str, symbol: str, captured_at: str) -> None:
    try:
        # Queue guard — notify user if waiting for a slot
        if _ANALYSIS_SEM._value == 0:
            await _send(chat_id, f"⏳ Очередь на анализ {symbol}... подожди немного.")

        async with _ANALYSIS_SEM:
            await _run_analysis(chat_id, image_path, symbol, captured_at)
    except Exception:
        tb = traceback.format_exc()
        print(f"ERROR _run_and_deliver | chat_id={chat_id} symbol={symbol}\n{tb}")
        try:
            await _send(chat_id, "Произошла ошибка при анализе. Попробуй позже.")
        except Exception:
            pass
    finally:
        _reset(chat_id)


async def _run_analysis(chat_id: str, image_path: str, symbol: str, captured_at: str) -> None:
    try:
        # Remind about open trade for THIS pair — context reminder, max once per 4h
        open_trades = pending_for_chat(chat_id, symbol=symbol)
        if open_trades:
            for e in open_trades:
                await _send_feedback_result_buttons(chat_id, e["id"], e["symbol"])
                await asyncio.sleep(0.3)

        await _send(chat_id, f"Анализирую {symbol}... ⏳")
        user_analyses_dir = USERS_ROOT / str(chat_id) / "analyses"
        user_analyses_dir.mkdir(parents=True, exist_ok=True)

        ts_label = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        run_dir  = user_analyses_dir / f"{ts_label}_{symbol}"
        run_dir.mkdir(parents=True, exist_ok=True)

        await analyze_run(
            symbol=symbol, captured_at_iso=captured_at, limit=100,
            image_path=image_path, output_dir=run_dir,
        )

        png_path  = run_dir / f"{symbol}_chart.png"
        snap_path = run_dir / f"{symbol}_snapshot.json"
        if not snap_path.exists():
            await _send(chat_id, "Анализ завершён, но результаты не найдены. Попробуй снова.")
            return

        # Read LLM-generated summary if available, else reconstruct from snapshot
        summary_text = None
        summary_file = run_dir / f"{symbol}_client_summary.txt"
        if summary_file.exists():
            import html as _html
            summary_text = _html.escape(summary_file.read_text(encoding="utf-8"))
        elif snap_path.exists():
            # Fallback: re-read snapshot expiry as minimal message
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
            ctx  = snap.get("llm_context", {})
            _sig = ctx.get("entry_signal", "NO_TRADE")
            _sym = snap.get("symbol", symbol)
            summary_text = _format_telegram(f"{_sym} — {_sig}\nПодробный отчёт не найден, повторите анализ.")

        if summary_text:
            await send_message_to(chat_id, summary_text)

        if png_path.exists():
            await send_photo_to(chat_id, str(png_path))
        else:
            await _send(chat_id, "Изображение не создано — возможно, скрин не был передан в engine.")

        # Disclaimer + feedback buttons — for ENTRY and WAIT signals
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        ctx = snap.get("llm_context", {})
        entry_signal = ctx.get("entry_signal", "")
        if entry_signal in ("ENTRY", "WAIT"):
            style = ctx.get("trade_style_hint", "")
            max_hours = 2 if style == "SCALP" else 8 if style == "PULLBACK" else 16
            leverage = 3 if style == "SCALP" else 5
            disclaimer = (
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ ПРАВИЛА ВХОДА\n"
                f"├─ Плечо: макс {leverage}x\n"
                "├─ Стоп: обязателен, не двигать дальше\n"
                f"├─ Время: закрыть через {max_hours}ч если уровни не достигнуты\n"
                "├─ Размер: 2-3% депозита на сделку\n"
                "└─ Это аналитика, не инвест-рекомендация\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            )
            await _send(chat_id, disclaimer)
            entry_id = save_entry(chat_id, symbol, snap, str(snap_path))
            await _send_feedback_entry_buttons(chat_id, entry_id, symbol, style)

    except Exception:
        tb = traceback.format_exc()
        print(f"ERROR _run_analysis | chat_id={chat_id} symbol={symbol}\n{tb}")
        raise


async def _start_analysis(chat_id: str, symbol: str) -> None:
    st = _state.get(chat_id, {})
    captured_at = datetime.fromtimestamp(st["msg_date"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _state[chat_id]["status"] = "processing"
    asyncio.create_task(_run_and_deliver(chat_id, st["image_path"], symbol, captured_at))


# ── Update handlers ────────────────────────────────────────────────────────────

async def _handle_image(msg: dict, file_id: str) -> None:
    chat_id = str(msg["chat"]["id"])
    if not is_subscribed(chat_id):
        await _tg(
            "sendMessage",
            chat_id=chat_id,
            text="🔒 Доступ по подписке.",
            reply_markup={"inline_keyboard": [
                [{"text": "✉️ Подключиться → @Krivonosoff", "url": ADMIN_LINK}],
                [{"text": "💬 Чат сообщества", "url": CHAT_LINK}],
            ]},
        )
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
    data    = cbq.get("data", "")

    if not is_subscribed(chat_id):
        await _tg("answerCallbackQuery", callback_query_id=cbq["id"])
        return

    # ── Start analysis button from /start banner ──────────────────────────
    if data == "__start_analysis__":
        await _tg("answerCallbackQuery", callback_query_id=cbq["id"])
        _state[chat_id] = {"status": "awaiting_symbol", "image_path": None,
                           "started_at": time.time(), "msg_date": int(time.time())}
        await _send_pair_keyboard(chat_id)
        return

    # ── Feedback callbacks — handled regardless of current state ──────────
    if data.startswith("fb_"):
        action, _, entry_id = data.partition(":")
        entry = next((e for e in load_entries(chat_id) if e["id"] == entry_id), None)
        if action == "fb_ack":
            await _tg("answerCallbackQuery", callback_query_id=cbq["id"], text="✅ Принято — торгуй по плану!")
            return
        if action == "fb_in":
            if entry and entry.get("entered") is not None:
                await _tg("answerCallbackQuery", callback_query_id=cbq["id"], text="Уже записано ✅")
                return
            update_entry(entry_id, chat_id=chat_id, entered=True)
            await _tg("answerCallbackQuery", callback_query_id=cbq["id"], text="Записал ✅ Торгуй по плану!")
        elif action == "fb_skip":
            if entry and entry.get("entered") is not None:
                await _tg("answerCallbackQuery", callback_query_id=cbq["id"], text="Уже записано ✅")
                return
            update_entry(entry_id, chat_id=chat_id, entered=False, result="skipped")
            await _tg("answerCallbackQuery", callback_query_id=cbq["id"], text="Понял, записал ⏭")
        elif action == "fb_hold":
            await _tg("answerCallbackQuery", callback_query_id=cbq["id"], text="🔓 Держишь — удачи!")
        elif action in ("fb_tp", "fb_sl", "fb_man"):
            if entry and entry.get("result") is not None:
                await _tg("answerCallbackQuery", callback_query_id=cbq["id"], text="Уже записано ✅")
                return
            result_map = {"fb_tp": "tp", "fb_sl": "sl", "fb_man": "manual"}
            label_map  = {"fb_tp": "TP ✅", "fb_sl": "SL ❌", "fb_man": "Закрыл вручную 🔧"}
            update_entry(entry_id, chat_id=chat_id, result=result_map[action])
            await _tg("answerCallbackQuery", callback_query_id=cbq["id"], text=f"Записал: {label_map[action]}")
        else:
            await _tg("answerCallbackQuery", callback_query_id=cbq["id"])
        return

    # ── Symbol selection callbacks — require awaiting_symbol state ─────────
    await _tg("answerCallbackQuery", callback_query_id=cbq["id"])

    if _timed_out(chat_id):
        _reset(chat_id)
        await _send(chat_id, "Время вышло. Отправь скрин заново.")
        return

    st = _state.get(chat_id, {})
    if st.get("status") != "awaiting_symbol":
        return

    if data == "__manual__":
        await _send(chat_id, "Напиши тикер пары (например: AVAX-USDT):\n\n⚠️ Сервис заточен под крипто-фьючерсы. Анализ золота, нефти и валют — в разработке, результат может быть некорректным.\n\n📊 Пары вне основного списка (BTC/ETH/SOL/DOGE/XRP) анализируются по общим параметрам — результат ориентировочный, точной калибровки под эту пару нет.")
    else:
        await _start_analysis(chat_id, data)


async def _handle_text(msg: dict) -> None:
    chat_id = str(msg["chat"]["id"])
    text = msg.get("text", "").strip()
    if not text:
        return

    # ── /start — show banner to everyone, no access check ────────────────────
    if text == "/start":
        if is_subscribed(chat_id):
            # Subscribed: show banner + start analysis + chat link
            await _tg(
                "sendMessage",
                chat_id=chat_id,
                text=START_TEXT,
                reply_markup={"inline_keyboard": [
                    [{"text": "📊 Начать анализ", "callback_data": "__start_analysis__"}],
                    [{"text": "💬 Чат сообщества", "url": CHAT_LINK}],
                ]},
            )
        else:
            # Not subscribed: show banner + contact admin + chat link
            await _tg(
                "sendMessage",
                chat_id=chat_id,
                text=(
                    START_TEXT
                    + "\n\n🔒 Доступ по подписке.\n\n"
                    + f"Твой ID для подключения: <code>{chat_id}</code>\n"
                    + "Скопируй и отправь администратору."
                ),
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [
                    [{"text": "✉️ Подключиться → @Krivonosoff", "url": ADMIN_LINK}],
                    [{"text": "💬 Чат сообщества", "url": CHAT_LINK}],
                ]},
            )
        return

    # ── Superadmin commands — checked before access gate ─────────────────────
    entry = get_status(chat_id)
    is_admin = entry and entry.get("plan") == "superadmin"

    if text.startswith("/add ") and is_admin:
        parts = text.split()
        if len(parts) == 3 and parts[2].isdigit():
            target_id, days = parts[1], int(parts[2])
            expiry = add_user(target_id, days)
            await _send(chat_id, f"✅ Подписка выдана: {target_id}\nДо: {expiry} (+{days} дней)")
        else:
            await _send(chat_id, "Формат: /add <chat_id> <дней>")
        return

    if text == "/users" and is_admin:
        users = list_users()
        if not users:
            await _send(chat_id, "👥 Нет пользователей.")
            return
        rows = []
        for u in users:
            expires = "∞" if u["expires"] is None else u["expires"]
            rows.append((u["chat_id"], u["status"], expires))
        col1 = max(len(r[0]) for r in rows)
        col2 = max(len(r[1]) for r in rows)
        header = f"{'ID':<{col1}}  {'Статус':<{col2}}  До"
        sep = "-" * (col1 + col2 + 14)
        table_lines = [header, sep]
        for cid, status, expires in rows:
            table_lines.append(f"{cid:<{col1}}  {status:<{col2}}  {expires}")
        body = "\n".join(table_lines)
        await _tg("sendMessage", chat_id=chat_id,
                  text=f"👥 Пользователей: {len(users)}\n\n<pre>{body}</pre>",
                  parse_mode="HTML")
        return

    if text.startswith("/del ") and is_admin:
        target_id = text.split()[1] if len(text.split()) == 2 else None
        if target_id:
            ok = remove_user(target_id)
            await _send(chat_id, f"✅ Удалён: {target_id}" if ok else f"❌ Не найден: {target_id}")
        else:
            await _send(chat_id, "Формат: /del <chat_id>")
        return

    if text == "/admin" and is_admin:
        await _send(chat_id, (
            "🛠 Панель администратора\n\n"
            "👤 Выдать или продлить доступ:\n"
            "  /add <chat_id> <дней>\n"
            "  пример: /add 123456789 10\n\n"
            "🗑 Удалить пользователя:\n"
            "  /del <chat_id>\n"
            "  пример: /del 123456789\n\n"
            "📋 Список всех пользователей:\n"
            "  /users\n\n"
            "ℹ️ Superadmin — постоянный доступ (без даты истечения).\n"
            "ℹ️ Обычный пользователь — блокируется автоматически по истечении срока."
        ))
        return

    # ── Access check — all non-admin traffic blocked here ────────────────────
    if not is_subscribed(chat_id):
        await _tg(
            "sendMessage",
            chat_id=chat_id,
            text="🔒 Доступ по подписке.",
            reply_markup={"inline_keyboard": [
                [{"text": "✉️ Подключиться → @Krivonosoff", "url": ADMIN_LINK}],
                [{"text": "💬 Чат сообщества", "url": CHAT_LINK}],
            ]},
        )
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

    if msg.get("chat", {}).get("type") != "private":
        return  # ignore group/channel messages

    # Photo (compressed by Telegram)
    if "photo" in msg:
        await _handle_image(msg, msg["photo"][-1]["file_id"])
        return

    # Document — accept image MIME types only
    if doc := msg.get("document"):
        if doc.get("mime_type", "") in IMAGE_MIMES:
            await _handle_image(msg, doc["file_id"])
        elif is_subscribed(str(msg["chat"]["id"])):
            await _send(str(msg["chat"]["id"]), "Отправь, пожалуйста, фото или изображение графика.")
        return

    if "text" in msg:
        await _handle_text(msg)


# ── Polling loop ───────────────────────────────────────────────────────────────

_SCANNER_PAIRS    = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ADA-USDT"]
_SCANNER_LOG      = ROOT / "logs" / "scanner.log"
_SCANNER_INTERVAL = 15  # minutes


def _scan_log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line = f"[{ts}] {msg}\n"
    print(line, end="")
    _SCANNER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_SCANNER_LOG, "a", encoding="utf-8") as f:
        f.write(line)


def _next_quarter(now: datetime) -> datetime:
    """Next clock-aligned 15-min boundary: :00, :15, :30, :45."""
    total_min = now.hour * 60 + now.minute
    next_min  = ((total_min // _SCANNER_INTERVAL) + 1) * _SCANNER_INTERVAL
    h, m = divmod(next_min % (24 * 60), 60)
    return now.replace(hour=h, minute=m, second=2, microsecond=0)


async def _scanner_loop() -> None:
    """Background task: scan all pairs at :00/:15/:30/:45, broadcast ENTRY signals."""
    import shutil

    await asyncio.sleep(30)  # let bot settle on startup

    now  = datetime.now(timezone.utc)
    first_run = _next_quarter(now)
    wait = (first_run - now).total_seconds()
    msk_h = (first_run.hour + 3) % 24
    _scan_log(f"Сканер запущен. Пары: {', '.join(_SCANNER_PAIRS)}. Первый запуск в {msk_h:02d}:{first_run.minute:02d} МСК")
    await asyncio.sleep(max(wait, 0))

    last_signal = {p: None for p in _SCANNER_PAIRS}

    while True:
        now  = datetime.now(timezone.utc)
        hour = now.hour

        if 1 <= hour < 7:  # night block UTC (04:00-10:00 МСК)
            wake = now.replace(hour=7, minute=0, second=2, microsecond=0)
            if wake <= now:
                wake += timedelta(days=1)
            _scan_log("Ночной блок — пауза до 10:00 МСК")
            await asyncio.sleep((wake - now).total_seconds())
            continue

        msk_str = f"{(now.hour+3)%24:02d}:{now.minute:02d} МСК"
        _scan_log(f"── Цикл сканирования {msk_str} ──────────────────")

        for pair in _SCANNER_PAIRS:
            try:
                captured_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                ts_label    = now.strftime("%Y-%m-%d_%H-%M-%S")
                scan_dir    = ROOT / "logs" / "scanner" / f"{ts_label}_{pair}"
                result = await analyze_run(
                    pair, captured_at, limit=100,
                    send_telegram=False, output_dir=scan_dir,
                )
            except Exception:
                err = traceback.format_exc().strip().splitlines()[-1]
                _scan_log(f"  ОШИБКА {pair}: {err}")
                continue

            if result is None:
                _scan_log(f"  {pair} — нет данных, пропуск")
                continue

            signal = result.get("entry_signal", "NO_TRADE")

            if signal == "ENTRY" and last_signal[pair] != "ENTRY":
                side   = result.get("side", "")
                text   = result.get("delivery_text", "")
                until  = result.get("expiry_time", "")
                arrow  = "🟢" if side == "buy" else "🔴"
                header = f"{arrow} <b>{pair}</b> — сигнал входа\n<i>Актуально до {until}</i>\n\n"
                msg    = header + f"<pre>{text}</pre>"
                active = [u["chat_id"] for u in list_users()
                          if u["status"] in ("active", "superadmin")]
                png_path = scan_dir / f"{pair}_chart.png"
                for chat_id in active:
                    await _send(chat_id, msg)
                    if png_path.exists():
                        await send_photo_to(chat_id, str(png_path))
                    await asyncio.sleep(0.3)
                _scan_log(f"  {pair} — СИГНАЛ ВХОДА ({side.upper()}) → отправлено {len(active)} клиентам")
            else:
                shutil.rmtree(scan_dir, ignore_errors=True)
                _scan_log(f"  {pair} — {signal}")

            last_signal[pair] = signal

        # Sleep until next :00/:15/:30/:45
        now      = datetime.now(timezone.utc)
        next_run = _next_quarter(now)
        await asyncio.sleep((next_run - now).total_seconds())

        await asyncio.sleep(60)  # check every minute which pairs are due


async def main() -> None:
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        return

    TEMP_DIR.mkdir(exist_ok=True)
    USERS_ROOT.mkdir(parents=True, exist_ok=True)

    users = list_users()
    print(f"Telegram bot started. Subscribed users: {len(users)}")

    # On startup: send 24h reminders for any open trades
    await _check_and_send_reminders()

    asyncio.create_task(_scanner_loop())

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
