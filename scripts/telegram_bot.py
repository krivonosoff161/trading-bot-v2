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
from scripts.analysis.feedback import (  # noqa: E402
    save_entry, update_entry, pending_reminders, pending_for_chat, load_entries,
)
from scripts.premium_prompts import PREMIUM_CATEGORY_NAMES  # noqa: E402
from scripts.subscriptions import is_subscribed, add_user, remove_user, list_users, get_status  # noqa: E402
from src.utils.llm_formatter import generate_premium_analysis  # noqa: E402
from src.utils.telegram import send_message_to, send_photo_to  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip("'\"")

TEMP_DIR    = Path(__file__).parent / "tg_temp"
USERS_ROOT  = ROOT / "logs" / "users"
SIGNAL_LOG      = ROOT / "logs" / "signals" / "signal_log.jsonl"
NOTRADE_LOG     = ROOT / "logs" / "signals" / "signal_log_notrade.jsonl"

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
_edu_cooldown: dict[str, float] = {}   # chat_id → last edu answer timestamp
_EDU_COOLDOWN_SEC = 180                # 3 min between questions

# Premium: chat_id → category key while user is expected to send a screenshot
_premium_state: dict[str, str] = {}

# Persistent bottom keyboard — set once on /start, stays in chat forever
_MAIN_REPLY_KB = {
    "keyboard":        [[{"text": "🔍 Анализ"}]],
    "resize_keyboard": True,
    "persistent":      True,
}

# Persistent HTTP session — one per bot lifetime, not per request
_SESSION: aiohttp.ClientSession | None = None

# Max concurrent analyses — prevents OKX rate limit hits and RAM spikes from matplotlib
_ANALYSIS_SEM = asyncio.Semaphore(3)


def _append_signal_log_entry(
    result: dict,
    *,
    captured_at: str,
    symbol: str,
    source: str,
) -> None:
    """Append one ENTRY signal to signal_log.jsonl for later outcome labeling."""
    if (result or {}).get("entry_signal") != "ENTRY":
        return

    side = result.get("side")
    style = result.get("trade_style") or result.get("trade_style_hint") or ""
    if not side or not style:
        return

    micro = result.get("microstructure", {}) or {}
    ts_ms = int(datetime.fromisoformat(captured_at.replace("Z", "+00:00")).timestamp() * 1000)
    entry = {
        "signal_id":       f"{ts_ms}_{symbol}_{side}_{style}",
        "source":          source,
        "ts_ms":           ts_ms,
        "symbol":          symbol,
        "side":            side,
        "regime":          result.get("regime", ""),
        "style":           style,
        "close":           result.get("entry_price"),
        "sl":              result.get("sl_price"),
        "tp":              result.get("tp1_price"),
        "max_hold_min":    result.get("max_hold_min") or result.get("max_hold_minutes"),
        "adx_1h":          result.get("adx_1h"),
        "adx_4h":          result.get("adx_4h"),
        "day_position":    result.get("day_position"),
        "vol_ratio":       result.get("vol_ratio") or result.get("volume_ratio_15m"),
        "funding":         result.get("funding") if result.get("funding") is not None else result.get("funding_rate"),
        "strong_4h_veto":  result.get("strong_4h_veto"),
        "slope_1h":        result.get("slope_1h"),
        "slope_15m":       result.get("slope_15m"),
        "obi5":            micro.get("obi_top5"),
        "trade_delta_100": micro.get("trade_delta_100"),
        "spread_bps":      micro.get("spread_bps"),
        "is_demo":         os.getenv("OKX_IS_DEMO", "1") == "1",
    }
    with open(SIGNAL_LOG, "a", encoding="utf-8") as lf:
        lf.write(json.dumps(entry) + "\n")


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
    buttons.append([{"text": "💡 Совет", "callback_data": "__edu__"}])
    await _tg(
        "sendMessage",
        chat_id=chat_id,
        text="\n".join(parts),
        reply_markup={"inline_keyboard": buttons},
    )


async def _send_main_menu(chat_id: str) -> None:
    """Top-level menu: OKX crypto scanner or Premium screenshot analysis."""
    await _tg(
        "sendMessage",
        chat_id=chat_id,
        text="Выберите тип анализа:",
        reply_markup={"inline_keyboard": [
            [{"text": "📊 OKX Крипто-анализ",        "callback_data": "__start_analysis__"}],
            [{"text": "⭐ Premium — анализ скрина",  "callback_data": "__premium__"}],
        ]},
    )


async def _send_premium_categories(chat_id: str) -> None:
    """Show asset class selection for Premium screenshot analysis."""
    await _tg(
        "sendMessage",
        chat_id=chat_id,
        text=(
            "⭐ Premium анализ\n\n"
            "ИИ анализирует ваш скриншот графика.\n"
            "Выберите тип актива:"
        ),
        reply_markup={"inline_keyboard": [
            [
                {"text": "₿ Крипта",   "callback_data": "premium_cat_CRYPTO"},
                {"text": "🌐 Форекс",  "callback_data": "premium_cat_FOREX"},
            ],
            [
                {"text": "📈 Акции",   "callback_data": "premium_cat_STOCKS"},
                {"text": "🛢 Ресурсы", "callback_data": "premium_cat_COMMODITIES"},
            ],
            [{"text": "📊 Фонды",      "callback_data": "premium_cat_FUNDS"}],
        ]},
    )


async def _run_premium_analysis(chat_id: str, image_path: str, category: str) -> None:
    """Send screenshot to Gemma 3 27B IT and deliver analysis to user."""
    cat_name = PREMIUM_CATEGORY_NAMES.get(category, category)
    try:
        await _send(chat_id, f"⭐ Анализирую {cat_name}... ⏳")
        result = await generate_premium_analysis(category, Path(image_path).read_bytes())
        if result:
            await _send(chat_id, result)
        else:
            await _send(chat_id, "Не удалось получить анализ. Попробуй позже.")
        try:
            user_dir = USERS_ROOT / str(chat_id)
            user_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "ts":       datetime.now(timezone.utc).isoformat(),
                "category": category,
                "response": result or "",
            }
            with open(user_dir / "premium_log.jsonl", "a", encoding="utf-8") as lf:
                lf.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as _le:
            print(f"[premium_log] error: {_le}")
    except Exception:
        tb = traceback.format_exc()
        print(f"ERROR _run_premium_analysis | chat_id={chat_id}\n{tb}")
        try:
            await _send(chat_id, "Произошла ошибка. Попробуй позже.")
        except Exception:
            pass
    finally:
        Path(image_path).unlink(missing_ok=True)
        _premium_state.pop(chat_id, None)
        _state.pop(chat_id, None)


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
            [{"text": "☑️ SL выставил, плечо 10x", "callback_data": f"fb_ack:{entry_id}"}],
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


async def _run_edu(chat_id: str, question: str) -> None:
    from src.utils.llm_formatter import generate_edu_text
    await _send(chat_id, "💭 Думаю...")
    answer = await generate_edu_text(question)
    if answer:
        await _send(chat_id, answer)
    else:
        await _send(chat_id, "Не смог ответить — попробуй переформулировать вопрос.")


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
        if entry_signal == "ENTRY":
            try:
                _manual_result = dict(ctx)
                _manual_result["microstructure"] = snap.get("microstructure", {}) or {}
                _append_signal_log_entry(
                    _manual_result,
                    captured_at=captured_at,
                    symbol=snap.get("symbol", symbol),
                    source="manual",
                )
            except Exception as _e:
                print(f"[signal_log/manual] error | symbol={symbol} err={_e}")
        else:
            # Log NO_TRADE manual requests for live funnel analysis
            try:
                _nt = {
                    "ts_ms":        int(datetime.fromisoformat(
                                        captured_at.replace("Z", "+00:00")).timestamp() * 1000),
                    "ts_dt":        captured_at[:16],
                    "symbol":       snap.get("symbol", symbol),
                    "entry_signal": entry_signal,
                    "drop_reason":  ctx.get("drop_reason") or "",
                    "regime":       ctx.get("regime") or "",
                    "adx_1h":       ctx.get("adx_1h") or "",
                    "adx_4h":       ctx.get("adx_4h") or "",
                    "vol_ratio":    ctx.get("vol_ratio") or "",
                    "slope_15m":    ctx.get("slope_15m") or "",
                    "slope_1h":     ctx.get("slope_1h") or "",
                    "source":       "manual",
                }
                with open(NOTRADE_LOG, "a", encoding="utf-8") as _lf:
                    _lf.write(json.dumps(_nt) + "\n")
            except Exception:
                pass

        # Log BB FADE hint from manual analysis (even when main signal is NO_TRADE)
        _fade_hint = ctx.get("5m_fade_hint")
        _fade_data = ctx.get("5m_fade_data") or {}
        if _fade_hint and _fade_data:
            try:
                _fade_side = "sell" if _fade_hint == "SHORT" else "buy"
                _fade_ms   = int(datetime.fromisoformat(
                    captured_at.replace("Z", "+00:00")).timestamp() * 1000)
                _fade_rec  = {
                    "signal_id":    f"{_fade_ms}_{symbol}_FADE_{_fade_side}_manual",
                    "source":       "bb_fade",
                    "ts_ms":        _fade_ms,
                    "symbol":       snap.get("symbol", symbol),
                    "side":         _fade_side,
                    "regime":       ctx.get("regime", "RANGING"),
                    "style":        "FADE",
                    "close":        _fade_data.get("entry"),
                    "sl":           _fade_data.get("sl"),
                    "tp":           _fade_data.get("tp"),
                    "max_hold_min": 60,
                    "adx_1h":       ctx.get("adx_1h"),
                    "funding":      ctx.get("funding_rate"),
                    "slope_1h":     ctx.get("slope_1h"),
                    "slope_15m":    ctx.get("slope_15m"),
                    "is_demo":      os.getenv("OKX_IS_DEMO", "1") == "1",
                }
                with open(SIGNAL_LOG, "a", encoding="utf-8") as _lf:
                    _lf.write(json.dumps(_fade_rec) + "\n")
                print(f"[signal_log/fade_manual] {symbol} {_fade_hint} logged")
            except Exception as _fe:
                print(f"[signal_log/fade_manual] error | symbol={symbol} err={_fe}")

        if entry_signal in ("ENTRY", "WAIT"):
            # Use real engine value, not ad-hoc lookup (was bug: default 16h).
            max_hold_min = int(ctx.get("max_hold_minutes") or 150)
            _h, _m = divmod(max_hold_min, 60)
            if _h and _m:
                _time_str = f"{_h}ч {_m}мин"
            elif _h:
                _time_str = f"{_h}ч"
            else:
                _time_str = f"{_m}мин"
            disclaimer = (
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ ПРАВИЛА ВХОДА\n"
                "├─ Плечо: 10x (выставляется автоматически)\n"
                "├─ Стоп: обязателен, не двигать дальше\n"
                f"├─ Время: закрыть через {_time_str} если уровни не достигнуты\n"
                "├─ Размер: нотионал = 1.5× баланс\n"
                "├─ Цель = основная фиксация (закрываем тут)\n"
                "├─ Стретч = опционально, только если импульс сохранится\n"
                "└─ Это аналитика, не инвест-рекомендация\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            )
            await _send(chat_id, disclaimer)
            entry_id = save_entry(chat_id, symbol, snap, str(snap_path))
            style = ctx.get("trade_style") or ctx.get("trade_style_hint") or ""
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

    # Premium screenshot routing — intercept before regular symbol selection
    if chat_id in _premium_state:
        category = _premium_state.pop(chat_id)
        _state[chat_id] = {"status": "processing"}
        asyncio.create_task(_run_premium_analysis(chat_id, str(dest), category))
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

    # ── Premium: show category menu ────────────────────────────────────────
    if data == "__premium__":
        await _tg("answerCallbackQuery", callback_query_id=cbq["id"])
        await _send_premium_categories(chat_id)
        return

    # ── Premium: category selected → await screenshot ──────────────────────
    if data.startswith("premium_cat_"):
        category = data[len("premium_cat_"):]
        await _tg("answerCallbackQuery", callback_query_id=cbq["id"])
        if category not in PREMIUM_CATEGORY_NAMES:
            return
        _premium_state[chat_id] = category
        cat_name = PREMIUM_CATEGORY_NAMES[category]
        await _send(chat_id, f"📸 {cat_name} выбрана.\n\nОтправьте скриншот графика.")
        return

    # ── Edu button ─────────────────────────────────────────────────────────
    if data == "__edu__":
        await _tg("answerCallbackQuery", callback_query_id=cbq["id"])
        last = _edu_cooldown.get(chat_id, 0)
        remaining = int(_EDU_COOLDOWN_SEC - (time.time() - last))
        if remaining > 0:
            await _send(chat_id, f"⏳ Следующий вопрос через {remaining} сек.")
            return
        _reset(chat_id)
        _state[chat_id] = {"status": "edu_awaiting", "started_at": time.time()}
        await _send(chat_id, (
            "💡 Напиши вопрос — объясню простыми словами.\n\n"
            "Примеры:\n"
            "• что такое funding rate?\n"
            "• объясни плечо 10x\n"
            "• зачем нужен стоп-лосс?\n"
            "• что значит лонг и шорт?"
        ))
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
            # Subscribed: show banner + set persistent bottom keyboard
            await _tg(
                "sendMessage",
                chat_id=chat_id,
                text=START_TEXT,
                reply_markup=_MAIN_REPLY_KB,
            )
            await _tg(
                "sendMessage",
                chat_id=chat_id,
                text="Нажми «🔍 Анализ» внизу чтобы начать.",
                reply_markup={"inline_keyboard": [
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

    if status == "edu_awaiting":
        last = _edu_cooldown.get(chat_id, 0)
        if time.time() - last < _EDU_COOLDOWN_SEC:
            remaining = int(_EDU_COOLDOWN_SEC - (time.time() - last))
            await _send(chat_id, f"⏳ Подожди ещё {remaining} сек. перед следующим вопросом.")
            return
        _reset(chat_id)
        _edu_cooldown[chat_id] = time.time()
        asyncio.create_task(_run_edu(chat_id, text))
        return

    # idle — "анализ" (or bottom keyboard button) → show main menu
    if "анализ" in text.lower():
        await _send_main_menu(chat_id)
    else:
        await _tg(
            "sendMessage",
            chat_id=chat_id,
            text="Нажми кнопку «🔍 Анализ» внизу чтобы начать.",
            reply_markup=_MAIN_REPLY_KB,
        )


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

_SCANNER_PAIRS    = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT"]
# BB FADE disabled for SOL/XRP: backtest 35d WR<30%, PF<1 at any ADX threshold
_FADE_PAIRS       = {"BTC-USDT", "ETH-USDT", "DOGE-USDT"}
_SCANNER_LOG      = ROOT / "logs" / "scanner.log"
_SCANNER_INTERVAL = 5   # minutes (was 15 — earlier entry, catch slope before candle exhausts)


_SCANNER_LOG_MAX_BYTES = 5 * 1024 * 1024   # 5 MB
_SCANNER_LOG_BACKUPS   = 3                  # keep scanner.log.1 / .2 / .3


def _rotate_scan_log() -> None:
    """Rotate scanner.log if ≥ 5 MB: .3 dropped, .2→.3, .1→.2, current→.1."""
    if not _SCANNER_LOG.exists() or _SCANNER_LOG.stat().st_size < _SCANNER_LOG_MAX_BYTES:
        return
    for i in range(_SCANNER_LOG_BACKUPS, 0, -1):
        src = Path(f"{_SCANNER_LOG}.{i}")
        dst = Path(f"{_SCANNER_LOG}.{i + 1}")
        if src.exists():
            if i == _SCANNER_LOG_BACKUPS:
                src.unlink()
            else:
                src.rename(dst)
    _SCANNER_LOG.rename(Path(f"{_SCANNER_LOG}.1"))


def _scan_log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line = f"[{ts}] {msg}\n"
    print(line, end="")
    _SCANNER_LOG.parent.mkdir(parents=True, exist_ok=True)
    _rotate_scan_log()
    with open(_SCANNER_LOG, "a", encoding="utf-8") as f:
        f.write(line)


def _next_quarter(now: datetime) -> datetime:
    """Next clock-aligned N-min boundary based on _SCANNER_INTERVAL."""
    total_min = now.hour * 60 + now.minute
    next_min  = ((total_min // _SCANNER_INTERVAL) + 1) * _SCANNER_INTERVAL
    h, m = divmod(next_min % (24 * 60), 60)
    result = now.replace(hour=h, minute=m, second=2, microsecond=0)
    if result <= now:
        result += timedelta(days=1)
    return result


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

    last_signal     = {p: None  for p in _SCANNER_PAIRS}
    last_fade_sent  = {p: None  for p in _SCANNER_PAIRS}  # ts_label[:13] of last FADE hint sent

    while True:
      try:
        now  = datetime.now(timezone.utc)
        hour = now.hour

        # No night block — scanner runs 24/7, disclaimer added to client summary
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
                micro  = result.get("microstructure", {}) or {}
                arrow  = "🟢" if side == "buy" else "🔴"
                header = f"{arrow} <b>{pair}</b> — сигнал входа\n<i>Актуально до {until}</i>\n\n"
                msg    = header + f"<pre>{text}</pre>"
                active = [u["chat_id"] for u in list_users()
                          if u["status"] in ("active", "superadmin")]
                png_path = scan_dir / f"{pair}_chart.png"
                for chat_id in active:
                    try:
                        await _send(chat_id, msg)
                        if png_path.exists():
                            await send_photo_to(chat_id, str(png_path))
                    except Exception as _send_err:
                        _scan_log(f"  [{pair}] ошибка отправки {chat_id}: {_send_err}")
                    await asyncio.sleep(0.3)
                _scan_log(
                    f"  {pair} — СИГНАЛ ВХОДА ({side.upper()}) → отправлено {len(active)} клиентам | "
                    f"obi5={micro.get('obi_top5')} delta100={micro.get('trade_delta_100')} spread_bps={micro.get('spread_bps')}"
                )
                try:
                    _append_signal_log_entry(
                        result,
                        captured_at=captured_at,
                        symbol=pair,
                        source="scanner",
                    )
                except Exception as _e:
                    _scan_log(f"  [signal_log] ошибка записи: {_e}")
                # Auto-execute on operator demo account
                try:
                    from scripts.auto_execute import execute_signal
                    await execute_signal(result)
                except Exception:
                    pass
            else:
                # Log NO_TRADE tick for live funnel analysis
                try:
                    _nt = {
                        "ts_ms":       int(captured_at.timestamp() * 1000),
                        "ts_dt":       captured_at.strftime("%Y-%m-%d %H:%M"),
                        "symbol":      pair,
                        "entry_signal": signal,
                        "drop_reason": result.get("drop_reason") or "",
                        "regime":      result.get("regime") or "",
                        "adx_1h":      result.get("adx_1h") or "",
                        "adx_4h":      result.get("adx_4h") or "",
                        "vol_ratio":   result.get("vol_ratio") or "",
                        "slope_15m":   result.get("slope_15m") or "",
                        "slope_1h":    result.get("slope_1h") or "",
                    }
                    with open(NOTRADE_LOG, "a", encoding="utf-8") as _lf:
                        _lf.write(json.dumps(_nt) + "\n")
                except Exception:
                    pass

                # Check for BB FADE hint even when main signal is NO_TRADE
                fade_hint = result.get("5m_fade_hint")
                fade_data = result.get("5m_fade_data") or {}
                hour_bucket = ts_label[:13]  # "YYYY-MM-DD_HH" — once per hour per pair
                if (fade_hint and fade_data
                        and pair in _FADE_PAIRS
                        and last_fade_sent[pair] != hour_bucket):
                    _dir  = "ЛОНГ" if fade_hint == "LONG" else "ШОРТ"
                    _arr  = "↗️" if fade_hint == "LONG" else "↘️"
                    _band = "нижней" if fade_hint == "LONG" else "верхней"
                    def _fp(v):
                        if v is None: return "?"
                        if v >= 1000: return f"{v:.1f}"
                        if v >= 10:   return f"{v:.3f}"
                        return f"{v:.5f}"
                    _until = result.get("expiry_time", "")
                    fade_msg = (
                        f"📍 <b>BB FADE — {_arr} {pair} {_dir}</b>\n"
                        f"Вход: {_fp(fade_data['entry'])}  "
                        f"🛑 SL: {_fp(fade_data['sl'])}  "
                        f"🎯 TP: {_fp(fade_data['tp'])}  "
                        f"(R:R ≈{fade_data['rr']:.1f})\n"
                        f"ADX низкий — боковик. Цена у {_band} BB, объём слабый.\n"
                        f"⚠️ Ручной вход — проверь 1m и стакан. Макс. 60 мин.\n"
                        f"<i>Актуально до {_until}</i>"
                    )
                    active = [u["chat_id"] for u in list_users()
                              if u["status"] in ("active", "superadmin")]
                    for chat_id in active:
                        try:
                            await _send(chat_id, fade_msg)
                        except Exception as _e:
                            _scan_log(f"  [{pair}] FADE ошибка отправки {chat_id}: {_e}")
                        await asyncio.sleep(0.2)
                    last_fade_sent[pair] = hour_bucket
                    _scan_log(f"  {pair} — BB FADE {_dir} → отправлено {len(active)} клиентам")
                    try:
                        _fade_side = "sell" if fade_hint == "SHORT" else "buy"
                        _fade_ms   = int(datetime.fromisoformat(
                            captured_at.replace("Z", "+00:00")).timestamp() * 1000)
                        _fade_rec  = {
                            "signal_id":    f"{_fade_ms}_{pair}_FADE_{_fade_side}",
                            "source":       "bb_fade",
                            "ts_ms":        _fade_ms,
                            "symbol":       pair,
                            "side":         _fade_side,
                            "regime":       result.get("regime", "RANGING"),
                            "style":        "FADE",
                            "close":        fade_data.get("entry"),
                            "sl":           fade_data.get("sl"),
                            "tp":           fade_data.get("tp"),
                            "max_hold_min": 60,
                            "adx_1h":       result.get("adx_1h"),
                            "funding":      result.get("funding") if result.get("funding") is not None
                                            else result.get("funding_rate"),
                            "is_demo":      os.getenv("OKX_IS_DEMO", "1") == "1",
                        }
                        with open(SIGNAL_LOG, "a", encoding="utf-8") as _lf:
                            _lf.write(json.dumps(_fade_rec) + "\n")
                    except Exception as _fe:
                        _scan_log(f"  [{pair}] FADE signal_log ошибка: {_fe}")
                    fade_root = ROOT / "logs" / "fade"
                    fade_root.mkdir(parents=True, exist_ok=True)
                    fade_dir = fade_root / scan_dir.name
                    try:
                        scan_dir.rename(fade_dir)
                    except Exception:
                        shutil.rmtree(scan_dir, ignore_errors=True)
                else:
                    shutil.rmtree(scan_dir, ignore_errors=True)
                    _scan_log(f"  {pair} — {signal}")

            last_signal[pair] = signal

        # Check timeouts on auto-positions
        try:
            from scripts.auto_execute import check_and_close_timeouts
            await check_and_close_timeouts()
        except Exception:
            pass

        # Sleep until next :00/:15/:30/:45
        now      = datetime.now(timezone.utc)
        next_run = _next_quarter(now)
        await asyncio.sleep(max(1.0, (next_run - now).total_seconds()))

      except Exception:
        _scan_log(f"  [scanner_loop] необработанная ошибка:\n{traceback.format_exc().strip()}")
        await asyncio.sleep(60)  # pause before next cycle on unexpected crash


async def _label_outcomes_loop() -> None:
    """Run label_outcomes.run() once every 24h. First run after 1h delay."""
    await asyncio.sleep(3600)   # wait 1h after bot start before first run
    while True:
        try:
            from scripts.label_outcomes import run as _label_run
            _scan_log("[label_outcomes] запуск...")
            await _label_run(logger=_scan_log)
            _scan_log("[label_outcomes] готово")
        except Exception as e:
            _scan_log(f"[label_outcomes] ошибка: {e}")
        await asyncio.sleep(24 * 3600)


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

    # scanner moved to scripts/ws/ws_scanner.py
    # label_outcomes runs separately via maintenance/update journal workflow

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


def _setup_rotating_log() -> None:
    """Redirect stdout to rotating log file (5 MB, 3 backups).
    Python owns the file — no bat-redirect conflict on Windows.
    """
    import logging
    import logging.handlers

    log_path = ROOT / "logs" / "telegram_bot.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    class _StreamToHandler:
        def __init__(self, h):
            self._h = h
            self._rec = logging.LogRecord("bot", logging.INFO, "", 0, "", (), None)
        def write(self, msg):
            msg = msg.rstrip()
            if msg:
                self._rec.msg = msg
                self._h.emit(self._rec)
        def flush(self):
            self._h.flush()

    sys.stdout = _StreamToHandler(handler)
    sys.stderr = _StreamToHandler(handler)


if __name__ == "__main__":
    _setup_rotating_log()
    asyncio.run(main())
