# -*- coding: utf-8 -*-
"""Tests for the scanner-to-farm bridge (scanner WATCH/GO -> bounded SweepSpec)."""
from __future__ import annotations

from src.research_lab.scanner_bridge import (
    MAX_SYMBOLS_CAP,
    MAX_VARIANTS_CAP,
    STATUS_NO_ASSET,
    STATUS_NO_FAMILY,
    STATUS_NOT_ELIGIBLE,
    STATUS_OK,
    watch_to_sweep,
    watches_to_sweeps,
)
from src.research_lab.sweep_spec import (
    SweepSpec,
    validate_sweep_spec,
)
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.timeframes import load_timeframe_profiles


def _watch(symbol="SPACE", inst="SPACE-USDT-SWAP", verdict="GO", watch_id="watch_abc"):
    return {
        "watch_id": watch_id,
        "card_id": "card_abc",
        "event_key": "evt_abc",
        "created_at": "2026-06-15T00:00:00Z",
        "scanner": {
            "verdict": verdict, "layer": 2, "normalized_side": "buy",
            "event_type": "listing", "lead_class": "leading",
        },
        "asset": {"symbol": symbol, "okx_inst": inst, "baseline_symbol": "BTC-USDT-SWAP"},
        "farm": {"eligible": True, "selected_timeframe": "1h", "data_readiness_status": "usable"},
        "trigger": {"headline": "New listing X", "source": "tg_new_listings_feed"},
    }


def test_watch_to_sweep_ok_builds_bounded_spec():
    res = watch_to_sweep(_watch(), timeframe="1d", max_variants=8)
    assert res.ok
    assert res.status == STATUS_OK
    assert res.symbol == "SPACE-USDT-SWAP"
    assert isinstance(res.sweep, SweepSpec)
    assert res.sweep.anchor_symbol == "SPACE-USDT-SWAP"
    assert res.sweep.backend == "auto"
    assert res.sweep.private_output_policy == "private_only"
    assert res.sweep.symbol_scope() == 1
    assert res.sweep.variant_count() <= res.sweep.max_variants
    # provenance carried, no profitability claim
    assert res.context["origin"] == "scanner_watch"
    assert res.context["verdict"] == "GO"
    assert "not a profitability claim" in res.context["note"]


def test_watch_to_sweep_honors_explicit_backend():
    res = watch_to_sweep(_watch(), timeframe="1d", max_variants=8, backend="cpu")
    assert res.ok
    assert res.sweep.backend == "cpu"


def test_watch_to_sweep_spec_passes_validation():
    res = watch_to_sweep(_watch(), timeframe="1d")
    result = validate_sweep_spec(
        res.sweep,
        timeframe_profiles=load_timeframe_profiles(),
        resource_policy=load_resource_policy(),
    )
    assert result.ok, result.errors


def test_watch_no_asset_is_not_ok_and_does_not_raise():
    bad = _watch()
    bad["asset"] = {}
    res = watch_to_sweep(bad)
    assert not res.ok
    assert res.status == STATUS_NO_ASSET
    assert res.sweep is None


def test_max_variants_capped():
    res = watch_to_sweep(_watch(), timeframe="1d", max_variants=9999)
    assert res.sweep.max_variants <= MAX_VARIANTS_CAP


def test_watches_to_sweeps_dedupes_and_caps_symbols():
    watches = [
        _watch(symbol="SPACE", inst="SPACE-USDT-SWAP", watch_id="w1"),
        _watch(symbol="SPACE", inst="SPACE-USDT-SWAP", watch_id="w2"),  # dup symbol
        _watch(symbol="DOGE", inst="DOGE-USDT-SWAP", watch_id="w3"),
    ]
    results = watches_to_sweeps(watches, timeframe="1d", max_symbols=MAX_SYMBOLS_CAP)
    ok = [r for r in results if r.ok]
    symbols = {r.symbol for r in ok}
    assert symbols == {"SPACE-USDT-SWAP", "DOGE-USDT-SWAP"}  # dup collapsed


def test_watch_to_sweep_honors_scanner_selected_timeframe():
    w = _watch()
    w["farm"] = {"eligible": True, "selected_timeframe": "15m", "data_readiness_status": "usable"}
    res = watch_to_sweep(w, timeframe="1d")   # default 1d overridden by scanner's 15m
    assert res.ok
    assert res.sweep.timeframe == "15m"


def test_watch_to_sweep_falls_back_from_1m_to_sweepable():
    w = _watch()
    w["farm"] = {"eligible": True, "selected_timeframe": "1m"}  # 1m is trigger-only, never swept
    res = watch_to_sweep(w, timeframe="1d")
    assert res.ok
    assert res.sweep.timeframe != "1m"


def test_watch_to_sweep_no_compatible_family_is_skipped():
    res = watch_to_sweep(_watch(), timeframe="1d", families=())
    assert not res.ok
    assert res.status == STATUS_NO_FAMILY
    assert res.sweep is None


def test_watch_not_farm_eligible_is_skipped_with_reason():
    w = _watch()
    w["asset"]["okx_resolved"] = False
    w["farm"] = {"eligible": False, "pending_reason": "no_okx_instrument"}
    res = watch_to_sweep(w)
    assert not res.ok
    assert res.status == STATUS_NOT_ELIGIBLE
    assert res.sweep is None
    assert res.context["farm_pending_reason"] == "no_okx_instrument"


def test_new_farm_block_without_positive_eligibility_is_skipped():
    w = _watch()
    w["farm"] = {"eligible": None, "pending_reason": "readiness_not_assessed"}
    res = watch_to_sweep(w)
    assert not res.ok
    assert res.status == STATUS_NOT_ELIGIBLE
    assert res.sweep is None
    assert res.context["farm_pending_reason"] == "readiness_not_assessed"


def test_legacy_watch_without_farm_gate_is_skipped():
    w = _watch()
    w.pop("farm")
    res = watch_to_sweep(w)
    assert not res.ok
    assert res.status == STATUS_NOT_ELIGIBLE
    assert res.sweep is None


def test_watches_to_sweeps_respects_symbol_cap():
    watches = [
        _watch(symbol=f"C{i}", inst=f"C{i}-USDT-SWAP", watch_id=f"w{i}")
        for i in range(10)
    ]
    results = watches_to_sweeps(watches, timeframe="1d", max_symbols=2)
    ok = [r for r in results if r.ok]
    assert len(ok) == 2
