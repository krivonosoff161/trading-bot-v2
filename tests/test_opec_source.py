# -*- coding: utf-8 -*-
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.sources.opec import fetch_opec_schedule  # noqa: E402


class _Resp:
    def __init__(self, text):
        self.text = text


def test_fetch_opec_schedule_parses_next_meeting(monkeypatch):
    index_html = """
    <html><body>
      <a href="pr-detail/604-7-june-2026.html">Read more</a>
    </body></html>
    """
    article_html = """
    <html><body>
      <h3>Saudi Arabia, Russia, Iraq, Kuwait, Kazakhstan, Algeria, and Oman adjust production and reaffirm commitment to market stability</h3>
      <p>The seven OPEC+ countries will hold monthly meetings to review market conditions, conformity, and compensation. The seven countries will meet on 5 July 2026.</p>
    </body></html>
    """

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/press-releases.html"):
            return _Resp(index_html)
        if url.endswith("pr-detail/604-7-june-2026.html"):
            return _Resp(article_html)
        raise AssertionError(url)

    monkeypatch.setattr("src.scout.sources.opec.requests.get", fake_get)

    rows = fetch_opec_schedule(limit=2)

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "opec"
    assert row["phase"] == "EXPECTED"
    assert row["event_type"] == "opec"
    assert row["asset"] == "CL"
    assert row["time"] == "2026-07-05T10:00:00Z"
