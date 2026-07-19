# -*- coding: utf-8 -*-
"""
test_source_onboarding.py — эксперимент «один источник на слой» (11.06.2026, без сети).

Контракты: реестр парсится с onboarding-полями; выключенный источник не читается;
включённый RSS даёт нормализованный item через существующий путь; новый источник
НЕ может создать GO сам по себе (LAGGING-предохранитель); отчёт онбординга видит
все новые источники; роллбэк = enabled:false.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import router  # noqa: E402
from src.scout import scanner_v0 as S  # noqa: E402
from scripts.analysis import source_onboarding_report as OR  # noqa: E402

ONBOARDING = {
    "etf_flow": (1, False, "needs_provider"),
    "token_unlocks": (2, True, "needs_key"),
    "investing_commodities": (3, False, "disabled"),
    "rigzone": (4, False, "disabled"),
    "globenewswire_public": (5, True, "candidate"),
}


# 1. конфиг парсится, onboarding-поля на месте, слои правильные
def test_registry_onboarding_entries():
    sources = OR.load_registry()
    for name, (layer, enabled, status) in ONBOARDING.items():
        meta = sources.get(name)
        assert meta, f"{name} отсутствует в реестре"
        assert meta.get("onboarding_status") == status, name
        assert bool(meta.get("enabled")) is enabled, name
        assert layer in (meta.get("layers") or []), name
        assert meta.get("expected_body"), name
        assert meta.get("quality_notes") and meta.get("rate_limit_notes"), name


def test_new_rss_sources_have_max_age_guard():
    sources = OR.load_registry()
    for name in ("investing_commodities", "rigzone", "globenewswire_public"):
        assert sources[name].get("max_age_hours") == 48     # анти-флуд бэкфилом
        assert sources[name].get("source_class") == "rss"
        assert sources[name].get("lead_class") == "LAGGING"  # GO-предохранитель применим


def test_disabled_sources_excluded_from_active():
    """Disabled onboarding sources не попадают в enabled_sources."""
    sources = OR.load_registry()
    for name in ("investing_commodities", "rigzone"):
        assert sources[name].get("enabled") is False
        assert sources[name].get("onboarding_status") == "disabled"


# 2. выключенный источник ничего не делает
def test_disabled_source_excluded(monkeypatch):
    fake = {"sources": {"dead": {"enabled": False, "source_class": "rss", "url": "http://x"},
                        "alive": {"enabled": True, "source_class": "rss", "url": "http://y"}}}
    monkeypatch.setattr(router, "_sources", lambda: fake)
    assert "dead" not in router.enabled_sources()
    assert "alive" in router.enabled_sources()


def test_etf_flow_is_context_not_rss_card_source():
    # etf_flow не должен попадать в rss_sources даже при enabled (source_class=context)
    names = [n for n, _u, _l in S.rss_sources()]
    assert "etf_flow" not in names


def test_rollback_one_line(monkeypatch):
    """Роллбэк = enabled:false → источник пропадает из rss_sources следующего прохода."""
    base = {"source_class": "rss", "url": "http://r", "lead_class": "LAGGING"}
    monkeypatch.setattr(S, "enabled_sources",
                        lambda: {"rigzone": {**base, "enabled": True}})
    assert [n for n, _u, _l in S.rss_sources()] == ["rigzone"]
    monkeypatch.setattr(S, "enabled_sources", lambda: {})
    assert "rigzone" not in [n for n, _u, _l in S.rss_sources()]   # fallback-ленты без rigzone


# 3. включённый источник производит нормализованный item существующим путём
RSS_FIXTURE = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Rigzone</title>
<item><title>OPEC Weighs Output Pause After Price Slide</title>
<link>https://www.rigzone.com/news/opec_weighs_output_pause</link>
<pubDate>Thu, 11 Jun 2026 18:00:00 GMT</pubDate></item>
<item><title></title><link>https://www.rigzone.com/broken</link></item>
</channel></rss>"""


def test_enabled_rss_source_yields_normalized_items(monkeypatch):
    monkeypatch.setattr(S, "rss_sources",
                        lambda: [("rigzone", "https://www.rigzone.com/news/rss/x", "LAGGING")])
    monkeypatch.setattr(S.requests, "get",
                        lambda url, **kw: SimpleNamespace(content=RSS_FIXTURE))
    items = S.fetch_rss()
    assert len(items) == 1                       # малформ-item (без title) пропущен, не крашит
    it = items[0]
    assert it["source"] == "rigzone" and it["source_class"] == "rss"
    assert it["lead_class"] == "LAGGING"
    assert it["title"].startswith("OPEC Weighs") and it["url"].startswith("https://")


def test_blocked_feed_does_not_crash(monkeypatch):
    monkeypatch.setattr(S, "rss_sources", lambda: [("rigzone", "http://x", "LAGGING")])
    monkeypatch.setattr(S.requests, "get",
                        lambda url, **kw: SimpleNamespace(content=b"<!doctype html><html>block</html>"))
    assert S.fetch_rss() == []                   # HTML вместо XML → тихий скип


# 4. новый источник НЕ может создать GO сам: LAGGING-предохранитель в process_item
def test_new_source_cannot_create_go(monkeypatch):
    journaled = []
    monkeypatch.setattr(S, "SCANNER_CHAT_ID", "")
    monkeypatch.setattr(S, "route_asset", lambda h, allowed_layers=None: {
        "asset": "CL", "okx_inst": "CL-USDT-SWAP", "layer": 4,
        "baseline": "CL-USDT-SWAP", "confidence": 0.9})
    monkeypatch.setattr(S, "score_materiality", lambda h, layer: {"score": 0.8, "family": "opec"})
    monkeypatch.setattr(S, "route_temporal", lambda h: {"phase": "REALIZED"})
    monkeypatch.setattr(S, "extract", lambda url: {"text": "x" * 600, "date": "2026-06-11"})
    monkeypatch.setattr(S, "is_stale_story", lambda *a, **k: False)
    monkeypatch.setattr(S, "okx_last", lambda inst: 90.0)
    monkeypatch.setattr(S.J, "write_row", lambda row: (journaled.append(row), row["card_id"])[1])
    monkeypatch.setattr(S.J, "write_routing_audit", lambda rec: None)
    monkeypatch.setattr(S.J, "write_event_audit", lambda rec: True)
    monkeypatch.setattr(S.R, "write_event_block", lambda b: True)
    monkeypatch.setattr(S.R, "write_reasoning_block", lambda b: True)
    monkeypatch.setattr(S.PS, "build_pending_from_journal", lambda row: None)
    monkeypatch.setattr(S.PS, "match_realized_event", lambda row: None)
    monkeypatch.setattr(S, "write_telegram_delivery", lambda event: None)
    if hasattr(S, "WQ"):   # параллельная ветка watch_queue (Codex) может быть не влита
        monkeypatch.setattr(S.WQ, "upsert_watch", lambda row: None)

    async def fake_process(news, asset, layer, lead_class, price, mline, **kw):
        return {"decision": "chief", "chief_called": True, "verdict": "GO", "side": "long",
                "send_channel": True, "usage": [{}], "pre_verdict": "GO_CANDIDATE",
                "should_escalate": True, "escalation_gate": "CHEAP_GO",
                "escalation_reason": "t",
                "agent": {"direction": "long", "confidence": 0.8, "phase": "realized",
                          "asset": asset, "event_type": "opec", "materiality": 0.8,
                          "red_flags": [], "veto_flags": [], "no_edge_flags": [],
                          "mechanics": [], "key_facts": []},
                "chief": {"verdict": "GO", "side": "long", "in_price": "no",
                          "surprise": "direction", "asymmetry": "а", "invalidation": "б",
                          "forecast": "в", "horizon_hours": 24, "confidence": 0.8,
                          "levels": {"entry": 90.0, "invalidation": 88.0, "target": 95.0},
                          "summary": "с", "journal_reason": "р", "_usage": {}}}
    monkeypatch.setattr(S.orchestrator, "process", fake_process)

    item = {"title": "OPEC surprise cut", "url": "https://www.rigzone.com/news/cut",
            "source": "rigzone", "lead_class": "LAGGING", "source_class": "rss",
            "time": "2026-06-11T18:00:00Z"}
    asyncio.run(S.process_item(item, None, dry=False))
    assert journaled and journaled[0]["verdict"] == "WATCH"   # GO с LAGGING → демоут в WATCH
    assert journaled[0]["side"] == "long"                     # сторона остаётся для журнала


# 5. отчёт онбординга видит все новые источники и ожидаемые поля
def test_onboarding_report_includes_new_sources():
    data = OR.build(only_onboarding=True)
    by_name = {r["source"]: r for r in data["rows"]}
    for name in ONBOARDING:
        assert name in by_name, name
        row = by_name[name]
        for field in ("layer", "enabled", "onboarding_status", "expected_body", "raw_items",
                      "resolved_urls", "machine_docs", "full_body", "title_only",
                      "avg_text_len", "cards", "chief_calls", "tg_watch_go", "no_go",
                      "matured_outcomes", "idio_miss", "idio_miss_rate", "cost_rub",
                      "recommendation"):
            assert field in row, f"{name}.{field}"
    md = OR.render_md(data)
    assert "Чеклист оценки 24–48ч" in md and "enabled: false" in md


def test_onboarding_recommendations_honest():
    # needs_key/needs_provider не превращаются в keep; disabled помечен роллбэком
    assert "needs key/provider" in OR.recommend({"onboarding_status": "needs_key",
                                                 "enabled": True}, {})
    assert "disabled" in OR.recommend({"onboarding_status": "candidate", "enabled": False}, {})
    rec = OR.recommend({"onboarding_status": "candidate", "enabled": True,
                        "expected_body": "direct_body"},
                       {"machine_docs": 10, "title_only": 8, "full_body": 2,
                        "chief_calls": 0, "tg_watch_go": 0, "idio_miss": 0})
    assert "needs parser" in rec
