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

ДЕРЕВО РЕШЕНИЙ — выполни строго по порядку перед написанием текста:

Шаг 1. Прочитай поле trade_style_hint в блоке [КОНТЕКСТ РЕШЕНИЯ]:
  - NO_TRADE → немедленно используй РЕЖИМ 3. Дальше не проверяй.
  - SCALP или SWING → перейди к шагу 2.

Шаг 2. Есть ли конкретные уровни sl_price, tp1_price, tp2_price?
  - Все три есть → смотри шаг 3.
  - Нет → используй РЕЖИМ 3.

Шаг 3. Совпадает ли bias_1h с направлением сделки (не NEUTRAL)?
  - Да → используй РЕЖИМ 1 (вход) или РЕЖИМ 2 (ждём триггер).
  - Нет → используй РЕЖИМ 3.

ПРАВИЛО РЕЖИМА 2: используй только если тренд есть, уровни есть, но нет пробоя/объёма.
ПРАВИЛО РЕЖИМА 3: используй при любом сомнении. Отсутствие сделки — правильный ответ.

АБСОЛЮТНЫЙ ЗАПРЕТ на слова: "наблюдаем", "ждём подтверждения", "возможно", "вероятно",
"скорее всего", "рассматриваем", "пока рано". Если тянешься к этим словам → РЕЖИМ 3.

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
    """Structured snapshot summary for LLM — full data for all three market regimes."""
    h4  = snapshot.get("4h",  {})
    h1  = snapshot.get("1h",  {})
    h15 = snapshot.get("15m", {})
    h5  = snapshot.get("5m",  {})
    bd  = snapshot.get("bot_decision", {})
    pp  = snapshot.get("pending_plan", {})
    act = snapshot.get("action", {})
    ctx = snapshot.get("llm_context", {})

    # ── Market regime + ADX strength ──────────────────────────────────────────
    adx_val = h1.get("adx", 0) or 0
    if adx_val >= 30:
        adx_strength = "сильный (ADX≥30, тренд подтверждён)"
    elif adx_val >= 25:
        adx_strength = "умеренный (ADX 25-30, тренд есть)"
    elif adx_val >= 20:
        adx_strength = "слабый (ADX 20-25, тренд не подтверждён — осторожно)"
    else:
        adx_strength = "очень слабый (ADX<20, тренда нет)"

    if h1.get("bull"):
        regime = f"ТРЕНД ВВЕРХ — {adx_strength}"
    elif h1.get("bear"):
        regime = f"ТРЕНД ВНИЗ — {adx_strength}"
    else:
        regime = f"БОКОВИК / НЕТ ТРЕНДА — {adx_strength}"

    # ── 1H full data ───────────────────────────────────────────────────────────
    close       = h15.get("close")
    swing_highs = h15.get("swing_highs") or []
    swing_lows  = h15.get("swing_lows")  or []
    swing_high  = swing_highs[-1] if swing_highs else None
    swing_low   = swing_lows[-1]  if swing_lows  else None

    price_pct = None
    if close and swing_high and swing_low:
        rng = swing_high - swing_low
        if rng > 0:
            price_pct = round((close - swing_low) / rng * 100, 1)

    # ── Bot decision ──────────────────────────────────────────────────────────
    reason = bd.get("reason", "—")
    stage  = bd.get("stopped_at_stage", "—")
    decision_text = f"нет сигнала (причина: {reason}, остановлено на: {stage})"
    if bd.get("side"):
        decision_text = f"сигнал {bd['side'].upper()}"

    # ── Build text ────────────────────────────────────────────────────────────
    # ── Volatility label ──────────────────────────────────────────────────────
    atr_pct = h15.get("atr_pct", 50)
    if atr_pct <= 30:
        vol_desc = "низкая (рынок сжался, готовится к движению)"
    elif atr_pct >= 70:
        vol_desc = "высокая (резкие колебания)"
    else:
        vol_desc = "умеренная (нормальный режим)"

    # ── Volume pullback label ──────────────────────────────────────────────────
    vol_ratio_pb = h15.get("vol_ratio_pb", 1.0)
    pb_weak = h15.get("pb_vol_weak", True)
    if pb_weak:
        vol_pb_desc = f"слабый (откат без агрессии, хороший признак для входа по тренду)"
    else:
        vol_pb_desc = f"сильный (агрессивное давление на откате, риск продолжения против тренда)"

    # ── 5m volume label ────────────────────────────────────────────────────────
    vol_strong_5m = h5.get("vol_strong", False)
    vol_ratio_5m  = h5.get("vol_ratio", 1.0)
    vol_5m_desc = f"{'подтверждает движение (выше среднего)' if vol_strong_5m else 'слабый (ниже среднего)'} — соотношение к среднему: {vol_ratio_5m}x"

    # ── DI direction label ────────────────────────────────────────────────────
    plus_di_5m  = h5.get("plus_di", 0)
    minus_di_5m = h5.get("minus_di", 0)
    if plus_di_5m > minus_di_5m * 1.2:
        di_desc = "покупатели сильнее продавцов"
    elif minus_di_5m > plus_di_5m * 1.2:
        di_desc = "продавцы сильнее покупателей"
    else:
        di_desc = "покупатели и продавцы в равновесии"
    di_confirm = "подтверждает направление" if h5.get("di_confirm") else "не подтверждает направление"

    # ── 4H contradiction warning ──────────────────────────────────────────────
    h4_warning = ""
    if h4:
        h4_bull = h4.get("bull", False)
        h4_bear = h4.get("bear", False)
        if h1.get("bull") and h4_bear:
            h4_warning = "⚠️ ВНИМАНИЕ: 4H тренд медвежий, 1H бычий — старший таймфрейм противоречит. Риск выше обычного."
        elif h1.get("bear") and h4_bull:
            h4_warning = "⚠️ ВНИМАНИЕ: 4H тренд бычий, 1H медвежий — старший таймфрейм противоречит. Риск выше обычного."
        elif h4_bull:
            h4_dir = "рост (бычий)"
        elif h4_bear:
            h4_dir = "падение (медвежий)"
        else:
            h4_dir = "боковик"

    lines = [
        f"Пара: {symbol}",
        f"Время анализа: {captured_at}",
        "",
        "=== [КОНТЕКСТ РЕШЕНИЯ] — читай первым ===",
        f"trade_style_hint: {ctx.get('trade_style_hint', 'NO_TRADE')}",
        f"bias_4h: {ctx.get('bias_4h', 'NEUTRAL')}",
        f"bias_1h: {ctx.get('bias_1h', 'NEUTRAL')}",
        f"adx_1h: {ctx.get('adx_1h', 0)}",
        f"adx_4h: {ctx.get('adx_4h', 0)}",
        f"rsi_1h: {ctx.get('rsi_1h', '—')}",
        f"volume_ratio_15m: {ctx.get('volume_ratio_15m', '—')}",
        f"sl_price: {ctx.get('sl_price', 'нет')}",
        f"tp1_price: {ctx.get('tp1_price', 'нет')}",
        f"tp2_price: {ctx.get('tp2_price', 'нет')}",
        "",
        f"РЕЖИМ РЫНКА: {regime}",
        "",
    ]
    if h4:
        h4_adx = h4.get("adx", 0)
        h4_dir_str = "рост (бычий)" if h4.get("bull") else ("падение (медвежий)" if h4.get("bear") else "боковик")
        bb_width_4h  = h4.get("bb_width", 0)
        range_mode   = h4.get("range_mode", False)
        range_label  = "ДА — рынок в боковике (BandWidth < 12%, ADX < 25). Приоритет: range trade у границ." if range_mode else f"нет (BandWidth {bb_width_4h}%)"
        lines += [
            "=== 4-часовой график (старший контекст) ===",
            f"Направление 4H: {h4_dir_str}",
            f"Сила 4H тренда: ADX {h4_adx}",
            f"Средняя быстрая (4H): {h4.get('ema20', '—')}",
            f"Средняя медленная (4H): {h4.get('ema50', '—')}",
            f"Bollinger Bands 4H: нижняя={h4.get('bb_lower','—')}  верхняя={h4.get('bb_upper','—')}  ширина={bb_width_4h}%",
            f"Режим боковика (range mode): {range_label}",
        ]
        if h4_warning:
            lines.append(h4_warning)
        lines.append("")
    lines += [
        "=== Часовой график (общая картина) ===",
        f"Сила тренда: {adx_strength}",
        f"Средняя линия быстрая (1H): {h1.get('ema20','—')}",
        f"Средняя линия медленная (1H): {h1.get('ema50','—')}",
        f"Направление часового тренда: {'рост' if h1.get('bull') else ('падение' if h1.get('bear') else 'боковик')}",
        f"RSI(14) на 1H: {h1.get('rsi', '—')} ({'перекуплен' if (h1.get('rsi') or 0) > 70 else ('перепродан' if (h1.get('rsi') or 0) < 30 else 'нейтральная зона')})",
        "",
        "=== 15-минутный график (точка входа) ===",
        f"Текущая цена: {close}",
        f"Средняя быстрая (15m): {h15.get('ema20','—')}",
        f"Средняя медленная (15m): {h15.get('ema50','—')}",
        f"RSI(14) на 15m: {h15.get('rsi', '—')} ({'перекуплен — осторожно с лонгом' if (h15.get('rsi') or 0) > 70 else ('перепродан — осторожно с шортом' if (h15.get('rsi') or 0) < 30 else 'нейтральная зона')})",
        f"Волатильность: {vol_desc}",
        f"Цена у средней быстрой линии: {'да' if h15.get('near_ema') else 'нет'}",
        f"Паттерн отката сформирован: {'да' if h15.get('structure_ok') else 'нет'}",
        f"Объём на откате: {vol_pb_desc}",
    ]

    if swing_highs:
        lines.append(f"Уровни сопротивления (от ближнего к дальнему): {swing_highs[::-1]}")
    if swing_lows:
        lines.append(f"Уровни поддержки (от ближнего к дальнему): {swing_lows[::-1]}")
    if swing_high and swing_low:
        lines.append(f"Текущий диапазон колебаний: {swing_low} (низ) — {swing_high} (верх)")
        if price_pct is not None:
            lines.append(f"Цена в диапазоне: {price_pct}% от низа к верху (0%=у низа, 100%=у верха)")

    # Bollinger Bands
    bb_upper  = h15.get("bb_upper")
    bb_middle = h15.get("bb_middle")
    bb_lower  = h15.get("bb_lower")
    bb_pct_b  = h15.get("bb_pct_b")
    bb_width  = h15.get("bb_width_pct")
    if bb_upper and bb_lower:
        if bb_pct_b is not None and bb_pct_b <= 20:
            bb_pos = "у нижней границы (зона перепроданности)"
        elif bb_pct_b is not None and bb_pct_b >= 80:
            bb_pos = "у верхней границы (зона перекупленности)"
        else:
            bb_pos = f"в середине полос ({bb_pct_b}%)"
        bb_squeeze = "полосы сужены (рынок сжался, ожидается взрыв)" if bb_width and bb_width < 2.0 else "полосы нормальные"
        lines += [
            f"Bollinger Bands: нижняя={bb_lower}  середина={bb_middle}  верхняя={bb_upper}",
            f"Положение цены в полосах: {bb_pos}",
            f"Ширина полос: {bb_width}% — {bb_squeeze}",
        ]

    # SuperTrend
    st_val  = h15.get("supertrend")
    st_dir  = h15.get("supertrend_dir")
    st_dist = h15.get("supertrend_dist")
    if st_val and st_dir:
        st_dir_ru = "вверх (бычий)" if st_dir == "up" else "вниз (медвежий)"
        lines.append(f"SuperTrend: {st_val} — направление {st_dir_ru}, цена отдалена на {st_dist}%"
                     f" (это уровень разворота тренда — если цена пересечёт его, тренд сменится)")

    # Chandelier Exit — trailing stop level
    ce_long  = h15.get("ce_long")
    ce_short = h15.get("ce_short")
    if ce_long and ce_short:
        lines += [
            f"Chandelier Exit (трейлинг-стоп): лонг={ce_long}  шорт={ce_short}",
            f"(если цена закроет 1H свечу ниже {ce_long} при лонге — трейлинг сработал, выход)",
        ]

    lines += [
        "",
        "=== 5-минутный график (подтверждение) ===",
        f"Текущая цена 5m: {h5.get('trigger_close','—')}",
        f"Пробой ключевого уровня: {'да' if h5.get('breakout') else 'нет'}",
        f"Объём на 5m: {vol_5m_desc}",
        f"Баланс покупателей и продавцов: {di_desc}, {di_confirm}",
        "",
        "=== Итог анализа ===",
        f"{decision_text}",
    ]

    # Invalidation level — выносим явно чтобы LLM не пропустила
    inv_level = pp.get("invalidation") if pp.get("available") else None
    if not inv_level and act.get("valid"):
        inv_level = act.get("sl")
    if inv_level:
        side_word = "ниже" if (h1.get("bull") or act.get("side") == "buy") else "выше"
        lines.append(f"УРОВЕНЬ ОТМЕНЫ СЦЕНАРИЯ: {inv_level} — если цена закроет 15m свечу {side_word} этого уровня, идея теряет смысл")

    # Confirmed trade levels
    if pp.get("available"):
        lines += [
            "",
            f"ПОДТВЕРЖДЁННЫЙ ПЛАН ({pp.get('side', '').upper()}):",
            f"  Зона входа: {pp.get('entry_zone', '—')}",
            f"  Триггер: {pp.get('trigger', '—')}",
            f"  Стоп: {pp.get('sl', '—')}",
            f"  Цель 1: {pp.get('tp1', '—')}",
            f"  Цель 2: {pp.get('tp2', '—')}",
            f"  Инвалидация: {pp.get('invalidation', '—')}",
            f"  R:R: {pp.get('rr', '—')}",
        ]
    elif act.get("valid"):
        lines += [
            "",
            f"АКТИВНЫЙ СИГНАЛ ({act.get('side', '').upper()}):",
            f"  Зона входа: {act.get('entry_zone', '—')}",
            f"  Стоп: {act.get('sl', '—')}",
            f"  Цель 1: {act.get('tp1', '—')}",
            f"  Цель 2: {act.get('tp2', '—')}",
        ]
    else:
        lines += ["", "Подтверждённых уровней для входа НЕТ. Ордера не ставятся."]
        hint = act.get("hint")
        if hint:
            lines.append(f"Наблюдение (не уровень для ордера): {hint}")

    expiry = snapshot.get("expiry_time")
    if expiry:
        lines += ["", f"Актуально до: {expiry}"]

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
