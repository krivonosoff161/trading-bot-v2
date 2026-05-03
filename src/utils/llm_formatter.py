"""
LLM formatter — Yandex AI Studio (Qwen3-235B).

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
_SUPPORTS_VISION = False   # Qwen3-235B = False
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

ДЛЯ РЕЖИМА 2 и 3 — СЦЕНАРНЫЕ УРОВНИ:
Берёшь конкретные цены из "Ближайшие сопротивления" (для бычьего сценария / условия LONG)
и "Ближайшие поддержки" (для медвежьего / условия SHORT).
Первое число в каждом списке = ближайший уровень, используй его.
Для LONG в РЕЖИМЕ 2: уровни входа/стопа/цели из "Расчётные уровни" в данных.
Для SHORT в РЕЖИМЕ 2: уровень входа = ближайшая поддержка, стоп и цель выводи самостоятельно из контекста.

ЕСЛИ В ДАННЫХ ЕСТЬ "⚠️ Ставка финансирования":
- Это обязательно упоминать в тексте простыми словами
- "экстремально высокая" → "рынок сильно перекуплен, покупателям приходится доплачивать"
- "повышенная" → "рынок немного перегрет, стоит быть осторожным"
- Это главная причина NO_TRADE если режим 3

ЕСЛИ В ДАННЫХ ЕСТЬ "Цена относительно дневного уровня":
- Использовать в секции СЕЙЧАС НА РЫНКЕ как контекст где находится цена
- "выше" → покупатели контролируют, сила за лонгами
- "ниже" → продавцы давят, осторожно с лонгами

КАК РАСШИФРОВЫВАТЬ ЦЕЛИ (важно!):
- "🎯 Цель" — это ОСНОВНАЯ точка фиксации прибыли. При касании — закрыть позицию.
  Это быстрый target скальперского типа: 87% сделок закрываются именно тут.
- "🔝 Стретч" — ДОПОЛНИТЕЛЬНАЯ зона, которая срабатывает только при сильном импульсе.
  Не ждать её по умолчанию. Если цена уже на "Цели" и импульс гаснет — фиксируй, не жди Стретч.
- Запрещено писать "закрыть половину на Цели, половину на Стретч" — клиент фиксирует
  по "Цели" целиком, Стретч — это бонус для тех кто хочет держать дольше.

Последняя строка ВСЕГДА: "🔄 ПОВТОРНЫЙ АНАЛИЗ\nЧерез N минут (после HH:MM UTC)."

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
🎯 Цель:   [tp1_price]   — основная точка фиксации
🔝 Стретч: [tp2_price]   — если импульс сохранится, можно продлить

❌ ЕСЛИ ЛИМИТКА НЕ СРАБОТАЛА
[условие отмены ордера]

❌ ЕСЛИ УЖЕ В ПОЗИЦИИ
Стоп на [sl_price] — не трогаем.

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут (после [HH:MM] UTC).

⚠️ ПРАВИЛА ВХОДА
├─ Плечо: 10x (выставляется автоматически)
├─ Стоп: обязателен, не двигать дальше
├─ Время: см. строку "Максимальное время удержания" выше
├─ Размер: нотионал = 1.5× баланс
└─ Это аналитика — не инвест-рекомендация

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 2 — ЖДЁМ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[1-2 предложения — тренд есть, цена у ключевого уровня, ждём подтверждения]

⏸️ ОРДЕР НЕ СТАВИМ
Рынок на развилке. Три сценария:

📈 БЫЧИЙ — [условие: закрытие свечи выше уровня X или пробой сопротивления]
  Лимитка LONG на [цена входа]
  Стоп: [sl_price] | Цель: [tp1_price] | Стретч: [tp2_price]

📉 МЕДВЕЖИЙ — [условие: пробой поддержки Y или разворот вниз на 15m]
  Сетап отменяется. [Если тренд позволяет шорт: лимитка SHORT на [цена], стоп [уровень], цель [уровень]. Если нет — просто ждём следующего анализа.]

⏸️ НЕЙТРАЛЬНЫЙ — цена остаётся между [support] и [resistance]
  Не входим. Ждём повторного анализа.

⚠️ ЕСЛИ ЦЕНА УЛЕТЕЛА
Момент упущен — не гонимся.

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут (после [HH:MM] UTC).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ 3 — НЕТ ТОРГОВЛИ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 СЕЙЧАС НА РЫНКЕ
[1-2 предложения — рынок в боковике / тренд слабый / причина почему нет сделки]

🚫 НЕТ СДЕЛКИ
[почему не торгуем — одно предложение]

👁️ ЧТО ДОЛЖНО ПРОИЗОЙТИ

📈 Чтобы появился LONG:
[конкретное условие — тренд на 1H развернётся вверх / цена закрепится выше уровня X / объём вырастет]

📉 Чтобы появился SHORT:
[конкретное условие — пробой поддержки Y / тренд на 1H уйдёт вниз / цена закрепится ниже Z]

⏸️ Пока ни то ни другое не случилось — в рынок не лезем.

⚠️ ПРИ ПРОБОЕ — НЕ ВХОДИТЬ СРАЗУ
Дождись повторного анализа.

❌ НЕ ДЕЛАТЬ
[1-2 конкретных запрета — ТОЛЬКО про эту ситуацию.
ЗАПРЕЩЕНО общие советы вроде "не торгуй краткосрочно". Пиши конкретно: "не входить в LONG без подтверждения тренда на 1H", "не интерпретировать пробой на 5m без контекста старшего таймфрейма"]

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
🎯 Цель:      [tp1_price]   — основная точка фиксации
🔝 Стретч:    [tp2_price]   — опционально, если импульс сохранится

⚠️ ВАЖНО
Вход на коррекции, не на пробое. Если цена пробьёт стоп — выходим без пересмотра.

❌ НЕ ДЕЛАТЬ
Не ждать возврата к вершине перед входом — момент будет упущен.
Не усредняться если цена идёт против.

🔄 ПОВТОРНЫЙ АНАЛИЗ
Через [N] минут (после [HH:MM] UTC).

⚠️ ПРАВИЛА ВХОДА
├─ Плечо: 10x (выставляется автоматически)
├─ Стоп: обязателен, не двигать дальше
├─ Время: см. строку "Максимальное время удержания" выше
├─ Размер: нотионал = 1.5× баланс
└─ Это аналитика — не инвест-рекомендация
"""

# ── Header builder (Python, not LLM) ───────────────────────────────────────────

_STATUS_LABELS = {"ENTRY": "ВХОД", "WAIT": "НАБЛЮДАЕМ", "NO_TRADE": "ВНЕ РЫНКА", "PULLBACK": "ОТКАТ В ТРЕНДЕ"}
_STYLE_LABELS  = {"FAST":     "⚡ БЫСТРЫЙ — закрыть в течение 2 часов, график 15m",
                  "SWING":    "📈 СВИНГ — держать до 4 часов, график 1H",
                  "SCALP":    "⚡ СКАЛЬП — закрыть в течение 2 часов, график 15m",
                  "PULLBACK": "🔄 ОТКАТ — закрыть в течение 8 часов, график 1H",
                  "NO_TRADE": "📊 НАБЛЮДАЕМ — ждём подтверждения на 5m"}


def _build_header(symbol: str, captured_at: str, entry_signal: str, trade_style: str,
                  bias_1h: str, bias_4h: str, side: str | None = None) -> str:
    status = _STATUS_LABELS.get(entry_signal, "НАБЛЮДАЕМ")
    style  = _STYLE_LABELS.get(trade_style, "")
    if trade_style == "SWING" and side == "sell":
        style = style.replace("📈", "📉")
    # For SCALP, bias_1h is NEUTRAL by design — use side from llm_context instead
    bias   = bias_1h if bias_1h != "NEUTRAL" else bias_4h
    if side == "buy":
        direction = "только LONG — короткая сторона не рассматривается"
    elif side == "sell":
        direction = "только SHORT — длинная сторона не рассматривается"
    elif bias == "UP":
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


def _fp(symbol: str, price) -> str:
    """Format price with correct decimal places for the instrument."""
    if price is None:
        return "—"
    base = symbol.split("-")[0].upper()
    if base == "BTC":
        return f"{float(price):.1f}"
    if base in ("ETH", "SOL"):
        return f"{float(price):.2f}"
    return f"{float(price):.4f}"


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
    side_raw = ctx.get("side")
    side_label = ("ЛОНГ (покупка) — вход на рост" if side_raw == "buy"
                  else "ШОРТ (продажа) — вход на снижение" if side_raw == "sell"
                  else None)

    lines = [
        f"РЕЖИМ: {mode_cmd}",
        f"Пара: {symbol}  |  Цена: {close}  |  Время: {captured_at}",
    ]
    if side_label:
        lines.append(f"Тип сделки: {side_label}")
    lines += [
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

    # Funding rate — show only if trade is active OR funding actually blocked it
    funding_blocked = ctx.get("funding_blocked", False)
    if funding is not None and abs(funding) > 0.0005 and (entry_signal != "NO_TRADE" or funding_blocked):
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
            f"  Вход:    {_fp(symbol, close)}",
            f"  Стоп:    {_fp(symbol, sl)}",
            f"  Цель:    {_fp(symbol, tp1)}  (основная, закрывать при касании)",
            f"  Стретч:  {_fp(symbol, tp2)}  (опционально, при сильном импульсе)",
        ]

    max_hold = ctx.get("max_hold_minutes")
    if max_hold and entry_signal != "NO_TRADE":
        lines.append(f"\n⏱ Максимальное время удержания: {max_hold} минут — закрыть вручную если уровни не достигнуты")

    expiry = snapshot.get("expiry_time")
    if expiry:
        lines.append(f"\n🕐 Актуально до: {expiry}")

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
    Call Qwen3-235B via Yandex AI Studio and return natural Russian text.
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
            side=ctx.get("side"),
        )
        return header + body

    except Exception as exc:
        print(f"LLM: error — {exc}")
        return None


# ── Premium vision (Gemma 3 27B IT) ───────────────────────────────────────────

_GEMMA_MODEL_URI = os.getenv("YANDEX_GEMMA_MODEL_URI", "").strip("'\"")
_GEMMA_MAX_TOKENS = 500
_GEMMA_TIMEOUT   = 90


async def generate_premium_analysis(category: str, image_bytes: bytes) -> str | None:
    """Call Gemma 3 27B IT (vision) to analyze a chart screenshot."""
    if not _API_KEY or not _GEMMA_MODEL_URI:
        print("Gemma: YANDEX_API_KEY or YANDEX_GEMMA_MODEL_URI not set — skipping")
        return None

    from scripts.premium_prompts import PREMIUM_SYSTEM_PROMPTS, PREMIUM_USER_PROMPT
    system_prompt = PREMIUM_SYSTEM_PROMPTS.get(category, PREMIUM_SYSTEM_PROMPTS["CRYPTO"])
    b64 = base64.b64encode(image_bytes).decode()

    payload = {
        "model": _GEMMA_MODEL_URI,
        "max_tokens": _GEMMA_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": PREMIUM_USER_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]},
        ],
    }
    headers = {
        "Authorization": f"Api-Key {_API_KEY}",
        "Content-Type":  "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _API_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=_GEMMA_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"Gemma: HTTP {resp.status} — {body[:200]}")
                    return None
                data = await resp.json()
        body = data["choices"][0]["message"]["content"].strip()
        tokens = data.get("usage", {})
        print(f"Gemma: OK — {tokens.get('total_tokens', '?')} tokens")
        return body or None
    except Exception as exc:
        print(f"Gemma: error — {exc}")
        return None


# ── Educational Q&A ────────────────────────────────────────────────────────────

_EDU_SYSTEM_PROMPT = """\
Ты — обучающий ассистент по крипторынку. Отвечаешь на вопросы о терминах и механизмах.
Язык: русский, простые слова. Отвечай по существу — сначала суть, потом пример если нужен.
Не увлекайся метафорами и аналогиями — одна короткая если помогает понять, больше не нужно.

ЗАПРЕЩЕНО:
- предсказывать цены или движение рынка
- давать торговые рекомендации ("купи", "продай", "сейчас хороший момент")
- называть конкретные монеты как "хорошую инвестицию"
- придумывать статистику и цифры
- отвечать на вопросы не про крипто/трейдинг

ЕСЛИ не знаешь точно — пиши: "Точно не знаю, лучше проверить в официальных источниках."
ЕСЛИ просят дать конкретный сигнал прямо сейчас ("что купить?", "входить ли в BTC?") — пиши: "Для анализа графика нажми кнопку Анализ."
Вопросы о том КАК работают SL/TP, индикаторы, стратегии — это образовательные вопросы, отвечай на них.
ЕСЛИ вопрос не про крипту — пиши: "Я отвечаю только на вопросы про крипторынок."

Формат ответа: до 200 слов, без списков если можно обойтись, живой текст.
"""


async def generate_edu_text(question: str) -> str | None:
    """Answer a user's educational question via Qwen. Returns None on error."""
    if not _API_KEY or not _FOLDER_ID:
        print("LLM edu: YANDEX_API_KEY or YANDEX_FOLDER_ID not set — skipping")
        return None

    payload = {
        "model": _MODEL_URI,
        "max_tokens": 400,
        "messages": [
            {"role": "system", "content": _EDU_SYSTEM_PROMPT},
            {"role": "user",   "content": question},
        ],
    }
    headers = {
        "Authorization": f"Api-Key {_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _API_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"LLM edu: HTTP {resp.status} — {body[:200]}")
                    return None
                data = await resp.json()
        body = data["choices"][0]["message"]["content"].strip()
        tokens = data.get("usage", {})
        print(f"LLM edu: OK — {tokens.get('total_tokens', '?')} tokens")
        return body or None
    except Exception as exc:
        print(f"LLM edu: error — {exc}")
        return None
