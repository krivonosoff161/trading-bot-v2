# -*- coding: utf-8 -*-
"""OKX instrument discovery: classification, snapshot/TTL/diff, discovered universe."""

from __future__ import annotations

import datetime as dt
import json

from scripts.strategy_lab.discover_okx_universe import discover
from src.research_lab.instrument_discovery import (
    build_snapshot,
    classify_symbol,
    diff_snapshots,
    discovered_universe,
    farm_readiness,
    is_fresh,
    snapshot_path,
)

NOW_MS = 1_750_000_000_000
HOUR = 3_600_000


def _iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat()


RAW = [
    {"instType": "SWAP", "instId": "BTC-USDT-SWAP", "settleCcy": "USDT", "state": "live"},
    {"instType": "SWAP", "instId": "DOGE-USDT-SWAP", "settleCcy": "USDT", "state": "live"},
    {"instType": "SWAP", "instId": "NVDA-USDT-SWAP", "settleCcy": "USDT", "state": "live"},
    {"instType": "SWAP", "instId": "FOO-USDT-SWAP", "settleCcy": "USDT", "state": "live"},
    {"instType": "SWAP", "instId": "BTC-USDC-SWAP", "settleCcy": "USDC", "state": "live"},   # not USDT
    {"instType": "SWAP", "instId": "OLD-USDT-SWAP", "settleCcy": "USDT", "state": "suspend"},  # not live
]


class _FakeInstruments:
    def __init__(self, raw):
        self.raw = raw
        self.calls = 0

    def fetch_instruments(self, inst_type="SWAP"):
        self.calls += 1
        return self.raw


def test_classify_symbol_confident_and_ambiguous():
    assert classify_symbol("BTC_USDT_SWAP") == "crypto_major"
    assert classify_symbol("DOGE_USDT_SWAP") == "meme_or_high_beta"
    assert classify_symbol("NVDA_USDT_SWAP") == "tokenized_equity"
    assert classify_symbol("XAU_USDT_SWAP") == "commodity"
    assert classify_symbol("FOO_USDT_SWAP") == "crypto_alt"   # residual confident crypto bucket
    assert classify_symbol("") == "unknown"                   # unparseable -> not guessed


def test_build_snapshot_filters_and_groups():
    snap = build_snapshot(RAW, generated_at=_iso(NOW_MS))
    assert snap["count"] == 4                                  # USDC + suspend excluded
    assert snap["groups"]["crypto_major"] == ["BTC_USDT_SWAP"]
    assert snap["groups"]["meme_or_high_beta"] == ["DOGE_USDT_SWAP"]
    assert snap["groups"]["tokenized_equity"] == ["NVDA_USDT_SWAP"]
    assert snap["groups"]["crypto_alt"] == ["FOO_USDT_SWAP"]


def test_snapshot_ttl():
    snap = build_snapshot(RAW, generated_at=_iso(NOW_MS - HOUR))
    assert is_fresh(snap, NOW_MS, ttl_seconds=2 * 3600) is True
    assert is_fresh(snap, NOW_MS, ttl_seconds=30 * 60) is False


def test_diff_new_and_delisted():
    old = build_snapshot([RAW[0], {"instType": "SWAP", "instId": "GONE-USDT-SWAP",
                                   "settleCcy": "USDT", "state": "live"}], generated_at=_iso(NOW_MS - HOUR))
    new = build_snapshot(RAW, generated_at=_iso(NOW_MS))
    diff = diff_snapshots(old, new)
    assert "GONE_USDT_SWAP" in diff["delisted"]
    assert "DOGE_USDT_SWAP" in diff["new_instruments"]


def test_group_change_detected():
    old = build_snapshot(RAW, generated_at=_iso(NOW_MS - HOUR))
    # same symbol reclassified by mutating the instruments map directly
    new = json.loads(json.dumps(old))
    new["instruments"]["FOO_USDT_SWAP"]["group"] = "meme_or_high_beta"
    changes = diff_snapshots(old, new)["group_changes"]
    assert any(c["symbol"] == "FOO_USDT_SWAP" and c["to"] == "meme_or_high_beta" for c in changes)


def test_discovered_universe_groups_prefixed():
    snap = build_snapshot(RAW, generated_at=_iso(NOW_MS))
    uni = discovered_universe(snap)
    assert "discovered_crypto_major" in uni.groups
    assert "BTC_USDT_SWAP" in uni.symbols_in("discovered_crypto_major")


def test_farm_readiness_splits(tmp_path):
    d = tmp_path / "market_data" / "1h"
    d.mkdir(parents=True)
    rows = [{"ts": NOW_MS + i * HOUR, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1} for i in range(80)]
    (d / "BTC_USDT_SWAP_x_1h.json").write_text(json.dumps(rows), encoding="utf-8")
    res = farm_readiness(["BTC_USDT_SWAP", "FOO_USDT_SWAP"], tmp_path, "1h")
    assert res["ready"] == ["BTC_USDT_SWAP"]
    assert res["missing"] == ["FOO_USDT_SWAP"]


def test_discover_cli_apply_persists_snapshot(tmp_path):
    res = discover(tmp_path, apply=True, now_ms=NOW_MS, provider=_FakeInstruments(RAW))
    assert res["status"] == "discovered" and res["count"] == 4
    assert snapshot_path(tmp_path).exists()
    # dry-run does not persist (use a fresh root)
    fresh = tmp_path / "fresh"
    res2 = discover(fresh, apply=False, now_ms=NOW_MS, provider=_FakeInstruments(RAW))
    assert res2["status"] == "would_discover"
    assert not snapshot_path(fresh).exists()


def test_discover_reuses_fresh_snapshot_without_provider_call(tmp_path):
    first = _FakeInstruments(RAW)
    res = discover(tmp_path, apply=True, now_ms=NOW_MS, provider=first)
    assert res["status"] == "discovered"
    assert first.calls == 1

    second = _FakeInstruments([])
    cached = discover(tmp_path, apply=True, now_ms=NOW_MS + HOUR, provider=second, ttl_seconds=2 * 3600)
    assert cached["status"] == "cached"
    assert cached["cached"] is True
    assert cached["count"] == 4
    assert second.calls == 0

    forced = discover(
        tmp_path,
        apply=False,
        now_ms=NOW_MS + HOUR,
        provider=second,
        ttl_seconds=2 * 3600,
        force_refresh=True,
    )
    assert forced["status"] == "would_discover"
    assert second.calls == 1
