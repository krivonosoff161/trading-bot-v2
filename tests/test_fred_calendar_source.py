# -*- coding: utf-8 -*-
import datetime as dt
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.sources.fred_calendar import fetch_fred_calendar  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_fred_calendar_silent_without_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert fetch_fred_calendar(limit=8) == []


def test_fetch_fred_calendar_builds_expected_macro_items(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fred-test")
    today = dt.datetime.now(dt.timezone.utc).date()

    def fake_get(url, params=None, headers=None, timeout=None):
        assert params["api_key"] == "fred-test"
        assert params["include_release_dates_with_no_data"] == "true"
        return _Resp(
            {
                "release_dates": [
                    {"release_id": 10, "release_name": "Consumer Price Index", "date": today.isoformat()},
                    {
                        "release_id": 11,
                        "release_name": "Employment Situation",
                        "date": (today + dt.timedelta(days=1)).isoformat(),
                    },
                    {
                        "release_id": 12,
                        "release_name": "Federal Open Market Committee",
                        "date": (today + dt.timedelta(days=2)).isoformat(),
                    },
                    {"release_id": 13, "release_name": "Retail Sales", "date": (today + dt.timedelta(days=3)).isoformat()},
                ]
            }
        )

    monkeypatch.setattr("src.scout.sources.fred_calendar.requests.get", fake_get)

    rows = fetch_fred_calendar(limit=8)

    assert len(rows) == 6
    btc_cpi = next(r for r in rows if r["asset"] == "BTC" and r["event_type"] == "cpi")
    xau_jobs = next(r for r in rows if r["asset"] == "XAU" and r["event_type"] == "jobs")
    assert btc_cpi["source"] == "fred_calendar"
    assert btc_cpi["phase"] == "EXPECTED"
    assert btc_cpi["layer"] == 1
    assert xau_jobs["layer"] == 3
    assert "FRED release calendar" in btc_cpi["text"]
