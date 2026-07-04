# -*- coding: utf-8 -*-
"""OI resolution audit: forward-fill age / density / fresh-share / no-look-ahead and the per-tf
dense vs delta_unreliable verdict. Deterministic — synthetic candles + points, no network."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.oi_resolution_audit import align_oi_to_candles, _tf_verdict  # noqa: E402

_BAR = 3600000  # 1h


def _candles(n, *, oi_from=None):
    rows = []
    for i in range(n):
        c = {"ts": i * _BAR, "open": 100, "high": 101, "low": 99, "close": 100}
        if oi_from is not None:
            c["oi"] = oi_from(i)
        rows.append(c)
    return rows


class TestAlign:
    def test_point_every_bar_is_fresh_and_dense(self):
        candles = _candles(10)
        points = [(i * _BAR, 1000 + i) for i in range(10)]
        a = align_oi_to_candles(candles, points)
        assert a["density"] == 1.0 and a["fresh_share"] == 1.0
        assert a["max_gap_bars"] == 0 and a["median_age_bars"] == 0

    def test_sparse_points_have_age_and_gap(self):
        candles = _candles(10)
        points = [(0, 1000), (5 * _BAR, 1005)]  # only bars 0 and 5 carry a raw point
        a = align_oi_to_candles(candles, points)
        assert a["density"] == 0.2
        assert a["max_gap_bars"] == 4  # bars 1-4 (age 1..4) then a fresh point at 5
        assert a["fresh_share"] == 0.2

    def test_no_lookahead_true_when_oi_matches_at_or_before(self):
        # candle oi forward-filled from the at-or-before point -> no look-ahead
        points = [(0, 1000.0), (5 * _BAR, 1005.0)]
        candles = _candles(10, oi_from=lambda i: 1000.0 if i < 5 else 1005.0)
        assert align_oi_to_candles(candles, points)["no_lookahead"] is True

    def test_no_lookahead_false_when_oi_uses_future_point(self):
        # candle 0 carries a value that only the FUTURE point has -> look-ahead leak
        points = [(0, 1000.0), (5 * _BAR, 1005.0)]
        candles = _candles(10, oi_from=lambda i: 1005.0)  # bar 0 already shows the future value
        assert align_oi_to_candles(candles, points)["no_lookahead"] is False


class TestVerdict:
    def test_dense_when_median_age_below_lookback(self):
        rows = [{"median_age_bars": 0.0, "fresh_share": 1.0, "no_lookahead": True}]
        v = _tf_verdict(rows)
        assert v["verdict"] == "dense"

    def test_delta_unreliable_when_sparse(self):
        rows = [{"median_age_bars": 8.0, "fresh_share": 0.1, "no_lookahead": True}]
        v = _tf_verdict(rows)
        assert v["verdict"] == "delta_unreliable"

    def test_delta_coarse_when_fresh_low_but_age_ok(self):
        # median age 0 (dOI resolvable) but only half the bars carry a raw point (15m case)
        rows = [{"median_age_bars": 0.0, "fresh_share": 0.5, "no_lookahead": True}]
        assert _tf_verdict(rows)["verdict"] == "delta_coarse"

    def test_lookahead_blocks_dense(self):
        rows = [{"median_age_bars": 0.0, "fresh_share": 1.0, "no_lookahead": False}]
        assert _tf_verdict(rows)["verdict"] == "delta_unreliable"

    def test_no_data(self):
        assert _tf_verdict([{"skipped": "no_file"}])["verdict"] == "no_data"
