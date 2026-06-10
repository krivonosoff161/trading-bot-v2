# -*- coding: utf-8 -*-
"""
layer_agent.py — дешёвый слой-агент (один класс, 5 промптов-фокусов из конфига).

Читает нормализованное событие → ИЗВЛЕКАЕТ факты по своему слою (тип/направление/числа/
red-flag/материальность). НЕ принимает финальное торговое решение — это chief.
Зовётся ОДИН профильный агент на новость (роутер уже отнёс в слой). Дешёвая модель (role=cheap).
"""
from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402

from src.utils import llm_client  # noqa: E402

_CFG = Path(__file__).resolve().parents[1] / "config" / "layer_agents.yaml"


@lru_cache(maxsize=1)
def _cfg() -> dict:
    return yaml.safe_load(_CFG.read_text(encoding="utf-8"))


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


def _num(v, default=0.0):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


async def analyze(event: dict, layer: int, asset: str | None = None) -> dict:
    """Событие + слой → структурные факты (dict) + _usage. На ошибке — пустой материальный 0."""
    cfg = _cfg()
    li = (cfg.get("layers", {}) or {}).get(layer) or (cfg.get("layers", {}) or {}).get(str(layer)) \
        or {"name": f"L{layer}", "focus": ""}
    system = cfg["base"].replace("{layer_name}", li.get("name", f"L{layer}"))
    system += "\nФОКУС СЛОЯ (что материально): " + li.get("focus", "")

    parts = [f"АКТИВ (предв.): {asset}" if asset else "", f"ЗАГОЛОВОК: {event.get('headline', '')}"]
    if event.get("date"):
        parts.append(f"ДАТА: {event['date']}")
    body = (event.get("text") or "").strip()
    if body:
        parts.append("ТЕКСТ: " + body[:2500])
    user = "\n".join(p for p in parts if p)

    raw, usage = await llm_client.call("cheap", system, user, json_mode=True, max_tokens=700)
    data = _parse(raw) or {}

    direction = str(data.get("direction", "none")).lower().strip()
    if direction not in ("long", "short", "none", "mixed"):
        direction = "none"
    phase = str(data.get("phase", "ambiguous")).lower().strip()
    if phase not in ("expected", "realized", "context", "ambiguous"):
        phase = "ambiguous"
    pre_verdict = str(data.get("pre_verdict") or "").upper().replace("-", "_").strip()
    if pre_verdict not in ("DROP", "JOURNAL_NO_GO", "WATCH_CANDIDATE", "GO_CANDIDATE"):
        pre_verdict = ""        # пусто → оркестратор применит консервативный fallback-маппинг
    try:
        horizon = int(float(data.get("suggested_horizon_hours")))
        horizon = horizon if 1 <= horizon <= 720 else None
    except (TypeError, ValueError):
        horizon = None

    return {
        "asset": data.get("asset") or asset,
        "event_type": str(data.get("event_type") or "unknown")[:40],
        "phase": phase,
        "direction": direction,
        "materiality": _num(data.get("materiality")),
        "confidence": _num(data.get("confidence")),
        "key_facts": data.get("key_facts") or [],
        "numbers": data.get("numbers") or [],
        "red_flags": data.get("red_flags") or [],
        "mechanics": data.get("mechanics") or [],
        "pre_verdict": pre_verdict,
        "should_escalate": bool(data.get("should_escalate")),
        "escalation_reason": str(data.get("escalation_reason") or "")[:300],
        "no_go_reason": str(data.get("no_go_reason") or "")[:300],
        "trigger_text": str(data.get("trigger_text") or "")[:300],
        "suggested_horizon_hours": horizon,
        "reason_to_escalate": str(data.get("reason_to_escalate") or "")[:300],
        "_usage": usage,
        "_ok": bool(raw),
    }
