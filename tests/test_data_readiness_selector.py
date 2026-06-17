# -*- coding: utf-8 -*-
"""Tests for the live data-readiness selector (fake provider; no real network)."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import data_readiness_selector as S  # noqa: E402
from src.research_lab.providers.okx_public import MarketDataError  # noqa: E402

_NOW_MS = 1_780_000_000_000  # fixed "now" so fresh-listing math is deterministic


def _candle(ts):
    return {"ts": ts, "date": "x", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "vol": 1.0}


class _FakeProvider:
    """fetch_ohlcv returns configured rows per timeframe (or raises a configured error)."""

    def __init__(self, rows_by_tf):
        self._rows_by_tf = rows_by_tf

    def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
        value = self._rows_by_tf.get(timeframe)
        if isinstance(value, Exception):
            raise value
        return [_candle(start_ts + i) for i in range(int(value or 0))]


def test_classify_usable_too_short_missing():
    prov = _FakeProvider({"1h": 100, "4h": 10, "1d": 0})
    assert S.classify_timeframe(prov, "RE-USDT-SWAP", "1h", min_rows=72, now_ms=_NOW_MS)["status"] == "usable"
    assert S.classify_timeframe(prov, "RE-USDT-SWAP", "4h", min_rows=60, now_ms=_NOW_MS)["status"] == "too_short"
    assert S.classify_timeframe(prov, "RE-USDT-SWAP", "1d", min_rows=60, now_ms=_NOW_MS)["status"] == "missing"


def test_classify_provider_error_is_caught():
    prov = _FakeProvider({"1h": MarketDataError("okx down")})
    res = S.classify_timeframe(prov, "RE-USDT-SWAP", "1h", min_rows=72, now_ms=_NOW_MS)
    assert res["status"] == "provider_error"
    assert res["rows"] == 0


def test_select_picks_first_usable_in_order():
    # crypto_alt order is [15m, 1h, 4h]; 15m too short, 1h usable -> pick 1h.
    prov = _FakeProvider({"15m": 10, "1h": 200, "4h": 200})
    res = S.select_timeframe(prov, "RE-USDT-SWAP",
                             candidate_timeframes=["15m", "1h", "4h"], now_ms=_NOW_MS)
    assert res["status"] == "usable"
    assert res["selected_timeframe"] == "1h"


def test_old_alt_selects_1d_when_intraday_thin():
    prov = _FakeProvider({"1h": 5, "4h": 5, "1d": 400})
    res = S.select_timeframe(prov, "BTC-USDT-SWAP",
                             candidate_timeframes=["1h", "4h", "1d"], now_ms=_NOW_MS)
    assert res["selected_timeframe"] == "1d"
    assert res["status"] == "usable"


def test_fresh_listing_falls_back_to_pending_not_dead():
    # New SWAP: 1d/1h too short but 15m has some bars; listTime recent -> fresh_listing_pending.
    prov = _FakeProvider({"1m": 30, "15m": 12, "1h": 2})
    res = S.select_timeframe(
        prov, "GEOD-USDT-SWAP",
        candidate_timeframes=["1m", "15m", "1h"],
        list_time_ms=_NOW_MS - 3 * 3_600_000,  # listed 3h ago
        now_ms=_NOW_MS,
    )
    assert res["status"] == "fresh_listing_pending"
    assert res["selected_timeframe"] is not None


def test_preopen_state_is_fresh_listing_even_without_history():
    prov = _FakeProvider({"1m": 0, "15m": 0, "1h": 0})
    res = S.select_timeframe(
        prov, "NEW-USDT-SWAP",
        candidate_timeframes=["1m", "15m", "1h"],
        listing_state="preopen", now_ms=_NOW_MS,
    )
    assert res["status"] == "fresh_listing_pending"


def test_old_too_short_without_freshness_is_too_short():
    prov = _FakeProvider({"15m": 10, "1h": 5, "4h": 3})
    res = S.select_timeframe(prov, "OLD-USDT-SWAP",
                             candidate_timeframes=["15m", "1h", "4h"], now_ms=_NOW_MS)
    assert res["status"] == "too_short"


def test_all_missing_is_missing():
    prov = _FakeProvider({"15m": 0, "1h": 0, "4h": 0})
    res = S.select_timeframe(prov, "DEAD-USDT-SWAP",
                             candidate_timeframes=["15m", "1h", "4h"], now_ms=_NOW_MS)
    assert res["status"] == "missing"
    assert res["selected_timeframe"] is None


def test_provider_error_when_all_calls_fail():
    prov = _FakeProvider({"15m": MarketDataError("x"), "1h": MarketDataError("x"), "4h": MarketDataError("x")})
    res = S.select_timeframe(prov, "RE-USDT-SWAP",
                             candidate_timeframes=["15m", "1h", "4h"], now_ms=_NOW_MS)
    assert res["status"] == "provider_error"


def test_legacy_status_mapping_present():
    prov = _FakeProvider({"1h": 200})
    res = S.select_timeframe(prov, "RE-USDT-SWAP", candidate_timeframes=["1h"], now_ms=_NOW_MS)
    assert res["legacy_status"] == "READY"


def test_timeframes_for_asset_class_from_config():
    assert S.timeframes_for_asset_class("crypto_major") == ["1h", "4h", "1d"]
    assert S.timeframes_for_asset_class("meme")[0] == "1m"
    # Farm view excludes 1m (trigger-only).
    assert "1m" not in S.timeframes_for_asset_class("meme", farm_only=True)


def test_min_rows_from_config():
    assert S.min_rows_for("1d") == 60
    assert S.min_rows_for("1h") == 72
    assert S.min_rows_for("nonsense") == 60  # floor
