# -*- coding: utf-8 -*-
"""Trade-path instrumentation + bounded backfill: the simulator records the PATH of each trade
(time-to-MFE/MAE, tp-before-sl, path_quality) over the already-decided hold window (no
look-ahead, no exit-logic change), and the backfill aggregates it per candidate for the memory."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.experiment import simulate_trades  # noqa: E402
from src.research_lab.trade_path_backfill import _path_agg, summarize_backfill  # noqa: E402


def _candle(ts, o, h, low, c):
    return {"ts": ts, "open": o, "high": h, "low": low, "close": c}


class TestSimulatorPathFields:
    def test_tp_hit_records_path(self):
        # entry at idx0 open=100, take_pct=2 -> take=102; bar2 high reaches 103 -> TP first.
        candles = [_candle(0, 100, 100.5, 99.8, 100.2), _candle(1, 100.2, 101, 100, 100.8),
                   _candle(2, 100.8, 103, 100.6, 102.5), _candle(3, 102.5, 103, 102, 102.4)]
        signals = [{"idx": 0, "side": "long", "reason": "t"}]
        trades = simulate_trades(candles, signals, {"hold_bars": 3, "stop_pct": 2, "take_pct": 2},
                                 fees_bps=7, slippage_bps=3)
        assert len(trades) == 1
        t = trades[0]
        for k in ("time_to_mfe", "time_to_mae", "tp_before_sl", "bars_to_tp", "bars_to_sl",
                  "adverse_before_favorable", "path_quality", "bars_held"):
            assert k in t
        assert t["outcome"] == "take"
        assert t["tp_before_sl"] is True and t["bars_to_tp"] == t["bars_held"] and t["bars_to_sl"] is None
        assert 0 <= t["time_to_mfe"] <= t["bars_held"]

    def test_stop_hit_records_path(self):
        # long entry, price falls -> stop=98 hit at bar1 (low 97).
        candles = [_candle(0, 100, 100.2, 99.9, 100), _candle(1, 99.9, 100, 97, 97.5),
                   _candle(2, 97.5, 98, 96, 96.5)]
        signals = [{"idx": 0, "side": "long", "reason": "t"}]
        trades = simulate_trades(candles, signals, {"hold_bars": 2, "stop_pct": 2, "take_pct": 5},
                                 fees_bps=7, slippage_bps=3)
        t = trades[0]
        assert t["outcome"] == "stop"
        assert t["tp_before_sl"] is False and t["bars_to_sl"] == t["bars_held"] and t["bars_to_tp"] is None

    def test_timeout_has_none_ordering(self):
        candles = [_candle(0, 100, 100.3, 99.8, 100.1), _candle(1, 100.1, 100.4, 99.9, 100.2),
                   _candle(2, 100.2, 100.5, 99.95, 100.3)]
        signals = [{"idx": 0, "side": "long", "reason": "t"}]
        trades = simulate_trades(candles, signals, {"hold_bars": 2, "stop_pct": 5, "take_pct": 5},
                                 fees_bps=7, slippage_bps=3)
        t = trades[0]
        assert t["outcome"] == "time_exit"
        assert t["tp_before_sl"] is None and t["bars_to_tp"] is None and t["bars_to_sl"] is None


class TestPathAgg:
    def test_empty(self):
        assert _path_agg([])["n_trades"] == 0

    def test_aggregates_shares(self):
        trades = [
            {"tp_before_sl": True, "time_to_mfe": 1, "time_to_mae": 3, "bars_held": 4,
             "capture_of_mfe": 0.8, "mfe_pct": 2.0, "mae_pct": 0.5, "adverse_before_favorable": False,
             "path_quality": "clean_capture"},
            {"tp_before_sl": False, "time_to_mfe": 3, "time_to_mae": 1, "bars_held": 2,
             "capture_of_mfe": -1.0, "mfe_pct": 0.3, "mae_pct": 1.0, "adverse_before_favorable": True,
             "path_quality": "gave_back"},
            {"tp_before_sl": None, "time_to_mfe": 2, "time_to_mae": 2, "bars_held": 5,
             "capture_of_mfe": 0.4, "mfe_pct": 1.0, "mae_pct": 0.4, "adverse_before_favorable": False,
             "path_quality": "partial"},
        ]
        a = _path_agg(trades)
        assert a["n_trades"] == 3
        assert a["tp_before_sl_share"] == round(1 / 3, 4)
        assert a["sl_before_tp_share"] == round(1 / 3, 4)
        assert a["timeout_share"] == round(1 / 3, 4)
        assert a["adverse_first_rate"] == round(1 / 3, 4)
        assert a["path_quality"] == {"clean_capture": 1, "gave_back": 1, "partial": 1}


class TestSummary:
    def test_distribution_and_per_subreason(self):
        rows = [
            {"uc_key": "a", "subreason": "wrong_exit", "n_trades": 6, "avg_capture": 0.2,
             "avg_time_to_mfe": 4.0, "tp_before_sl_share": 0.1, "path_quality": {"gave_back": 5, "partial": 1}},
            {"uc_key": "b", "subreason": "wrong_exit", "n_trades": 8, "avg_capture": 0.25,
             "avg_time_to_mfe": 5.0, "tp_before_sl_share": 0.2, "path_quality": {"gave_back": 6}},
            {"uc_key": "c", "skipped": "no_signals"},
        ]
        s = summarize_backfill(rows)
        assert s["evaluated"] == 2 and s["skipped"] == 1
        assert s["path_quality_distribution"]["gave_back"] == 11
        we = s["by_subreason"]["wrong_exit"]
        assert we["n"] == 2 and we["avg_capture"] == round((0.2 + 0.25) / 2, 4)
