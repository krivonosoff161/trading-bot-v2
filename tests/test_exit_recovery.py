# -*- coding: utf-8 -*-
"""T3-A — exit-recovery research (read-only re-sim). Recovered is a re-validation candidate,
NEVER paper-ready. Tests cover the deterministic grid/aggregation/classification, not timing."""
from __future__ import annotations

from src.research_lab.exit_recovery import (
    MIN_TRADES_RECOVER,
    _agg,
    _exit_grid,
    exit_recovered_candidates,
    summarize_recovery,
)


class TestExitGrid:
    def test_has_baseline_and_alternatives(self) -> None:
        names = [n for n, _ in _exit_grid({"stop_pct": 1.0, "take_pct": 2.0, "hold_bars": 6})]
        assert names[0] == "baseline"
        for n in ("tp_half", "tp_0.66", "rr2", "rr3", "hold_short", "hold_long"):
            assert n in names

    def test_asymmetric_variants_keep_rr2(self) -> None:
        grid = dict(_exit_grid({"stop_pct": 1.0, "take_pct": 5.0, "hold_bars": 6}))
        assert grid["rr2"]["take_pct"] >= 1.0 * 2          # take >= 2*stop
        st = grid["stop_tight_rr2"]
        assert st["take_pct"] >= st["stop_pct"] * 2 - 1e-9


class TestAgg:
    def test_empty(self) -> None:
        assert _agg([])["n_trades"] == 0

    def test_aggregates(self) -> None:
        trades = [{"net_pct": 1.0, "capture_of_mfe": 0.5, "mfe_pct": 1.5, "outcome": "take"},
                  {"net_pct": -0.5, "capture_of_mfe": -0.2, "mfe_pct": 0.3, "outcome": "stop"}]
        a = _agg(trades)
        assert a["n_trades"] == 2 and a["net"] == 0.5 and a["n_tp"] == 1 and a["n_sl"] == 1


class TestRecoveredClass:
    def _row(self, recovered, thin, n=8, net=0.5):
        return {"symbol": "X", "timeframe": "1h", "family": "mean_reversion_fade",
                "best_variant": "tp_half", "best_net": net, "baseline_net": -0.2,
                "n_trades": n, "recovered": recovered, "thin_recovered": thin}

    def test_only_recovered_in_class(self) -> None:
        rows = [self._row(True, False), self._row(False, True, n=2), self._row(False, False, net=-0.3)]
        cands = exit_recovered_candidates(rows)
        assert len(cands) == 1
        assert cands[0]["research_class"] == "exit_recovered_candidate"

    def test_invariant_never_paper_ready(self) -> None:
        cands = exit_recovered_candidates([self._row(True, False), self._row(True, False)])
        assert all(c["paper_forward_ready"] is False for c in cands)

    def test_summary_splits_recovered_and_thin(self) -> None:
        rows = [self._row(True, False), self._row(False, True, n=2), self._row(False, True, n=1)]
        s = summarize_recovery(rows)
        assert s["recovered"] == 1
        assert s["thin_recovered"] == 2

    def test_min_trades_constant_is_meaningful(self) -> None:
        assert MIN_TRADES_RECOVER >= 3  # below 3 the hard validator can't score anything
