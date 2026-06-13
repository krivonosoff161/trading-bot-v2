# -*- coding: utf-8 -*-

import pytest

from src.research_lab.market_data_provider import (
    NullMarketDataProvider,
    ProviderNotConfigured,
    SyntheticMarketDataProvider,
    get_provider,
)

MINUTE = 60_000


def test_null_provider_not_configured():
    p = NullMarketDataProvider()
    assert p.configured is False
    with pytest.raises(ProviderNotConfigured):
        p.fetch_ohlcv("BTC_USDT_SWAP", "1m", 0, MINUTE)


def test_get_provider_defaults_to_null():
    assert get_provider("null").configured is False
    assert get_provider(None).configured is False
    assert get_provider("okx").configured is False  # no adapter shipped -> null
    assert get_provider("synthetic").configured is False  # gated off by default


def test_synthetic_requires_explicit_optin():
    assert get_provider("synthetic", allow_synthetic=True).name == "synthetic"
    assert get_provider("synthetic", allow_synthetic=False).name == "null"


def test_synthetic_generates_sorted_marked_1m():
    rows = SyntheticMarketDataProvider().fetch_ohlcv("BTC_USDT_SWAP", "1m", 1_700_000_000_000, 1_700_000_000_000 + 9 * MINUTE)
    assert len(rows) == 10
    ts = [r["ts"] for r in rows]
    assert ts == sorted(ts)
    for r in rows:
        assert {"ts", "open", "high", "low", "close", "vol"} <= set(r)
        assert r["high"] >= r["low"]
        assert r.get("synthetic") is True  # clearly not real market data


def test_synthetic_rejects_non_1m():
    with pytest.raises(ValueError):
        SyntheticMarketDataProvider().fetch_ohlcv("X_USDT_SWAP", "15m", 0, MINUTE)
