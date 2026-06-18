# -*- coding: utf-8 -*-
"""Bounded funding enrichment: dry-run safety, apply, cap, backoff/TTL, field preservation."""

from __future__ import annotations

import json
from pathlib import Path

from src.research_lab.experiment import load_candles
from src.research_lab.flow_enrich import FlowEnrichState, run_flow_enrich
from src.research_lab.timeframes import load_timeframe_profiles
from src.research_lab.universe import load_universe
from src.research_lab.universe_refill import build_worklist

HOUR = 3_600_000
BASE = 1_700_000_000_000


class FakeFunding:
    def __init__(self, points):
        self.points = points
        self.calls = 0

    def fetch_funding(self, symbol, start, end):
        self.calls += 1
        return [(ts, r) for ts, r in self.points if start <= ts <= end]


class FailFunding:
    def __init__(self):
        self.calls = 0

    def fetch_funding(self, symbol, start, end):
        self.calls += 1
        raise RuntimeError("boom")


def _write_candle_file(root: Path, symbol: str, tf: str = "1h", n: int = 100) -> Path:
    rows = [{"ts": BASE + i * HOUR, "date": "", "open": 10.0, "high": 11.0,
             "low": 9.0, "close": 10.0, "vol": 100.0} for i in range(n)]
    d = root / "market_data" / tf
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{symbol}_{BASE}_{BASE + (n - 1) * HOUR}_{tf}.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _units():
    return build_worklist(load_universe(), groups=["core_market"], timeframes=["1h"])


def _ctx(tmp_path):
    return {"universe": load_universe(), "profiles": load_timeframe_profiles(), "private_root": tmp_path}


def test_dry_run_never_calls_provider(tmp_path):
    _write_candle_file(tmp_path, "BTC_USDT_SWAP")
    provider = FakeFunding([(BASE + 10 * HOUR, 0.0005)])
    res = run_flow_enrich(_units(), **_ctx(tmp_path), provider=provider, state=FlowEnrichState(),
                          apply=False, now_ms=BASE + 200 * HOUR, allow_public_output=True)
    assert provider.calls == 0
    assert res["counters"]["would_enrich"] >= 1
    assert res["counters"]["enriched"] == 0


def test_apply_adds_funding_field_and_load_preserves_it(tmp_path):
    path = _write_candle_file(tmp_path, "BTC_USDT_SWAP")
    provider = FakeFunding([(BASE + 5 * HOUR, 0.0005), (BASE + 50 * HOUR, 0.0009)])
    res = run_flow_enrich(_units(), **_ctx(tmp_path), provider=provider, state=FlowEnrichState(),
                          apply=True, now_ms=BASE + 200 * HOUR, max_enrich=8, allow_public_output=True)
    assert provider.calls >= 1
    assert res["counters"]["enriched"] >= 1
    enriched = load_candles(path)
    assert any(c.get("funding") == 0.0009 for c in enriched), "load_candles must preserve funding"


def test_cap_limits_enrichment(tmp_path):
    for sym in ("BTC_USDT_SWAP", "ETH_USDT_SWAP", "SOL_USDT_SWAP"):
        _write_candle_file(tmp_path, sym)
    provider = FakeFunding([(BASE + 5 * HOUR, 0.0005)])
    res = run_flow_enrich(_units(), **_ctx(tmp_path), provider=provider, state=FlowEnrichState(),
                          apply=True, now_ms=BASE + 200 * HOUR, max_enrich=1, allow_public_output=True)
    assert res["counters"]["enriched"] == 1
    assert res["counters"]["skipped_cap"] >= 1


def test_failure_then_backoff(tmp_path):
    _write_candle_file(tmp_path, "BTC_USDT_SWAP")
    state = FlowEnrichState()
    provider = FailFunding()
    now = BASE + 200 * HOUR
    for _ in range(3):
        run_flow_enrich(_units(), **_ctx(tmp_path), provider=provider, state=state,
                        apply=True, now_ms=now, max_enrich=8, max_attempts=3, allow_public_output=True)
    # after >= max_attempts failures within cooldown, the file is backed off (no more calls)
    calls_before = provider.calls
    res = run_flow_enrich(_units(), **_ctx(tmp_path), provider=provider, state=state,
                          apply=True, now_ms=now, max_enrich=8, max_attempts=3, allow_public_output=True)
    assert res["counters"]["skipped_backoff"] >= 1
    assert provider.calls == calls_before  # backed off -> provider not called again


def test_ttl_skips_recent_success(tmp_path):
    _write_candle_file(tmp_path, "BTC_USDT_SWAP")
    state = FlowEnrichState()
    provider = FakeFunding([(BASE + 5 * HOUR, 0.0005)])
    now = BASE + 200 * HOUR
    run_flow_enrich(_units(), **_ctx(tmp_path), provider=provider, state=state, apply=True,
                    now_ms=now, max_enrich=8, ttl_seconds=12 * 3600, allow_public_output=True)
    calls = provider.calls
    res = run_flow_enrich(_units(), **_ctx(tmp_path), provider=provider, state=state, apply=True,
                          now_ms=now + 3600 * 1000, max_enrich=8, ttl_seconds=12 * 3600,
                          allow_public_output=True)
    assert res["counters"]["already_enriched_recently"] >= 1
    assert provider.calls == calls  # within TTL -> not re-fetched
