# -*- coding: utf-8 -*-
"""Public OKX open-interest loader: keyless provider, end-pagination, enrich, gate unblock."""
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.farm_coordinator import run_coordinator_cycle  # noqa: E402
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path  # noqa: E402
from src.research_lab.flow_enrich import enrich_oi_one  # noqa: E402
from src.research_lab.providers.okx_flow import (  # noqa: E402
    OKX_BASE_URL,
    OkxPublicOpenInterestProvider,
    oi_period_for_timeframe,
)
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402

PROFILES = load_timeframe_profiles()
POLICY = load_resource_policy()
_HOUR = 3_600_000


def _oi_http(series):
    """Fake keyless GET: positional [ts,oi,oiCcy,oiUsd]; honors the backward `end` cursor."""
    captured = {}

    def get(url, timeout):
        captured["url"] = url
        q = parse_qs(urlparse(url).query)
        assert "instId" in q and "period" in q, "OI request must carry instId+period"
        # keyless: no auth params, no private endpoints
        for bad in ("api_key", "apikey", "sign", "signature", "passphrase", "token"):
            assert bad not in url.lower()
        assert "/account" not in url and "/trade" not in url
        end = int(q["end"][0]) if "end" in q else None
        pts = [p for p in series if end is None or p[0] < end]
        pts = sorted(pts, key=lambda p: p[0], reverse=True)[:100]  # newest-100 desc, like OKX
        return {"code": "0", "data": [[str(ts), str(oi), "0", "0"] for ts, oi in pts]}

    get.captured = captured
    return get


def test_oi_period_mapping():
    assert oi_period_for_timeframe("1h") == "1H"
    assert oi_period_for_timeframe("4h") == "4H"
    assert oi_period_for_timeframe("1d") == "1D"
    assert oi_period_for_timeframe("15m") == "15m"
    assert oi_period_for_timeframe("1m") == "5m"  # no 1m OI -> fall back to 5m


def test_provider_parses_window_and_is_keyless():
    series = [(1_700_000_000_000 + i * _HOUR, 1000.0 + i) for i in range(50)]
    http = _oi_http(series)
    prov = OkxPublicOpenInterestProvider(http_get=http, sleep=lambda _s: None)
    start, end = series[10][0], series[40][0]
    out = prov.fetch_open_interest("BTC-USDT-SWAP", "1h", start, end)
    assert out == sorted(out)  # ascending
    assert all(start <= ts <= end for ts, _ in out)
    assert out[0][0] == series[10][0] and out[-1][0] == series[40][0]
    assert http.captured["url"].startswith(OKX_BASE_URL)
    assert "BTC-USDT-SWAP" in http.captured["url"] and "period=1H" in http.captured["url"]


def test_provider_pages_backward_via_end_cursor():
    # 250 points (> 2 pages of 100) -> must page backward via `end` to cover the deep window
    series = [(1_700_000_000_000 + i * _HOUR, 1000.0 + i) for i in range(250)]
    prov = OkxPublicOpenInterestProvider(http_get=_oi_http(series), sleep=lambda _s: None, max_pages=12)
    out = prov.fetch_open_interest("ETH-USDT-SWAP", "1h", series[0][0], series[-1][0])
    assert len(out) == 250  # full deep series stitched across pages
    assert out[0][0] == series[0][0] and out[-1][0] == series[-1][0]


def _write_candles(private_root, symbol, tf="1h", n=120):
    d = private_root / "market_data" / tf
    d.mkdir(parents=True, exist_ok=True)
    start = 1_700_000_000_000
    rows = [{"ts": start + i * _HOUR, "date": "x", "open": 1.0, "high": 1.1,
             "low": 0.9, "close": 1.0, "vol": 5.0} for i in range(n)]
    (d / f"{symbol}_{start}_{start + (n - 1) * _HOUR}_{tf}.json").write_text(json.dumps(rows), encoding="utf-8")
    return start, start + (n - 1) * _HOUR


def test_enrich_oi_one_merges_field(tmp_path):
    start, end = _write_candles(tmp_path, "AAA_USDT_SWAP")
    path = next((tmp_path / "market_data" / "1h").glob("*.json"))
    series = [(start + i * _HOUR, 1000.0 + i) for i in range(120)]
    prov = OkxPublicOpenInterestProvider(http_get=_oi_http(series), sleep=lambda _s: None)
    status, n = enrich_oi_one(path, "AAA-USDT-SWAP", "1h", provider=prov, now_ms=end)
    assert status == "enriched" and n > 0
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert all("oi" in r for r in rows)  # forward-filled onto every candle


class _FakeOiProvider:
    def fetch_open_interest(self, symbol, timeframe, start_ts, end_ts):
        return [(int(start_ts) + i * _HOUR, 1000.0 + i) for i in range((int(end_ts) - int(start_ts)) // _HOUR + 1)]


def _oi_event(symbol="OIX-USDT-SWAP"):
    return {"event_id": f"{symbol}:oi", "symbol": symbol, "source": "test", "reason": "oi",
            "observed_at": 1000.0, "priority": 2, "asset_class": "crypto_major",
            "suggested_timeframes": ["1h"], "evidence": {}, "raw_ref": {}}


def test_blocked_oi_then_enrich_then_unblock(tmp_path):
    # candles present, OI family -> blocked NEEDS_OI_DATA + enrich_oi task; enrich fills oi; next cycle unblocks
    _write_candles(tmp_path, "OIX_USDT_SWAP")
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    out1 = run_coordinator_cycle(
        tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY, intake_events=[_oi_event()],
        families=("oi_price_quadrant",), oi_provider=_FakeOiProvider(), apply=True,
        now=1_700_500_000.0, run_worker=False,
    )
    assert out1["counters"]["planned_blocked"] >= 1
    assert out1["counters"]["enriched_oi_ok"] >= 1  # OI fetched + merged this cycle
    path = next((tmp_path / "market_data" / "1h").glob("*.json"))
    assert all("oi" in r for r in json.loads(path.read_text(encoding="utf-8")))
    assert tasks.tasks_in_state("blocked", task_type="run_sweep")  # still blocked until next unblock pass
    # cycle 2: gate clears (oi now on candles) -> run_sweep requeued + materialized
    out2 = run_coordinator_cycle(
        tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY, intake_events=[],
        families=("oi_price_quadrant",), oi_provider=_FakeOiProvider(), apply=True,
        now=1_700_600_000.0, run_worker=False,
    )
    assert out2["counters"]["unblocked"] >= 1
    assert not tasks.tasks_in_state("blocked", task_type="run_sweep")
    tasks.close()
