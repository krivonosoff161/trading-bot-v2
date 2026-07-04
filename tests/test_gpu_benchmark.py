# -*- coding: utf-8 -*-
"""Phase 1.4 — GPU vs CPU benchmark recommendation logic (timing itself is not asserted)."""
from __future__ import annotations

from scripts.strategy_lab.gpu_benchmark import _recommend, _synth_candles


def _rows(key: str, pairs):
    return [{"n_bars": n, "cpu": {key: c}, "gpu": {key: g}} for n, c, g in pairs]


class TestSynthCandles:
    def test_shape_and_validity(self) -> None:
        candles = _synth_candles(50)
        assert len(candles) == 50
        for c in candles:
            assert c["high"] >= c["low"] > 0
            assert "ts" in c and "close" in c

    def test_deterministic(self) -> None:
        assert _synth_candles(20) == _synth_candles(20)


class TestRecommend:
    def test_cpu_when_gpu_always_slower(self) -> None:
        rec = _recommend("signal", _rows("signal_ms", [(200, 0.1, 1.0), (2000, 1.0, 2.0)]))
        assert rec["recommend"] == "cpu"

    def test_gpu_when_always_faster(self) -> None:
        rec = _recommend("signal", _rows("signal_ms", [(200, 2.0, 1.0), (2000, 5.0, 1.0)]))
        assert rec["recommend"] == "gpu"
        assert rec["speedup_at_largest"] == 5.0

    def test_gpu_large_only(self) -> None:
        rec = _recommend("simulation", _rows("sim_ms", [(200, 0.5, 1.0), (2000, 5.0, 1.0)]))
        assert rec["recommend"] == "gpu_large_only"

    def test_cpu_when_no_samples(self) -> None:
        rec = _recommend("signal", [])
        assert rec["recommend"] == "cpu"
