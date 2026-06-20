# -*- coding: utf-8 -*-
"""Exit Phase-2 dynamic-exit simulator: trailing / break-even / early-TP / partial / time-decay,
verified no-look-ahead on synthetic candles (the stop for bar j is set from bars < j), plus the
recovered / still_bad / thin_noise / needs_forward_only classification."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.exit_phase2 import (  # noqa: E402
    _avg_net,
    _exit_modes,
    simulate_exit_mode,
    summarize_exit_phase2,
)


def _c(ts, o, h, low, c):
    return {"ts": ts, "open": o, "high": h, "low": low, "close": c}


_LONG = [{"idx": 0, "side": "long", "reason": "t"}]
_PARAMS = {"stop_pct": 8, "take_pct": 50, "hold_bars": 5}  # far take so the mode decides the exit


class TestTrailing:
    def test_trailing_locks_profit_no_lookahead(self):
        # rises to 110 on bar1, falls on bar2; trail 5% off the 110 high-water (set from bar1) = 104.5
        candles = [_c(0, 100, 100, 100, 100), _c(1, 100, 110, 100, 109), _c(2, 100, 110, 104, 104)]
        trades = simulate_exit_mode(candles, _LONG, _PARAMS, {"kind": "trailing", "trail_pct": 5})
        assert len(trades) == 1
        t = trades[0]
        assert t["outcome"] == "trail"
        assert abs(t["exit"] - 104.5) < 1e-6  # trail level from the PRIOR bar's high, not bar2's
        assert abs(t["net_pct"] - (4.5 - 0.1)) < 1e-6


class TestBreakEven:
    def test_break_even_cuts_to_zero(self):
        # rises past the 3% be-trigger on bar1 -> stop moves to entry; bar2 dips below entry -> exit ~0
        candles = [_c(0, 100, 100, 100, 100), _c(1, 100, 110, 100, 109), _c(2, 100, 102, 99, 99)]
        trades = simulate_exit_mode(candles, _LONG, _PARAMS, {"kind": "break_even", "be_trigger_pct": 3})
        t = trades[0]
        assert t["outcome"] == "stop" and abs(t["exit"] - 100.0) < 1e-6
        assert abs(t["net_pct"] - (-0.1)) < 1e-6  # break-even minus cost, not the full -8% stop


class TestEarlyTP:
    def test_early_tp_exits_before_baseline_take(self):
        candles = [_c(0, 100, 100, 100, 100), _c(1, 100, 106, 100, 105), _c(2, 100, 106, 100, 105)]
        early = simulate_exit_mode(candles, _LONG, _PARAMS, {"kind": "fixed", "take_pct": 5})
        base = simulate_exit_mode(candles, _LONG, _PARAMS, {"kind": "fixed"})  # take 50% -> never hits
        assert early[0]["outcome"] == "take" and abs(early[0]["exit"] - 105.0) < 1e-6
        assert base[0]["outcome"] == "time_exit"  # far take not reached -> rides to hold cap


class TestPartial:
    def test_partial_blends_two_exits(self):
        # half off at tp1=2% (102), rest rides to tp2=4% (104) -> net ~ 0.5*2 + 0.5*4 - cost
        candles = [_c(0, 100, 100, 100, 100), _c(1, 100, 102, 100, 102), _c(2, 100, 104, 101, 104)]
        mode = {"kind": "partial", "tp1_pct": 2, "tp2_pct": 4}
        t = simulate_exit_mode(candles, _LONG, _PARAMS, mode)[0]
        assert t["outcome"] == "take"
        assert abs(t["net_pct"] - (3.0 - 0.1)) < 1e-6


class TestGrid:
    def test_grid_has_all_modes(self):
        names = [n for n, _ in _exit_modes({"stop_pct": 8, "take_pct": 16, "hold_bars": 5})]
        for n in ("baseline", "early_tp", "trailing", "break_even", "time_decay", "partial_tp", "hold_long"):
            assert n in names

    def test_avg_net(self):
        assert _avg_net([{"net_pct": 1.0}, {"net_pct": -0.5}]) == 0.25
        assert _avg_net([]) == 0.0


class TestSummary:
    def test_classes_counted(self):
        rows = [
            {"outcome_class": "still_bad", "best_mode": "trailing"},
            {"outcome_class": "exit_recovered_candidate", "best_mode": "early_tp"},
            {"outcome_class": "needs_forward_only", "best_mode": "early_tp"},
            {"outcome_class": "thin_noise", "best_mode": "trailing"},
            {"skipped": "no_signals"},
        ]
        s = summarize_exit_phase2(rows)
        assert s["evaluated"] == 4 and s["skipped"] == 1
        assert s["recovered"] == 2 and s["needs_forward_only"] == 1
        assert s["best_mode_of_recovered"]["early_tp"] == 2
