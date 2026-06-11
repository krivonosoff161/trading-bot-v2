# -*- coding: utf-8 -*-
"""
test_orchestrator_gate.py — кодовый гейт эскалации к chief (LLM замокан, без сети).

Контракт: chief зовётся только за RED_FLAG / LEADING / FLOW_SIGNAL / CHEAP_GO /
CHEAP_WATCH(пороги) / MECHANICS; голая materiality на lagging — блокируется.
"""
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.agents import orchestrator as O  # noqa: E402
from src.scout import scanner_v0 as S  # noqa: E402


def _agent(**kw):
    a = {"asset": "BTC", "event_type": "news", "phase": "realized", "direction": "none",
         "materiality": 0.5, "confidence": 0.5, "key_facts": [], "numbers": [],
         "red_flags": [], "mechanics": [], "pre_verdict": "JOURNAL_NO_GO",
         "should_escalate": False, "escalation_reason": "", "no_go_reason": "пересказ без триггера",
         "trigger_text": "", "suggested_horizon_hours": 24, "reason_to_escalate": "",
         "_usage": {"role": "cheap", "total_tokens": 100}, "_ok": True}
    a.update(kw)
    return a


def _run(agent, *, layer=1, lead="LAGGING", chief_result="ok", **src):
    """process() с замоканными layer_agent/chief. chief_result: ok|none."""
    calls = {"chief": 0}

    async def fake_analyze(event, layer_, asset=None):
        return agent

    async def fake_decide(event, agent_, price, mctx=None):
        calls["chief"] += 1
        if chief_result == "none":
            return None
        return {"verdict": "WATCH", "side": "none", "in_price": "partial", "surprise": "timing",
                "asymmetry": "а", "invalidation": "б", "forecast": "в", "horizon_hours": 24,
                "confidence": 0.6, "levels": {"entry": None, "invalidation": None, "target": None},
                "summary": "с", "journal_reason": "р", "_usage": {"role": "chief", "total_tokens": 1000}}

    orig_a, orig_c = O.layer_agent.analyze, O.chief_mod.decide
    O.layer_agent.analyze, O.chief_mod.decide = fake_analyze, fake_decide
    try:
        out = asyncio.run(O.process({"headline": "h", "text": "t"}, "BTC", layer, lead, 100.0, None, **src))
    finally:
        O.layer_agent.analyze, O.chief_mod.decide = orig_a, orig_c
    return out, calls["chief"]


# 1. lagging google, title-only, высокая материальность, нет триггера → journal, без chief
def test_lagging_high_materiality_without_trigger_blocked():
    out, n = _run(_agent(materiality=0.9, pre_verdict="JOURNAL_NO_GO"),
                  source="google_news_crypto", source_trust="aggregator", low_confidence=True)
    assert n == 0 and out["chief_called"] is False
    assert out["verdict"] == "NO_GO"                      # NO_GO остаётся в журнале
    assert out["escalation_gate"] == "MATERIALITY_ONLY_BLOCKED"
    assert out["send_channel"] is False


# 2. dexscreener flow WATCH_CANDIDATE → chief, FLOW_SIGNAL
def test_flow_watch_candidate_escalates():
    out, n = _run(_agent(pre_verdict="WATCH_CANDIDATE", materiality=0.4),
                  layer=2, lead="COINCIDENT", source="dexscreener",
                  source_class="api", source_trust="primary")
    assert n == 1 and out["escalation_gate"] == "FLOW_SIGNAL"


# 2b. flow защищён от пере-подавления: даже cheap-NO_GO эскалируется (кроме DROP)
def test_flow_journal_no_go_still_escalates():
    out, n = _run(_agent(pre_verdict="JOURNAL_NO_GO", materiality=0.4),
                  layer=2, lead="COINCIDENT", source="dexscreener",
                  source_class="api", source_trust="primary")
    assert n == 1 and out["escalation_gate"] == "FLOW_SIGNAL"
    out2, n2 = _run(_agent(pre_verdict="DROP", materiality=0.05, phase="context"),
                    layer=2, lead="COINCIDENT", source="dexscreener")
    assert n2 == 0 and out2["decision"] == "trash"


# 3. LEADING официальный → chief (если не мусор)
def test_leading_escalates():
    out, n = _run(_agent(pre_verdict="JOURNAL_NO_GO"), lead="LEADING",
                  source="sec_edgar", source_class="api", source_trust="official")
    assert n == 1 and out["escalation_gate"] == "LEADING"


def test_leading_trash_still_dropped():
    out, n = _run(_agent(pre_verdict="DROP", materiality=0.05, phase="context"), lead="LEADING")
    assert n == 0 and out["decision"] == "trash" and out["verdict"] == "DROP"
    assert out["escalation_gate"] == "DROP_RULE"


# 4. veto-флаг (словарь рисков) → chief всегда; legacy red_flags классифицируются
def test_veto_flag_escalates():
    out, n = _run(_agent(red_flags=["insider concentration 95%"], pre_verdict="JOURNAL_NO_GO"),
                  source="google_news_crypto", source_trust="aggregator", low_confidence=True)
    assert n == 1 and out["escalation_gate"] == "VETO_FLAG"


# 4b. P0 аудита 11.06: «нет конкретики» в red_flags — НЕ эскалация (было 52% вызовов, 100% NO_GO)
def test_absence_red_flag_does_not_escalate():
    out, n = _run(_agent(red_flags=["Нет конкретного триггера для движения цены"],
                         pre_verdict="JOURNAL_NO_GO", materiality=0.5),
                  source="google_news_crypto", source_trust="aggregator", low_confidence=True)
    assert n == 0 and out["chief_called"] is False
    assert out["verdict"] == "NO_GO" and out["escalation_gate"] == "CHEAP_NO_GO"
    assert out["no_edge_flags"] and not out["veto_flags"]


def test_generic_analysis_flags_route_to_journal():
    out, n = _run(_agent(red_flags=["Текст — обобщённый анализ рынка без триггеров",
                                    "Мнение аналитиков без подтверждения механики"],
                         pre_verdict="JOURNAL_NO_GO", materiality=0.7),
                  source="google_news_metals", source_trust="aggregator", low_confidence=True)
    assert n == 0 and out["escalation_gate"] == "MATERIALITY_ONLY_BLOCKED"


# 4c. настоящие вето-слова по-прежнему эскалируются
def test_true_veto_vocabulary_escalates():
    for flag in ("rug pull risk", "protocol exploit confirmed", "взлом моста на $20M",
                 "mintable contract", "honeypot signature", "санкции OFAC на адреса"):
        out, n = _run(_agent(red_flags=[flag], pre_verdict="JOURNAL_NO_GO"),
                      source="google_news_crypto", source_trust="aggregator")
        assert n == 1 and out["escalation_gate"] == "VETO_FLAG", flag


# 4d. explicit-контракт: veto_flags эскалирует, no_edge_flags — нет;
#     absence-текст внутри veto_flags демоутится (защита от дрейфа промпта)
def test_explicit_flag_lists():
    out, n = _run(_agent(veto_flags=["скам-паттерн в контракте"], no_edge_flags=[],
                         red_flags=[], pre_verdict="JOURNAL_NO_GO"))
    assert n == 1 and out["escalation_gate"] == "VETO_FLAG"

    out2, n2 = _run(_agent(veto_flags=[], no_edge_flags=["нет конкретики"], red_flags=[],
                           pre_verdict="JOURNAL_NO_GO", materiality=0.4))
    assert n2 == 0 and out2["escalation_gate"] == "CHEAP_NO_GO"

    out3, n3 = _run(_agent(veto_flags=["Отсутствие конкретных данных о продажах"],
                           no_edge_flags=[], red_flags=[],
                           pre_verdict="JOURNAL_NO_GO", materiality=0.4))
    assert n3 == 0 and out3["no_edge_flags"]                 # демоут absence из veto_flags


# 4e. смешанный legacy-список: вето-слово достаточно для эскалации
def test_legacy_mixed_flags_keep_veto():
    out, n = _run(_agent(red_flags=["Нет конкретики по объёмам", "rug-риск: 95% у инсайдеров"],
                         pre_verdict="JOURNAL_NO_GO"))
    assert n == 1 and out["escalation_gate"] == "VETO_FLAG"
    assert len(out["veto_flags"]) == 1 and len(out["no_edge_flags"]) == 1


# 4f. no_edge-флаги не ломают другие гейты: flow и LEADING работают как раньше
def test_no_edge_flags_do_not_block_flow_and_leading():
    out, n = _run(_agent(red_flags=["Нет новых данных"], pre_verdict="JOURNAL_NO_GO"),
                  layer=2, lead="COINCIDENT", source="dexscreener",
                  source_class="api", source_trust="primary")
    assert n == 1 and out["escalation_gate"] == "FLOW_SIGNAL"

    out2, n2 = _run(_agent(red_flags=["Отсутствие деталей формы 8-K"], pre_verdict="JOURNAL_NO_GO"),
                    lead="LEADING", source="sec_edgar", source_class="api", source_trust="official")
    assert n2 == 1 and out2["escalation_gate"] == "LEADING"


# 5. L3 металлы lagging-пересказ → без chief, пока нет сильного конкретного триггера
def test_metals_lagging_recap_blocked():
    out, n = _run(_agent(pre_verdict="WATCH_CANDIDATE", materiality=0.7, confidence=0.7,
                         trigger_text="золото обновило максимум"),
                  layer=3, source="google_news_metals", source_trust="aggregator")
    assert n == 0 and out["escalation_gate"] == "MATERIALITY_ONLY_BLOCKED"

    out2, n2 = _run(_agent(pre_verdict="WATCH_CANDIDATE", materiality=0.85, confidence=0.7,
                           trigger_text="LME запасы упали на 40% за неделю"),
                    layer=3, source="google_news_metals", source_trust="aggregator")
    assert n2 == 1 and out2["escalation_gate"] == "CHEAP_WATCH"     # сильный триггер проходит


# 6. cheap GO_CANDIDATE → chief
def test_cheap_go_candidate_escalates():
    out, n = _run(_agent(pre_verdict="GO_CANDIDATE", trigger_text="анлок 40% сапплая завтра"))
    assert n == 1 and out["escalation_gate"] == "CHEAP_GO"


# 7. cheap JOURNAL_NO_GO → без chief, но в журнал
def test_cheap_no_go_journaled_without_chief():
    out, n = _run(_agent(pre_verdict="JOURNAL_NO_GO", materiality=0.3))
    assert n == 0 and out["verdict"] == "NO_GO" and out["decision"] == "journal"
    assert out["escalation_gate"] == "CHEAP_NO_GO"
    assert out["escalation_reason"]                      # причина не-эскалации записана


# 8. chief упал на кандидате → НЕ обычный NO_GO: chief_error + retry_status, в канал нельзя
def test_chief_failure_marks_retryable():
    out, n = _run(_agent(pre_verdict="GO_CANDIDATE"), chief_result="none")
    assert n == 1 and out["chief_called"] is True
    assert out["chief_error"] is True
    assert out["retry_status"] == "chief_error_pending"
    assert out["escalation_gate"] == "CHIEF_ERROR_PENDING"
    assert out["send_channel"] is False
    assert out["verdict"] == "NO_GO"          # безопасный дефолт для старых потребителей


# 8b. cheap-only JOURNAL_NO_GO не помечается chief_error (обычный NO_GO как был)
def test_cheap_only_no_go_not_marked_chief_error():
    out, n = _run(_agent(pre_verdict="JOURNAL_NO_GO", materiality=0.3))
    assert n == 0 and out["chief_error"] is False and out["retry_status"] is None
    assert out["verdict"] == "NO_GO" and out["escalation_gate"] == "CHEAP_NO_GO"


# 9. телега: только GO/WATCH (гейт scanner_v0 не изменился)
def test_telegram_gate_unchanged(monkeypatch):
    monkeypatch.delenv("SCANNER_SEND_NO_GO", raising=False)
    assert S.should_send_to_channel("WATCH", True)
    assert not S.should_send_to_channel("NO_GO", True)


# WATCH_CANDIDATE на обычных порогах (не металлы) эскалируется
def test_watch_candidate_normal_thresholds():
    out, n = _run(_agent(pre_verdict="WATCH_CANDIDATE", materiality=0.6, confidence=0.6,
                         trigger_text="конкретный триггер"),
                  source="decrypt", source_trust="wire")
    assert n == 1 and out["escalation_gate"] == "CHEAP_WATCH"


# title-only агрегатор БЕЗ триггера — блок; С триггером — проходит
def test_title_only_aggregator_needs_trigger():
    base = dict(pre_verdict="WATCH_CANDIDATE", materiality=0.7, confidence=0.7)
    out, n = _run(_agent(**base, trigger_text=""),
                  source="google_news_crypto", source_trust="aggregator", low_confidence=True)
    assert n == 0
    out2, n2 = _run(_agent(**base, trigger_text="SEC одобрила ETF с опционами"),
                    source="google_news_crypto", source_trust="aggregator", low_confidence=True)
    assert n2 == 1


# fallback-маппинг старых ответов агента (нет pre_verdict)
def test_legacy_agent_fallback_mapping():
    out, n = _run(_agent(pre_verdict="", direction="long", confidence=0.8, materiality=0.8,
                         trigger_text="х"), source="decrypt", source_trust="wire")
    assert n == 1                                        # сильное направление+мат → WATCH-кандидат
    out2, n2 = _run(_agent(pre_verdict="", direction="none", materiality=0.7),
                    source="google_news_crypto", source_trust="aggregator")
    assert n2 == 0                                       # голая материальность — блок
    assert out2["escalation_gate"] == "MATERIALITY_ONLY_BLOCKED"


# механика на прямом источнике эскалируется без порогов
def test_mechanics_on_direct_source():
    out, n = _run(_agent(pre_verdict="WATCH_CANDIDATE", materiality=0.4, confidence=0.4,
                         mechanics=["token unlock 12%"]),
                  layer=2, source="token_unlocks", source_class="api", source_trust="primary")
    assert n == 1 and out["escalation_gate"] == "MECHANICS"
