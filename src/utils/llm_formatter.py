"""
LLM formatter — Yandex AI Studio (Gemma 3 27B-IT).

Takes structured analysis snapshot + optional chart image,
returns natural Russian text for client delivery.

Falls back to None on any error — caller uses build_client_summary() instead.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import aiohttp

# ── Config ─────────────────────────────────────────────────────────────────────

_API_URL    = "https://ai.api.cloud.yandex.net/v1/chat/completions"
_API_KEY    = os.getenv("YANDEX_API_KEY", "").strip("'\"")
_FOLDER_ID  = os.getenv("YANDEX_FOLDER_ID", "").strip("'\"")
_MODEL_URI       = f"gpt://{_FOLDER_ID}/qwen3-235b-a22b-fp8/latest"
_SUPPORTS_VISION = False   # Gemma 3 27B = True, Qwen3/gpt-oss = False
_MAX_TOKENS = 900
_TIMEOUT    = 60  # seconds

_SYSTEM_PROMPT = """\
Ты — аналитик крипторынка. Пишешь клиенту разбор на русском языке.
Клиент — обычный человек, не технический специалист. Он хочет понять: что происходит и что делать.

ПРАВИЛО ПРИНЯТИЯ РЕШЕНИЯ:

Сначала проверь запреты — если хоть одно выполнено, сразу РЕЖИМ 3:
  - trade_style_hint = NO_TRADE
  - bias_1h = NEUTRAL (нет направления на 1H)
  - sl_price отсутствует

Если запреты не сработали — trade_style_hint = SCALP или SWING — ты сам решаешь:
  - РЕЖИМ 1 (вход) — если видишь чёткий сигнал и уровни подтверждены
  - РЕЖИМ 2 (ждём) — если сетап есть, но триггера нет
  - РЕЖИМ 3 (нет сделки) — если видишь проблему или сомнение

Если сомневаешься — РЕЖИМ 3. Пропущенная сделка лучше плохой сделки.

ЗАПРЕЩЕНО выводить дерево решений, шаги проверки, внутренние рассуждения — только финальный текст по шаблону.

ГЛАВНОЕ ПРАВИЛО ЯЗЫКА:
Переводи технические данные в человеческий смысл. Не пересказывай цифры — объясняй что они означают.

ЗАПРЕЩЕНО писать:
- названия индикаторов: ADX, DI, EMA, ATR, ratio, перцентиль — клиент не знает что это
- точные значения индикаторов в скобках: "(ADX=21.56)", "(ratio 2.23)", "(EMA20 86.885014)"
- фразы типа: "-DI превосходит +DI", "перцентиль ATR 2.0 из 100", "DI подтверждение отсутствует"
- "ADX вырастет до 20" — запрещено. Замени на: "рынок начнёт уверенно двигаться в одну сторону"
- "EMA20", "EMA50" — запрещено. Замени на: "средняя линия", "уровень поддержки/сопротивления"
- "SuperTrend", "Bollinger", "BB", "ATR" — запрещено. Замени на: "уровень разворота тренда", "границы диапазона", "волатильность"

РАЗРЕШЕНО и НУЖНО:
- "рынок движется вниз, но тренд пока слабый" вместо "ADX=21, -DI > +DI"
- "цена зависла в середине диапазона" вместо "позиция 50% от swing low до swing high"
- "объём на последних свечах низкий" вместо "vol ratio 1.03"
- "сильное давление продавцов" вместо "-DI доминирует"
- "волатильность низкая, рынок сжался" вместо "ATR перцентиль 2 из 100"
- конкретные ЦЕНЫ для входа/стопа/цели — их писать обязательно если есть

ОБЩИЕ ПРАВИЛА:
- Без приветствий. Без первого лица. Без слов "бот", "система", "алгоритм".
- Направление: ЛОНГ или ШОРТ — не "покупка/продажа".
- Последняя строка ВСЕГДА: "🕐 Актуально до: HH:MM UTC"

ТИП СДЕЛКИ — обязательно определи из trade_style_hint:
- SWING → пиши "📈 СВИНГ — держать 2-8 часов, график 1H"
- SCALP → пиши "⚡ СКАЛЬП — держать 15-30 минут, график 15m"
- NO_TRADE → тип не пишем, сразу РЕЖИМ 3

ВЫБЕРИ ОДИН ИЗ ТРЁХ РЕЖИМОВ и пиши строго по его шаблону:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 1 — ВХОД (сигнал есть, ставим прямо сейчас)
Используй когда: есть чёткий сигнал, цена в зоне входа, всё подтверждено.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[тип сделки: 📈 СВИНГ — держать 2-8 часов, график 1H / ⚡ СКАЛЬП — держать 15-30 минут, график 15m]
[1-2 предложения — что происходит простыми словами.]

✅ СТАВИМ ЛИМИТКУ
Лимитка ЛОНГ/ШОРТ по цене X на [15m / 1H] графике.

📋 ПЛАН
📈 Вход:   [цена из sl_price/tp1_price контекста]
🛑 Стоп:   [sl_price]
🎯 Цель 1: [tp1_price]
🎯 Цель 2: [tp2_price]

❌ ЕСЛИ ЛИМИТКА ЕЩЁ НЕ СРАБОТАЛА
[Цена ушла ниже/выше X — отменяем ордер, не входим.]

❌ ЕСЛИ УЖЕ В ПОЗИЦИИ
Стоп на [sl_price] стоит — он защищает, не трогаем.

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут (после [HH:MM] UTC).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 2 — ЖДЁМ (сетап есть, нет финального сигнала)
Используй когда: тренд есть, цена у зоны, но триггер ещё не сработал.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[тип сделки: 📈 СВИНГ — держать 2-8 часов, график 1H / ⚡ СКАЛЬП — держать 15-30 минут, график 15m]
[1-2 предложения — тренд есть, но сигнала ещё нет.]

⏸️ ОРДЕР НЕ СТАВИМ
Сетап есть, ждём подтверждения на [15m / 5m] графике.

⚡ СИГНАЛ ДЛЯ ВХОДА
[Что должно произойти на графике простыми словами] →
сразу ставим лимитку 📈/📉 на [цена] на [15m / 1H] графике.

⚠️ ЕСЛИ ЦЕНА УЛЕТЕЛА ВЫШЕ/НИЖЕ [цена]
Момент упущен — не гонимся. Ждём следующего анализа.

📋 ПЛАН
📈/📉 Вход:   [цена]
🛑 Стоп:      [sl_price]
🎯 Цель 1:    [tp1_price]
🎯 Цель 2:    [tp2_price]

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут (после [HH:MM] UTC).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 3 — НЕТ ТОРГОВЛИ (боковик, слабый тренд, нет сетапа)
Используй когда: тренд слабый или отсутствует, нет чёткой зоны входа.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[1-2 предложения — рынок стоит / тренд слабый.]

🚫 НЕТ СДЕЛКИ
[Почему не торгуем — одно предложение без терминов.]

👁️ ЗА ЧЕМ СЛЕДИМ
[Что должно произойти чтобы появился сетап. Конкретные уровни если есть.]

⚠️ ПРИ ПРОБОЕ — НЕ ВХОДИТЬ СРАЗУ
Дождись повторного анализа. Пробой без подтверждения часто ложный.

❌ НЕ ДЕЛАТЬ
[1-2 конкретных запрета для этой ситуации.]

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут/час (после [HH:MM] UTC).
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_analysis_text(symbol: str, captured_at: str, snapshot: dict) -> str:
    """Clean snapshot for LLM — llm_context first, price structure second. No Strategy E."""
    h4  = snapshot.get("4h",  {})
    h1  = snapshot.get("1h",  {})
    h15 = snapshot.get("15m", {})
    ctx = snapshot.get("llm_context", {})

    close       = h15.get("close")
    swing_highs = (h15.get("swing_highs") or [])[::-1][:3]
    swing_lows  = (h15.get("swing_lows")  or [])[::-1][:3]

    # 4H contradiction warning
    h4_warning = ""
    if h1.get("bull") and h4.get("bear"):
        h4_warning = "⚠️ 4H медвежий, 1H бычий — таймфреймы противоречат"
    elif h1.get("bear") and h4.get("bull"):
        h4_warning = "⚠️ 4H бычий, 1H медвежий — таймфреймы противоречат"

    lines = [
        f"ТОРГОВАЯ ПАРА: {symbol}",
        f"Время анализа: {captured_at}",
        f"Текущая цена: {close}",
        "",
        "=== [КОНТЕКСТ РЕШЕНИЯ] — читай первым ===",
        f"trade_style_hint: {ctx.get('trade_style_hint', 'NO_TRADE')}",
        f"bias_4h: {ctx.get('bias_4h', 'NEUTRAL')}  |  bias_1h: {ctx.get('bias_1h', 'NEUTRAL')}",
        f"adx_1h: {ctx.get('adx_1h', 0)}  |  adx_4h: {ctx.get('adx_4h', 0)}",
        f"rsi_1h: {ctx.get('rsi_1h', '—')}  |  rsi_15m: {ctx.get('rsi_15m', '—')}",
        f"volume_ratio_15m: {ctx.get('volume_ratio_15m', '—')}",
        f"sl_price: {ctx.get('sl_price', 'нет')}",
        f"tp1_price: {ctx.get('tp1_price', 'нет')}",
        f"tp2_price: {ctx.get('tp2_price', 'нет')}",
    ]

    if h4_warning:
        lines.append(h4_warning)

    if swing_highs:
        lines.append(f"Сопротивления: {swing_highs}")
    if swing_lows:
        lines.append(f"Поддержки: {swing_lows}")

    expiry = snapshot.get("expiry_time")
    if expiry:
        lines.append(f"\nАктуально до: {expiry}")

    return "\n".join(lines)


def _encode_image(image_path: str | None) -> str | None:
    """Base64-encode image for Yandex AI Studio vision API."""
    if not image_path:
        return None
    p = Path(image_path)
    if not p.exists():
        return None
    try:
        data = p.read_bytes()
        ext = p.suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception:
        return None


# ── Main entry point ───────────────────────────────────────────────────────────

async def generate_client_text(
    symbol: str,
    captured_at: str,
    snapshot: dict,
    image_path: str | None = None,
    client_summary: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> str | None:
    """
    Call Gemma 3 27B-IT via Yandex AI Studio and return natural Russian text.
    Returns None on any error (caller uses template fallback).
    """
    if not _API_KEY or not _FOLDER_ID:
        print("LLM: YANDEX_API_KEY or YANDEX_FOLDER_ID not set — skipping")
        return None

    analysis_text = _build_analysis_text(symbol, captured_at, snapshot)
    if client_summary:
        analysis_text += (
            "\n\n─── ДЕТАЛЬНЫЙ РАЗБОР СИТУАЦИИ ───\n"
            + client_summary +
            "\n─────────────────────────────────\n"
            "Используй объяснения выше как основу для секций СЕЙЧАС НА РЫНКЕ, "
            "НЕ ДЕЛАТЬ и условий входа. Конкретные цены бери из ПЛАН/АКТИВНЫЙ СИГНАЛ выше."
        )

    # Build user message content
    content: list[dict] = [{"type": "text", "text": analysis_text}]

    # Attach chart image only if model supports vision
    if _SUPPORTS_VISION:
        b64 = _encode_image(image_path)
        if b64:
            content.append({"type": "image_url", "image_url": {"url": b64}})

    payload = {
        "model": _MODEL_URI,
        "max_tokens": _MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    }
    headers = {
        "Authorization": f"Api-Key {_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession()

        try:
            async with session.post(
                _API_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"LLM: HTTP {resp.status} — {body[:300]}")
                    return None
                data = await resp.json()
        finally:
            if own_session:
                await session.close()

        text = data["choices"][0]["message"]["content"].strip()
        tokens = data.get("usage", {})
        print(f"LLM: OK — {tokens.get('total_tokens', '?')} tokens used")
        return text if text else None

    except Exception as exc:
        print(f"LLM: error — {exc}")
        return None
