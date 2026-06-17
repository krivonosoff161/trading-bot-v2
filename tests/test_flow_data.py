# -*- coding: utf-8 -*-
"""Public funding-rate-history provider (offline fake http) + pure flow merge."""

from __future__ import annotations

from src.research_lab.flow_merge import coverage, forward_fill, merge_funding, merge_oi
from src.research_lab.providers.okx_flow import OkxPublicFundingProvider

EIGHT_H = 8 * 3_600_000


def _fake_http(points):
    """Build a fake OKX funding-history http_get returning one page of records."""
    def http_get(url, timeout):
        data = [{"instId": "BTC-USDT-SWAP", "fundingRate": str(r), "fundingTime": str(ts)}
                for ts, r in points]
        return {"code": "0", "data": data}
    return http_get


def test_funding_provider_window_filter():
    base = 1_700_000_000_000
    pts = [(base + i * EIGHT_H, 0.0001 * (i + 1)) for i in range(6)]
    provider = OkxPublicFundingProvider(http_get=_fake_http(pts), sleep=lambda s: None)
    got = provider.fetch_funding("BTC_USDT_SWAP", base + EIGHT_H, base + 3 * EIGHT_H)
    ts_got = [ts for ts, _ in got]
    assert ts_got == [base + EIGHT_H, base + 2 * EIGHT_H, base + 3 * EIGHT_H]


def test_funding_provider_api_error_raises():
    def http_get(url, timeout):
        return {"code": "51000", "data": []}
    provider = OkxPublicFundingProvider(http_get=http_get, sleep=lambda s: None)
    import pytest
    from src.research_lab.providers.okx_flow import FlowDataError
    with pytest.raises(FlowDataError):
        provider.fetch_funding("BTC_USDT_SWAP", 0, EIGHT_H)


def test_forward_fill_no_lookahead():
    candles = [{"ts": t, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}
               for t in (100, 200, 300, 400)]
    points = [(150, 0.0005), (350, 0.0010)]
    out = merge_funding(candles, points)
    assert out[0].get("funding") is None       # before first point -> untouched
    assert out[1]["funding"] == 0.0005          # 200 -> latest <=200 is 150
    assert out[2]["funding"] == 0.0005          # 300 -> still 150's value
    assert out[3]["funding"] == 0.0010          # 400 -> 350's value


def test_merge_oi_and_coverage():
    candles = [{"ts": t, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1} for t in (10, 20, 30)]
    out = merge_oi(candles, [(15, 1_000_000.0)])
    assert out[0].get("oi") is None and out[1]["oi"] == 1_000_000.0
    cov = coverage(out, "oi")
    assert cov["with_value"] == 2 and cov["candles"] == 3


def test_forward_fill_empty_points_is_noop_copy():
    candles = [{"ts": 1, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}]
    out = forward_fill(candles, [], "funding")
    assert out == candles and out is not candles
