# -*- coding: utf-8 -*-
"""Scanner integration of OKX instrument resolution + data-readiness (Этап 3b).

Gate SCANNER_FARM_RESOLVE: off by default (no network, no farm fields); on stamps
verified-instrument + readiness + farm fields onto the journal row and watch queue.
The live resolver/provider is monkeypatched here so the test never hits the network.
"""
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import scanner_v0 as S  # noqa: E402


def _gate_item():
    return {"title": "BTC ETF inflows hit record", "url": "https://example.com/farm-etf",
            "source": "cointelegraph", "lead_class": "LAGGING", "source_class": "rss",
            "time": "2026-06-09T00:00:00Z"}


def _patch_pipeline(monkeypatch, verdict, side="none"):
    journaled = []
    monkeypatch.setattr(S, "SCANNER_CHAT_ID", "12345")
    monkeypatch.setattr(S, "route_asset", lambda h, allowed_layers=None: {
        "asset": "BTC", "okx_inst": "BTC-USDT-SWAP", "layer": 1,
        "baseline": "BTC-USDT-SWAP", "confidence": 0.9})
    monkeypatch.setattr(S, "score_materiality", lambda h, layer: {"score": 0.7, "family": "etf_flow"})
    monkeypatch.setattr(S, "route_temporal", lambda h: {"phase": "REALIZED"})
    monkeypatch.setattr(S, "extract", lambda url: {"text": "x" * 400, "date": "2026-06-09"})
    monkeypatch.setattr(S, "is_stale_story", lambda *a, **k: False)
    monkeypatch.setattr(S, "okx_last", lambda inst: 100.0)
    monkeypatch.setattr(S, "make_chart", lambda *a, **k: None)
    monkeypatch.setattr(S.J, "write_row", lambda row: (journaled.append(row), row["card_id"])[1])
    monkeypatch.setattr(S.J, "write_routing_audit", lambda rec: None)
    monkeypatch.setattr(S.J, "write_event_audit", lambda rec: True)
    monkeypatch.setattr(S.R, "write_event_block", lambda b: True)
    monkeypatch.setattr(S.R, "write_reasoning_block", lambda b: True)
    monkeypatch.setattr(S.PS, "build_pending_from_journal", lambda row: None)
    monkeypatch.setattr(S.PS, "match_realized_event", lambda row: None)
    monkeypatch.setattr(S.WQ, "upsert_watch", lambda row: ("w", True))
    monkeypatch.setattr(S, "write_telegram_delivery", lambda event: None)

    async def fake_send(chat_id, payload, **kw):
        return 777

    monkeypatch.setattr(S, "send_message_to", fake_send)
    monkeypatch.setattr(S, "send_photo_to", fake_send)

    async def fake_process(news, asset, layer, lead_class, price, mline, **kw):
        return {"decision": "chief", "chief_called": True, "verdict": verdict, "side": side,
                "send_channel": True, "usage": [{}],
                "pre_verdict": "WATCH_CANDIDATE", "should_escalate": True,
                "escalation_gate": "CHEAP_WATCH", "escalation_reason": "test",
                "agent": {"direction": "none", "confidence": 0.5, "phase": "realized",
                          "asset": asset, "event_type": "etf_flow", "materiality": 0.7,
                          "red_flags": [], "mechanics": [], "key_facts": []},
                "chief": {"verdict": verdict, "side": side, "in_price": "partial",
                          "surprise": "timing", "asymmetry": "a", "invalidation": "b",
                          "forecast": "c", "horizon_hours": 24, "confidence": 0.6,
                          "levels": {"entry": None, "invalidation": None, "target": None},
                          "summary": "s", "journal_reason": "r", "_usage": {}}}

    monkeypatch.setattr(S.orchestrator, "process", fake_process)
    return journaled


def test_farm_resolve_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SCANNER_FARM_RESOLVE", raising=False)

    def boom(*a, **k):
        raise AssertionError("enrich_trigger must not run when SCANNER_FARM_RESOLVE is off")

    monkeypatch.setattr(S, "enrich_trigger", boom)
    journaled = _patch_pipeline(monkeypatch, "WATCH")
    asyncio.run(S.process_item(_gate_item(), None, dry=False))
    assert journaled and journaled[0]["verdict"] == "WATCH"
    assert journaled[0].get("okx_resolved") is None
    assert journaled[0].get("farm_eligible") is None


def test_farm_resolve_enabled_stamps_resolution(monkeypatch):
    monkeypatch.setenv("SCANNER_FARM_RESOLVE", "true")

    def fake_enrich(item, asset, inst, body_text, use_buffer):
        return {
            "okx_resolved": True, "okx_inst_type": "SWAP", "okx_asset_class": "crypto_major",
            "okx_resolution_reason": "exact_swap_match", "data_readiness_status": "usable",
            "selected_timeframe": "1h", "farm_eligible": True, "farm_pending_reason": None,
        }

    monkeypatch.setattr(S, "enrich_trigger", fake_enrich)
    journaled = _patch_pipeline(monkeypatch, "WATCH")
    asyncio.run(S.process_item(_gate_item(), None, dry=False))
    row = journaled[0]
    assert row["okx_resolved"] is True
    assert row["okx_inst_type"] == "SWAP"
    assert row["data_readiness_status"] == "usable"
    assert row["selected_timeframe"] == "1h"
    assert row["farm_eligible"] is True
    rec = S.WQ.build_watch_record(row)
    assert rec["farm"]["eligible"] is True
    assert rec["farm"]["selected_timeframe"] == "1h"
    assert rec["asset"]["okx_resolved"] is True
    assert rec["execution_allowed"] is False


def test_farm_resolve_unresolved_not_eligible(monkeypatch):
    monkeypatch.setenv("SCANNER_FARM_RESOLVE", "true")

    def fake_enrich(item, asset, inst, body_text, use_buffer):
        return {
            "okx_resolved": False, "okx_inst_type": None, "okx_asset_class": None,
            "okx_resolution_reason": "no_okx_instrument", "data_readiness_status": None,
            "selected_timeframe": None, "farm_eligible": False,
            "farm_pending_reason": "no_okx_instrument",
        }

    monkeypatch.setattr(S, "enrich_trigger", fake_enrich)
    journaled = _patch_pipeline(monkeypatch, "WATCH")
    asyncio.run(S.process_item(_gate_item(), None, dry=False))
    row = journaled[0]
    assert row["okx_resolved"] is False
    assert row["farm_eligible"] is False
    assert row["farm_pending_reason"] == "no_okx_instrument"


def _farm_card_row(**kw):
    row = {
        "verdict": "WATCH", "side": "none", "asset": "RE", "layer": 2,
        "summary": "Разбор события.", "asymmetry": "", "forecast": "", "invalidation": "",
        "in_price": "unknown", "levels": {}, "price_at_decision": 1.0, "horizon_hours": 24,
        "low_confidence": False, "headline": "RE listed", "source_url": "https://x/y",
        "event_phase": "realized", "lead_class": "LEADING", "okx_inst": "RE-USDT-SWAP",
    }
    row.update(kw)
    return row


def test_farm_status_ru_variants():
    assert S.farm_status_ru({"okx_resolved": None}) is None
    assert "нет торгуемого" in S.farm_status_ru({"okx_resolved": False})
    assert "расчёт" in S.farm_status_ru(
        {"okx_resolved": True, "farm_eligible": True, "selected_timeframe": "1h"})
    assert "свежий листинг" in S.farm_status_ru(
        {"okx_resolved": True, "farm_eligible": False, "farm_pending_reason": "fresh_listing_pending"})


def test_format_card_shows_resolved_instrument_and_readiness():
    card = S.format_card(_farm_card_row(
        okx_resolved=True, okx_inst_type="SWAP", okx_asset_class="crypto_alt",
        data_readiness_status="usable", selected_timeframe="15m", farm_eligible=True,
    ))
    assert "Биржа/данные:" in card
    assert "RE-USDT-SWAP" in card
    assert "расчёт ТФ 15m" in card


def test_format_card_shows_no_instrument_for_unresolved():
    card = S.format_card(_farm_card_row(asset="GEOD", okx_inst="GEOD-USDT-SWAP",
                                        okx_resolved=False, farm_eligible=False,
                                        farm_pending_reason="no_okx_instrument"))
    assert "нет торгуемого инструмента на OKX" in card
    assert "GEOD-USDT-SWAP" not in card  # do not parade a fake instrument as tradable


def test_format_card_body_note_is_not_ugly_placeholder():
    card = S.format_card(_farm_card_row(low_confidence=True))
    assert "тело не извлечено" not in card
    assert "оценка по заголовку" in card


def test_farm_resolve_skipped_for_no_go(monkeypatch):
    monkeypatch.setenv("SCANNER_FARM_RESOLVE", "true")

    def boom(*a, **k):
        raise AssertionError("NO_GO must not trigger farm resolution")

    monkeypatch.setattr(S, "enrich_trigger", boom)
    journaled = _patch_pipeline(monkeypatch, "NO_GO")
    asyncio.run(S.process_item(_gate_item(), None, dry=False))
    assert journaled[0]["verdict"] == "NO_GO"
    assert journaled[0].get("farm_eligible") is None
