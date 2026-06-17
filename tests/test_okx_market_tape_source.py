# -*- coding: utf-8 -*-
from src.scout.sources import okx_market_tape as OKXT


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_okx_market_tape_emits_broad_market_mover(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        params = params or {}
        if url.endswith("/api/v5/market/tickers"):
            return _Resp(
                {
                    "code": "0",
                    "data": [
                        {
                            "instId": "BTC-USDT-SWAP",
                            "last": "115000",
                            "open24h": "100000",
                            "volCcy24h": "100000000",
                        },
                        {
                            "instId": "ETH-USDT-SWAP",
                            "last": "2500",
                            "open24h": "2490",
                            "volCcy24h": "50000000",
                        },
                    ],
                }
            )
        if url.endswith("/api/v5/market/history-candles") and params.get("instId") == "BTC-USDT-SWAP":
            return _Resp({"code": "0", "data": [["1780826400000", "0", "0", "0", "114500"], ["1780822800000", "0", "0", "0", "100000"]]})
        if url.endswith("/api/v5/market/history-candles") and params.get("instId") == "ETH-USDT-SWAP":
            return _Resp({"code": "0", "data": [["1780826400000", "0", "0", "0", "2495"], ["1780822800000", "0", "0", "0", "2490"]]})
        return _Resp({"code": "0", "data": []})

    monkeypatch.setattr(OKXT.requests, "get", fake_get)

    rows = OKXT.fetch_okx_market_tape(limit=5, candidate_limit=5)

    btc = next((row for row in rows if row["asset"] == "BTC"), None)
    assert btc is not None
    assert btc["source"] == "okx_market_tape"
    assert btc["lead_class"] == "COINCIDENT"
    assert btc["event_type"] == "market_mover"
    assert btc["market_tape_trigger"] == "1h_move"
    assert btc["requires_context"] is False
    assert btc["move_1h_pct"] == 15.0


def test_fetch_okx_market_tape_stays_silent_on_small_moves(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/api/v5/market/tickers"):
            return _Resp(
                {
                    "code": "0",
                    "data": [
                        {
                            "instId": "BTC-USDT-SWAP",
                            "last": "100500",
                            "open24h": "100000",
                            "volCcy24h": "100000000",
                        }
                    ],
                }
            )
        if url.endswith("/api/v5/market/history-candles"):
            return _Resp({"code": "0", "data": [["1780822800000", "0", "0", "0", "100000"]]})
        return _Resp({"code": "0", "data": []})

    monkeypatch.setattr(OKXT.requests, "get", fake_get)

    assert OKXT.fetch_okx_market_tape(limit=5, candidate_limit=5) == []
