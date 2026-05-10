"""
Unit tests for compute_metrics() in bt_sweep_drift.py.

Tests cover 5 cases:
1. Empty trades list → returns {}
2. 100% TP → wr=100.0, honest_wr=100.0, pf=99.0
3. 100% SL → wr=0.0, sim negative, honest_wr=0.0
4. Mix of TP + SL + TIME_EXIT → honest_wr < wr (TIME_EXIT not in wr denom but in honest_wr)
5. PF by exit_r, not pnl — same pnl but different exit_r gives different PF
"""

import pytest
from scripts.backtest.bt_sweep_drift import compute_metrics


class TestComputeMetrics:
    """Test suite for compute_metrics function."""

    def test_empty_trades_returns_empty_dict(self):
        """Case 1: Empty trades list should return empty dict."""
        result = compute_metrics([])
        assert result == {}

    def test_100_percent_tp(self):
        """Case 2: All TP trades → wr=100.0, honest_wr=100.0, pf=99.0."""
        trades = [
            {"outcome": "TP", "pnl": 50.0, "exit_r": 1.0, "regime": "DRIFT", "trade_style": "FAST", "executed": True}
            for _ in range(5)
        ]
        result = compute_metrics(trades)

        assert result["wr"] == 100.0
        assert result["honest_wr"] == 100.0
        assert result["pf"] == 99.0
        assert result["n"] == 5
        assert result["n_tp"] == 5
        assert result["n_sl"] == 0
        assert result["n_te"] == 0

    def test_100_percent_sl(self):
        """Case 3: All SL trades → wr=0.0, sim negative, honest_wr=0.0."""
        trades = [
            {"outcome": "STOP", "pnl": -100.0, "exit_r": -1.0, "regime": "DRIFT", "trade_style": "FAST", "executed": True}
            for _ in range(5)
        ]
        result = compute_metrics(trades)

        assert result["wr"] == 0.0
        assert result["honest_wr"] == 0.0
        assert result["sim"] < 0
        assert result["n"] == 5
        assert result["n_tp"] == 0
        assert result["n_sl"] == 5

    def test_mixed_tp_sl_time_exit_honest_wr_less_than_wr(self):
        """Case 4: Mix of TP + SL + TIME_EXIT → honest_wr < wr."""
        # 4 TP, 2 SL, 4 TIME_EXIT = 10 total
        # wr = 4 / (4 + 2) = 66.7%
        # honest_wr = 4 / 10 = 40.0%
        trades = [
            {"outcome": "TP", "pnl": 50.0, "exit_r": 1.0, "regime": "DRIFT", "trade_style": "FAST", "executed": True}
            for _ in range(4)
        ]
        trades += [
            {"outcome": "STOP", "pnl": -100.0, "exit_r": -1.0, "regime": "DRIFT", "trade_style": "FAST", "executed": True}
            for _ in range(2)
        ]
        trades += [
            {"outcome": "TIME_EXIT", "pnl": -10.0, "exit_r": -0.2, "regime": "DRIFT", "trade_style": "FAST", "executed": True}
            for _ in range(4)
        ]

        result = compute_metrics(trades)

        assert result["n"] == 10
        assert result["n_tp"] == 4
        assert result["n_sl"] == 2
        assert result["n_te"] == 4
        assert result["wr"] == pytest.approx(66.7, rel=0.01)
        assert result["honest_wr"] == pytest.approx(40.0, rel=0.01)
        assert result["honest_wr"] < result["wr"]

    def test_pf_by_exit_r_not_pnl(self):
        """Case 5: PF calculated by exit_r, not pnl."""
        # Set A: exit_r=2.0 win vs exit_r=-1.0 loss → PF=2.0
        trades_a = [
            {"outcome": "TP", "pnl": 100.0, "exit_r": 2.0, "regime": "DRIFT", "trade_style": "FAST", "executed": True},
            {"outcome": "STOP", "pnl": -50.0, "exit_r": -1.0, "regime": "DRIFT", "trade_style": "FAST", "executed": True},
        ]
        result_a = compute_metrics(trades_a)

        # Set B: same pnl structure, exit_r=1.0 win → PF=1.0
        trades_b = [
            {"outcome": "TP", "pnl": 100.0, "exit_r": 1.0, "regime": "DRIFT", "trade_style": "FAST", "executed": True},
            {"outcome": "STOP", "pnl": -50.0, "exit_r": -1.0, "regime": "DRIFT", "trade_style": "FAST", "executed": True},
        ]
        result_b = compute_metrics(trades_b)

        assert result_a["pf"] == 2.0
        assert result_b["pf"] == 1.0
        assert result_a["pf"] != result_b["pf"]
