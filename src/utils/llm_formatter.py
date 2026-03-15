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
Ты — аналитик крипторынка. Пишешь клиенту короткий разбор торговой ситуации на русском языке.

Правила формата:
- Опирайся ТОЛЬКО на структурированные данные из JSON. Изображение — визуальный контекст, не источник решений.
- Тон: деловой, конкретный. Без приветствий ("Привет!", "Добрый день"). Без первого лица ("я считаю", "я согласен").
- Не используй слова "бот", "ботовская", "система" — просто описывай ситуацию от своего имени как аналитик.
- Длина: 130–210 слов. Коротко и по делу.
- Структура (без заголовков-капслока, пиши свободно):
  1. Что сейчас происходит на рынке (режим, волатильность)
  2. Что делать: ждать, готовиться к входу, или рынок не интересен
  3. Конкретные уровни ТОЛЬКО если они есть в JSON — пиши числа
  4. Когда идея теряет смысл (инвалидация)
- Направление сделки: всегда пиши ЛОНГ или ШОРТ, не "покупка"/"продажа".
- Если в данных написано "Подтверждённых уровней для входа НЕТ" — не предлагай лимитные ордера вообще. Никаких конкретных цен для входа. Объясни что ждём.
- Если есть ПОДТВЕРЖДЁННЫЙ ПЛАН или АКТИВНЫЙ СИГНАЛ с entry_zone/trigger — можно написать что трейдер может выставить лимитный ордер на уровне триггера заранее.
- Инвалидация: пиши что именно сломает сценарий (пробой уровня, разворот), а не что его подтвердит.
- Никогда не давай гарантий. Никогда не пиши "советую" или "рекомендую купить/продать".
- В конце одна строка: "Актуально до: HH:MM UTC" — возьми из поля expiry_time.

Уверенность сигнала (смотри ADX в поле РЕЖИМ РЫНКА):
- ADX < 20: тренда нет, трендовые входы не предлагать
- ADX 20-25: тренд слабый, не подтверждён — обязательно предупредить: "сигнал слабый, высок риск разворота"
- ADX 25-30: тренд есть, можно описывать вход но с осторожностью
- ADX ≥ 30: тренд сильный, уверенное описание

Инструкции по режиму рынка (смотри поле РЕЖИМ РЫНКА):
- ТРЕНД ВВЕРХ: ищи точку входа в ЛОНГ на откате к EMA20 или EMA50. Опиши где ждать откат и при каком условии входить.
- ТРЕНД ВНИЗ: ищи точку входа в ШОРТ на откате к EMA20 или EMA50. Опиши где ждать откат.
- БОКОВИК / НЕТ ТРЕНДА: не рекомендуй трендовые входы. Опиши диапазон (swing high / swing low как границы).
  Если цена в диапазоне менее 25% от низа — можно рассматривать ЛОНГ к середине или верху диапазона.
  Если цена более 75% от низа к верху — можно рассматривать ШОРТ к середине или низу диапазона.
  Если цена в середине (25–75%) — ждать подхода к границе.
  TP в боковике = противоположная граница диапазона. SL = за границей с запасом.
  Инвалидация в боковике = закрытие свечи за границей диапазона.
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
    lines = [
        f"Пара: {symbol}",
        f"Время: {captured_at}",
        "",
        f"РЕЖИМ РЫНКА: {regime}",
        "",
        "=== 1H (часовой таймфрейм) ===",
        f"ADX={h1.get('adx','—')}  +DI={h1.get('plus_di','—')}  -DI={h1.get('minus_di','—')}",
        f"EMA20={h1.get('ema20','—')}  EMA50={h1.get('ema50','—')}",
        f"Тренд: {'бычий' if h1.get('bull') else ('медвежий' if h1.get('bear') else 'нет')}",
        "",
        "=== 15m (основной таймфрейм) ===",
        f"Цена (close): {close}",
        f"EMA20={h15.get('ema20','—')}  EMA50={h15.get('ema50','—')}",
        f"ATR={h15.get('atr','—')}  ATR перцентиль={h15.get('atr_pct','—')}/100  режим={h15.get('atr_label','—')}",
        f"Структура для входа: {'есть' if h15.get('structure_ok') else 'нет'}",
        f"Цена у EMA20: {'да' if h15.get('near_ema') else 'нет'}",
        f"Объём пулбэка: недавний={h15.get('vol_recent','—')}  норма={h15.get('vol_prior','—')}  "
        f"ratio={h15.get('vol_ratio_pb','—')}  слабый={'да' if h15.get('pb_vol_weak') else 'нет'}",
    ]

    if swing_highs:
        lines.append(f"Swing Highs (сопротивления, от новых к старым): {swing_highs[::-1]}")
    if swing_lows:
        lines.append(f"Swing Lows  (поддержки, от новых к старым): {swing_lows[::-1]}")
    if swing_high and swing_low:
        lines.append(f"Ближайший диапазон: {swing_low} — {swing_high}")
        if price_pct is not None:
            lines.append(f"Позиция цены в диапазоне: {price_pct}% от низа к верху")

    lines += [
        "",
        "=== 5m (подтверждение входа) ===",
        f"Цена 5m: {h5.get('trigger_close','—')}",
        f"Пробой уровня: {'да' if h5.get('breakout') else 'нет'}",
        f"Объём 5m: {h5.get('trigger_vol','—')}  SMA объёма={h5.get('vol_sma','—')}  "
        f"ratio={h5.get('vol_ratio','—')}  сильный={'да' if h5.get('vol_strong') else 'нет'}",
        f"+DI={h5.get('plus_di','—')}  -DI={h5.get('minus_di','—')}  DI подтверждение={'да' if h5.get('di_confirm') else 'нет'}",
        "",
        "=== Решение системы ===",
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
