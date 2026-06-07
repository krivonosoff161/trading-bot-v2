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
