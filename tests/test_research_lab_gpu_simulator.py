# -*- coding: utf-8 -*-
"""Parity tests for the batched GPU/numpy trade simulator vs the CPU reference.

The batched simulator must be bit-identical to experiment.simulate_trades. All
tests run on a GPU-less machine via numpy (xp=np); one real-GPU (cupy) parity test
is skipif-gated.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.research_lab import gpu_runtime
from src.research_lab.experiment import simulate_trades
from src.research_lab.gpu_simulator import (
    GPU_SIM_MEMORY_CAP_CELLS,
    simulate_trades_batched,
    supported_simulation_mode,
    within_memory_cap,
)


def _candles(rows):
    """rows: list of (open, high, low, close) -> load_candles dict shape."""
    return [
        {"ts": i * 1000, "date": f"d{i}", "open": o, "high": h, "low": lo, "close": c, "vol": 1.0}
        for i, (o, h, lo, c) in enumerate(rows)
    ]


def _parity(candles, signals, params, *, fees=7.0, slip=3.0, xp=np):
    cpu = simulate_trades(candles, signals, params, fees_bps=fees, slippage_bps=slip)
    gpu = simulate_trades_batched(candles, signals, params, fees_bps=fees, slippage_bps=slip, xp=xp)
    return cpu, gpu


def _sig(idx, side="long", reason="r"):
    return {"idx": idx, "side": side, "reason": reason}


# --- explicit barrier scenarios --------------------------------------------

def test_long_take_first():
    c = _candles([(100, 100, 100, 100), (100, 101.5, 99.5, 101), (100, 100, 100, 100)])
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 5, "stop_pct": 2, "take_pct": 1})
    assert cpu == gpu and cpu[0]["outcome"] == "take"


def test_long_stop_first():
    c = _candles([(100, 100, 100, 100), (100, 101, 98.5, 99), (100, 100, 100, 100)])
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 5, "stop_pct": 1, "take_pct": 5})
    assert cpu == gpu and cpu[0]["outcome"] == "stop"


def test_short_take_first():
    c = _candles([(100, 100, 100, 100), (100.5, 100.5, 98.5, 99), (100, 100, 100, 100)])
    cpu, gpu = _parity(c, [_sig(0, "short")], {"hold_bars": 5, "stop_pct": 2, "take_pct": 1})
    assert cpu == gpu and cpu[0]["outcome"] == "take"


def test_short_stop_first():
    c = _candles([(100, 100, 100, 100), (101, 101.5, 99, 101), (100, 100, 100, 100)])
    cpu, gpu = _parity(c, [_sig(0, "short")], {"hold_bars": 5, "stop_pct": 1, "take_pct": 5})
    assert cpu == gpu and cpu[0]["outcome"] == "stop"


def test_stop_wins_tie_long():
    c = _candles([(100, 100, 100, 100), (100, 101.5, 98.5, 100)])
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 5, "stop_pct": 1, "take_pct": 1})
    assert cpu == gpu and cpu[0]["outcome"] == "stop"


def test_stop_wins_tie_short():
    c = _candles([(100, 100, 100, 100), (100, 101.5, 98.5, 100)])
    cpu, gpu = _parity(c, [_sig(0, "short")], {"hold_bars": 5, "stop_pct": 1, "take_pct": 1})
    assert cpu == gpu and cpu[0]["outcome"] == "stop"


def test_time_exit_no_touch():
    c = _candles([(100, 100.5, 99.5, 100)] * 7)
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 5, "stop_pct": 2, "take_pct": 2})
    assert cpu == gpu and cpu[0]["outcome"] == "time_exit"


def test_entry_bar_touch_k0():
    c = _candles([(100, 100, 100, 100), (100, 101, 98, 99)] + [(100, 100, 100, 100)] * 4)
    cpu, gpu = _parity(c, [_sig(1)], {"hold_bars": 5, "stop_pct": 1, "take_pct": 5})
    assert cpu == gpu and cpu and cpu[0]["outcome"] == "stop" and cpu[0]["exit_ts"] == 1000


def test_max_holding_cap_near_end():
    c = _candles([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (100, 100.5, 99.5, 100), (100, 100.5, 99.5, 100.2)])
    cpu, gpu = _parity(c, [_sig(1)], {"hold_bars": 5, "stop_pct": 0, "take_pct": 0})
    assert cpu == gpu and cpu[0]["outcome"] == "time_exit"


def test_stop_zero_only_take():
    c = _candles([(100, 100, 100, 100), (100, 101.5, 90, 101)])
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 5, "stop_pct": 0, "take_pct": 1})
    assert cpu == gpu and cpu[0]["outcome"] == "take"


def test_take_zero_only_stop():
    c = _candles([(100, 100, 100, 100), (100, 110, 98.5, 99)])
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 5, "stop_pct": 1, "take_pct": 0})
    assert cpu == gpu and cpu[0]["outcome"] == "stop"


def test_both_zero_pure_time_exit():
    c = _candles([(100, 130, 70, 100)] * 8)
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 3, "stop_pct": 0, "take_pct": 0})
    assert cpu == gpu and cpu[0]["outcome"] == "time_exit"


def test_stop_pct_100_long_disabled():
    # long stop level = entry*(1-100/100)=0.0 -> CPU `if stop` falsy -> disabled
    c = _candles([(100, 100, 100, 100), (100, 101.5, 0.5, 101)])
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 5, "stop_pct": 100, "take_pct": 1})
    assert cpu == gpu and cpu[0]["outcome"] == "take"


def test_no_trade_signal_at_last_bar():
    c = _candles([(100, 100, 100, 100)] * 5)
    cpu, gpu = _parity(c, [_sig(4)], {"hold_bars": 5, "stop_pct": 1, "take_pct": 1})
    assert cpu == gpu == []


def test_no_trade_idx_beyond_n():
    c = _candles([(100, 100, 100, 100)] * 5)
    cpu, gpu = _parity(c, [_sig(7)], {"hold_bars": 5, "stop_pct": 1, "take_pct": 1})
    assert cpu == gpu == []


def test_entry_le_zero_skipped_but_valid_kept():
    c = _candles([(0, 0, 0, 0), (100, 101.5, 99, 101), (100, 100, 100, 100)])
    cpu, gpu = _parity(c, [_sig(0), _sig(1)], {"hold_bars": 3, "stop_pct": 0, "take_pct": 1})
    assert cpu == gpu and len(cpu) == 1


def test_empty_signals():
    c = _candles([(100, 100, 100, 100)] * 5)
    cpu, gpu = _parity(c, [], {"hold_bars": 5, "stop_pct": 1, "take_pct": 1})
    assert cpu == gpu == []


def test_fees_slippage_identical():
    c = _candles([(100, 100.5, 99.5, 100), (100, 100.5, 99.5, 100), (100, 100.5, 99.5, 102)])
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 2, "stop_pct": 0, "take_pct": 0}, fees=5, slip=5)
    assert cpu == gpu
    cpu0, gpu0 = _parity(c, [_sig(0)], {"hold_bars": 2, "stop_pct": 0, "take_pct": 0}, fees=0, slip=0)
    assert cpu0 == gpu0 and cpu0[0]["net_pct"] != cpu[0]["net_pct"]


def test_order_preserved_mixed():
    c = _candles([(100, 100.5, 99.5, 100)] * 20)
    sigs = [_sig(2, "long", "a"), _sig(5, "short", "b"), _sig(9, "long", "c")]
    cpu, gpu = _parity(c, sigs, {"hold_bars": 3, "stop_pct": 5, "take_pct": 5})
    assert cpu == gpu and [t["reason"] for t in gpu] == ["a", "b", "c"]


def test_mfe_window_truncated_at_actual_exit():
    # stop fires at bar1; a huge favorable spike at bar2 is AFTER exit -> excluded from mfe
    c = _candles([(100, 100, 100, 100), (100, 100.5, 98.5, 99), (100, 130, 99, 129)])
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 5, "stop_pct": 1, "take_pct": 20})
    assert cpu == gpu and cpu[0]["outcome"] == "stop"


def test_first_touch_not_later_touch():
    # bar1 hits take, bar2 hits stop -> CPU breaks at first (take)
    c = _candles([(100, 100, 100, 100), (100, 101.5, 100, 101), (100, 100, 98.5, 99)])
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 5, "stop_pct": 1, "take_pct": 1})
    assert cpu == gpu and cpu[0]["outcome"] == "take"


def test_deep_overshoot_exit_pinned_to_level():
    c = _candles([(100, 100, 100, 100), (100, 130, 99.5, 129)])
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 5, "stop_pct": 5, "take_pct": 1})
    assert cpu == gpu and abs(cpu[0]["exit"] - 101.0) < 1e-9  # exit at take level, not bar high


def test_high_precision_rounding_parity():
    e = 100.123456789012
    c = _candles([(e, e, e, e), (e, e * 1.05, e * 0.99, e * 1.02)])
    cpu, gpu = _parity(c, [_sig(0)], {"hold_bars": 3, "stop_pct": 0, "take_pct": 1.234567})
    assert cpu == gpu


# --- property-based fuzz ----------------------------------------------------

def test_random_fuzz_parity():
    rng = np.random.default_rng(12345)
    mism = 0
    for _ in range(400):
        n = int(rng.integers(3, 45))
        price = 100.0
        rows = []
        for _i in range(n):
            price = max(1.0, price * (1 + float(rng.normal(0, 0.04))))
            hi = price * (1 + abs(float(rng.normal(0, 0.03))))
            lo = price * (1 - abs(float(rng.normal(0, 0.03))))
            rows.append((price, hi, lo, price))
        candles = _candles(rows)
        sigs = [_sig(int(rng.integers(0, n)), "long" if rng.random() < 0.5 else "short")
                for _ in range(int(rng.integers(0, 6)))]
        params = {"hold_bars": int(rng.integers(0, 9)),
                  "stop_pct": float(rng.choice([0, 1, 2, 5, 100])),
                  "take_pct": float(rng.choice([0, 1, 2, 5, 100]))}
        cpu, gpu = _parity(candles, sigs, params)
        if cpu != gpu:
            mism += 1
    assert mism == 0


# --- mode + memory gates ----------------------------------------------------

def test_supported_simulation_mode():
    assert supported_simulation_mode({"hold_bars": 5, "stop_pct": 8, "take_pct": 16}) == (True, "")
    ok, reason = supported_simulation_mode({"hold_bars": 5, "trailing_stop_pct": 3})
    assert not ok and reason == "unsupported_exit_mode:trailing_stop_pct"


def test_within_memory_cap():
    assert within_memory_cap(100, 5) is True
    assert within_memory_cap(200000, 256) is False
    assert GPU_SIM_MEMORY_CAP_CELLS > 0


# --- real GPU parity --------------------------------------------------------

@pytest.mark.skipif(not gpu_runtime.gpu_available(), reason="no real GPU backend installed")
def test_real_gpu_simulator_parity():
    cap = gpu_runtime.detect_gpu()
    xp = gpu_runtime.array_module(cap.backend_name)
    rng = np.random.default_rng(7)
    for _ in range(50):
        n = int(rng.integers(5, 40))
        price = 100.0
        rows = []
        for _i in range(n):
            price = max(1.0, price * (1 + float(rng.normal(0, 0.05))))
            rows.append((price, price * 1.03, price * 0.97, price))
        candles = _candles(rows)
        sigs = [_sig(int(rng.integers(0, n)), "long" if rng.random() < 0.5 else "short")
                for _ in range(int(rng.integers(0, 5)))]
        params = {"hold_bars": int(rng.integers(1, 8)),
                  "stop_pct": float(rng.choice([0, 1, 5])), "take_pct": float(rng.choice([0, 1, 5]))}
        cpu = simulate_trades(candles, sigs, params, fees_bps=7, slippage_bps=3)
        gpu = simulate_trades_batched(candles, sigs, params, fees_bps=7, slippage_bps=3, xp=xp)
        assert cpu == gpu
