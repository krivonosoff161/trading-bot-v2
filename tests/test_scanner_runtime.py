# -*- coding: utf-8 -*-
"""
test_scanner_runtime.py - runtime guards for scanner_v0 filters.

Checks small pure helpers that keep noisy lagging content away from expensive LLM calls.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.scanner_v0 import is_stale_story, parse_source_ts  # noqa: E402


def test_parse_source_ts_rss_pubdate():
    ts = parse_source_ts("Sat, 07 Jun 2026 04:00:36 GMT")
    assert ts is not None
    assert ts.year == 2026 and ts.month == 6 and ts.day == 7


def test_stale_story_drops_old_google_news():
    assert is_stale_story(
        "Thu, 30 Apr 2026 07:00:00 GMT",
        source="google_news_equities",
        lead_class="LAGGING",
        source_class="rss",
        now_utc=parse_source_ts("Sun, 07 Jun 2026 07:00:00 GMT"),
    )


def test_stale_story_keeps_recent_google_news():
    assert not is_stale_story(
        "Sat, 07 Jun 2026 04:00:36 GMT",
        source="google_news_equities",
        lead_class="LAGGING",
        source_class="rss",
        now_utc=parse_source_ts("Sun, 07 Jun 2026 07:00:00 GMT"),
    )


def test_stale_story_never_blocks_leading():
    assert not is_stale_story(
        "Thu, 30 Apr 2026 07:00:00 GMT",
        source="sec_edgar",
        lead_class="LEADING",
        source_class="api",
        now_utc=parse_source_ts("Sun, 07 Jun 2026 07:00:00 GMT"),
    )
