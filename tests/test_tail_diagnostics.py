# -*- coding: utf-8 -*-
"""Structural decode of farm lifecycle tails (provider_error / too_short / NEEDS_OI), research-only."""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import tail_diagnostics as TD  # noqa: E402


class TestProviderError:
    def test_absent_from_universe_is_delisted(self):
        d = TD.decode_provider_error("GEOD_USDT_SWAP", {"BTC_USDT_SWAP", "ETH_USDT_SWAP"})
        assert d["reason"] == "no_instrument_or_delisted"

    def test_present_is_transient(self):
        d = TD.decode_provider_error("BTC_USDT_SWAP", {"BTC_USDT_SWAP"})
        assert d["reason"] == "provider_fetch_failed_transient"

    def test_empty_universe_defaults_transient(self):
        assert TD.decode_provider_error("X_USDT_SWAP", set())["reason"] == "provider_fetch_failed_transient"


class TestTooShort:
    def test_long_tf_is_window_mismatch(self):
        assert TD.decode_too_short("BTC_USDT_SWAP", "1d", 0)["reason"] == "window_too_short_for_timeframe"

    def test_fresh_listing(self):
        assert TD.decode_too_short("NEW_USDT_SWAP", "15m", 0)["reason"] == "fresh_listing_pending"

    def test_incomplete_after_retry(self):
        assert TD.decode_too_short("X_USDT_SWAP", "1h", 2)["reason"] == "data_incomplete_after_prepare"


class TestNeedsOi:
    def test_unowned_when_no_oi_families(self):
        d = TD.decode_needs_oi(99, oi_families_planned=False)
        assert d["reason"] == "oi_unmeasured_no_oi_families_planned" and d["count"] == 99


class TestSummarizeAndUniverse:
    def test_summarize_buckets(self):
        blocked = [{"symbol": "GEOD_USDT_SWAP", "machine_reason": "prepare_backoff:provider_error"}]
        deferred = [{"symbol": "BTC_USDT_SWAP", "timeframe": "1d", "attempts": 0,
                     "machine_reason": "too_short"}]
        out = TD.summarize_tails(blocked, deferred, {"BTC_USDT_SWAP"}, 99)
        assert "no_instrument_or_delisted" in out["provider_error"]
        assert "window_too_short_for_timeframe" in out["too_short"]
        assert out["needs_oi"]["count"] == 99

    def test_load_universe_symbols(self, tmp_path):
        d = tmp_path / "discovery"
        d.mkdir()
        (d / "live_universe.json").write_text(json.dumps(
            {"detail": {"core": [{"symbol": "BTC_USDT_SWAP", "inst_id": "BTC-USDT-SWAP"}]}}), encoding="utf-8")
        syms = TD.load_universe_symbols(tmp_path)
        assert "BTC_USDT_SWAP" in syms

    def test_load_universe_missing_is_empty(self, tmp_path):
        assert TD.load_universe_symbols(tmp_path) == set()
