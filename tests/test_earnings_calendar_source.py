# -*- coding: utf-8 -*-
import datetime as dt
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.sources.earnings_calendar import fetch_earnings_calendar  # noqa: E402


class _Resp:
    def __init__(self, *, content=None, text=None):
        self.content = content or b""
        self.text = text or ""


def test_fetch_earnings_calendar_builds_expected_l5_items(monkeypatch):
    now = dt.datetime(2026, 6, 7, tzinfo=dt.timezone.utc)

    class _FakeDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)

    feed_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>8-K - NVIDIA CORP (0001045810) (Filer)</title>
        <updated>2026-06-06T12:00:00+00:00</updated>
        <link href="https://www.sec.gov/Archives/edgar/data/1045810/nvda-8k.htm" />
      </entry>
      <entry>
        <title>8-K - TESLA, INC. (0001318605) (Filer)</title>
        <updated>2026-04-01T12:00:00+00:00</updated>
        <link href="https://www.sec.gov/Archives/edgar/data/1318605/tsla-8k.htm" />
      </entry>
    </feed>"""
    filing_html = """
    <html><body>
      NVIDIA announced it will release financial results on August 20, 2026
      and host a conference call after market close.
    </body></html>
    """

    def fake_get(url, headers=None, timeout=None):
        if "getcurrent" in url:
            return _Resp(content=feed_xml)
        if "nvda-8k.htm" in url:
            return _Resp(text=filing_html)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("src.scout.sources.earnings_calendar.dt.datetime", _FakeDateTime)
    monkeypatch.setattr("src.scout.sources.earnings_calendar.requests.get", fake_get)

    rows = fetch_earnings_calendar(limit=8)

    assert len(rows) == 1
    row = rows[0]
    assert row["asset"] == "NVDA"
    assert row["layer"] == 5
    assert row["phase"] == "EXPECTED"
    assert row["event_type"] == "earnings"
    assert row["source"] == "earnings_calendar"
    assert row["event_key"] == "earnings:NVDA:2026-08-20"
    assert row["time"] == "2026-08-20T00:00:00Z"
    assert "SEC-linked company announcement" in row["text"]
