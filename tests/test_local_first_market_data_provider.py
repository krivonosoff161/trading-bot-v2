# -*- coding: utf-8 -*-

from src.research_lab.data_prepare import write_candles
from src.research_lab.candle_store import CandleStore
from src.research_lab.paths import market_data_dir
from src.research_lab.providers.local_first import LocalFirstMarketDataProvider


def _row(ts: int, close: float = 100.0) -> dict:
    return {"ts": ts, "open": close, "high": close + 1, "low": close - 1, "close": close, "vol": 10.0}


class FallbackProvider:
    name = "fallback"
    configured = True

    def __init__(self) -> None:
        self.calls = 0

    def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
        self.calls += 1
        return [_row(start_ts, 200.0)]


def test_local_first_provider_reads_private_cache_before_fallback(tmp_path):
    hour = 60 * 60_000
    rows = [_row(i * hour, 100.0 + i) for i in range(30)]
    write_candles(
        rows,
        symbol="BTC_USDT_SWAP",
        start_ts=0,
        end_ts=29 * hour,
        timeframe="1h",
        data_dir=market_data_dir(tmp_path, "1h"),
    )
    fallback = FallbackProvider()
    provider = LocalFirstMarketDataProvider(tmp_path, fallback, min_rows=20)

    out = provider.fetch_ohlcv("BTC-USDT-SWAP", "1h", 5 * hour, 25 * hour)

    assert fallback.calls == 0
    assert len(out) == 21
    assert out[0]["ts"] == 5 * hour
    assert out[-1]["ts"] == 25 * hour


def test_local_first_prefers_canonical_store_over_json(tmp_path):
    hour = 60 * 60_000
    json_rows = [_row(i * hour, 100.0 + i) for i in range(30)]
    write_candles(
        json_rows,
        symbol="BTC_USDT_SWAP",
        start_ts=0,
        end_ts=29 * hour,
        timeframe="1h",
        data_dir=market_data_dir(tmp_path, "1h"),
    )
    store_rows = [_row(i * hour, 500.0 + i) for i in range(30)]
    CandleStore(tmp_path).upsert_candles(
        "BTC_USDT_SWAP", "1h", store_rows, source="canonical-test",
    )
    fallback = FallbackProvider()
    provider = LocalFirstMarketDataProvider(tmp_path, fallback, min_rows=20)

    out = provider.fetch_ohlcv("BTC-USDT-SWAP", "1h", 5 * hour, 25 * hour)

    assert fallback.calls == 0
    assert out[0]["close"] == 505.0
    assert out[-1]["close"] == 525.0


def test_local_first_provider_falls_back_when_cache_is_too_short(tmp_path):
    quarter_hour = 15 * 60_000
    rows = [_row(i * quarter_hour, 100.0 + i) for i in range(3)]
    write_candles(
        rows,
        symbol="ETH_USDT_SWAP",
        start_ts=0,
        end_ts=2 * quarter_hour,
        timeframe="15m",
        data_dir=market_data_dir(tmp_path, "15m"),
    )
    fallback = FallbackProvider()
    provider = LocalFirstMarketDataProvider(tmp_path, fallback, min_rows=20)

    out = provider.fetch_ohlcv("ETH_USDT_SWAP", "15m", 0, 30 * 60_000)

    assert fallback.calls == 1
    assert out == [_row(0, 200.0)]


def test_local_first_provider_falls_back_when_cache_is_stale(tmp_path):
    quarter_hour = 15 * 60_000
    rows = [_row(i * quarter_hour, 100.0 + i) for i in range(30)]
    write_candles(
        rows,
        symbol="LAB_USDT_SWAP",
        start_ts=0,
        end_ts=29 * quarter_hour,
        timeframe="15m",
        data_dir=market_data_dir(tmp_path, "15m"),
    )
    fallback = FallbackProvider()
    provider = LocalFirstMarketDataProvider(tmp_path, fallback, min_rows=20, max_stale_bars=3)

    out = provider.fetch_ohlcv("LAB_USDT_SWAP", "15m", 0, 10 * 15 * 60_000)

    assert fallback.calls == 1
    assert out == [_row(0, 200.0)]
