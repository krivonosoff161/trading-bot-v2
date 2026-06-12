# -*- coding: utf-8 -*-
from src.scout.sources import okx_listings as OKXL


class _Resp:
    def json(self):
        return {
            "data": [
                {
                    "instId": "CGNX-USDT-SWAP",
                    "state": "live",
                    "listTime": "1000000",
                },
                {
                    "instId": "SUI-USDT-SWAP",
                    "state": "live",
                    "listTime": "999000",
                },
            ]
        }


def test_okx_listings_classifies_stock_style_swaps_as_l5(monkeypatch):
    monkeypatch.setattr(OKXL.time, "time", lambda: 1000.0)
    monkeypatch.setattr(OKXL.requests, "get", lambda *a, **k: _Resp())

    items = OKXL.fetch_new_listings(within_hours=1, limit=10)

    by_asset = {row["asset"]: row for row in items}
    assert by_asset["CGNX"]["layer"] == 5
    assert by_asset["CGNX"]["baseline"] == "QQQ-USDT-SWAP"
    assert by_asset["SUI"]["layer"] == 2
    assert by_asset["SUI"]["baseline"] == "BTC-USDT-SWAP"
