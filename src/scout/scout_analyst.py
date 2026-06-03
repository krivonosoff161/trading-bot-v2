# -*- coding: utf-8 -*-
"""
scout_analyst.py — мозг инфо-эдж сканера (GO / NO-GO, не «купи»).

Принимает новость + контекст слоя → отдаёт структурный вердикт-фильтр.
Использует общий транспорт llm_formatter._call_yandex (Qwen3 через Yandex AI Studio).
НЕ путать с generate_client_text — тот чарт-аналитик продукта (не трогаем).

Ядро (из дизайн-разговора, SCANNER_SPEC.md):
- система = машина-ФИЛЬТР: дефолт = NO-GO / «в цене». Это фича, не баг.
- «известный катализатор ≠ эдж»: толпа видит ту же новость. Эдж = (1) СЮРПРИЗ
  против консенсуса, (2) механика инструмента, (3) red-flag VETO на ловушке.
- честность: направление = сценарий, не гарантия; без обещаний WR; paper-first.

Выход: dict со структурными полями (для журнала) либо None (ошибка/нет ключей).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# repo root на путь, чтобы импортировать src.utils.llm_formatter при прямом запуске
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.utils.llm_formatter import _call_yandex  # noqa: E402


_SYSTEM_PROMPT = """\
Ты — аналитик-ФИЛЬТР информационного сканера рынка. Твоя задача — НЕ «купи/продай»,
а вердикт GO / NO-GO по новостному триггеру. Пишешь на русском, кратко, по делу.

ГЛАВНЫЙ ПРИНЦИП (не нарушать):
- «Известный катализатор ≠ эдж». Если новость публична и датирована — толпа видит её
  тоже, и она УЖЕ в цене. Дефолт вердикта = NO_GO / «в цене». Это правильно.
- Эдж бывает только в трёх местах: (1) СЮРПРИЗ против консенсуса (вышло раньше/крупнее/
  иначе, чем ждали); (2) механика инструмента (ребейз/делистинг/спред); (3) red-flag VETO
  — скам/инсайдер-памп → NO_GO лонг даже если «растёт».
- Лучше честный NO_GO, чем натянутый GO. Система ценна тем, что отсеивает ловушки.

ЧЕСТНОСТЬ:
- Направление = СЦЕНАРИЙ, не гарантия. Никаких процентов успеха / win-rate.
- Всегда давай инвалидацию («сценарий неверен, если …»).
- Это paper-фильтр, не инвест-рекомендация.

Ты получишь: заголовок + текст новости + (опц.) рыночный фон + слой + актив.
Оцени и верни СТРОГО ОДИН JSON-объект (без markdown, без текста вокруг) с полями:
{
  "asset": "тикер или короткое имя (BTC/ETH/нефть/NVDA/…)",
  "catalyst": "суть события одной строкой",
  "in_price": "yes|partial|no — и почему (датировано? публично? ожидаемо?)",
  "red_flag": "none — или конкретный флаг (скам/инсайдер/тонкая ликвидность)",
  "mechanics": "none — или особенность инструмента (ребейз/делист/спред)",
  "surprise": "есть ли сюрприз против консенсуса: yes|no|unknown + кратко",
  "verdict": "GO|NO_GO|WATCH",
  "side": "long|short|none",
  "asymmetry": "где перекос риск/выгода, одной строкой (или 'нет')",
  "invalidation": "сценарий неверен, если …",
  "forecast": "короткий направленный сценарий",
  "horizon_hours": <целое число часов горизонта прогноза>,
  "summary": "2-4 предложения простым языком: что это и почему вердикт такой",
  "levels": {"entry": <число|null>, "invalidation": <число|null>, "target": <число|null>}
}
LEVELS — ТОЛЬКО для verdict=GO: дай числовые цены (entry ~ текущая цена, invalidation = где
сценарий ломается, target = реалистичная цель), привязанные к ТЕКУЩЕЙ ЦЕНЕ из входа. Для
NO_GO/WATCH ставь все три null. Только JSON. Если данных мало — verdict NO_GO/WATCH, скажи почему в summary."""


def _strip_to_json(raw: str) -> dict | None:
    """Достать первый JSON-объект из ответа LLM (на случай обёртки в markdown)."""
    if not raw:
        return None
    # убрать ```json ... ``` обёртки
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    # fallback: первый сбалансированный {...}
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start:i + 1])
                except Exception:
                    return None
    return None


_REQUIRED = ("verdict", "horizon_hours")
_VALID_VERDICTS = {"GO", "NO_GO", "WATCH"}


def _build_user_text(news: dict, layer: int, trigger: str,
                     asset_hint: str | None, market_ctx_line: str | None,
                     price: float | None = None) -> str:
    parts = [
        f"СЛОЙ: {layer}",
        f"ТРИГГЕР: {trigger}",
    ]
    if asset_hint:
        parts.append(f"АКТИВ (предварительно): {asset_hint}")
    if price is not None:
        parts.append(f"ТЕКУЩАЯ ЦЕНА: {price}")
    parts.append("")
    parts.append(f"ЗАГОЛОВОК: {news.get('headline') or news.get('title') or '—'}")
    if news.get("date"):
        parts.append(f"ДАТА НОВОСТИ: {news['date']}")
    body = (news.get("text") or "").strip()
    if body:
        parts.append("")
        parts.append("ТЕКСТ НОВОСТИ:")
        parts.append(body[:4000])
    else:
        parts.append("")
        parts.append("(тело новости не извлечено — оценивай по заголовку, "
                     "понижай уверенность, склоняйся к WATCH/NO_GO)")
    if market_ctx_line:
        parts.append("")
        parts.append(f"РЫНОЧНЫЙ ФОН (контекст риска, НЕ направление): {market_ctx_line}")
    return "\n".join(parts)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def generate_scout_card(
    news: dict,
    layer: int = 1,
    trigger: str = "rss_headline",
    asset_hint: str | None = None,
    market_ctx_line: str | None = None,
    low_confidence: bool = False,
    price: float | None = None,
) -> dict | None:
    """Новость + контекст → структурный вердикт-фильтр (dict) либо None.

    news: {"headline"/"title", "text", "date", "url"}.
    Возвращает поля: asset/catalyst/in_price/red_flag/mechanics/surprise/verdict/
    side/asymmetry/invalidation/forecast/horizon_hours/summary (+ low_confidence).
    """
    user_text = _build_user_text(news, layer, trigger, asset_hint, market_ctx_line, price)
    raw, tokens = await _call_yandex(_SYSTEM_PROMPT, user_text, max_tokens=750)
    if not raw:
        return None
    data = _strip_to_json(raw)
    if not isinstance(data, dict):
        print("scout_analyst: LLM не вернул валидный JSON")
        return None

    # минимальная валидация / нормализация
    verdict = str(data.get("verdict", "")).upper().replace("-", "_").strip()
    if verdict not in _VALID_VERDICTS:
        verdict = "WATCH"
    data["verdict"] = verdict

    try:
        data["horizon_hours"] = int(float(data.get("horizon_hours") or 48))
    except (TypeError, ValueError):
        data["horizon_hours"] = 48

    side = str(data.get("side", "none")).lower().strip()
    data["side"] = side if side in ("long", "short", "none") else "none"

    if low_confidence:
        data["low_confidence"] = True
        # тело не извлеклось → не даём «уверенный» GO
        if data["verdict"] == "GO":
            data["verdict"] = "WATCH"
    else:
        data.setdefault("low_confidence", False)

    # числовые уровни — только для GO (для прорисовки рекомендации на графике)
    lv = data.get("levels") if isinstance(data.get("levels"), dict) else {}
    if data["verdict"] == "GO":
        data["levels"] = {"entry": _num(lv.get("entry")),
                          "invalidation": _num(lv.get("invalidation")),
                          "target": _num(lv.get("target"))}
    else:
        data["levels"] = {"entry": None, "invalidation": None, "target": None}

    data["_tokens"] = tokens
    return data
