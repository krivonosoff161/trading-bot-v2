# -*- coding: utf-8 -*-
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.sources.btc_eth_tactical import fetch_btc_eth_tactical  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_btc_eth_tactical_emits_liquidation_regime(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        inst = (params or {}).get("instId")
        if url.endswith("/api/v5/market/ticker") and inst == "BTC-USDT-SWAP":
            return _Resp({"code": "0", "data": [{"last": "102000", "volCcy24h": "188000"}]})
        if url.endswith("/api/v5/market/history-candles") and inst == "BTC-USDT-SWAP":
            return _Resp({"code": "0", "data": [["1780822800000", "0", "0", "0", "100000"]]})
        if url.endswith("/api/v5/public/funding-rate") and inst == "BTC-USDT-SWAP":
            return _Resp({"code": "0", "data": [{"fundingRate": "-0.00031"}]})
        if url.endswith("/api/v5/rubik/stat/contracts/open-interest-history") and inst == "BTC-USDT-SWAP":
            return _Resp({"code": "0", "data": [{"oi": "900"}, {"oi": "1000"}]})

        if url.endswith("/api/v5/market/ticker") and inst == "ETH-USDT-SWAP":
            return _Resp({"code": "0", "data": [{"last": "2500", "volCcy24h": "90000"}]})
        if url.endswith("/api/v5/market/history-candles") and inst == "ETH-USDT-SWAP":
            return _Resp({"code": "0", "data": [["1780822800000", "0", "0", "0", "2498"]]})
        if url.endswith("/api/v5/public/funding-rate") and inst == "ETH-USDT-SWAP":
            return _Resp({"code": "0", "data": [{"fundingRate": "0.00005"}]})
        if url.endswith("/api/v5/rubik/stat/contracts/open-interest-history") and inst == "ETH-USDT-SWAP":
            return _Resp({"code": "0", "data": [{"oi": "1000"}, {"oi": "1000"}]})

        return _Resp({"code": "0", "data": []})

    monkeypatch.setattr("src.scout.sources.btc_eth_tactical.requests.get", fake_get)

    rows = fetch_btc_eth_tactical(limit=4)

    btc = next((r for r in rows if r["asset"] == "BTC"), None)
    assert btc is not None
    assert btc["source"] == "btc_eth_tactical"
    assert btc["lead_class"] == "LEADING"
    assert btc["event_type"] == "liquidation_regime"
    assert btc["bias_hint"] == "long"
    assert btc["oi_delta_1h_pct"] == -10.0


def test_fetch_btc_eth_tactical_emits_funding_squeeze(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        inst = (params or {}).get("instId")
        if url.endswith("/api/v5/market/ticker") and inst == "BTC-USDT-SWAP":
            return _Resp({"code": "0", "data": [{"last": "101000", "volCcy24h": "150000"}]})
        if url.endswith("/api/v5/market/history-candles") and inst == "BTC-USDT-SWAP":
            return _Resp({"code": "0", "data": [["1780822800000", "0", "0", "0", "100900"]]})
        if url.endswith("/api/v5/public/funding-rate") and inst == "BTC-USDT-SWAP":
            return _Resp({"code": "0", "data": [{"fundingRate": "0.00004"}]})
        if url.endswith("/api/v5/rubik/stat/contracts/open-interest-history") and inst == "BTC-USDT-SWAP":
            return _Resp({"code": "0", "data": [{"oi": "1000"}, {"oi": "1000"}]})

        if url.endswith("/api/v5/market/ticker") and inst == "ETH-USDT-SWAP":
            return _Resp({"code": "0", "data": [{"last": "2505", "volCcy24h": "98000"}]})
        if url.endswith("/api/v5/market/history-candles") and inst == "ETH-USDT-SWAP":
            return _Resp({"code": "0", "data": [["1780822800000", "0", "0", "0", "2500"]]})
        if url.endswith("/api/v5/public/funding-rate") and inst == "ETH-USDT-SWAP":
            return _Resp({"code": "0", "data": [{"fundingRate": "0.00042"}]})
        if url.endswith("/api/v5/rubik/stat/contracts/open-interest-history") and inst == "ETH-USDT-SWAP":
            return _Resp({"code": "0", "data": [{"oi": "1120"}, {"oi": "1000"}]})

        return _Resp({"code": "0", "data": []})

    monkeypatch.setattr("src.scout.sources.btc_eth_tactical.requests.get", fake_get)

    rows = fetch_btc_eth_tactical(limit=4)

    eth = next((r for r in rows if r["asset"] == "ETH"), None)
    assert eth is not None
    assert eth["event_type"] == "funding_squeeze"
    assert eth["bias_hint"] == "short"
    assert eth["funding_rate"] == 0.00042
    assert eth["oi_delta_1h_pct"] == 12.0


def test_fetch_btc_eth_tactical_stays_silent_on_small_changes(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        inst = (params or {}).get("instId")
        if url.endswith("/api/v5/market/ticker"):
            return _Resp({"code": "0", "data": [{"last": "100000", "volCcy24h": "120000"}]})
        if url.endswith("/api/v5/market/history-candles"):
            return _Resp({"code": "0", "data": [["1780822800000", "0", "0", "0", "99950"]]})
        if url.endswith("/api/v5/public/funding-rate"):
            return _Resp({"code": "0", "data": [{"fundingRate": "0.00005"}]})
        if url.endswith("/api/v5/rubik/stat/contracts/open-interest-history"):
            return _Resp({"code": "0", "data": [{"oi": "1010"}, {"oi": "1000"}]})
        raise AssertionError(inst)

    monkeypatch.setattr("src.scout.sources.btc_eth_tactical.requests.get", fake_get)

    rows = fetch_btc_eth_tactical(limit=4)
    assert rows == []
