# -*- coding: utf-8 -*-
"""Tests for multi-timeframe OKX public provider and prepare workflow (Phase 2).

Verifies:
- Provider rejects unsupported timeframes.
- Provider uses correct OKX bar mapping.
- Dry-run no network.
- Apply with mocked provider writes correct canonical OHLCV.
- No private endpoint strings in provider module.
- Readiness sees prepared timeframe.
"""

import json
import urllib.parse
from pathlib import Path

import pytest

from src.research_lab.data_prepare import (
    MarketDataPrepareItem,
    market_data_dir,
    prepare_market_data,
    slice_filename,
)
from src.research_lab.market_data_provider import SyntheticMarketDataProvider, get_provider
from src.research_lab.paths import market_data_glob
from src.research_lab.providers.okx_public import (
    SUPPORTED_TIMEFRAMES,
    OkxPublicMarketDataProvider,
    _resolve_timeframe,
)

MINUTE_MS = 60_000
HOUR_MS = 3600_000
DAY_MS = 86_400_000
START = 1_700_000_000_000


def _fake_http_get(*_args, **_kwargs):
    return {"code": "0", "msg": "", "data": []}


def _provider(http_get=None):
    return OkxPublicMarketDataProvider(
        http_get=http_get or _fake_http_get, sleep=lambda _s: None,
    )


def test_supported_timeframes_are_1m_15m_1h_4h_1d():
    assert set(SUPPORTED_TIMEFRAMES) == {"1m", "15m", "1h", "4h", "1d"}


def test_resolve_timeframe_returns_correct_bar_and_interval():
    bar, interval = _resolve_timeframe("15m")
    assert bar == "15m"
    assert interval == 15 * MINUTE_MS
    bar, interval = _resolve_timeframe("1h")
    assert bar == "1H"
    assert interval == HOUR_MS
    bar, interval = _resolve_timeframe("4h")
    assert bar == "4H"
    assert interval == 4 * HOUR_MS
    bar, interval = _resolve_timeframe("1d")
    assert bar == "1Dutc"
    assert interval == DAY_MS


def test_unsupported_timeframe_raises():
    with pytest.raises(ValueError, match="supports"):
        _resolve_timeframe("5m")


def test_okx_bar_parameter_in_request_url():
    calls = []
    rows = [[str(START + i * 15 * MINUTE_MS), "100", "101", "99", "100", "10", "1", "1", "1"]
            for i in range(3)]
    rows_desc = list(reversed(rows))

    def http_get(url, timeout):
        calls.append(url)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        after = int(query.get("after", "999999999999999"))
        limit = int(query.get("limit", "100"))
        page = [r for r in rows_desc if int(r[0]) < after][:limit]
        return {"code": "0", "msg": "", "data": page}

    p = _provider(http_get)
    out = p.fetch_ohlcv("BTC_USDT_SWAP", "15m", START, START + 30 * MINUTE_MS)
    assert len(out) == 3
    assert calls, "expected at least one request"
    bar_params = []
    for url in calls:
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        bar_params.append(query.get("bar"))
    assert all(b == "15m" for b in bar_params)


def test_provider_has_no_private_or_order_endpoints():
    from pathlib import Path as P
    src = P("src/research_lab/providers/okx_public.py").read_text(encoding="utf-8")
    low = src.lower()
    for forbidden in ("/account", "/trade", "place-order", "ok-access", "passphrase",
                      "secretkey", "secret_key", "apikey", "api_key"):
        assert forbidden not in low, f"unexpected private/auth token in provider: {forbidden}"


def test_dry_run_no_network():
    p = _provider()
    items = [MarketDataPrepareItem("BTC_USDT_SWAP", "15m", START, START + 15 * MINUTE_MS)]
    report = prepare_market_data(items, provider=p, private_root=Path("/tmp/none"),
                                 timeframe="15m", apply=False)
    assert report.would_download == 1
    assert report.downloaded == 0
    assert report.files_written == []


def test_prepare_rejects_item_timeframe_mismatch(tmp_path):
    p = _provider()
    items = [MarketDataPrepareItem("BTC_USDT_SWAP", "1h", START, START + HOUR_MS)]
    report = prepare_market_data(items, provider=p, private_root=tmp_path,
                                 timeframe="15m", apply=False)
    assert report.would_download == 0
    assert report.downloaded == 0
    assert report.skipped == [{"symbol": "BTC_USDT_SWAP", "reason": "timeframe_mismatch"}]
    assert report.items[0]["action"] == "timeframe_mismatch"


def test_apply_with_mocked_provider_writes_correct_files(tmp_path):
    rows_15m = []
    for i in range(5):
        ts = START + i * 15 * MINUTE_MS
        rows_15m.append({
            "ts": ts, "date": f"2026-01-{i+1:02d}", "open": 100.0, "high": 101.0,
            "low": 99.0, "close": 100.0, "vol": 1000.0,
        })

    def http_get(url, timeout):
        return {"code": "0", "msg": "", "data": []}

    synth = SyntheticMarketDataProvider()
    items = [MarketDataPrepareItem("BTC_USDT_SWAP", "15m", START, START + 4 * 15 * MINUTE_MS)]
    report = prepare_market_data(items, provider=synth, private_root=tmp_path,
                                 timeframe="15m", apply=True)
    assert report.downloaded == 1
    data_dir = market_data_dir(tmp_path, "15m")
    files = list(data_dir.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert len(data) >= 5
    for row in data:
        assert {"ts", "date", "open", "high", "low", "close", "vol"} <= set(row)


def test_slice_filename_includes_timeframe():
    name = slice_filename("BTC_USDT_SWAP", START, START + DAY_MS, "15m")
    assert "15m" in name
    assert "BTC_USDT_SWAP" in name
    name_1d = slice_filename("BTC_USDT_SWAP", START, START + DAY_MS, "1d")
    assert "1d" in name_1d


def test_market_data_dir_per_timeframe(tmp_path):
    d15 = market_data_dir(tmp_path, "15m")
    d1h = market_data_dir(tmp_path, "1h")
    assert d15.name == "15m"
    assert d1h.name == "1h"
    assert d15 != d1h


def test_market_data_glob_per_timeframe(tmp_path):
    g = market_data_glob(tmp_path, "15m")
    assert "15m" in g


def test_prepare_item_bar_count_uses_timeframe_interval():
    item = MarketDataPrepareItem("BTC_USDT_SWAP", "15m", START, START + 4 * 15 * MINUTE_MS)
    assert item.bar_count() == 5
    daily = MarketDataPrepareItem("BTC_USDT_SWAP", "1d", START, START + 2 * DAY_MS)
    assert daily.bar_count() == 3


def test_prepare_item_rejects_unknown_timeframe_for_bar_count():
    item = MarketDataPrepareItem("BTC_USDT_SWAP", "5m", START, START + 5 * MINUTE_MS)
    with pytest.raises(ValueError, match="unsupported timeframe"):
        item.bar_count()


def test_readiness_sees_prepared_timeframe(tmp_path):
    from src.research_lab.data_readiness import assess
    from src.research_lab.strategy_requirements import derive_requirement

    rows = []
    for i in range(100):
        ts = START + i * 15 * MINUTE_MS
        rows.append({
            "ts": ts, "date": f"2026-01-{(i % 28) + 1:02d}", "open": 100.0 + i * 0.1,
            "high": 101.0 + i * 0.1, "low": 99.0 + i * 0.1, "close": 100.5 + i * 0.1,
            "vol": 1000.0,
        })
    data_dir = market_data_dir(tmp_path, "15m")
    data_dir.mkdir(parents=True, exist_ok=True)
    f = data_dir / f"BTC_USDT_SWAP_{START}_{START + 99 * 15 * MINUTE_MS}_15m.json"
    f.write_text(json.dumps(rows), encoding="utf-8")

    glob_pattern = str(data_dir / "{symbol}_*_15m.json")
    req = derive_requirement("momentum_breakout", "BTC_USDT_SWAP", "15m")
    result = assess(req, data_glob=glob_pattern)
    assert result.is_ready()


def test_get_provider_returns_okx_public():
    p = get_provider("okx-public")
    assert p.name == "okx-public"
    assert p.configured is True
