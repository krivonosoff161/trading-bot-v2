# -*- coding: utf-8 -*-
"""
orchestrator.py — оркестратор (обычный Python-код + правила, НЕ LLM).

Поток на одно событие: дешёвый слой-агент извлекает факты → правила решают:
  • trash   → мусор, дропаем (chief не зовём, токены не тратим);
  • journal → дешёвый NO_GO в журнал (датасет), chief НЕ зовём, в канал НЕ шлём;
  • chief   → зовём мощную модель → финальный вердикт + сторона → в журнал И в канал.

Это экономит токены: chief только на реальных кандидатах. Возвращает единый dict для журнала.
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


@lru_cache(maxsize=1)
def _esc() -> dict:
    return (yaml.safe_load(_CFG.read_text(encoding="utf-8")) or {}).get("escalation", {}) or {}


def _route(agent: dict, lead_class: str) -> str:
    """trash | journal | chief — на основе фактов агента + класса источника."""
    e = _esc()
    m = agent.get("materiality", 0.0)
    c = agent.get("confidence", 0.0)
    d = agent.get("direction", "none")
    if (m >= e.get("materiality_min", 0.65)
            or (d in ("long", "short") and c >= e.get("direction_conf_min", 0.55))
            or bool(agent.get("red_flags"))
            or lead_class == "LEADING"):
        return "chief"
    if m < e.get("trash_materiality_max", 0.2) and agent.get("phase") in ("context", "ambiguous"):
        return "trash"
    return "journal"


async def process(event: dict, asset: str | None, layer: int, lead_class: str,
                  price: float | None, market_ctx: str | None = None) -> dict:
    """Одно событие → решение оркестратора (для журнала/канала). Поля:
    decision · chief_called · verdict · side · agent · chief · usage[] · send_channel."""
    agent = await layer_agent.analyze(event, layer, asset)
    usage = [agent.get("_usage", {})]
    decision = _route(agent, lead_class)

    out = {"decision": decision, "chief_called": False, "agent": agent, "chief": None,
           "usage": usage, "send_channel": False}

    if decision == "trash":
        out["verdict"] = "DROP"
        return out

    if decision == "chief":
        ch = await chief_mod.decide(event, agent, price, market_ctx)
        out["chief_called"] = True
        if ch:
            usage.append(ch.get("_usage", {}))
            out["chief"] = ch
            out["verdict"] = ch["verdict"]
            out["side"] = ch["side"]
            out["send_channel"] = True       # chief-карточка → в канал
            return out
        # chief упал → мягкая деградация в дешёвый NO_GO (в журнал)

    # journal-path: дешёвый NO_GO (датасет), chief не зван / упал → только журнал, не в канал
    out["verdict"] = "NO_GO"
    out["side"] = "none"
    out["send_channel"] = False
    return out
