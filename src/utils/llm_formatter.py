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

    # ── Market regime ──────────────────────────────────────────────────────────
    if h1.get("bull"):
        regime = "ТРЕНД ВВЕРХ"
    elif h1.get("bear"):
        regime = "ТРЕНД ВНИЗ"
    else:
        regime = "БОКОВИК / НЕТ ТРЕНДА"

    # ── Current price and swing levels ────────────────────────────────────────
    close       = h15.get("close")
    ema20_15m   = h15.get("ema20")
    ema50_15m   = h15.get("ema50")
    swing_highs = h15.get("swing_highs") or []
    swing_lows  = h15.get("swing_lows")  or []
    swing_high  = swing_highs[-1] if swing_highs else None
    swing_low   = swing_lows[-1]  if swing_lows  else None

    price_pct = None
    if close and swing_high and swing_low:
        rng = swing_high - swing_low
        if rng > 0:
            price_pct = round((close - swing_low) / rng * 100, 1)

    # ── Volatility ─────────────────────────────────────────────────────────────
    atr_label = h15.get("atr_label", "—")
    atr_pct   = h15.get("atr_pct")

    # ── Volume context (15m pullback) ──────────────────────────────────────────
    vol_recent   = h15.get("vol_recent")
    vol_prior    = h15.get("vol_prior")
    vol_ratio_pb = h15.get("vol_ratio_pb")
    pb_vol_weak  = h15.get("pb_vol_weak")

    # ── 5m confirmation ───────────────────────────────────────────────────────
    breakout_5m   = h5.get("breakout")
    vol_strong_5m = h5.get("vol_strong")
    vol_ratio_5m  = h5.get("vol_ratio")
    di_confirm_5m = h5.get("di_confirm")
    plus_di_5m    = h5.get("plus_di")
    minus_di_5m   = h5.get("minus_di")

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
        f"1H: ADX={h1.get('adx', '—')}, +DI={h1.get('plus_di', '—')}, -DI={h1.get('minus_di', '—')}",
        "",
    ]

    # Price context
    if close:
        lines.append(f"Текущая цена: {close}")
    if ema20_15m:
        lines.append(f"EMA20 (15m): {ema20_15m}")
    if ema50_15m:
        lines.append(f"EMA50 (15m): {ema50_15m}")
    if swing_high and swing_low:
        lines.append(f"Swing High (ближайший максимум): {swing_high}")
        lines.append(f"Swing Low  (ближайший минимум): {swing_low}")
        if price_pct is not None:
            lines.append(f"Цена в диапазоне: {price_pct}% от низа к верху")
    lines.append("")

    # Volatility — atr_pct is ATR percentile vs last 50 bars (0–100), not % of price
    atr_line = f"Волатильность 15m: {atr_label}"
    if atr_pct is not None:
        atr_line += f" (перцентиль ATR: {atr_pct} из 100)"
    lines.append(atr_line)

    # Volume 15m
    if vol_ratio_pb is not None:
        desc = f"Объём пулбэка (15m): ratio={vol_ratio_pb}"
        if vol_recent is not None and vol_prior is not None:
            desc += f" (недавний={round(vol_recent)}, норма={round(vol_prior)})"
        if pb_vol_weak is not None:
            desc += f" — {'слабый (норм. для отката)' if pb_vol_weak else 'сильный (агрессивный откат)'}"
        lines.append(desc)

    lines.append("")

    # 15m structure
    structure = "есть" if h15.get("structure_ok") else "нет"
    near_ema  = "да"   if h15.get("near_ema")     else "нет"
    lines += [
        f"15m структура для входа: {structure}",
        f"15m цена у EMA20: {near_ema}",
    ]

    # 5m confirmation
    if any(v is not None for v in [breakout_5m, vol_strong_5m, di_confirm_5m]):
        lines += ["", "5m подтверждение:"]
        if breakout_5m is not None:
            lines.append(f"  пробой уровня: {'да' if breakout_5m else 'нет'}")
        if vol_strong_5m is not None:
            ratio_str = f" (×{vol_ratio_5m})" if vol_ratio_5m is not None else ""
            lines.append(f"  сильный объём: {'да' if vol_strong_5m else 'нет'}{ratio_str}")
        if di_confirm_5m is not None:
            di_str = ""
            if plus_di_5m is not None and minus_di_5m is not None:
                di_str = f" (+DI={plus_di_5m}, -DI={minus_di_5m})"
            lines.append(f"  DI подтверждение: {'да' if di_confirm_5m else 'нет'}{di_str}")

    lines += ["", f"Решение: {decision_text}"]

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
