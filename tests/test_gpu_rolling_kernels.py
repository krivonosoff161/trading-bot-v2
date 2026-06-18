# -*- coding: utf-8 -*-
"""Exact scalar<->vectorized parity for the new rolling-window GPU kernels.

The vectorized kernels (numpy/cupy) must produce the identical signal list
(idx + side + reason, in order) as the scalar reference in strategies/. Tested on a
deterministic fixture whose gate decisions are robust to ~1e-12 float-order noise
(the parity standard used for momentum_breakout). One real-GPU parity test is
skipif-gated.
"""

from __future__ import annotations

import math
import random

import numpy as np
import pytest

from src.research_lab import gpu_runtime
from src.research_lab.gpu_kernels import generate_signals_vectorized
from src.research_lab.strategies.pump_dump import signals_pump_dump_scalp
from src.research_lab.strategies.range_family import signals_range_volume_breakout

HOUR = 3_600_000


def _fixture(n: int = 360, seed: int = 3) -> list[dict]:
    """Random-walk OHLCV with periodic volume bursts and occasional tight ranges,
    so both families fire and exercise their gates."""
    rng = random.Random(seed)
    price = 100.0
    out: list[dict] = []
    for i in range(n):
        # alternate calm/tight stretches with trending bursts
        step = (rng.uniform(-0.25, 0.25) if (i // 18) % 2 == 0
                else math.sin(i / 6.0) * 1.4 + rng.uniform(-0.7, 0.7))
        price = max(2.0, price + step)
        high = price + abs(rng.uniform(0.05, 1.0))
        low = price - abs(rng.uniform(0.05, 1.0))
        burst = 4.2 if i % 19 == 0 else 1.0
        out.append({
            "ts": 1_700_000_000_000 + i * HOUR,
            "open": round(low + (high - low) * rng.random(), 4),
            "high": round(high, 4), "low": round(low, 4),
            "close": round(low + (high - low) * rng.random(), 4),
            "vol": round(rng.uniform(120, 1800) * burst, 2),
        })
    return out


PUMP_PARAMS = [
    {"min_body_pct": 3.0, "vol_mult": 3.0, "vol_period": 20, "min_close_pos": 0.6},
    {"min_body_pct": 1.5, "vol_mult": 2.5, "vol_period": 14, "min_close_pos": 0.55},
]
RANGE_PARAMS = [
    {"range_lookback": 20, "max_range_pct": 12.0, "min_accumulation": 1.0, "min_vol_ratio": 1.5, "vol_period": 20},
    {"range_lookback": 14, "max_range_pct": 18.0, "min_accumulation": 0.9, "min_vol_ratio": 1.2, "vol_period": 14},
]


def test_pump_dump_numpy_parity():
    candles = _fixture()
    for params in PUMP_PARAMS:
        scalar = signals_pump_dump_scalp(candles, params)
        vec = generate_signals_vectorized(candles, "pump_dump_scalp", params, xp=np)
        assert scalar == vec, f"pump_dump parity mismatch for {params}"


def test_range_volume_numpy_parity():
    candles = _fixture()
    for params in RANGE_PARAMS:
        scalar = signals_range_volume_breakout(candles, params)
        vec = generate_signals_vectorized(candles, "range_volume_breakout", params, xp=np)
        assert scalar == vec, f"range_volume parity mismatch for {params}"


def test_kernels_actually_fire():
    candles = _fixture()
    pump = generate_signals_vectorized(candles, "pump_dump_scalp", PUMP_PARAMS[1], xp=np)
    rng_sig = generate_signals_vectorized(candles, "range_volume_breakout", RANGE_PARAMS[1], xp=np)
    assert pump, "pump_dump fixture should produce at least one signal"
    assert rng_sig, "range_volume fixture should produce at least one signal"


def test_new_families_in_support_matrix():
    from src.research_lab.gpu_kernels import supported_family
    assert supported_family("pump_dump_scalp")
    assert supported_family("range_volume_breakout")
    assert "pump_dump_scalp" in gpu_runtime.GPU_SUPPORTED_FAMILIES
    assert "range_volume_breakout" in gpu_runtime.GPU_SUPPORTED_FAMILIES


@pytest.mark.skipif(not gpu_runtime.gpu_available(), reason="no real GPU backend installed")
def test_real_gpu_rolling_parity():
    candles = _fixture()
    cap = gpu_runtime.detect_gpu()
    xp = gpu_runtime.array_module(cap.backend_name)
    for family, params in (("pump_dump_scalp", PUMP_PARAMS[0]), ("range_volume_breakout", RANGE_PARAMS[0])):
        scalar = generate_signals_vectorized(candles, family, params, xp=np)
        gpu = generate_signals_vectorized(candles, family, params, xp=xp)
        assert scalar == gpu, f"real-GPU parity mismatch for {family}"
