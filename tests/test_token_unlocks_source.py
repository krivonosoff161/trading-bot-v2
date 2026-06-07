# -*- coding: utf-8 -*-
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.sources.token_unlocks import fetch_upcoming_unlocks  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_upcoming_unlocks_silent_without_key(monkeypatch):
    monkeypatch.delenv("TOKENOMIST_API_KEY", raising=False)
    rows = fetch_upcoming_unlocks(limit=10)
    assert rows == []


def test_fetch_upcoming_unlocks_filters_to_tracked_l2_and_thresholds(monkeypatch):
    monkeypatch.setenv("TOKENOMIST_API_KEY", "tok-test")

    def fake_get(url, params=None, headers=None, timeout=None):
        assert headers["x-api-key"] == "tok-test"
        return _Resp(
            {
                "metadata": {"totalPages": 1},
                "data": [
                    {
                        "tokenId": "arbitrum",
                        "tokenName": "Arbitrum",
                        "tokenSymbol": "ARB",
                        "marketCap": 700000000,
                        "releasedPercentage": 42.5,
                        "upcomingEvent": {
                            "unlockDate": "2026-06-10T12:00:00Z",
                            "cliffUnlocks": {
                                "cliffAmount": 12500000,
                                "cliffValue": 10500000,
                                "valueToMarketCap": 1.5,
                                "allocationBreakdown": [
                                    {
                                        "allocationName": "Team",
                                        "standardAllocationName": "Founder / Team",
                                        "cliffAmount": 7000000,
                                        "cliffValue": 5900000,
                                    }
                                ],
                            },
                        },
                    },
                    {
                        "tokenId": "btc",
                        "tokenName": "Bitcoin",
                        "tokenSymbol": "BTC",
                        "marketCap": 1000000000,
                        "releasedPercentage": 99.0,
                        "upcomingEvent": {
                            "unlockDate": "2026-06-10T12:00:00Z",
                            "cliffUnlocks": {
                                "cliffAmount": 1,
                                "cliffValue": 10000000,
                                "valueToMarketCap": 2.0,
                                "allocationBreakdown": [],
                            },
                        },
                    },
                    {
                        "tokenId": "sui",
                        "tokenName": "Sui",
                        "tokenSymbol": "SUI",
                        "marketCap": 800000000,
                        "releasedPercentage": 35.0,
                        "upcomingEvent": {
                            "unlockDate": "2026-06-11T12:00:00Z",
                            "cliffUnlocks": {
                                "cliffAmount": 1000000,
                                "cliffValue": 500000,
                                "valueToMarketCap": 0.1,
                                "allocationBreakdown": [],
                            },
                        },
                    },
                ],
            }
        )

    monkeypatch.setattr("src.scout.sources.token_unlocks.requests.get", fake_get)

    rows = fetch_upcoming_unlocks(limit=10)

    assert len(rows) == 1
    assert rows[0]["asset"] == "ARB"
    assert rows[0]["phase"] == "EXPECTED"
    assert rows[0]["event_type"] == "unlock"
    assert rows[0]["trigger_type"] == "token_unlock_calendar"
    assert rows[0]["unlock_value_to_mcap_pct"] == 1.5
