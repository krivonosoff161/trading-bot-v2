# -*- coding: utf-8 -*-
"""
chief.py — главный судья (мощная модель, role=chief). Зовётся РЕДКО (только кандидаты).

Получает событие + факты слой-агента → финальный вердикт GO/NO_GO/WATCH + СТОРОНА long/short/none
+ инвалидация + горизонт + асимметрия + сюрприз. Принцип: «известный катализатор ≠ эдж»
(в цене → NO_GO); эдж = сюрприз vs консенсус / механика / red-flag. ОБЕ стороны равноправны.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.utils import llm_client  # noqa: E402

_SYSTEM = """\
Ты — ГЛАВНЫЙ СУДЬЯ инфо-эдж сканера рынка. Получаешь событие + факты, извлечённые слой-агентом.
Твоя задача — финальный вердикт (GO/NO_GO/WATCH), НЕ пересказ.

ГДЕ ЭДЖ: (1) СЮРПРИЗ vs консенсус/ожидание, (2) механика инструмента (анлок/листинг/сквиз/
делеверидж/rebase), (3) red-flag VETO, (4) раннее материальное событие, которое рынок ещё
НЕ УСПЕЛ переварить (мелкие альты/неликвид перевариваются часами-днями, мажоры — минутами).

КАЛИБРОВКА (не нарушать):
- «Новость публичная» САМА ПО СЕБЕ не значит «в цене». in_price=yes пиши ТОЛЬКО при конкретном
  факте отыгрыша: цена уже сходила на событии / событие старое / давно анонсировано и ожидаемо.
  Нет такого факта → partial или unknown, не yes.
- NO_GO требует КОНКРЕТНОЙ причины из списка: уже отыграно (чем видно) · нет асимметрии для
  актива · red-flag · событие нематериально для ЭТОГО слоя · нет данных. Шаблон «известный
  катализатор уже в цене» без факта — запрещён.
- WATCH — событие материально/развивается, но подтверждения нет: в forecast напиши ЧТО ПОДТВЕРДИТ
  сценарий, в invalidation — что его отменит. Не прячь развивающийся кейс в NO_GO.
- GO — ранний/механический/сюрприз-кейс с понятной асимметрией. ОБЕ стороны равноправны:
  бычье → long; медвежье → short; нет асимметрии → none.
- Запаздывающее ценовое движение (цена уже сходила) = НЕ эдж (поздно гнаться в любую сторону).
- summary: 2-4 предложения КОНКРЕТНО про это событие и этот слой (что нового, какой механизм,
  почему важно/неважно именно здесь). Общие фразы, применимые к любой новости, — запрещены.
- Честность: направление = сценарий, не гарантия; без обещаний %. Это paper-фильтр.
- Все текстовые поля пиши на русском языке. Английский оставляй только для тикеров, названий
  компаний/продуктов, URL и общепринятых терминов вроде ETF/SEC/API.

Верни СТРОГО один JSON (слово JSON обязательно):
{"verdict":"GO|NO_GO|WATCH","side":"long|short|none","in_price":"yes|partial|no|unknown",
 "surprise":"none|timing|magnitude|direction|unknown","asymmetry":"кратко","invalidation":"сценарий неверен если…",
 "forecast":"короткий сценарий","horizon_hours":<int>,"confidence":0.0,
 "levels":{"entry":<num|null>,"invalidation":<num|null>,"target":<num|null>},
 "summary":"2-4 предложения простым языком","journal_reason":"почему такой вердикт (для журнала)"}
levels — только для GO (числа привязать к текущей цене). Для NO_GO/WATCH = null."""


def _parse(raw: str) -> dict | None:
    if not raw:
        return None
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
    try:
        return json.loads(raw)
    except Exception:
        i = raw.find("{")
        if i == -1:
            return None
        depth = 0
        for j in range(i, len(raw)):
            depth += (raw[j] == "{") - (raw[j] == "}")
            if depth == 0:
                try:
                    return json.loads(raw[i:j + 1])
                except Exception:
                    return None
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def decide(event: dict, agent: dict, price: float | None, market_ctx: str | None = None) -> dict | None:
    """Событие + факты агента + цена → финальный вердикт (dict) либо None."""
    parts = [
        f"АКТИВ: {agent.get('asset') or event.get('asset')}",
        f"ЗАГОЛОВОК: {event.get('headline', '')}",
        f"ТЕКУЩАЯ ЦЕНА: {price}" if price is not None else "",
        "",
        "ФАКТЫ СЛОЙ-АГЕНТА (извлечены, не финал):",
        f"  тип={agent.get('event_type')} · фаза={agent.get('phase')} · направление-намёк={agent.get('direction')}",
        f"  материальность={agent.get('materiality')} · уверенность={agent.get('confidence')}",
        f"  факты={agent.get('key_facts')}",
        f"  числа={agent.get('numbers')}",
        f"  veto_flags={agent.get('veto_flags') if agent.get('veto_flags') is not None else agent.get('red_flags')}"
        f" · слабости={agent.get('no_edge_flags') or []} · механика={agent.get('mechanics')}",
    ]
    if event.get("asset_class"):
        parts.append(f"\nТИП АКТИВА: {event['asset_class']}")
    if event.get("trigger_role"):
        parts.append(f"РОЛЬ ТРИГГЕРА: {event['trigger_role']}")
    if event.get("context_package"):
        parts.append(f"КОНТЕКСТ ИЗ ИСТОЧНИКОВ: {event['context_package']}")
    if market_ctx:
        parts += ["", f"РЫНОЧНЫЙ ФОН (контекст риска): {market_ctx}"]
    body = (event.get("text") or "").strip()
    if body:
        parts += ["", "ТЕКСТ (сжато): " + body[:1500]]

    raw, usage = await llm_client.call("chief", _SYSTEM, "\n".join(p for p in parts if p),
                                       json_mode=True, max_tokens=750)
    data = _parse(raw)
    if not isinstance(data, dict):
        if usage.get("status") == "budget_skipped":
            return {"_error": "budget_skipped", "_usage": usage}
        return None

    verdict = str(data.get("verdict", "")).upper().replace("-", "_").strip()
    if verdict not in ("GO", "NO_GO", "WATCH"):
        verdict = "WATCH"
    side = str(data.get("side", "none")).lower().strip()
    if side not in ("long", "short", "none"):
        side = "none"
    try:
        horizon = int(float(data.get("horizon_hours") or 48))
    except (TypeError, ValueError):
        horizon = 48
    lv = data.get("levels") if isinstance(data.get("levels"), dict) else {}
    levels = ({"entry": _num(lv.get("entry")), "invalidation": _num(lv.get("invalidation")),
               "target": _num(lv.get("target"))} if verdict == "GO"
              else {"entry": None, "invalidation": None, "target": None})

    return {
        "verdict": verdict, "side": side,
        "in_price": str(data.get("in_price") or "unknown"),
        "surprise": str(data.get("surprise") or "unknown"),
        "asymmetry": str(data.get("asymmetry") or ""),
        "invalidation": str(data.get("invalidation") or ""),
        "forecast": str(data.get("forecast") or ""),
        "horizon_hours": horizon,
        "confidence": (_num(data.get("confidence")) or 0.0),
        "levels": levels,
        "summary": str(data.get("summary") or ""),
        "journal_reason": str(data.get("journal_reason") or ""),
        "_usage": usage,
    }
