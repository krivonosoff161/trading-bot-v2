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

Правила:
- Опирайся ТОЛЬКО на структурированные данные из JSON. Изображение — визуальный контекст, не источник решений.
- Тон: деловой, конкретный. Без приветствий ("Привет!", "Добрый день"). Без первого лица ("я считаю", "я согласен").
- Не используй слова "бот", "ботовская", "система" — просто описывай ситуацию от своего имени как аналитик.
- Длина: 120–200 слов. Коротко и по делу.
- Структура (без заголовков-капслока, пиши свободно):
  1. Что сейчас происходит на рынке (тренд/боковик, волатильность)
  2. Что делать: ждать, готовиться к входу, или рынок не интересен
  3. Конкретные уровни ТОЛЬКО если они есть в JSON (entry_zone, trigger, sl, tp1, tp2) — пиши числа
  4. Когда идея теряет смысл (инвалидация)
- Направление сделки: всегда пиши ЛОНГ или ШОРТ, не "покупка"/"продажа".
- Если в данных написано "Подтверждённых уровней для входа НЕТ" — не предлагай лимитные ордера вообще. Никаких конкретных цен для входа. Объясни что ждём.
- Если есть ПОДТВЕРЖДЁННЫЙ ПЛАН или АКТИВНЫЙ СИГНАЛ с entry_zone/trigger — тогда можно написать что трейдер может выставить лимитный ордер на уровне триггера заранее.
- Инвалидация: пиши что именно сломает сценарий (пробой уровня, разворот), а не что его подтвердит.
- Никогда не давай гарантий. Никогда не пиши "советую" или "рекомендую купить/продать".
- В конце одна строка: "Актуально до: HH:MM UTC" — возьми из поля expiry_time.
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_analysis_text(symbol: str, captured_at: str, snapshot: dict) -> str:
    """Structured snapshot summary for LLM — only confirmed data, no raw dicts."""
    h1  = snapshot.get("1h",  {})
    h15 = snapshot.get("15m", {})
    bd  = snapshot.get("bot_decision", {})
    pp  = snapshot.get("pending_plan", {})
    act = snapshot.get("action", {})

    # 1H trend status
    if h1.get("bull"):
        trend_1h = f"бычий (ADX={h1.get('adx', '—')}, +DI={h1.get('plus_di', '—')}, -DI={h1.get('minus_di', '—')})"
    elif h1.get("bear"):
        trend_1h = f"медвежий (ADX={h1.get('adx', '—')}, +DI={h1.get('plus_di', '—')}, -DI={h1.get('minus_di', '—')})"
    else:
        trend_1h = f"нет тренда (ADX={h1.get('adx', '—')})"

    # 15m structure
    structure = "есть" if h15.get("structure_ok") else "нет"
    near_ema  = "да"   if h15.get("near_ema")     else "нет"

    # Bot decision in plain text
    reason = bd.get("reason", "—")
    stage  = bd.get("stopped_at_stage", "—")
    decision_text = f"нет сигнала (причина: {reason}, остановлено на: {stage})"
    if bd.get("side"):
        decision_text = f"сигнал {bd['side'].upper()}"

    lines = [
        f"Пара: {symbol}",
        f"Время: {captured_at}",
        "",
        f"1H тренд: {trend_1h}",
        f"15m структура: {structure}",
        f"15m цена у EMA20: {near_ema}",
        f"15m ATR режим: {h15.get('atr_label', '—')}",
        "",
        f"Решение: {decision_text}",
    ]

    # Confirmed trade levels — ONLY if pending_plan is available
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
        lines += [
            "",
            "Подтверждённых уровней для входа НЕТ. Ордера не ставятся.",
        ]
        hint = act.get("hint")
        if hint:
            lines += [f"Наблюдение (не уровень для ордера): {hint}"]

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
