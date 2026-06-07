# -*- coding: utf-8 -*-
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.sources.eia import fetch_eia_schedule  # noqa: E402


class _Resp:
    def __init__(self, text):
        self.text = text


def test_fetch_eia_schedule_parses_next_release(monkeypatch):
    html = """
    <html><body>
    <h1>Weekly Petroleum Status Report</h1>
    Data for week ending May 29, 2026 Release Date: June 3, 2026 Next Release Date: June 10, 2026
    </body></html>
    """

    monkeypatch.setattr("src.scout.sources.eia.requests.get", lambda *a, **k: _Resp(html))

    rows = fetch_eia_schedule(limit=2)

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "eia"
    assert row["phase"] == "EXPECTED"
    assert row["event_type"] == "inventory"
    assert row["asset"] == "CL"
    assert row["time"] == "2026-06-10T15:30:00Z"
