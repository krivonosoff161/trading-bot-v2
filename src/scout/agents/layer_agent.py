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


# ── вето vs «нет эджа»: детерминированная классификация флагов ───────────────
# Аудит 11.06: cheap писал «нет конкретики» в red_flags, гейт читал как скам-вето →
# 52% эскалаций, 100% NO_GO. Вето = ТОЛЬКО словарь реальных рисков; остальное = no_edge.
_VETO_VOCAB = (
    # скам/раг/мошенничество
    "scam", "скам", "rug", "раг", "honeypot", "ханипот", "fraud", "мошенн", "ponzi", "понци",
    "пирамид", "malicious", "вредонос",
    # взлом/эксплойт/кража/уязвимость
    "hack", "взлом", "хакер", "exploit", "эксплойт", "уязвим", "vulnerab", "drain", "дрейн",
    "кража", "украден", "stolen", "theft", "хищени",
    # механика контракта
    "mintable", "mint function", "минт", "freeze", "заморозк", "заморож", "frozen",
    "blacklist", "чёрный список", "черный список",
    # манипуляция/инсайдеры
    "манипуляц", "manipulat", "инсайдер", "insider", "wash trad", "вош-трейд",
    # санкции/конфискации/регуляторное преследование/ЧС
    "санкци", "sanction", "конфискац", "seizure", "изъят", "арест", "enforcement",
    "правоприменит", "уголовн", "criminal", "запрет", "ban ", "экстренн", "emergency",
    # инциденты инфраструктуры/платёжеспособности
    "депег", "depeg", "insolven", "несостоятельн", "банкрот", "bankrupt", "делистинг", "delist",
    "halt", "остановка торгов", "остановка вывода", "withdrawal", "вывод средств заморожен",
)
# «отсутствие/слабость» — анти-паттерны: даже если модель положила это в veto_flags, демоутим
_ABSENCE_PAT = (
    "нет ", "нет.", "отсутств", "не указ", "не содерж", "не назван", "не объясн", "без конкрет",
    "без подтвержд", "без деталей", "без новых", "только общ", "обобщ", "общие фразы",
    "no specific", "no concrete", "lack of", "generic", "пересказ", "мнение", "не упомина",
)


def _is_veto(flag: str) -> bool:
    s = str(flag).lower()
    return any(w in s for w in _VETO_VOCAB)


def _is_absence(flag: str) -> bool:
    s = str(flag).lower()
    return any(w in s for w in _ABSENCE_PAT)


def classify_flags(veto_raw, no_edge_raw, legacy_red) -> tuple[list[str], list[str]]:
    """(veto_flags, no_edge_flags): explicit-поля + консервативный разбор legacy red_flags.

    Правила: legacy red_flag = вето ТОЛЬКО при матче словаря (иначе no_edge);
    explicit veto_flag демоутится в no_edge, если это «отсутствие конкретики» без вето-слова."""
    veto: list[str] = []
    no_edge: list[str] = [str(f) for f in (no_edge_raw or [])]
    for f in (veto_raw or []):
        (no_edge if (_is_absence(f) and not _is_veto(f)) else veto).append(str(f))
    for f in (legacy_red or []):
        s = str(f)
        if s in veto or s in no_edge:
            continue
        (veto if _is_veto(s) else no_edge).append(s)
    return veto, no_edge


def split_flags(agent: dict) -> tuple[list[str], list[str]]:
    """Флаги агента → (veto, no_edge). Старые ответы (только red_flags) — через словарь;
    explicit-списки тоже проходят демоут absence-текста (защита от дрейфа промпта)."""
    if "veto_flags" in agent or "no_edge_flags" in agent:
        return classify_flags(agent.get("veto_flags"), agent.get("no_edge_flags"), None)
    return classify_flags([], [], agent.get("red_flags"))


_L5_PREIPO_ACTION_TERMS = (
    "spcx", "xstocks", "tokenized", "tokenised", "pre-ipo", "public debut",
    "market debut", "trading debut", "nasdaq debut", "historic debut",
    "largest ipo", "prices largest ipo", "priced", "prices", "allocation",
    "allocations", "underwriter", "synthetic price", "premium", "derivative",
    "derivatives", "refund", "refunds", "scrap", "scraps", "cancel",
    "cancels", "surges", "surged", "whale", "opens", "long",
)
_L5_PREIPO_CONTEXT_TERMS = (
    "what does it mean", "how will", "bull and bear case", "is it a good investment",
    "price analysis", "prediction", "opinion", "guide",
)


def _apply_l5_preipo_guard(data: dict, event: dict, layer: int) -> dict:
    """Protect L5 pre-IPO/perp market mechanics from being discarded as generic IPO chatter."""
    if int(layer or 0) != 5:
        return data
    headline = str(event.get("headline") or "")
    low = headline.lower()
    if not any(term in low for term in _L5_PREIPO_ACTION_TERMS):
        return data
    # Generic explainers stay weak unless they also contain concrete market mechanics.
    if any(term in low for term in _L5_PREIPO_CONTEXT_TERMS) and not any(
        term in low for term in ("spcx", "premium", "allocation", "priced", "surges", "$", "%")
    ):
        return data

    out = dict(data)
    out["event_type"] = out.get("event_type") or "pre_ipo_market_mechanics"
    if str(out.get("event_type") or "unknown").lower() in ("unknown", "none", "unclassified"):
        out["event_type"] = "pre_ipo_market_mechanics"
    out["materiality"] = max(_num(out.get("materiality")), 0.65)
    out["confidence"] = max(_num(out.get("confidence")), 0.55)
    out["pre_verdict"] = "WATCH_CANDIDATE"
    out["should_escalate"] = True
    out["escalation_reason"] = (
        out.get("escalation_reason")
        or "L5 pre-IPO/tokenized market mechanics with concrete price/access/allocation facts"
    )
    out["trigger_text"] = out.get("trigger_text") or headline[:240]
    if str(out.get("phase") or "").lower() not in ("expected", "realized"):
        if any(term in low for term in ("surges", "surged", "priced", "prices", "debut", "refund", "cancel", "scrap")):
            out["phase"] = "realized"
    if str(out.get("direction") or "").lower() not in ("long", "short", "mixed"):
        out["direction"] = "mixed"
    facts = list(out.get("key_facts") or [])
    if not facts:
        facts.append(headline[:240])
    out["key_facts"] = facts
    return out


async def analyze(event: dict, layer: int, asset: str | None = None) -> dict:
    """Событие + слой → структурные факты (dict) + _usage. На ошибке — пустой материальный 0."""
    cfg = _cfg()
    li = (cfg.get("layers", {}) or {}).get(layer) or (cfg.get("layers", {}) or {}).get(str(layer)) \
        or {"name": f"L{layer}", "focus": ""}
    system = cfg["base"].replace("{layer_name}", li.get("name", f"L{layer}"))
    system += "\nФОКУС СЛОЯ (что материально): " + li.get("focus", "")

    parts = [f"АКТИВ (предв.): {asset}" if asset else "", f"ЗАГОЛОВОК: {event.get('headline', '')}"]
    if event.get("asset_class"):
        parts.append(f"ТИП АКТИВА: {event['asset_class']}")
    if event.get("trigger_role"):
        parts.append(f"РОЛЬ ТРИГГЕРА: {event['trigger_role']}")
    if event.get("identity_reason"):
        parts.append(f"ПРИЧИНА ИДЕНТИФИКАЦИИ: {event['identity_reason']}")
    if event.get("date"):
        parts.append(f"ДАТА: {event['date']}")
    body = (event.get("text") or "").strip()
    if body:
        parts.append("ТЕКСТ: " + body[:2500])
    if event.get("context_package"):
        parts.append(f"КОНТЕКСТ ИЗ ИСТОЧНИКОВ: {event['context_package']}")
    user = "\n".join(p for p in parts if p)

    raw, usage = await llm_client.call("cheap", system, user, json_mode=True, max_tokens=700)
    data = _parse(raw) or {}
    data = _apply_l5_preipo_guard(data, event, layer)

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

    veto_flags, no_edge_flags = classify_flags(
        data.get("veto_flags"), data.get("no_edge_flags"), data.get("red_flags"))

    return {
        "asset": data.get("asset") or asset,
        "event_type": str(data.get("event_type") or "unknown")[:40],
        "phase": phase,
        "direction": direction,
        "materiality": _num(data.get("materiality")),
        "confidence": _num(data.get("confidence")),
        "key_facts": data.get("key_facts") or [],
        "numbers": data.get("numbers") or [],
        "red_flags": data.get("red_flags") or [],   # сырой ответ модели (backward compat)
        "veto_flags": veto_flags,                   # риск-вето → эскалация
        "no_edge_flags": no_edge_flags,             # слабость новости → НЕ эскалация
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
