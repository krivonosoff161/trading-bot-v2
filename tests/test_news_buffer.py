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
