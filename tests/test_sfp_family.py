# -*- coding: utf-8 -*-
"""SFP / liquidity-sweep family: stop-run reversal, no look-ahead, registered + executable (RR>=2)."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.strategies.sfp import signals_sfp_liquidity_sweep  # noqa: E402


def _c(o, h, low, cl, v=10.0):
    return {"ts": 0, "open": o, "high": h, "low": low, "close": cl, "vol": v}


def _flat(n, px=100.0):
    # a calm range so window_high/low are well-defined around px
    return [_c(px, px + 1, px - 1, px) for _ in range(n)]


class TestSweep:
    def test_sweep_above_high_then_close_below_is_short(self):
        candles = _flat(20)                                   # swing high ~101
        candles.append(_c(100, 105, 100, 100.5))             # pierces 101, closes back below -> SFP short
        candles.append(_c(100.5, 101, 100, 100.5))           # entry bar exists (idx+1)
        sigs = signals_sfp_liquidity_sweep(candles, {"lookback": 20})
        assert any(s["side"] == "short" and s["reason"] == "sfp_sweep_high" for s in sigs)

    def test_sweep_below_low_then_close_above_is_long(self):
        candles = _flat(20)                                   # swing low ~99
        candles.append(_c(100, 100, 95, 99.5))               # pierces 99, closes back above -> SFP long
        candles.append(_c(99.5, 100, 99, 99.5))
        sigs = signals_sfp_liquidity_sweep(candles, {"lookback": 20})
        assert any(s["side"] == "long" and s["reason"] == "sfp_sweep_low" for s in sigs)

    def test_real_breakout_no_reclaim_no_signal(self):
        candles = _flat(20)
        candles.append(_c(100, 105, 100, 104))               # pierces AND closes above -> real breakout, no SFP
        candles.append(_c(104, 105, 103, 104))
        sigs = signals_sfp_liquidity_sweep(candles, {"lookback": 20})
        assert not any(s["reason"] == "sfp_sweep_high" for s in sigs)

    def test_volume_confirmation_filters_quiet_sweep(self):
        candles = _flat(20, px=100.0)                        # avg vol 10
        candles.append(_c(100, 105, 100, 100.5, v=1.0))      # sweep but LOW volume
        candles.append(_c(100.5, 101, 100, 100.5))
        sigs = signals_sfp_liquidity_sweep(candles, {"lookback": 20, "vol_mult": 2.0})
        assert sigs == []                                    # low-volume sweep rejected

    def test_no_lookahead_entry_is_next_bar(self):
        candles = _flat(20)
        candles.append(_c(100, 105, 100, 100.5))
        candles.append(_c(100.5, 101, 100, 100.5))
        sigs = signals_sfp_liquidity_sweep(candles, {"lookback": 20})
        # signal idx must be the bar AFTER the sweep bar (entry at next open), never the sweep bar itself
        assert all(s["idx"] == 21 for s in sigs)


class TestRegistered:
    def test_in_registry_and_executable_rr2(self):
        from src.research_lab.param_schemas import executable_exit_params, validate_params
        from src.research_lab.strategy_registry import REGISTRY
        assert "sfp_liquidity_sweep" in REGISTRY
        p = executable_exit_params("sfp_liquidity_sweep")
        assert validate_params("sfp_liquidity_sweep", p, require_executable=True).ok  # RR>=2 enforced
