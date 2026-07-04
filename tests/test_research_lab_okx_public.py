# -*- coding: utf-8 -*-

import json
import time
import urllib.error
import urllib.parse
from pathlib import Path

import httpx
import pytest

from src.research_lab.market_data_provider import get_provider
from src.research_lab.providers.okx_public import (
    MarketDataError,
    OkxPublicMarketDataProvider,
    _default_http_get,
    _httpx_get_direct,
    _httpx_get_with_hard_deadline,
    to_inst_id,
)

MINUTE = 60_000
START = 1_700_000_000_000


def _ok_worker_success(_url, _timeout, out_queue):
    out_queue.put(("ok", {"code": "0", "data": []}))


def _slow_worker(_url, _timeout, _out_queue):
    time.sleep(5)


def _okx_row(ts, o="100", h="101", lo="99", c="100", vol="10", confirm="1"):
    # OKX candle: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
    return [str(ts), o, h, lo, c, vol, "1", "1", confirm]


def _make_http_get(rows_newest_first, *, calls=None):
    """Fake OKX endpoint honoring the `after` cursor (records calls if given)."""
    def http_get(url, timeout):
        if calls is not None:
            calls.append(url)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        after = int(query.get("after", "999999999999999"))
        limit = int(query.get("limit", "100"))
        page = [r for r in rows_newest_first if int(r[0]) < after][:limit]
        return {"code": "0", "msg": "", "data": page}
    return http_get


def _provider(http_get, **kw):
    return OkxPublicMarketDataProvider(http_get=http_get, sleep=lambda _s: None, **kw)


def test_symbol_maps_to_okx_inst_id():
    assert to_inst_id("BTC_USDT_SWAP") == "BTC-USDT-SWAP"
    assert to_inst_id("zec_usdt_swap") == "ZEC-USDT-SWAP"


def test_normalizes_and_sorts_ascending():
    rows = [_okx_row(START + i * MINUTE) for i in range(5)]
    rows_desc = list(reversed(rows))  # OKX returns newest-first
    p = _provider(_make_http_get(rows_desc))
    out = p.fetch_ohlcv("BTC_USDT_SWAP", "1m", START, START + 4 * MINUTE)
    assert [r["ts"] for r in out] == [START + i * MINUTE for i in range(5)]  # ascending
    first = out[0]
    assert {"ts", "date", "open", "high", "low", "close", "vol"} <= set(first)
    assert isinstance(first["ts"], int) and isinstance(first["open"], float)


def test_deduplicates_timestamps():
    rows = [_okx_row(START), _okx_row(START), _okx_row(START + MINUTE)]
    p = _provider(_make_http_get(rows))
    out = p.fetch_ohlcv("BTC_USDT_SWAP", "1m", START, START + MINUTE)
    assert [r["ts"] for r in out] == [START, START + MINUTE]


def test_filters_rows_outside_window():
    rows = [_okx_row(START + i * MINUTE) for i in range(10)]
    p = _provider(_make_http_get(list(reversed(rows))))
    out = p.fetch_ohlcv("BTC_USDT_SWAP", "1m", START + 2 * MINUTE, START + 5 * MINUTE)
    assert [r["ts"] for r in out] == [START + i * MINUTE for i in range(2, 6)]


def test_skips_unconfirmed_candle():
    rows = [_okx_row(START, confirm="1"), _okx_row(START + MINUTE, confirm="0")]
    p = _provider(_make_http_get(rows))
    out = p.fetch_ohlcv("BTC_USDT_SWAP", "1m", START, START + MINUTE)
    assert [r["ts"] for r in out] == [START]  # unconfirmed dropped


def test_empty_response_is_clean():
    p = _provider(_make_http_get([]))
    assert p.fetch_ohlcv("BTC_USDT_SWAP", "1m", START, START + MINUTE) == []


def test_network_error_raises_marketdataerror():
    def boom(url, timeout):
        raise urllib.error.URLError("no network")
    with pytest.raises(MarketDataError):
        _provider(boom).fetch_ohlcv("BTC_USDT_SWAP", "1m", START, START + MINUTE)


def test_httpx_direct_bounds_connect_read_timeout(monkeypatch):
    def fake_get(_url, *, headers, timeout):
        assert headers["User-Agent"]
        assert isinstance(timeout, httpx.Timeout)
        raise httpx.ConnectTimeout("handshake timeout")

    monkeypatch.setattr(httpx, "get", fake_get)

    with pytest.raises(httpx.ConnectTimeout):
        _httpx_get_direct("https://www.okx.com/api/v5/market/history-candles", 0.1)


def test_default_http_get_parses_json_response(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": "0", "data": []}

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: FakeResponse())

    assert _httpx_get_direct("https://www.okx.com/api/v5/market/history-candles", 0.1) == {
        "code": "0",
        "data": [],
    }


def test_default_http_get_accepts_worker_payload():
    assert _default_http_get(
        "https://www.okx.com/api/v5/market/history-candles",
        0.1,
        worker=_ok_worker_success,
    ) == {"code": "0", "data": []}


def test_default_http_get_kills_stuck_worker():
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        _httpx_get_with_hard_deadline(
            "https://www.okx.com/api/v5/market/history-candles",
            0.1,
            worker=_slow_worker,
        )
    assert time.monotonic() - started < 3


def test_api_error_code_raises():
    def err(url, timeout):
        return {"code": "51001", "msg": "instrument not found", "data": []}
    with pytest.raises(MarketDataError):
        _provider(err).fetch_ohlcv("BTC_USDT_SWAP", "1m", START, START + MINUTE)


def test_no_api_key_in_request_url():
    calls = []
    rows = [_okx_row(START)]
    p = _provider(_make_http_get(rows, calls=calls))
    p.fetch_ohlcv("BTC_USDT_SWAP", "1m", START, START)
    assert calls, "expected at least one request"
    blob = " ".join(calls).lower()
    for forbidden in ("apikey", "api_key", "passphrase", "sign", "secret", "authorization", "token"):
        assert forbidden not in blob


def test_pagination_is_bounded_by_max_pages():
    calls = []
    # 1000 contiguous candles; window asks for all of them, but max_pages=3 caps it.
    rows = [_okx_row(START + i * MINUTE) for i in range(1000)]
    p = _provider(_make_http_get(list(reversed(rows)), calls=calls), max_pages=3)
    out = p.fetch_ohlcv("BTC_USDT_SWAP", "1m", START, START + 999 * MINUTE)
    assert len(calls) <= 3  # no infinite pagination
    assert len(out) <= 300  # <= max_pages * PAGE_LIMIT


def test_rejects_unsupported_timeframe():
    with pytest.raises(ValueError):
        _provider(_make_http_get([])).fetch_ohlcv("BTC_USDT_SWAP", "3m", START, START + MINUTE)


def test_supports_5m_15m_1h_4h_1d():
    p = _provider(_make_http_get([]))
    for tf in ("5m", "15m", "1h", "4h", "1d"):
        rows = p.fetch_ohlcv("BTC_USDT_SWAP", tf, START, START)
        assert isinstance(rows, list)


def test_rejects_absurd_window():
    with pytest.raises(ValueError):
        _provider(_make_http_get([])).fetch_ohlcv("BTC_USDT_SWAP", "1m", 0, 10_000 * MINUTE)


def test_get_provider_returns_okx_public():
    p = get_provider("okx-public")
    assert p.name == "okx-public" and p.configured is True


def test_provider_module_has_no_private_or_order_endpoints():
    src = Path("src/research_lab/providers/okx_public.py").read_text(encoding="utf-8")
    low = src.lower()
    # Concrete private/auth/order indicators (OKX private endpoints need OK-ACCESS-* headers).
    for forbidden in ("/account", "/trade", "place-order", "ok-access", "passphrase",
                      "secretkey", "secret_key", "apikey", "api_key"):
        assert forbidden not in low, f"unexpected private/auth token in provider: {forbidden}"
    assert "history-candles" in low  # only the public candles endpoint is used


def test_prepare_apply_with_okx_public_writes_canonical(tmp_path):
    from src.research_lab.data_cache import CacheCheck
    from src.research_lab.data_prepare import prepare
    from src.research_lab.data_requirements import DataRequirement
    from src.research_lab.paths import one_minute_data_dir

    rows = [_okx_row(START + i * MINUTE) for i in range(5)]
    provider = _provider(_make_http_get(list(reversed(rows))))
    check = CacheCheck(DataRequirement("BTC_USDT_SWAP", "1m", START, START + 4 * MINUTE, "r", "manual", 360, "missing"), "", 0, False)

    rep = prepare([check], provider=provider, private_root=tmp_path, apply=True)
    assert rep.downloaded == 1 and rep.provider == "okx-public"
    files = list(one_minute_data_dir(tmp_path).glob("*_1m.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    ts = [r["ts"] for r in data]
    assert ts == sorted(set(ts)) == [START + i * MINUTE for i in range(5)]
