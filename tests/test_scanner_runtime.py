# -*- coding: utf-8 -*-
"""
test_scanner_runtime.py - runtime guards for scanner_v0 filters.

Checks small pure helpers that keep noisy lagging content away from expensive LLM calls.
"""
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import scanner_v0 as S  # noqa: E402


def test_parse_source_ts_rss_pubdate():
    ts = S.parse_source_ts("Sat, 07 Jun 2026 04:00:36 GMT")
    assert ts is not None
    assert ts.year == 2026 and ts.month == 6 and ts.day == 7


def test_stale_story_drops_old_google_news():
    assert S.is_stale_story(
        "Thu, 30 Apr 2026 07:00:00 GMT",
        source="google_news_equities",
        lead_class="LAGGING",
        source_class="rss",
        now_utc=S.parse_source_ts("Sun, 07 Jun 2026 07:00:00 GMT"),
    )


def test_stale_story_keeps_recent_google_news():
    assert not S.is_stale_story(
        "Sat, 07 Jun 2026 04:00:36 GMT",
        source="google_news_equities",
        lead_class="LAGGING",
        source_class="rss",
        now_utc=S.parse_source_ts("Sun, 07 Jun 2026 07:00:00 GMT"),
    )


def test_stale_story_never_blocks_leading():
    assert not S.is_stale_story(
        "Thu, 30 Apr 2026 07:00:00 GMT",
        source="sec_edgar",
        lead_class="LEADING",
        source_class="api",
        now_utc=S.parse_source_ts("Sun, 07 Jun 2026 07:00:00 GMT"),
    )


def test_process_item_logs_routing_audit_for_no_tracked_asset(monkeypatch):
    audits = []
    monkeypatch.setattr(S.J, "write_routing_audit", audits.append)
    monkeypatch.setattr(S, "route_asset", lambda headline, allowed_layers=None: None)

    res = asyncio.run(
        S.process_item(
            {"title": "Unrelated policy story", "url": "https://example.com/a", "source": "cointelegraph",
             "lead_class": "LAGGING", "source_class": "rss"},
            mline=None,
            dry=True,
        )
    )

    assert res == {"skipped": "no_tracked_asset", "headline": "Unrelated policy story"}
    assert audits and audits[0]["skipped"] == "no_tracked_asset"
    assert audits[0]["source"] == "cointelegraph"


def test_process_item_logs_routing_audit_for_context_gate(monkeypatch):
    audits = []
    monkeypatch.setattr(S.J, "write_routing_audit", audits.append)
    monkeypatch.setattr(
        S,
        "route_asset",
        lambda headline, allowed_layers=None: {
            "asset": "BTC",
            "okx_inst": "BTC-USDT-SWAP",
            "layer": 1,
            "baseline": "BTC-USDT-SWAP",
            "confidence": 0.91,
        },
    )
    monkeypatch.setattr(S, "score_materiality", lambda headline, layer: {"score": 0.6, "family": "etf_flow", "drop_reason": None})
    monkeypatch.setattr(S, "route_temporal", lambda headline: {"phase": "CONTEXT"})

    res = asyncio.run(
        S.process_item(
            {"title": "Bitcoin price analysis ahead of earnings", "url": "https://example.com/b", "source": "cointelegraph",
             "lead_class": "LAGGING", "source_class": "rss"},
            mline=None,
            dry=True,
        )
    )

    assert res == {"skipped": "context_commentary", "headline": "Bitcoin price analysis ahead of earnings", "asset": "BTC"}
    assert audits and audits[0]["skipped"] == "context_commentary"
    assert audits[0]["headline_phase"] == "CONTEXT"


def test_should_send_to_channel_default_quiet(monkeypatch):
    monkeypatch.delenv("SCANNER_SEND_NO_GO", raising=False)
    assert S.should_send_to_channel("GO", True)
    assert S.should_send_to_channel("WATCH", True)
    assert not S.should_send_to_channel("NO_GO", True)
    assert not S.should_send_to_channel("GO", False)      # не-chief карточка не идёт вовсе
    assert not S.should_send_to_channel("DROP", True)


def test_should_send_to_channel_env_override(monkeypatch):
    monkeypatch.setenv("SCANNER_SEND_NO_GO", "true")
    assert S.should_send_to_channel("NO_GO", True)
    monkeypatch.setenv("SCANNER_SEND_NO_GO", "false")
    assert not S.should_send_to_channel("NO_GO", True)


def test_layer_label_known_and_fallback():
    assert S.layer_label(1) == "крипта/мажоры"
    assert S.layer_label("3") == "металлы"
    assert S.layer_label(9) == "L9"
    assert S.layer_label(None) == "—"


def _card_row(verdict, **kw):
    row = {
        "verdict": verdict, "side": "none", "asset": "PEPE", "layer": 2,
        "summary": "Конкретный разбор события.", "asymmetry": "", "forecast": "",
        "invalidation": "", "in_price": "unknown", "levels": {},
        "price_at_decision": None, "horizon_hours": 24, "low_confidence": False,
        "headline": "PEPE unlock ahead", "source_url": "https://example.com/x?utm=1",
        "event_phase": "expected", "lead_class": "LAGGING",
    }
    row.update(kw)
    return row


def test_format_card_watch_emphasizes_confirm_and_invalidate():
    card = S.format_card(_card_row(
        "WATCH", asymmetry="ранний анлок", forecast="рост объёма на DEX",
        invalidation="отмена анлока", price_at_decision=0.0000012, in_price="partial",
    ))
    assert "альты/мемы" in card
    assert "Почему наблюдаем:" in card
    assert "Что подтвердит:" in card
    assert "Что отменит:" in card
    assert "в цене: частично" in card


def test_format_card_skips_empty_none_fields():
    card = S.format_card(_card_row("NO_GO"))
    assert "none" not in card
    assert "None" not in card
    assert "Фаза:" not in card and "класс:" not in card     # системная строка убрана
    assert "Сценарий" not in card                            # пустые поля не печатаем
    assert "сторона" not in card                             # side=none не показываем


def test_format_card_go_keeps_levels():
    card = S.format_card(_card_row(
        "GO", side="long", levels={"entry": 1.0, "invalidation": 0.9, "target": 1.2},
        forecast="сценарий", invalidation="ниже 0.9", price_at_decision=1.0,
    ))
    assert "Уровни:" in card and "лонг" in card


def _gate_item():
    return {"title": "BTC ETF inflows hit record", "url": "https://example.com/etf",
            "source": "cointelegraph", "lead_class": "LAGGING", "source_class": "rss",
            "time": "2026-06-09T00:00:00Z"}


def _patch_pipeline(monkeypatch, verdict, side="none"):
    """process_item без сети/LLM: фиксированный chief-вердикт, перехват журнала и Telegram."""
    sent, journaled = [], []
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
        return {"decision": "chief", "chief_called": True, "verdict": verdict, "side": side,
                "send_channel": True, "usage": [{}],
                "pre_verdict": "WATCH_CANDIDATE", "should_escalate": True,
                "escalation_gate": "CHEAP_WATCH", "escalation_reason": "test",
                "agent": {"direction": "none", "confidence": 0.5, "phase": "realized",
                          "asset": asset, "event_type": "etf_flow", "materiality": 0.7,
                          "red_flags": [], "mechanics": [], "key_facts": []},
                "chief": {"verdict": verdict, "side": side, "in_price": "partial",
                          "surprise": "timing", "asymmetry": "а", "invalidation": "б",
                          "forecast": "в", "horizon_hours": 24, "confidence": 0.6,
                          "levels": {"entry": None, "invalidation": None, "target": None},
                          "summary": "с", "journal_reason": "р", "_usage": {}}}
    monkeypatch.setattr(S.orchestrator, "process", fake_process)
    return sent, journaled


def test_chief_no_go_journaled_but_not_sent(monkeypatch):
    monkeypatch.delenv("SCANNER_SEND_NO_GO", raising=False)
    sent, journaled = _patch_pipeline(monkeypatch, "NO_GO")
    res = asyncio.run(S.process_item(_gate_item(), None, dry=False))
    assert journaled and journaled[0]["verdict"] == "NO_GO"   # журнал/датасет — без изменений
    assert sent == []                                          # Telegram молчит
    assert res["sent"] is None


def test_chief_watch_still_sent(monkeypatch):
    monkeypatch.delenv("SCANNER_SEND_NO_GO", raising=False)
    sent, journaled = _patch_pipeline(monkeypatch, "WATCH")
    res = asyncio.run(S.process_item(_gate_item(), None, dry=False))
    assert journaled and journaled[0]["verdict"] == "WATCH"
    assert len(sent) == 1
    assert res["sent"] is not None


def test_chief_no_go_sent_only_with_env_override(monkeypatch):
    monkeypatch.setenv("SCANNER_SEND_NO_GO", "1")
    sent, journaled = _patch_pipeline(monkeypatch, "NO_GO")
    asyncio.run(S.process_item(_gate_item(), None, dry=False))
    assert len(sent) == 1


def test_process_item_uses_structured_text_for_pre_routed_items(monkeypatch):
    monkeypatch.setattr(S, "write_telegram_delivery", lambda event: None)
    res = asyncio.run(
        S.process_item(
            {
                "title": "PEPE DEX volume spike: +18.4% / 24h with $910,000 volume on uniswap",
                "text": "DexScreener observed PEPE against WETH on ethereum/uniswap. Liquidity $240,000.",
                "url": "https://dexscreener.com/ethereum/pair-pepe-good",
                "time": S.J.now_iso(),
                "source": "dexscreener",
                "source_class": "api",
                "lead_class": "COINCIDENT",
                "asset": "PEPE",
                "okx_inst": "PEPE-USDT-SWAP",
                "layer": 2,
                "baseline": "BTC-USDT-SWAP",
                "phase": "REALIZED",
                "event_type": "dex_momentum",
                "trigger_type": "dexscreener_signal",
            },
            mline=None,
            dry=True,
        )
    )

    assert res is not None
    assert res["row"]["asset"] == "PEPE"
    assert res["row"]["source"] == "dexscreener"
    assert res["row"]["low_confidence"] is False


def test_context_status_not_always_none(monkeypatch):
    """context_status is injected into news dict AFTER context-building, not before.

    Regression: context_status was injected at line 725 (before context block at 752),
    so it was always None. After fix, source_is_confirmation reaches LLM framing.
    """
    monkeypatch.setattr(S, "write_telegram_delivery", lambda event: None)
    monkeypatch.setattr(S, "okx_last", lambda inst: None)
    monkeypatch.setattr(S, "extract", lambda url: {"text": "x" * 600, "date": "2026-06-14"})
    monkeypatch.setattr(S, "is_stale_story", lambda *a, **k: False)
    monkeypatch.setattr(S.J, "write_row", lambda row: row["card_id"])
    monkeypatch.setattr(S.J, "write_routing_audit", lambda rec: None)
    monkeypatch.setattr(S.R, "write_event_block", lambda b: True)
    monkeypatch.setattr(S.R, "write_reasoning_block", lambda b: True)
    monkeypatch.setattr(S.PS, "build_pending_from_journal", lambda row: None)
    monkeypatch.setattr(S.PS, "match_realized_event", lambda row: None)
    monkeypatch.setattr(S.WQ, "upsert_watch", lambda row: None)

    captured_news = {}

    async def fake_orch(news, asset, layer, lead_class, price, mline, **kw):
        captured_news.update(news)
        return {"decision": "journal", "verdict": "NO_GO", "side": "none",
                "chief_called": False, "agent": {"direction": "none", "confidence": 0.5,
                "phase": "realized", "asset": asset, "event_type": "listing",
                "materiality": 0.6, "red_flags": [], "veto_flags": [],
                "no_edge_flags": [], "mechanics": [], "key_facts": []},
                "chief": None, "usage": [{}], "send_channel": False,
                "pre_verdict": "JOURNAL_NO_GO", "should_escalate": False,
                "escalation_gate": "CHEAP_NO_GO", "escalation_reason": "test",
                "veto_flags": [], "no_edge_flags": [], "chief_error": False,
                "llm_error": False, "retry_status": None}
    monkeypatch.setattr(S.orchestrator, "process", fake_orch)

    # Item with requires_context=True + official source → source_is_confirmation
    item = {
        "title": "$SPCXX listed on OKX spot",
        "text": "$SPCXX listed on OKX spot",
        "url": "https://t.me/NewListingsFeed/9999",
        "source": "tg_new_listings_feed", "source_class": "telegram_web",
        "lead_class": "LEADING", "asset": "SPCXX", "okx_inst": "SPCXX-USDT-SWAP",
        "layer": 5, "baseline": "QQQ-USDT-SWAP", "asset_class": "tokenized_equity",
        "trigger_role": "needs_context", "requires_context": True,
        "identity_reason": "known_tokenized_ticker:SPCXX",
    }
    asyncio.run(S.process_item(item, None, dry=False, use_buffer=False))

    # context_status should be "source_is_confirmation" in the news dict passed to LLM
    assert captured_news.get("context_status") == "source_is_confirmation", (
        f"context_status should be 'source_is_confirmation' but got: {captured_news.get('context_status')}"
    )


def test_future_prerouted_event_goes_to_pending_without_llm(monkeypatch):
    monkeypatch.setattr(S, "write_telegram_delivery", lambda event: None)
    monkeypatch.setattr(S, "is_stale_story", lambda *a, **k: False)
    monkeypatch.setattr(S.J, "write_event_audit", lambda rec: True)
    monkeypatch.setattr(S.J, "write_routing_audit", lambda rec: None)

    pending_records = []

    def fake_upsert(record):
        pending_records.append(record)
        return record["pending_id"], True

    monkeypatch.setattr(S.PS, "upsert_pending", fake_upsert)

    async def fail_orch(*args, **kwargs):
        raise AssertionError("future/expected events must not call LLM")

    monkeypatch.setattr(S.orchestrator, "process", fail_orch)

    item = {
        "title": "OKX will launch PROS/USD and PROS/EUR for spot trading",
        "text": "Official OKX announcement: OKX will launch PROS/USD and PROS/EUR for spot trading.",
        "url": "https://www.okx.com/help/pros-listing",
        "time": S.J.now_iso(),
        "source": "okx_announcements",
        "source_class": "api",
        "lead_class": "LEADING",
        "asset": "PROS",
        "okx_inst": "PROS-USDT-SWAP",
        "layer": 2,
        "baseline": "BTC-USDT-SWAP",
        "phase": "FUTURE",
        "event_type": "listing",
        "trigger_type": "okx_official_announcement",
        "event_key": "okx_ann:a1:PROS",
    }

    res = asyncio.run(S.process_item(item, None, dry=False, use_buffer=False))

    assert res["handled"] == "future_pending_no_llm"
    assert pending_records
    assert pending_records[0]["asset"] == "PROS"
    assert pending_records[0]["event_type"] == "listing"


def test_market_mover_goes_to_audit_without_llm(monkeypatch):
    monkeypatch.setattr(S, "write_telegram_delivery", lambda event: None)
    monkeypatch.setattr(S, "is_stale_story", lambda *a, **k: False)
    monkeypatch.setattr(S.J, "write_event_audit", lambda rec: True)
    monkeypatch.setattr(S.J, "write_routing_audit", lambda rec: None)

    async def fail_orch(*args, **kwargs):
        raise AssertionError("market_mover audit events must not call LLM")

    monkeypatch.setattr(S.orchestrator, "process", fail_orch)

    item = {
        "title": "BTC OKX market mover: 1h +15.00% / 24h +18.00%",
        "text": "OKX public market tape observed BTC-USDT-SWAP moving up.",
        "url": "https://www.okx.com/trade-swap/btc-usdt-swap",
        "time": S.J.now_iso(),
        "source": "okx_market_tape",
        "source_class": "api",
        "lead_class": "COINCIDENT",
        "asset": "BTC",
        "okx_inst": "BTC-USDT-SWAP",
        "layer": 1,
        "baseline": "BTC-USDT-SWAP",
        "phase": "REALIZED",
        "event_type": "market_mover",
        "trigger_type": "okx_market_tape",
        "event_key": "okx_tape:BTC-USDT-SWAP:1h_move:20260617T0300Z",
        "market_tape_trigger": "1h_move",
        "move_1h_pct": 15.0,
        "move_24h_pct": 18.0,
    }

    res = asyncio.run(S.process_item(item, None, dry=False, use_buffer=False))

    assert res["handled"] == "market_mover_audit_only"
    assert res["asset"] == "BTC"


def test_tg_markettwits_calendar_digest_goes_to_audit_without_llm(monkeypatch):
    monkeypatch.setattr(S, "write_telegram_delivery", lambda event: None)
    monkeypatch.setattr(S, "is_stale_story", lambda *a, **k: False)
    monkeypatch.setattr(S.J, "write_event_audit", lambda rec: True)
    monkeypatch.setattr(S.J, "write_routing_audit", lambda rec: None)

    async def fail_orch(*args, **kwargs):
        raise AssertionError("calendar digest must not call LLM")

    monkeypatch.setattr(S.orchestrator, "process", fail_orch)

    item = {
        "title": "КАЛЕНДАРЬ НА СЕГОДНЯ — 2026.06.17 CPI 09:00мск",
        "text": "КАЛЕНДАРЬ НА СЕГОДНЯ — 2026.06.17 CPI 09:00мск",
        "url": "https://t.me/markettwits/1",
        "time": S.J.now_iso(),
        "source": "tg_markettwits",
        "source_class": "telegram_web",
        "lead_class": "LEADING",
        "asset": "BTC",
        "okx_inst": "BTC-USDT-SWAP",
        "layer": 1,
        "baseline": "BTC-USDT-SWAP",
        "phase": "AMBIGUOUS",
        "event_type": "news_trigger",
    }

    res = asyncio.run(S.process_item(item, None, dry=False, use_buffer=False))

    assert res["handled"] == "calendar_digest_audit_only"


def test_tg_markettwits_calendar_digest_detects_mojibake():
    assert S.is_channel_calendar_digest(
        {"title": "mt в max РљРђР›Р•РќР”РђР Р¬ РќРђ РЎР•Р“РћР”РќРЇ"},
        "tg_markettwits",
    )
