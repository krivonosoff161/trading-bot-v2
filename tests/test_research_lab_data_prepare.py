# -*- coding: utf-8 -*-

import json

from src.research_lab.data_cache import CacheCheck
from src.research_lab.data_prepare import (
    prepare,
    read_prepare_report,
    write_1m_candles,
    write_prepare_report,
)
from src.research_lab.data_requirements import DataRequirement
from src.research_lab.market_data_provider import NullMarketDataProvider
from src.research_lab.paths import one_minute_data_dir

MINUTE = 60_000


class _FakeProvider:
    name = "fake"
    configured = True

    def __init__(self):
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
        self.calls.append((symbol, timeframe, start_ts, end_ts))
        # deliberately unsorted with a duplicate ts to exercise dedup/sort
        return [
            {"ts": end_ts, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1},
            {"ts": start_ts, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1},
            {"ts": start_ts, "open": 2, "high": 2, "low": 2, "close": 2, "vol": 2},
        ]


def _check(symbol, start, end, status="missing"):
    return CacheCheck(DataRequirement(symbol, "1m", start, end, "r", "manual", 360, status), "", 0, False)


def test_writer_dedups_and_sorts(tmp_path):
    data_dir = one_minute_data_dir(tmp_path)
    rows = [
        {"ts": 120_000, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1},
        {"ts": 60_000, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1},
        {"ts": 60_000, "open": 2, "high": 2, "low": 2, "close": 2, "vol": 2},  # dup ts
    ]
    path, bars = write_1m_candles(rows, symbol="BTC_USDT_SWAP", start_ts=60_000, end_ts=120_000, data_dir=data_dir)
    assert bars == 2
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [r["ts"] for r in data] == [60_000, 120_000]


def test_writer_merges_existing_without_dupes(tmp_path):
    data_dir = one_minute_data_dir(tmp_path)
    write_1m_candles([{"ts": 60_000, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}],
                     symbol="BTC_USDT_SWAP", start_ts=60_000, end_ts=120_000, data_dir=data_dir)
    _, bars = write_1m_candles([{"ts": 120_000, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1},
                                {"ts": 60_000, "open": 9, "high": 9, "low": 9, "close": 9, "vol": 9}],
                               symbol="BTC_USDT_SWAP", start_ts=60_000, end_ts=120_000, data_dir=data_dir)
    assert bars == 2  # merged, no duplicate ts


def test_dry_run_writes_nothing(tmp_path):
    rep = prepare([_check("BTC_USDT_SWAP", 60_000, 120_000)],
                  provider=NullMarketDataProvider(), private_root=tmp_path, apply=False)
    assert rep.would_download == 1 and rep.downloaded == 0
    assert not list(one_minute_data_dir(tmp_path).glob("*.json"))


def test_apply_null_provider_writes_nothing(tmp_path):
    rep = prepare([_check("BTC_USDT_SWAP", 60_000, 120_000)],
                  provider=NullMarketDataProvider(), private_root=tmp_path, apply=True)
    assert rep.downloaded == 0 and rep.provider_configured is False
    assert rep.to_dict()["missing"] >= 1
    assert not list(one_minute_data_dir(tmp_path).glob("*.json"))


def test_apply_fake_provider_writes_canonical_sorted_unique(tmp_path):
    fake = _FakeProvider()
    rep = prepare([_check("BTC_USDT_SWAP", 60_000, 180_000)],
                  provider=fake, private_root=tmp_path, apply=True)
    assert rep.downloaded == 1 and len(fake.calls) == 1
    files = list(one_minute_data_dir(tmp_path).glob("*_1m.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    ts = [r["ts"] for r in data]
    assert ts == sorted(set(ts))  # sorted, no duplicates


def test_present_requirement_is_skipped(tmp_path):
    fake = _FakeProvider()
    rep = prepare([_check("BTC_USDT_SWAP", 60_000, 120_000, status="present")],
                  provider=fake, private_root=tmp_path, apply=True)
    assert rep.downloaded == 0 and fake.calls == []


def test_outside_policy_requirement_is_skipped(tmp_path):
    fake = _FakeProvider()
    rep = prepare([_check("BTC_USDT_SWAP", 60_000, 120_000, status="outside_policy")],
                  provider=fake, private_root=tmp_path, apply=True)
    assert rep.downloaded == 0 and fake.calls == []


def test_report_roundtrip_has_no_absolute_paths(tmp_path):
    rep = prepare([_check("BTC_USDT_SWAP", 60_000, 120_000)],
                  provider=NullMarketDataProvider(), private_root=tmp_path, apply=False)
    path = write_prepare_report(tmp_path, rep)
    text = path.read_text(encoding="utf-8")
    assert str(tmp_path) not in text
    assert read_prepare_report(tmp_path)["mode"] == "dry_run"
