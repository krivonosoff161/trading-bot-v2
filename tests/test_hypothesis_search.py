# -*- coding: utf-8 -*-
"""Hypothesis search + exhaustion_fade: authored grid, OOS verdicts, no-look-ahead fade, research-only."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import hypothesis_search as HS  # noqa: E402
from src.research_lab.strategies.exhaustion_fade import signals_exhaustion_fade  # noqa: E402


def _c(o, h, low, cl, v=10.0):
    return {"ts": 0, "open": o, "high": h, "low": low, "close": cl, "vol": v}


class TestExhaustionFade:
    def test_up_run_with_climax_is_short(self):
        candles = [_c(100, 101, 99, 100) for _ in range(6)]
        candles.append(_c(118, 122, 117, 120, v=50.0))   # +20% over 6 bars + volume climax -> short
        candles.append(_c(120, 121, 118, 119))
        sigs = signals_exhaustion_fade(candles, {"run_lookback": 6, "run_pct": 15, "vol_climax_mult": 1.5})
        assert any(s["side"] == "short" and s["reason"] == "exhaustion_fade_up" for s in sigs)

    def test_down_capitulation_is_long(self):
        candles = [_c(100, 101, 99, 100) for _ in range(6)]
        candles.append(_c(82, 83, 78, 80, v=50.0))       # -20% flush + climax -> long
        candles.append(_c(80, 82, 79, 81))
        sigs = signals_exhaustion_fade(candles, {"run_lookback": 6, "run_pct": 15, "vol_climax_mult": 1.5})
        assert any(s["side"] == "long" and s["reason"] == "exhaustion_fade_down" for s in sigs)

    def test_small_move_no_signal(self):
        candles = [_c(100, 101, 99, 100) for _ in range(8)]
        assert signals_exhaustion_fade(candles, {"run_lookback": 6, "run_pct": 15}) == []

    def test_no_lookahead_entry_next_bar(self):
        candles = [_c(100, 101, 99, 100) for _ in range(6)]
        candles.append(_c(118, 122, 117, 120, v=50.0))
        candles.append(_c(120, 121, 118, 119))
        sigs = signals_exhaustion_fade(candles, {"run_lookback": 6, "run_pct": 15, "vol_climax_mult": 1.5})
        assert all(s["idx"] == 7 for s in sigs)          # entry is the bar AFTER the exhaustion bar


class TestGridAndVerdict:
    def test_grid_includes_new_family_and_exit_variations(self):
        grid = HS._grid()
        fams = {c["family"] for c in grid}
        exits = {c["exit"] for c in grid}
        assert "exhaustion_fade" in fams and "momentum_breakout" in fams
        assert {"baseline", "trailing_tight", "early_tp", "hold_long"} & exits

    def test_verdict(self):
        assert HS._verdict(2.5, 1.6, 0.58) == "holds_oos_candidate"
        assert HS._verdict(1.0, -0.3, 0.5) == "in_sample_only"
        assert HS._verdict(-1.0, -0.5, 0.3) == "weak_or_negative"

    def test_rank_drops_underpowered_and_sorts(self):
        acc = {"a": {"label": "x", "family": "f", "timeframe": "4h", "exit": "early_tp", "symbols": 6,
                     "is": [2.0] * 6, "oos": [1.5] * 6, "oos_pos": 5},
               "b": {"label": "y", "family": "g", "timeframe": "4h", "exit": "early_tp", "symbols": 2,
                     "is": [9.0, 9.0], "oos": [9.0, 9.0], "oos_pos": 2}}
        ranked = HS._rank(acc)
        assert len(ranked) == 1 and ranked[0]["label"] == "x"   # underpowered (n=2) dropped
        assert ranked[0]["verdict"] == "holds_oos_candidate"


class TestRegistered:
    def test_exhaustion_fade_registered_executable(self):
        from src.research_lab.param_schemas import executable_exit_params, validate_params
        from src.research_lab.strategy_registry import REGISTRY
        assert "exhaustion_fade" in REGISTRY
        p = executable_exit_params("exhaustion_fade")
        assert validate_params("exhaustion_fade", p, require_executable=True).ok
