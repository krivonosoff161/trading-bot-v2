# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.sources import telegram_web as TG  # noqa: E402
from src.scout import router  # noqa: E402
from src.scout import scanner_v0 as S  # noqa: E402


HTML = """
<section class="tgme_channel_history js-message_history">
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="NewListingsFeed/3321">
  <div class="tgme_widget_message_text js-message_text" dir="auto"><b>&#036;ARX</b>, <b>&#036;RE</b> added to <a href="https://www.coinbase.com/roadmap">Coinbase roadmap</a></div>
  <a class="tgme_widget_message_date" href="https://t.me/NewListingsFeed/3321"><time datetime="2026-06-09T20:03:42+00:00" class="time">20:03</time></a>
</div></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="HyperliquidLiquidations/64112">
  <div class="tgme_widget_message_text js-message_text" dir="auto">🔴 #xyz:CL Liquidated Long: $64.6K at $80.77 - hyperlens</div>
  <a class="tgme_widget_message_date" href="https://t.me/HyperliquidLiquidations/64112"><time datetime="2026-06-13T16:49:22+00:00" class="time">16:49</time></a>
</div></div>
</section>
"""

SPCXX_HTML = """
<section class="tgme_channel_history js-message_history">
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="NewListingsFeed/3322">
  <div class="tgme_widget_message_text js-message_text" dir="auto"><b>&#036;SPCXX</b> listed on Bybit spot</div>
  <a class="tgme_widget_message_date" href="https://t.me/NewListingsFeed/3322"><time datetime="2026-06-12T16:22:26+00:00" class="time">16:22</time></a>
</div></div>
</section>
"""


def test_parse_channel_html_extracts_posts():
    posts = TG.parse_channel_html(HTML, channel="NewListingsFeed")
    assert len(posts) == 1
    assert posts[0].post_id == "3321"
    assert posts[0].published_at == "2026-06-09T20:03:42Z"
    assert "$ARX, $RE added to Coinbase roadmap" in posts[0].text
    assert posts[0].links == ("https://www.coinbase.com/roadmap",)


def test_listing_post_fans_out_to_ticker_events(monkeypatch):
    monkeypatch.setattr(TG, "source_meta", lambda source_id: {
        "enabled": True,
        "source_class": "telegram_web",
        "channel": "NewListingsFeed",
        "url": "https://t.me/s/NewListingsFeed",
        "telegram_kind": "listing",
        "lead_class": "LEADING",
    })
    items = TG.fetch_source("tg_new_listings_feed", fetch=lambda url: HTML)
    by_asset = {item["asset"]: item for item in items if item.get("source") == "tg_new_listings_feed"}
    assert set(by_asset) == {"ARX", "RE"}
    assert by_asset["ARX"]["event_key"] == "tg_new_listings_feed:3321:ARX"
    assert by_asset["ARX"]["source_class"] == "telegram_web"
    assert by_asset["ARX"]["lead_class"] == "LEADING"
    assert by_asset["ARX"]["layer"] == 2


def test_tokenized_equity_listing_routes_to_l5(monkeypatch):
    monkeypatch.setattr(TG, "source_meta", lambda source_id: {
        "enabled": True,
        "source_class": "telegram_web",
        "channel": "NewListingsFeed",
        "url": "https://t.me/s/NewListingsFeed",
        "telegram_kind": "listing",
        "lead_class": "LEADING",
    })

    items = TG.fetch_source("tg_new_listings_feed", fetch=lambda url: SPCXX_HTML)

    assert len(items) == 1
    item = items[0]
    assert item["asset"] == "SPCXX"
    assert item["layer"] == 5
    assert item["baseline"] == "QQQ-USDT-SWAP"
    assert item["event_type"] == "tokenized_equity_listing"


def test_liquidation_post_is_coincident_context(monkeypatch):
    monkeypatch.setattr(TG, "source_meta", lambda source_id: {
        "enabled": True,
        "source_class": "telegram_web",
        "channel": "HyperliquidLiquidations",
        "url": "https://t.me/s/HyperliquidLiquidations",
        "telegram_kind": "liquidations",
        "lead_class": "COINCIDENT",
    })
    items = TG.fetch_source("tg_hyperliquid_liquidations", fetch=lambda url: HTML)
    item = [x for x in items if x.get("source") == "tg_hyperliquid_liquidations"][0]
    assert item["asset"] == "CL"
    assert item["layer"] == 4
    assert item["event_type"] == "liquidation_flow"
    assert item["lead_class"] == "COINCIDENT"


def test_fetch_telegram_web_sources_reads_enabled_sources(monkeypatch):
    monkeypatch.setattr(TG, "enabled_sources", lambda: {
        "tg_new_listings_feed": {
            "enabled": True,
            "source_class": "telegram_web",
            "channel": "NewListingsFeed",
            "url": "https://t.me/s/NewListingsFeed",
            "telegram_kind": "listing",
            "lead_class": "LEADING",
        },
        "cointelegraph": {"enabled": True, "source_class": "rss"},
    })
    monkeypatch.setattr(TG, "source_meta", lambda source_id: TG.enabled_sources()[source_id])
    monkeypatch.setattr(TG, "_fetch_url", lambda url: HTML)
    items = TG.fetch_telegram_web_sources()
    assert {item["source"] for item in items} == {"tg_new_listings_feed"}
    assert {item["asset"] for item in items} == {"ARX", "RE"}


def test_registry_has_three_public_telegram_sources():
    sources = router.enabled_sources()
    for name in ("tg_new_listings_feed", "tg_markettwits", "tg_hyperliquid_liquidations"):
        assert sources[name]["source_class"] == "telegram_web"
        assert sources[name]["enabled"] is True
        assert sources[name]["expected_body"] == "telegram_text"


def test_scanner_ingest_summary_includes_tg_web(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("TRADING_BOT_RESEARCH_ROOT", str(tmp_path))
    monkeypatch.setattr(S, "fetch_rss", lambda: [])
    monkeypatch.setattr(S, "fetch_new_listings", lambda **kw: [])
    monkeypatch.setattr(S, "fetch_recent_filings", lambda **kw: [])
    monkeypatch.setattr(S, "fetch_btc_eth_tactical", lambda **kw: [])
    monkeypatch.setattr(S, "fetch_fred_calendar", lambda **kw: [])
    monkeypatch.setattr(S, "fetch_eia_schedule", lambda **kw: [])
    monkeypatch.setattr(S, "fetch_opec_schedule", lambda **kw: [])
    monkeypatch.setattr(S, "fetch_earnings_calendar", lambda **kw: [])
    monkeypatch.setattr(S, "fetch_upcoming_unlocks", lambda **kw: [])
    monkeypatch.setattr(S, "fetch_alt_flow_signals", lambda **kw: [])
    monkeypatch.setattr(S, "fetch_token_risk_signals", lambda items, **kw: [])
    monkeypatch.setattr(S, "fetch_telegram_web_sources", lambda **kw: [
        {"title": "$ATOM listed on Robinhood", "url": "https://t.me/NewListingsFeed/1",
         "source": "tg_new_listings_feed", "source_class": "telegram_web",
         "lead_class": "LEADING", "asset": "ATOM", "layer": 2},
    ])
    monkeypatch.setattr(S, "load_seen", lambda: set())
    monkeypatch.setattr(S, "save_seen", lambda seen: None)
    monkeypatch.setattr(S, "market_ctx_line", lambda: None)
    monkeypatch.setattr(S, "okx_last", lambda inst: None)
    monkeypatch.setattr(S.NB, "ingest_items", lambda items: {"inserted": len(items), "updated": 0})
    monkeypatch.setattr(
        S.NB,
        "resolve_pending",
        lambda limit, **kwargs: {
            "resolved": 0,
            "remaining_selected": 4,
            "budget_exhausted": True,
            "stop_requested": False,
        },
    )
    monkeypatch.setattr(S.NB, "normalize_pending", lambda limit: {"ready": 0, "dropped": 0})
    monkeypatch.setattr(S.NB, "ready_items", lambda limit: [])
    monkeypatch.setattr(S.J, "write_ingest", lambda rows: len(rows))
    monkeypatch.setattr(S.J, "write_budget", lambda row: None)
    monkeypatch.setattr(S.J, "ensure_pending_store", lambda: None)
    monkeypatch.setattr(S.PS, "expire_old", lambda: {"expired": 0})

    import asyncio
    asyncio.run(
        S.run(
            limit=1,
            dry=False,
            use_buffer=True,
            max_pass_seconds=30.0,
            resolve_max_seconds=10.0,
        )
    )
    assert "TG_WEB=1" in capsys.readouterr().out
    checkpoint = json.loads(
        (
            tmp_path
            / "state"
            / "product_progress"
            / "scanner.json"
        ).read_text(encoding="utf-8")
    )
    assert checkpoint["status"] == "degraded"
    assert checkpoint["metrics"]["budget_exhausted"] is True
    assert checkpoint["metrics"]["resolver_deferred"] == 4
    assert checkpoint["metrics"]["completed_chunks"] >= 2
