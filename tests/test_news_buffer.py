# -*- coding: utf-8 -*-
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import news_buffer as NB  # noqa: E402


def test_news_buffer_ingest_resolve_normalize_ready(tmp_path):
    db = tmp_path / "news_buffer.sqlite"
    item = {
        "title": "ZEC Crashes 38% as Zcash Discloses Critical Counterfeiting Vulnerability",
        "url": "https://decrypt.co/example-zec-bug",
        "time": "2026-06-06",
        "source": "decrypt",
        "source_class": "rss",
        "lead_class": "LAGGING",
        "text": "Zcash disclosed a critical counterfeiting vulnerability. " * 20,
    }

    assert NB.ingest_items([item], path=db)["inserted"] == 1
    assert NB.resolve_pending(limit=10, path=db, dry=True)["resolved"] == 1
    norm = NB.normalize_pending(limit=10, path=db)
    assert norm == {"ready": 1, "dropped": 0}

    ready = NB.ready_items(limit=10, path=db)
    assert len(ready) == 1
    assert ready[0]["asset"] == "ZEC"
    assert ready[0]["layer"] == 2
    assert ready[0]["event_key"] == "ZEC::security_incident"
    assert ready[0]["text"]

    NB.mark_status(ready[0]["buffer_doc_id"], NB.STATUS_ANALYZED, path=db)
    stats = NB.stats(path=db)
    assert stats["raw"][NB.STATUS_ANALYZED] == 1


def test_recent_mentions_returns_asset_history(tmp_path):
    db = tmp_path / "nb_mentions.sqlite"
    item = {
        "title": "ZEC Crashes 38% as Zcash Discloses Critical Counterfeiting Vulnerability",
        "url": "https://decrypt.co/example-zec-mentions",
        "time": "2026-06-06",
        "source": "decrypt",
        "source_class": "rss",
        "lead_class": "LAGGING",
        "text": "Zcash disclosed a critical counterfeiting vulnerability. " * 20,
    }
    NB.ingest_items([item], path=db)
    NB.resolve_pending(limit=10, path=db, dry=True)
    NB.normalize_pending(limit=10, path=db)

    hits = NB.recent_mentions("ZEC", limit=5, path=db)
    assert len(hits) == 1
    assert hits[0]["asset"] == "ZEC"
    assert hits[0]["source"] == "decrypt"
    assert hits[0]["title"]
    # Unknown asset yields nothing, never raises.
    assert NB.recent_mentions("NOPE", path=db) == []
    assert NB.recent_mentions("", path=db) == []


def test_recent_mentions_newest_first_and_limit(tmp_path, monkeypatch):
    db = tmp_path / "nb_order.sqlite"

    # Monotonic clock so each item's created_at is strictly later than the previous,
    # making the ORDER BY created_at DESC deterministic (ordering is on created_at,
    # set per normalize batch, NOT on the item's published time).
    counter = {"n": 0}

    def _clock():
        counter["n"] += 1
        n = counter["n"]
        return f"2026-06-17T00:{n // 60:02d}:{n % 60:02d}Z"

    monkeypatch.setattr(NB, "now_iso", _clock)

    def _ingest_one(url, title):
        item = {"title": title, "url": url, "time": "2026-06-17", "source": "decrypt",
                "source_class": "rss", "lead_class": "LAGGING",
                "text": "Zcash discloses a critical counterfeiting vulnerability. " * 20}
        NB.ingest_items([item], path=db)
        NB.resolve_pending(limit=10, path=db, dry=True)
        NB.normalize_pending(limit=10, path=db)

    _ingest_one("https://decrypt.co/zec-older", "ZEC older Zcash story one")
    _ingest_one("https://decrypt.co/zec-newer", "ZEC newer Zcash story two")

    hits = NB.recent_mentions("ZEC", limit=5, path=db)
    assert len(hits) == 2
    assert hits[0]["url"].endswith("zec-newer")     # newest first
    assert hits[1]["url"].endswith("zec-older")
    # limit truncation returns only the newest.
    top = NB.recent_mentions("ZEC", limit=1, path=db)
    assert len(top) == 1
    assert top[0]["url"].endswith("zec-newer")


def test_news_buffer_propagates_cross_layer_flag(tmp_path):
    # L5-имя от крипто-ленты (decrypt allows [1,2]) → восстановлено strong cross-layer, флаг должен дойти
    db = tmp_path / "nb_xl.sqlite"
    spacex = {
        "title": "Kraken offers SpaceX IPO access through xStocks",
        "url": "https://decrypt.co/example-spacex", "time": "2026-06-07",
        "source": "decrypt", "source_class": "rss", "lead_class": "LAGGING",
        "text": "Kraken now offers SpaceX pre-IPO access via xStocks. " * 20,
    }
    NB.ingest_items([spacex], path=db)
    NB.resolve_pending(limit=10, path=db, dry=True)
    NB.normalize_pending(limit=10, path=db)
    ready = NB.ready_items(limit=10, path=db)
    assert len(ready) == 1 and ready[0]["asset"] == "SPACEX" and ready[0]["layer"] == 5
    assert ready[0]["cross_layer"] is True

    # in-layer матч (ZEC L2 от крипто-ленты) → НЕ cross_layer
    db2 = tmp_path / "nb_inlayer.sqlite"
    zec = {
        "title": "Zcash fixes Orchard bug after emergency network upgrade",
        "url": "https://decrypt.co/example-zec2", "time": "2026-06-07",
        "source": "decrypt", "source_class": "rss", "lead_class": "LAGGING",
        "text": "Zcash patched an Orchard bug after an upgrade. " * 20,
    }
    NB.ingest_items([zec], path=db2)
    NB.resolve_pending(limit=10, path=db2, dry=True)
    NB.normalize_pending(limit=10, path=db2)
    r2 = NB.ready_items(limit=10, path=db2)
    assert len(r2) == 1 and r2[0]["asset"] == "ZEC"
    assert not r2[0]["cross_layer"]


def test_news_buffer_keeps_distinct_prerouted_api_events_by_event_key(tmp_path):
    db = tmp_path / "news_buffer.sqlite"
    first = {
        "title": "BTC tactical flush A",
        "text": "OKX tactical monitor sees BTC flush.",
        "url": "https://www.okx.com/trade-swap/btc-usdt-swap",
        "time": "2026-06-07T12:00:00Z",
        "source": "btc_eth_tactical",
        "source_class": "api",
        "lead_class": "LEADING",
        "asset": "BTC",
        "okx_inst": "BTC-USDT-SWAP",
        "layer": 1,
        "baseline": "BTC-USDT-SWAP",
        "phase": "REALIZED",
        "event_type": "liquidation_regime",
        "trigger_type": "tactical_market_regime",
        "event_key": "tactical:BTC:liquidation_regime:20260607T1200Z",
    }
    second = {
        **first,
        "title": "BTC tactical flush B",
        "event_key": "tactical:BTC:liquidation_regime:20260607T1300Z",
        "time": "2026-06-07T13:00:00Z",
    }

    res = NB.ingest_items([first, second], path=db)
    assert res == {"inserted": 2, "updated": 0}

    stats = NB.stats(path=db)
    assert stats["raw"][NB.STATUS_NEW] == 2


def test_news_buffer_preserves_prerouted_event_key_after_normalize(tmp_path):
    db = tmp_path / "news_buffer.sqlite"
    item = {
        "title": "$ARX added to Coinbase roadmap",
        "text": "$ARX added to Coinbase roadmap",
        "url": "https://t.me/NewListingsFeed/3321",
        "time": "2026-06-09T20:03:42Z",
        "source": "tg_new_listings_feed",
        "source_class": "telegram_web",
        "lead_class": "LEADING",
        "asset": "ARX",
        "okx_inst": "ARX-USDT-SWAP",
        "layer": 2,
        "baseline": "BTC-USDT-SWAP",
        "phase": "REALIZED",
        "event_type": "exchange_listing",
        "trigger_type": "telegram_listing_feed",
        "event_key": "tg_new_listings_feed:3321:ARX",
    }

    assert NB.ingest_items([item], path=db)["inserted"] == 1
    assert NB.resolve_pending(limit=10, path=db, dry=True)["resolved"] == 1
    assert NB.normalize_pending(limit=10, path=db) == {"ready": 1, "dropped": 0}

    ready = NB.ready_items(limit=10, path=db)
    assert len(ready) == 1
    assert ready[0]["event_key"] == "tg_new_listings_feed:3321:ARX"


# ── Phase 1: identity/context field persistence ────────────────────────────


def test_news_buffer_preserves_okx_market_tape_fields(tmp_path):
    db = tmp_path / "news_buffer.sqlite"
    item = {
        "title": "BTC OKX market mover: 1h +15.00% / 24h +18.00%",
        "text": "OKX public market tape observed BTC-USDT-SWAP moving up.",
        "url": "https://www.okx.com/trade-swap/btc-usdt-swap",
        "time": "2026-06-17T03:00:00Z",
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
        "volume_quote_24h": 123456789.0,
    }

    assert NB.ingest_items([item], path=db)["inserted"] == 1
    assert NB.resolve_pending(limit=10, path=db, dry=True)["resolved"] == 1
    assert NB.normalize_pending(limit=10, path=db) == {"ready": 1, "dropped": 0}

    ready = NB.ready_items(limit=10, path=db)
    assert ready[0]["market_tape_trigger"] == "1h_move"
    assert ready[0]["move_1h_pct"] == 15.0
    assert ready[0]["move_24h_pct"] == 18.0
    assert ready[0]["volume_quote_24h"] == 123456789.0


def test_identity_fields_prerouted_tokenized_equity(tmp_path):
    """Pre-routed tokenized equity item preserves identity fields through buffer."""
    db = tmp_path / "nb_ident_tok.sqlite"
    item = {
        "title": "SPACEX listing on OKX via SPCX token",
        "url": "https://t.me/NewListingsFeed/9999",
        "time": "2026-06-14T10:00:00Z",
        "source": "tg_new_listings_feed",
        "source_class": "telegram_web",
        "lead_class": "LEADING",
        "asset": "SPACEX",
        "okx_inst": "SPACEX-USDT-SWAP",
        "layer": 5,
        "baseline": "QQQ-USDT-SWAP",
        "phase": "LEADING",
        "event_type": "tokenized_equity_listing",
        "event_key": "tg_new_listings_feed:9999:SPACEX",
        "asset_class": "pre_ipo_equity",
        "trigger_role": "signal",
        "requires_context": True,
        "identity_reason": "equity_alias_match:spacex",
        "identity_confidence": 0.90,
        "channel_kind": "listing",
    }
    assert NB.ingest_items([item], path=db)["inserted"] == 1
    NB.resolve_pending(limit=10, path=db, dry=True)
    NB.normalize_pending(limit=10, path=db)
    ready = NB.ready_items(limit=10, path=db)
    assert len(ready) == 1
    r = ready[0]
    assert r["asset"] == "SPACEX"
    assert r["asset_class"] == "pre_ipo_equity"
    assert r["trigger_role"] == "signal"
    assert r["requires_context"] is True
    assert r["identity_reason"] == "equity_alias_match:spacex"
    assert r["channel_kind"] == "listing"


def test_identity_fields_normal_crypto_rss(tmp_path):
    """RSS crypto item gets identity fields from identify_asset during normalize."""
    db = tmp_path / "nb_ident_rss.sqlite"
    item = {
        "title": "Bitcoin surges past $64,000 as ETF inflows rebound",
        "url": "https://cointelegraph.com/news/btc-etf",
        "time": "2026-06-14",
        "source": "cointelegraph",
        "source_class": "rss",
        "lead_class": "LAGGING",
        "text": "Bitcoin surged past $64,000 on renewed ETF inflows. " * 20,
    }
    assert NB.ingest_items([item], path=db)["inserted"] == 1
    NB.resolve_pending(limit=10, path=db, dry=True)
    NB.normalize_pending(limit=10, path=db)
    ready = NB.ready_items(limit=10, path=db)
    assert len(ready) == 1
    r = ready[0]
    assert r["asset"] == "BTC"
    assert r["asset_class"] is not None
    assert r["trigger_role"] is not None


def test_identity_fields_liquidation_flow(tmp_path):
    """Liquidation flow item preserves identity fields through buffer."""
    db = tmp_path / "nb_ident_liq.sqlite"
    item = {
        "title": "ETH Liquidated Long: $352K at $1,656",
        "url": "https://t.me/HyperliquidLiquidations/12345",
        "time": "2026-06-14T14:40:31Z",
        "source": "tg_hyperliquid_liquidations",
        "source_class": "telegram_web",
        "lead_class": "COINCIDENT",
        "asset": "ETH",
        "okx_inst": "ETH-USDT-SWAP",
        "layer": 1,
        "baseline": "BTC-USDT-SWAP",
        "phase": "COINCIDENT",
        "event_type": "liquidation_flow",
        "event_key": "tg_hyperliquid_liquidations:12345:ETH",
        "asset_class": "liquidation_flow",
        "trigger_role": "context_trigger",
        "requires_context": False,
        "identity_reason": "liquidation_flow_source",
        "identity_confidence": 0.85,
        "channel_kind": "liquidations",
    }
    assert NB.ingest_items([item], path=db)["inserted"] == 1
    NB.resolve_pending(limit=10, path=db, dry=True)
    NB.normalize_pending(limit=10, path=db)
    ready = NB.ready_items(limit=10, path=db)
    assert len(ready) == 1
    r = ready[0]
    assert r["asset"] == "ETH"
    assert r["asset_class"] == "liquidation_flow"
    assert r["trigger_role"] == "context_trigger"
    assert r["requires_context"] is False
    assert r["channel_kind"] == "liquidations"


def test_identity_fields_markettwits_unknown_ticker(tmp_path):
    """Markettwits item with unknown ticker gets needs_context identity."""
    db = tmp_path / "nb_ident_mt.sqlite"
    item = {
        "title": "$G7 саммит лидеров стран G7 во Франции",
        "url": "https://t.me/markettwits/55555",
        "time": "2026-06-14T12:46:33Z",
        "source": "tg_markettwits",
        "source_class": "telegram_web",
        "lead_class": "LEADING",
        "asset": "G7",
        "okx_inst": None,
        "layer": None,
        "baseline": None,
        "phase": "AMBIGUOUS",
        "event_type": "news_trigger",
        "event_key": "tg_markettwits:55555:G7",
        "asset_class": "unknown",
        "trigger_role": "needs_context",
        "requires_context": True,
        "identity_reason": "unknown_ticker_no_entity_match",
        "identity_confidence": 0.30,
        "channel_kind": "news",
    }
    assert NB.ingest_items([item], path=db)["inserted"] == 1
    NB.resolve_pending(limit=10, path=db, dry=True)
    NB.normalize_pending(limit=10, path=db)
    ready = NB.ready_items(limit=10, path=db)
    assert len(ready) == 1
    r = ready[0]
    assert r["asset"] == "G7"
    assert r["asset_class"] == "unknown"
    assert r["trigger_role"] == "needs_context"
    assert r["requires_context"] is True
    assert r["channel_kind"] == "news"
