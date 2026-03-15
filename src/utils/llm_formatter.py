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
_MODEL_URI  = f"gpt://{_FOLDER_ID}/gemma-3-27b-it/latest"
_MAX_TOKENS = 900
_TIMEOUT    = 60  # seconds

_SYSTEM_PROMPT = """\
Ты — аналитик крипторынка. Пишешь клиенту короткий разбор на русском языке.
Клиент — обычный человек, не технический специалист. Он хочет понять: что происходит и что делать.

ГЛАВНОЕ ПРАВИЛО ЯЗЫКА:
Переводи технические данные в человеческий смысл. Не пересказывай цифры — объясняй что они означают.

ЗАПРЕЩЕНО писать:
- названия индикаторов: ADX, DI, EMA, ATR, ratio, перцентиль — клиент не знает что это
- точные значения индикаторов в скобках: "(ADX=21.56)", "(ratio 2.23)", "(EMA20 86.885014)"
- фразы типа: "-DI превосходит +DI", "перцентиль ATR 2.0 из 100", "DI подтверждение отсутствует"
- "ADX вырастет до 20" — запрещено. Замени на: "рынок начнёт уверенно двигаться в одну сторону"
- "EMA20", "EMA50" — запрещено. Замени на: "средняя линия", "уровень поддержки/сопротивления"

РАЗРЕШЕНО и НУЖНО:
- "рынок движется вниз, но тренд пока слабый" вместо "ADX=21, -DI > +DI"
- "цена зависла в середине диапазона" вместо "позиция 50% от swing low до swing high"
- "объём на последних свечах низкий" вместо "vol ratio 1.03"
- "сильное давление продавцов" вместо "-DI доминирует"
- "волатильность низкая, рынок сжался" вместо "ATR перцентиль 2 из 100"
- конкретные ЦЕНЫ для входа/стопа/цели — их писать обязательно если есть

Правила:
- Без приветствий. Без первого лица. Без слов "бот", "система".
- Длина: 100–160 слов. Коротко и по делу.
- Структура (пиши свободно, без заголовков):
  1. Что сейчас на рынке — простыми словами
  2. Что делать: ждать, смотреть на вход, или не интересно
  3. Если есть уровни — написать конкретные цены входа, стопа, цели
  4. Что сломает сценарий (инвалидация)
- Направление: ЛОНГ или ШОРТ — не "покупка/продажа"
- Если уровней нет — объясни что именно ждём, без цен
- Если тренд слабый (ADX 20-25) — предупреди: "сигнал слабый, возможен разворот"
- В конце одна строка: "Актуально до: HH:MM UTC"

Режимы рынка:
- Тренд вверх → ищи ЛОНГ на откате, опиши где ждать
- Тренд вниз → ищи ШОРТ на откате, опиши где ждать
- Боковик → опиши диапазон словами (верхняя/нижняя граница с ценами)
  Цена у нижней границы (<25%) → возможен ЛОНГ к верхней границе
  Цена у верхней границы (>75%) → возможен ШОРТ к нижней границе
  Цена в середине → ждать подхода к границе
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_analysis_text(symbol: str, captured_at: str, snapshot: dict) -> str:
    """Structured snapshot summary for LLM — full data for all three market regimes."""
    h1  = snapshot.get("1h",  {})
    h15 = snapshot.get("15m", {})
    h5  = snapshot.get("5m",  {})
    bd  = snapshot.get("bot_decision", {})
    pp  = snapshot.get("pending_plan", {})
    act = snapshot.get("action", {})

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

    lines = [
        f"Пара: {symbol}",
        f"Время анализа: {captured_at}",
        "",
        f"РЕЖИМ РЫНКА: {regime}",
        "",
        "=== Часовой график (общая картина) ===",
        f"Сила тренда: {adx_strength}",
        f"Средняя линия быстрая (1H): {h1.get('ema20','—')}",
        f"Средняя линия медленная (1H): {h1.get('ema50','—')}",
        f"Направление часового тренда: {'рост' if h1.get('bull') else ('падение' if h1.get('bear') else 'боковик')}",
        "",
        "=== 15-минутный график (точка входа) ===",
        f"Текущая цена: {close}",
        f"Средняя быстрая (15m): {h15.get('ema20','—')}",
        f"Средняя медленная (15m): {h15.get('ema50','—')}",
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

    # Build user message content
    content: list[dict] = [{"type": "text", "text": analysis_text}]

    # Attach chart image if available (original chart, not annotated)
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
