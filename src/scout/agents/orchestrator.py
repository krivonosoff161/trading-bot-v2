# -*- coding: utf-8 -*-
"""
orchestrator.py — оркестратор (обычный Python-код + правила, НЕ LLM).

Поток на одно событие: дешёвый слой-агент даёт факты + ПРЕДВАРИТЕЛЬНЫЙ вердикт →
кодовый гейт decide_escalation решает:
  • trash   → мусор, дропаем (chief не зовём, токены не тратим);
  • journal → дешёвый NO_GO в журнал (датасет), chief НЕ зовём, в канал НЕ шлём;
  • chief   → зовём мощную модель → финальный вердикт + сторона.

Гейт ЯВНЫЙ (escalation_gate) и пишется в reasoning/журнал — видно, за что платим chief.
Голая materiality>=0.65 на lagging/агрегаторах больше НЕ эскалирует (аудит 10.06:
128 chief-вызовов / 315k токенов по этой причине → 99% NO_GO).
Защищено от пере-подавления (по NO_GO аудиту): flow-источники (dexscreener),
veto-флаги, LEADING — эскалируются всегда/легко.

Аудит 11.06: эскалирует ТОЛЬКО veto_flags (словарь реальных рисков, layer_agent.split_flags);
no_edge_flags («нет конкретики/общий анализ») — НЕ эскалация (было 52% вызовов chief, 100% NO_GO).
Падение chief на кандидате → chief_error/retry_status (ретрай в scanner_v0), не финальный NO_GO.
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402

from src.scout.agents import chief as chief_mod      # noqa: E402
from src.scout.agents import layer_agent             # noqa: E402

_CFG = Path(__file__).resolve().parents[1] / "config" / "layer_agents.yaml"

PRE_VERDICTS = ("DROP", "JOURNAL_NO_GO", "WATCH_CANDIDATE", "GO_CANDIDATE")


@lru_cache(maxsize=1)
def _esc() -> dict:
    return (yaml.safe_load(_CFG.read_text(encoding="utf-8")) or {}).get("escalation", {}) or {}


def _fallback_pre_verdict(agent: dict, e: dict) -> str:
    """Старый/несовместимый ответ агента (нет pre_verdict) → консервативный маппинг."""
    pre = str(agent.get("pre_verdict") or "").upper()
    if pre in PRE_VERDICTS:
        return pre
    m = agent.get("materiality") or 0.0
    c = agent.get("confidence") or 0.0
    if (agent.get("direction") in ("long", "short")
            and c >= e.get("direction_conf_min", 0.55)
            and m >= e.get("materiality_min", 0.65)):
        return "WATCH_CANDIDATE"
    return "JOURNAL_NO_GO"


def decide_escalation(agent: dict, *, layer: int, lead_class: str,
                      source: str | None = None, source_class: str | None = None,
                      source_trust: str | None = None,
                      low_confidence: bool = False) -> tuple[str, str, str]:
    """Кодовый гейт: (decision trash|journal|chief, escalation_gate, escalation_reason)."""
    e = _esc()
    pre = _fallback_pre_verdict(agent, e)
    m = float(agent.get("materiality") or 0.0)
    c = float(agent.get("confidence") or 0.0)
    trigger = str(agent.get("trigger_text") or "").strip()
    lead = str(lead_class or "").upper()
    # veto (риск) ≠ no_edge (слабость новости): только veto — повод будить chief
    veto, _no_edge = layer_agent.split_flags(agent)

    # 1) мусор: cheap сказал DROP, либо детерминированное правило шума
    if pre == "DROP" or (m < e.get("trash_materiality_max", 0.2)
                         and agent.get("phase") in ("context", "ambiguous")
                         and not veto and lead != "LEADING"):
        return "trash", "DROP_RULE", agent.get("no_go_reason") or "cheap: мусор/нерелевантно слою"

    # 2) veto-флаг (скам/взлом/манипуляция/санкции — словарь) — всегда финальный разбор
    if veto:
        return "chief", "VETO_FLAG", f"veto_flags: {', '.join(map(str, veto))[:120]}"

    # 3) LEADING (official/api до новостного цикла) — эскалация лёгкая
    if lead == "LEADING":
        return "chief", "LEADING", "опережающий источник"

    # 4) flow/телеметрия (DEX-объёмы и т.п.): «уже в цене» неприменимо; режем только явный DROP.
    #    Шире, чем «cheap сказал WATCH»: аудит 10.06 — dexscreener 7/18 idio-промахов,
    #    5 из них cheap-fallback резал; объём источника мал (~2 карточки/день), цена защиты копейки.
    if str(source or "") in set(e.get("flow_sources") or ()):
        return "chief", "FLOW_SIGNAL", f"flow-источник {source}: {pre.lower() or 'телеметрия'}"

    # 5) GO-кандидат от cheap (требует trigger_text по контракту промпта)
    if pre == "GO_CANDIDATE":
        return "chief", "CHEAP_GO", agent.get("escalation_reason") or trigger or "cheap: GO-кандидат"

    # 6) WATCH-кандидат: пороги (металлы-lagging строже — аудит 0/22 idio);
    #    механика на прямом/официальном источнике эскалируется без порогов
    if pre == "WATCH_CANDIDATE":
        if agent.get("mechanics") and (str(source_trust or "") in ("official", "primary")
                                       or str(source_class or "") in ("api", "ws", "web")):
            return "chief", "MECHANICS", f"механика на прямом источнике: {', '.join(map(str, agent['mechanics']))[:120]}"
        mat_min = (e.get("metals_lagging_materiality_min", 0.8)
                   if (int(layer or 0) == 3 and lead == "LAGGING")
                   else e.get("watch_materiality_min", 0.55))
        title_only_aggregator = low_confidence and str(source_trust or "") == "aggregator" and not trigger
        if m >= mat_min and c >= e.get("watch_confidence_min", 0.5) and not title_only_aggregator:
            return "chief", "CHEAP_WATCH", agent.get("escalation_reason") or trigger or "cheap: watch-кандидат"
        return ("journal", "MATERIALITY_ONLY_BLOCKED",
                f"watch-кандидат ниже порогов слоя (mat={m:.2f}<{mat_min:g} или conf={c:.2f}, "
                f"title_only_agg={title_only_aggregator})")

    # 7) дефолт: cheap не эскалирует; высокая «голая» материальность больше не повод
    if m >= e.get("materiality_min", 0.65):
        return ("journal", "MATERIALITY_ONLY_BLOCKED",
                "только материальность без конкретного триггера — недостаточно для lagging")
    return "journal", "CHEAP_NO_GO", agent.get("no_go_reason") or "cheap: нет конкретного триггера"


async def process(event: dict, asset: str | None, layer: int, lead_class: str,
                  price: float | None, market_ctx: str | None = None, *,
                  source: str | None = None, source_class: str | None = None,
                  source_trust: str | None = None, low_confidence: bool = False) -> dict:
    """Одно событие → решение оркестратора (для журнала/канала). Поля:
    decision · chief_called · verdict · side · agent · chief · usage[] · send_channel
    · pre_verdict · should_escalate · escalation_gate · escalation_reason."""
    agent = await layer_agent.analyze(event, layer, asset)
    usage = [agent.get("_usage", {})]
    decision, gate, reason = decide_escalation(
        agent, layer=layer, lead_class=lead_class, source=source,
        source_class=source_class, source_trust=source_trust, low_confidence=low_confidence)

    veto, no_edge = layer_agent.split_flags(agent)
    out = {"decision": decision, "chief_called": False, "agent": agent, "chief": None,
           "usage": usage, "send_channel": False,
           "pre_verdict": _fallback_pre_verdict(agent, _esc()),
           "should_escalate": decision == "chief",
           "escalation_gate": gate, "escalation_reason": reason,
           "veto_flags": veto, "no_edge_flags": no_edge,
           "chief_error": False, "retry_status": None}

    if decision == "trash":
        out["verdict"] = "DROP"
        return out

    if decision == "chief":
        ch = await chief_mod.decide(event, agent, price, market_ctx)
        out["chief_called"] = True
        if ch and ch.get("_error") == "budget_skipped":
            usage.append(ch.get("_usage", {}))
            out["chief_error"] = False
            out["retry_status"] = "chief_budget_skipped"
            out["escalation_gate"] = "CHIEF_BUDGET_SKIPPED"
            out["escalation_reason"] = "chief skipped by LLM budget guard"
            out["verdict"] = "NO_GO"
            out["side"] = "none"
            out["send_channel"] = False
            return out
        if ch:
            usage.append(ch.get("_usage", {}))
            out["chief"] = ch
            out["verdict"] = ch["verdict"]
            out["side"] = ch["side"]
            out["send_channel"] = True       # chief-карточка → кандидат в канал (гейт GO/WATCH в scanner_v0)
            return out
        # chief упал → НЕ финализируем кандидата обычным NO_GO: помечаем для ретрая
        # (scanner_v0 вернёт событие в очередь; после капа — CHIEF_UNAVAILABLE). Аудит 11.06:
        # SPACEX WATCH-кандидат умер тихим NO_GO, когда chief был недоступен.
        out["chief_error"] = True
        out["retry_status"] = "chief_error_pending"
        out["escalation_gate"] = "CHIEF_ERROR_PENDING"
        out["escalation_reason"] = f"chief недоступен (гейт был {gate})"

    # journal-path: дешёвый NO_GO (датасет), chief не зван / упал → только журнал, не в канал
    out["verdict"] = "NO_GO"
    out["side"] = "none"
    out["send_channel"] = False
    return out
