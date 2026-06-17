# -*- coding: utf-8 -*-
from src.scout.sources import okx_announcements as OKXA


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_okx_announcements_fans_out_listing_assets(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _Resp(
            {
                "code": "0",
                "data": [
                    {
                        "details": [
                            {
                                "annId": "a1",
                                "annType": "announcements-new-listings",
                                "title": "OKX will launch PROS/USD and PROS/EUR for spot trading",
                                "url": "/help/pros-listing",
                                "pTime": "1780822800000",
                            }
                        ]
                    }
                ],
            }
        )

    monkeypatch.setattr(OKXA.requests, "get", fake_get)

    rows = OKXA.fetch_okx_announcements(limit=10)

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "okx_announcements"
    assert row["asset"] == "PROS"
    assert row["okx_inst"] == "PROS-USDT-SWAP"
    assert row["event_type"] == "listing"
    assert row["phase"] == "FUTURE"
    assert row["requires_context"] is False


def test_fetch_okx_announcements_skips_unroutable_operational_notice(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _Resp(
            {
                "code": "0",
                "data": {
                    "details": [
                        {
                            "annId": "a2",
                            "annType": "announcements-api",
                            "title": "OKX to adjust API maintenance window",
                            "url": "/help/api-maintenance",
                        }
                    ]
                },
            }
        )

    monkeypatch.setattr(OKXA.requests, "get", fake_get)

    assert OKXA.fetch_okx_announcements(limit=10) == []
