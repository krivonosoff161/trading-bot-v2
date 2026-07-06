from __future__ import annotations

import pytest

from src.scout.public_channel.collector import enrich_public_source_rows
from src.scout.public_channel.contracts import item_from_source
from src.scout.public_channel.editor import build_post, deterministic_post
from src.scout.public_channel.safety import has_forbidden_advice, validate_public_post
from src.scout.public_channel.stats import format_public_stats_html
from src.scout.public_channel.telegram_format import format_telegram_html


def test_public_channel_deterministic_post_is_not_trade_advice():
    item = item_from_source(
        {
            "title": "OKX lists NEWCOIN perpetual swap",
            "url": "https://www.okx.com/example",
            "source": "okx_listings",
            "source_class": "api",
            "lead_class": "LEADING",
            "layer": 2,
            "event_type": "exchange_listing",
            "text": "OKX announced a new perpetual swap listing for NEWCOIN.",
        }
    )
    assert item is not None

    post = deterministic_post(item)
    ok, reason = validate_public_post(post)
    text = format_telegram_html(post)

    assert ok, reason
    assert "источник" in text
    assert "Не торговая рекомендация" in text
    assert "Что произошло:" not in text
    assert "Почему важно:" not in text
    assert not has_forbidden_advice(text.replace("perpetual swap", "perp"))


def test_public_channel_blocks_direct_trade_terms():
    assert has_forbidden_advice("Рекомендую вход от 1.23, стоп ниже, тейк выше")
    assert has_forbidden_advice("buy now with leverage")
    assert not has_forbidden_advice("наблюдаем новость и сверяем с первоисточником")


def test_public_channel_key_prefers_stable_event_key_over_title_numbers():
    base = {
        "url": "https://dexscreener.com/arbitrum/pair",
        "source": "dexscreener",
        "source_class": "api",
        "event_type": "dex_momentum",
        "event_key": "dex:ARB:dex_momentum:pair",
    }
    a = item_from_source({**base, "title": "ARB volume $1,000,000"})
    b = item_from_source({**base, "title": "ARB volume $1,500,000"})

    assert a is not None and b is not None
    assert a.key == b.key


def test_public_stats_format_is_aggregate_only():
    text = format_public_stats_html(
        {
            "product_trades": 12,
            "product_active_trades": 3,
            "training_by_result": {"take": 2, "stop": 1, "simple_be": 4, "expired_no_entry": 5},
            "product_active_by_family": {"continuation": 2, "pullback": 1},
            "delivery_sent": 5,
            "delivery_sent_cards": 2,
            "delivery_errors": 0,
        }
    )

    assert "12" in text
    assert "continuation" in text
    assert "Paper-режим" in text
    assert "entry" not in text.lower()


def test_public_channel_enriches_rss_title_with_machine_doc(monkeypatch):
    def fake_extract(url: str) -> dict:
        return {
            "url": url,
            "title": "Vitalik Buterin shares Lean Ethereum priorities",
            "date": "2026-07-06",
            "text": "Vitalik Buterin outlined Lean Ethereum priorities. "
            "The document discusses protocol simplification, security margins, "
            "and long-term Ethereum roadmap tradeoffs. " * 8,
        }

    monkeypatch.setattr("src.scout.page_extract.extract", fake_extract)
    rows = enrich_public_source_rows(
        [
            {
                "title": "Vitalik Buterin shares top priorities for new Lean Ethereum strawmap",
                "url": "https://example.test/vitalik",
                "source": "cointelegraph",
                "source_class": "rss",
            }
        ],
        limit=1,
    )

    row = rows[0]
    assert row["public_extraction_status"] == "extracted"
    assert row["public_text_quality"] == "full"
    assert "protocol simplification" in row["text"]
    assert row["public_machine_doc"]["schema"] == "PublicNewsMachineDoc.v1"


@pytest.mark.asyncio
async def test_llm_editor_cleans_field_labels_and_keeps_russian_card(monkeypatch):
    async def fake_call(*args, **kwargs):
        return (
            """
            {
              "headline": "Обновление Lean Ethereum",
              "category": "Ethereum roadmap",
              "what_happened": "Что произошло: Виталик Бутерин описал приоритеты Lean Ethereum.",
              "why_matters": "Почему важно: это задает контекст для будущих изменений протокола.",
              "watch_points": [
                "На что смотреть: реакция разработчиков Ethereum",
                "Why it matters: обновления по безопасности протокола"
              ],
              "public_ok": true,
              "skip_reason": ""
            }
            """,
            {"provider": "test", "status": "ok"},
        )

    monkeypatch.setattr("src.utils.llm_client.call", fake_call)
    item = item_from_source(
        {
            "title": "Vitalik Buterin shares top priorities for new Lean Ethereum strawmap",
            "url": "https://example.test/vitalik",
            "source": "cointelegraph",
            "source_class": "rss",
            "lead_class": "LAGGING",
            "text": "Vitalik Buterin outlined Lean Ethereum priorities and protocol simplification.",
            "public_extraction_status": "extracted",
            "public_text_quality": "usable",
        }
    )
    assert item is not None

    post, usage = await build_post(item, use_llm=True)
    text = format_telegram_html(post)

    assert usage["provider"] == "test"
    assert "Что произошло:" not in text
    assert "Почему важно:" not in text
    assert "На что смотреть:" not in text
    assert "Why it matters:" not in text
    assert "Виталик Бутерин описал" in text
    assert "Следим за:" in text
    assert not has_forbidden_advice(text)
