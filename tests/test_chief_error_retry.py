# -*- coding: utf-8 -*-
"""
test_chief_error_retry.py — падение chief на кандидате НЕ финализируется тихим NO_GO.

P0 аудита 11.06: SPACEX WATCH-кандидат умер CHIEF_ERROR_FALLBACK'ом. Контракт:
ретрай через существующий llm_failed-путь (буфер→READY, RSS→не-seen), кап ретраев,
после капа — журнальная карточка с гейтом CHIEF_UNAVAILABLE (не обычный NO_GO).
"""
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import scanner_v0 as S  # noqa: E402


def _patch_retry_state(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "CHIEF_RETRY_PATH", tmp_path / "chief_retry_state.json")


def test_chief_retry_decision_caps_and_resets(tmp_path, monkeypatch):
    _patch_retry_state(tmp_path, monkeypatch)
    assert S.chief_retry_decision("k", retry_max=2) == "retry"
    assert S.chief_retry_decision("k", retry_max=2) == "retry"
    assert S.chief_retry_decision("k", retry_max=2) == "finalize"   # кап исчерпан
    # после finalize счётчик очищен — новый сбой того же ключа начинает заново
    assert S.chief_retry_decision("k", retry_max=2) == "retry"
    # другой ключ не задет
    assert S.chief_retry_decision("other", retry_max=2) == "retry"
    state = json.loads(S.CHIEF_RETRY_PATH.read_text(encoding="utf-8"))
    assert state == {"k": 1, "other": 1}


def _gate_item():
    return {"title": "SpaceX crypto rails race", "url": "https://example.com/spacex",
            "source": "cointelegraph", "lead_class": "LAGGING", "source_class": "rss",
            "time": "2026-06-11T00:00:00Z"}


def _patch_pipeline_chief_error(monkeypatch, tmp_path):
    """process_item без сети/LLM: оркестратор возвращает chief_error-кандидата."""
    _patch_retry_state(tmp_path, monkeypatch)
    sent, journaled, audits = [], [], []
    monkeypatch.setattr(S, "SCANNER_CHAT_ID", "12345")
    monkeypatch.setattr(S, "route_asset", lambda h, allowed_layers=None: {
        "asset": "SPACEX", "okx_inst": "SPACEX-USDT-SWAP", "layer": 5,
        "baseline": "BTC-USDT-SWAP", "confidence": 0.9})
    monkeypatch.setattr(S, "score_materiality", lambda h, layer: {"score": 0.7, "family": "listing"})
    monkeypatch.setattr(S, "route_temporal", lambda h: {"phase": "REALIZED"})
    monkeypatch.setattr(S, "extract", lambda url: {"text": "x" * 400, "date": "2026-06-11"})
    monkeypatch.setattr(S, "is_stale_story", lambda *a, **k: False)
    monkeypatch.setattr(S, "okx_last", lambda inst: 100.0)
    monkeypatch.setattr(S, "make_chart", lambda *a, **k: None)
    monkeypatch.setattr(S.J, "write_row", lambda row: (journaled.append(row), row["card_id"])[1])
    monkeypatch.setattr(S.J, "write_routing_audit", audits.append)
    monkeypatch.setattr(S.R, "write_event_block", lambda b: True)
    monkeypatch.setattr(S.R, "write_reasoning_block", lambda b: True)
    monkeypatch.setattr(S.PS, "build_pending_from_journal", lambda row: None)
    monkeypatch.setattr(S.PS, "match_realized_event", lambda row: None)
    monkeypatch.setattr(S.WQ, "upsert_watch", lambda row: None)
    monkeypatch.setattr(S, "write_telegram_delivery", lambda event: None)

    async def fake_send(chat_id, payload, **kw):
        sent.append(payload)
        return 777
    monkeypatch.setattr(S, "send_message_to", fake_send)
    monkeypatch.setattr(S, "send_photo_to", fake_send)

    async def fake_process(news, asset, layer, lead_class, price, mline, **kw):
        return {"decision": "chief", "chief_called": True, "verdict": "NO_GO", "side": "none",
                "send_channel": False, "usage": [{"role": "cheap", "total_tokens": 200,
                                                  "cost_rub": 0.01}],
                "pre_verdict": "WATCH_CANDIDATE", "should_escalate": True,
                "chief_error": True, "retry_status": "chief_error_pending",
                "escalation_gate": "CHIEF_ERROR_PENDING",
                "escalation_reason": "chief недоступен (гейт был CHEAP_WATCH)",
                "agent": {"direction": "none", "confidence": 0.6, "phase": "realized",
                          "asset": asset, "event_type": "listing", "materiality": 0.7,
                          "red_flags": [], "veto_flags": [], "no_edge_flags": [],
                          "mechanics": [], "key_facts": []},
                "chief": None}
    monkeypatch.setattr(S.orchestrator, "process", fake_process)
    return sent, journaled, audits


def test_chief_error_requeues_not_journals(monkeypatch, tmp_path):
    sent, journaled, audits = _patch_pipeline_chief_error(monkeypatch, tmp_path)
    res = asyncio.run(S.process_item(_gate_item(), None, dry=False))
    assert res["skipped"] == "llm_failed"        # run(): буфер→READY / RSS→не-seen → ретрай
    assert journaled == []                       # карточка НЕ финализирована
    assert sent == []                            # Telegram молчит
    assert audits and audits[-1]["skipped"] == "chief_error_retry"
    assert audits[-1]["escalation_gate"] == "CHIEF_ERROR_PENDING"
    assert res["tokens"] == 200                  # cheap-токены учтены в бюджете


def test_chief_error_finalizes_after_cap(monkeypatch, tmp_path):
    sent, journaled, audits = _patch_pipeline_chief_error(monkeypatch, tmp_path)
    # выжигаем кап: CHIEF_RETRY_MAX ретраев уже сделано
    for _ in range(S.CHIEF_RETRY_MAX):
        res = asyncio.run(S.process_item(_gate_item(), None, dry=False))
        assert res["skipped"] == "llm_failed"
    res = asyncio.run(S.process_item(_gate_item(), None, dry=False))
    assert "skipped" not in res or not res.get("skipped")
    assert journaled and journaled[0]["verdict"] == "NO_GO"
    assert journaled[0]["escalation_gate"] == "CHIEF_UNAVAILABLE"   # не обычный NO_GO в аудитах
    assert sent == []                                               # в канал не идёт
    # дедуп цел: card_id детерминирован от canonical url
    assert journaled[0]["card_id"]
