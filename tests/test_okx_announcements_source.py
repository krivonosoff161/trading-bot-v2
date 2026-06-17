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


def test_extract_assets_catches_perp_dash_pairs():
    assert "RE" in OKXA._extract_assets("OKX lists RE-USDT-SWAP perpetual")
    assert "GRAM" in OKXA._extract_assets("Trading update: GRAM-USDT")
    # quote/excluded tickers are not emitted as assets
    assert "USDT" not in OKXA._extract_assets("RE-USDT-SWAP listing")


def test_fetch_preserves_announcement_provenance(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _Resp({"code": "0", "data": [{"details": [{
            "annId": "a9", "annType": "announcements-new-listings",
            "title": "OKX will list RE/USDT for spot trading",
            "url": "/help/re-listing", "pTime": "1780822800000"}]}]})

    monkeypatch.setattr(OKXA.requests, "get", fake_get)
    rows = OKXA.fetch_okx_announcements(limit=5)
    assert rows[0]["announcement_id"] == "a9"
    assert rows[0]["detail_url"].endswith("/help/re-listing")
    assert rows[0]["extraction_status"] == "title_only"


def test_with_body_enriches_via_injected_fetch(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _Resp({"code": "0", "data": [{"details": [{
            "annId": "a10", "annType": "announcements-new-listings",
            "title": "OKX will list RE/USDT for spot trading",
            "url": "/help/re-listing", "pTime": "1780822800000"}]}]})

    monkeypatch.setattr(OKXA.requests, "get", fake_get)

    def fake_fetch(url):
        return {"text": "Official detail body about the RE listing. " * 20}

    rows = OKXA.fetch_okx_announcements(limit=5, with_body=True, fetch=fake_fetch)
    assert rows[0]["extraction_status"] == "api_detail_body"
    assert "Detail:" in rows[0]["text"]


def test_event_type_and_phase_branches():
    cases = [
        ("OKX will delist FOO/USDT", "delisting", "FUTURE"),
        ("OKX has completed delisting of FOO/USDT", "delisting", "REALIZED"),
        ("OKX lists RE/USDT for spot trading", "listing", "LEADING"),
        ("OKX will suspend deposits for BAR-USDT", "deposit_withdrawal_update", "FUTURE"),
        ("OKX launches BAZ event contracts", "event_contract", "REALIZED"),
    ]
    for title, exp_type, exp_phase in cases:
        et = OKXA._event_type("", title)
        assert et == exp_type, (title, et)
        assert OKXA._phase(et, title) == exp_phase, (title, OKXA._phase(et, title))


def test_with_body_marks_too_short_when_no_body(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _Resp({"code": "0", "data": [{"details": [{
            "annId": "a11", "annType": "announcements-new-listings",
            "title": "OKX will list RE/USDT for spot trading",
            "url": "/help/re-listing", "pTime": "1780822800000"}]}]})

    monkeypatch.setattr(OKXA.requests, "get", fake_get)
    rows = OKXA.fetch_okx_announcements(limit=5, with_body=True, fetch=lambda url: {"text": "tiny"})
    assert rows[0]["extraction_status"] == "detail_too_short"
