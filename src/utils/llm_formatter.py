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
_MODEL_URI       = "gpt://b1git4svubpojuiga5pn/qwen3-235b-a22b-fp8/latest"
_SUPPORTS_VISION = False   # Gemma 3 27B = True, Qwen3/gpt-oss = False
_MAX_TOKENS = 900
_TIMEOUT    = 60  # seconds

_SYSTEM_PROMPT = """\
Ты — аналитик крипторынка. Пишешь клиенту разбор на русском языке.
Клиент — обычный человек. Он хочет понять: что происходит и что делать.

ЗАГОЛОВОК (Статус, Тип, Направление) уже написан автоматически — ты его НЕ пишешь.
Твоя задача — написать ТОЛЬКО ТЕЛО, начиная строго с "📊 СЕЙЧАС НА РЫНКЕ".

Первая строка ответа ОБЯЗАТЕЛЬНО: 📊 СЕЙЧАС НА РЫНКЕ

Ты получишь РЕЖИМ — это команда какой шаблон использовать. Менять режим нельзя.

ЗАПРЕЩЕНО:
- ADX, DI, EMA, ATR, ratio, перцентиль, SuperTrend, Bollinger, BB, VWAP, funding, OI — клиент не знает что это
- значения индикаторов в скобках: "(ADX=21)", "(EMA20 86.88)"
- "EMA20/50" → замени на "средняя линия" / "уровень поддержки"
- слова "бот", "система", "алгоритм", приветствия, первое лицо
- внутренние рассуждения в скобках типа "(Проще: ...)"

РАЗРЕШЕНО:
- "тренд вверх и сильный" вместо "ADX=36, bias=UP"
- "объём низкий" вместо "vol ratio 1.03"
- конкретные ЦЕНЫ входа/стопа/цели — обязательно если есть в данных
- "рынок перегрет — покупателей слишком много" вместо "funding 0.6%"
- "цена ниже дневного уровня равновесия — давление продавцов" вместо "ниже VWAP"
- "диапазон дня X — Y" — использовать для объяснения где находимся

ЕСЛИ В ДАННЫХ ЕСТЬ "⚠️ Ставка финансирования":
- Это обязательно упоминать в тексте простыми словами
- "экстремально высокая" → "рынок сильно перекуплен, покупателям приходится доплачивать"
- "повышенная" → "рынок немного перегрет, стоит быть осторожным"
- Это главная причина NO_TRADE если режим 3

ЕСЛИ В ДАННЫХ ЕСТЬ "Цена относительно дневного уровня":
- Использовать в секции СЕЙЧАС НА РЫНКЕ как контекст где находится цена
- "выше" → покупатели контролируют, сила за лонгами
- "ниже" → продавцы давят, осторожно с лонгами

Последняя строка ВСЕГДА: "🕐 Актуально до: HH:MM UTC"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 1 — ВХОД
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[1-2 предложения что происходит простыми словами]

✅ СТАВИМ ЛИМИТКУ
Лимитка ЛОНГ/ШОРТ по цене X.

📋 ПЛАН
📈 Вход:   [цена]
🛑 Стоп:   [sl_price]
🎯 Цель 1: [tp1_price]
🎯 Цель 2: [tp2_price]

❌ ЕСЛИ ЛИМИТКА НЕ СРАБОТАЛА
[условие отмены ордера]

❌ ЕСЛИ УЖЕ В ПОЗИЦИИ
Стоп на [sl_price] — не трогаем.

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут (после [HH:MM] UTC).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 2 — ЖДЁМ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[1-2 предложения — тренд есть, но сигнала ещё нет]

⏸️ ОРДЕР НЕ СТАВИМ
Ждём подтверждения на [15m / 5m] графике.

⚡ СИГНАЛ ДЛЯ ВХОДА
[что должно произойти] → ставим лимитку 📈/📉 на [цена].

⚠️ ЕСЛИ ЦЕНА УЛЕТЕЛА
Момент упущен — не гонимся.

📋 ПЛАН
📈/📉 Вход:   [цена]
🛑 Стоп:      [sl_price]
🎯 Цель 1:    [tp1_price]
🎯 Цель 2:    [tp2_price]

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут (после [HH:MM] UTC).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 3 — НЕТ ТОРГОВЛИ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[1-2 предложения — рынок стоит / тренд слабый]

🚫 НЕТ СДЕЛКИ
[почему не торгуем — одно предложение]

👁️ ЗА ЧЕМ СЛЕДИМ
[что должно произойти для сетапа]

⚠️ ПРИ ПРОБОЕ — НЕ ВХОДИТЬ СРАЗУ
Дождись повторного анализа.

❌ НЕ ДЕЛАТЬ
[1-2 конкретных запрета]

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут/час (после [HH:MM] UTC).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 4 — ОТКАТ В ТРЕНДЕ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[1-2 предложения — старший тренд активен, цена откатила к нижней/верхней зоне дня]

🔄 ВХОД НА ОТКАТЕ
Тренд на старшем графике [вверх/вниз] — цена временно откатила, это точка входа по тренду.
Лимитка ЛОНГ/ШОРТ по цене X.

📋 ПЛАН
📈/📉 Вход:   [цена]
🛑 Стоп:      [sl_price] — под локальным минимумом
🎯 Цель 1:    [tp1_price]
🎯 Цель 2:    [tp2_price]

⚠️ ВАЖНО
Вход на коррекции, не на пробое. Если цена пробьёт стоп — выходим без пересмотра.

❌ НЕ ДЕЛАТЬ
Не ждать возврата к вершине перед входом — момент будет упущен.
Не усредняться если цена идёт против.

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут (после [HH:MM] UTC).
"""

# ── Header builder (Python, not LLM) ───────────────────────────────────────────

_STATUS_LABELS = {"ENTRY": "ВХОД", "WAIT": "НАБЛЮДАЕМ", "NO_TRADE": "ВНЕ РЫНКА", "PULLBACK": "ОТКАТ В ТРЕНДЕ"}
_STYLE_LABELS  = {"SWING":    "📈 СВИНГ — держать 2-8 часов, график 1H",
                  "SCALP":    "⚡ СКАЛЬП — держать 15-30 минут, график 15m",
                  "PULLBACK": "🔄 ОТКАТ — вход на коррекции в тренде, график 1H"}


def _build_header(symbol: str, captured_at: str, entry_signal: str, trade_style: str,
                  bias_1h: str, bias_4h: str) -> str:
    status = _STATUS_LABELS.get(entry_signal, "НАБЛЮДАЕМ")
    style  = _STYLE_LABELS.get(trade_style, "")
    bias   = bias_1h if bias_1h != "NEUTRAL" else bias_4h
    if bias == "UP":
        direction = "только LONG — короткая сторона не рассматривается"
    elif bias == "DOWN":
        direction = "только SHORT — длинная сторона не рассматривается"
    else:
        direction = "направления нет — ни LONG, ни SHORT не рассматриваются"

    sep   = "═" * 46
    lines = [sep, f"  {symbol}  |  {captured_at}", sep, "",
             f"  Статус:      {status}"]
    if style and entry_signal != "NO_TRADE":
        lines.append(f"  Тип:         {style}")
    lines += [f"  Направление: {direction}", ""]
    return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_analysis_text(symbol: str, captured_at: str, snapshot: dict) -> str:
    """Human-readable data snapshot for LLM — plain words, no indicator names or raw numbers."""
    h15 = snapshot.get("15m", {})
    ctx = snapshot.get("llm_context", {})

    close = h15.get("close")

    # Trend: words only, no ADX numbers
    def trend_str(bias, adx):
        adx = adx or 0
        strength = "сильный" if adx >= 30 else ("умеренный" if adx >= 25 else ("слабый" if adx >= 15 else "очень слабый / нет тренда"))
        if bias == "UP":
            return f"вверх, {strength}"
        elif bias == "DOWN":
            return f"вниз, {strength}"
        else:
            # NEUTRAL bias with high ADX = correction/consolidation within trend, not flat
            if adx >= 25:
                return f"консолидация в тренде, {strength}"
            return "боковик, нет тренда"

    bias_4h = ctx.get("bias_4h", "NEUTRAL")
    bias_1h = ctx.get("bias_1h", "NEUTRAL")
    adx_4h  = ctx.get("adx_4h", 0)
    adx_1h  = ctx.get("adx_1h", 0)
    rsi_1h  = ctx.get("rsi_1h")
    rsi_15m = ctx.get("rsi_15m")
    vol     = ctx.get("volume_ratio_15m")
    sl      = ctx.get("sl_price")
    tp1     = ctx.get("tp1_price")
    tp2     = ctx.get("tp2_price")

    entry_signal = ctx.get("entry_signal", "WAIT")
    trade_style  = ctx.get("trade_style_hint", "NO_TRADE")
    funding      = ctx.get("funding_rate")
    vwap         = ctx.get("vwap_day")
    day_high     = ctx.get("day_high")
    day_low      = ctx.get("day_low")

    conflict = bias_4h != "NEUTRAL" and bias_1h != "NEUTRAL" and bias_4h != bias_1h

    # РЕЖИМ 4 triggered by trade_style=PULLBACK; otherwise by entry_signal
    if trade_style == "PULLBACK" and entry_signal != "NO_TRADE":
        mode_cmd = "РЕЖИМ 4 — ОТКАТ В ТРЕНДЕ"
    else:
        mode_cmd = {
            "ENTRY":    "РЕЖИМ 1 — ВХОД",
            "WAIT":     "РЕЖИМ 2 — ЖДЁМ",
            "NO_TRADE": "РЕЖИМ 3 — НЕТ ТОРГОВЛИ",
        }.get(entry_signal, "РЕЖИМ 2 — ЖДЁМ")
    lines = [
        f"РЕЖИМ: {mode_cmd}",
        f"Пара: {symbol}  |  Цена: {close}  |  Время: {captured_at}",
        "",
        f"Тренд на 4H: {trend_str(bias_4h, adx_4h)}",
        f"Тренд на 1H: {trend_str(bias_1h, adx_1h)}",
    ]

    if conflict:
        lines.append("⚠️ Таймфреймы противоречат друг другу — повышенный риск")

    if rsi_1h is not None:
        rsi_label = "перекуплен" if rsi_1h > 70 else ("перепродан" if rsi_1h < 30 else "нейтральная зона")
        lines.append(f"Импульс (1H): {rsi_label}")

    if rsi_15m is not None:
        rsi_label = "перекуплен" if rsi_15m > 70 else ("перепродан" if rsi_15m < 30 else "нейтральная зона")
        lines.append(f"Импульс (15m): {rsi_label}")

    if vol is not None:
        vol_label = "высокий — подтверждает движение" if float(vol) >= 1.5 else ("нормальный" if float(vol) >= 1.0 else "низкий — движение слабое")
        lines.append(f"Объём: {vol_label}")

    # VWAP context
    if vwap and close:
        vwap_rel = "выше VWAP — покупатели контролируют день" if float(close) >= float(vwap) else "ниже VWAP — продавцы контролируют день"
        lines.append(f"Цена относительно дневного уровня: {vwap_rel}")

    if day_high and day_low:
        lines.append(f"Диапазон дня: {day_low} — {day_high}")

    # Swing levels filtered by price (resistance above, support below)
    if close:
        _price = float(close)
        swing_highs = sorted([h for h in (h15.get("swing_highs") or []) if float(h) > _price])[:3]
        swing_lows  = sorted([l for l in (h15.get("swing_lows")  or []) if float(l) < _price], reverse=True)[:3]
    else:
        swing_highs = (h15.get("swing_highs") or [])[::-1][:3]
        swing_lows  = (h15.get("swing_lows")  or [])[::-1][:3]

    if swing_highs:
        lines.append(f"Ближайшие сопротивления: {swing_highs}")
    if swing_lows:
        lines.append(f"Ближайшие поддержки: {swing_lows}")

    # Funding rate — context-aware relative to mode threshold
    if funding is not None and abs(funding) > 0.0005:
        pct = round(abs(funding) * 100, 3)
        direction_word = "лонги переплачивают шортам" if funding > 0 else "шорты переплачивают лонгам"
        _limits = {"SWING": 0.3, "PULLBACK": 0.8, "SCALP": 0.5}
        _limit  = _limits.get(trade_style, 0.1)
        if pct > _limit:
            level = "экстремально высокая — сделка заблокирована"
        elif pct > _limit * 0.8:
            level = f"повышенная, но в пределах для этого типа входа — не держать позицию дольше 4 часов"
        else:
            level = "умеренная — учитывай при удержании позиции"
        lines.append(f"\n⚠️ Ставка финансирования: {level} ({direction_word}, {pct}%)")

    # Levels — only for ENTRY/WAIT, not NO_TRADE
    if entry_signal != "NO_TRADE" and sl:
        lines += [
            "",
            f"Расчётные уровни:",
            f"  Вход:   {close}",
            f"  Стоп:   {sl}",
            f"  Цель 1: {tp1}",
            f"  Цель 2: {tp2}",
        ]

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

        body = data["choices"][0]["message"]["content"].strip()
        tokens = data.get("usage", {})
        print(f"LLM: OK — {tokens.get('total_tokens', '?')} tokens used")
        if not body:
            return None
        ctx = snapshot.get("llm_context", {})
        header = _build_header(
            symbol, captured_at,
            ctx.get("entry_signal", "WAIT"),
            ctx.get("trade_style_hint", "NO_TRADE"),
            ctx.get("bias_1h", "NEUTRAL"),
            ctx.get("bias_4h", "NEUTRAL"),
        )
        return header + body

    except Exception as exc:
        print(f"LLM: error — {exc}")
        return None
