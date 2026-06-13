# -*- coding: utf-8 -*-

import pytest

from src.research_lab.entry_timing import entry_timing_metrics


def _candles(rows: list[tuple[float, float, float, float]]) -> list[dict]:
    return [
        {"ts": 1_700_000_000_000 + i * 900_000, "open": o, "high": h, "low": low, "close": c}
        for i, (o, h, low, c) in enumerate(rows)
    ]


# Long move: close 100 (idx4) -> 120 (idx10), smooth rise.
_LONG_ROWS = [
    (100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
    (100, 100, 100, 100), (100, 100, 100, 100),
    (100, 105, 100, 104), (104, 109, 103, 108), (108, 113, 107, 112),
    (112, 116, 111, 115), (115, 119, 114, 118), (118, 121, 117, 120),
    (120, 120, 120, 120), (120, 120, 120, 120),
]


def test_perfect_early_entry_low_lag_high_capture():
    m = entry_timing_metrics(
        _candles(_LONG_ROWS), move_start_idx=4, move_end_idx=10, entry_idx=5, direction="long"
    )
    assert m["entry_lag_bars"] == 1
    assert m["missed_move_pct"] == 0.0
    assert m["capture_ratio"] == pytest.approx(1.0)
    assert m["zero_movement"] is False


def test_late_entry_misses_most_of_move():
    m = entry_timing_metrics(
        _candles(_LONG_ROWS), move_start_idx=4, move_end_idx=10, entry_idx=9, direction="long"
    )
    assert m["entry_lag_bars"] == 5
    assert m["missed_move_pct"] == pytest.approx(75.0)
    assert m["capture_ratio"] == pytest.approx(0.25)


def test_early_entry_shows_adverse_excursion_and_false_early_flag():
    rows = [
        (100, 100, 100, 100), (100, 100, 100, 100),
        (100, 100, 95, 100), (100, 100, 95, 100),  # dip to 95 before the move
        (100, 100, 100, 100),
        (100, 105, 100, 104), (104, 109, 103, 108), (108, 113, 107, 112),
        (112, 116, 111, 115), (115, 119, 114, 118), (118, 121, 117, 120),
    ]
    m = entry_timing_metrics(
        _candles(rows), move_start_idx=4, move_end_idx=10, entry_idx=2, direction="long"
    )
    assert m["entry_before_impulse"] is True
    assert m["max_adverse_excursion_pct"] == pytest.approx(5.0)
    assert m["false_early_entry"] is True


def test_short_direction_capture():
    rows = [
        (100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100),
        (100, 100, 100, 100), (100, 100, 100, 100),
        (100, 100, 95, 96), (96, 97, 91, 92), (92, 93, 87, 88),
        (88, 89, 84, 85), (85, 86, 81, 82), (82, 83, 79, 80),
    ]
    m = entry_timing_metrics(
        _candles(rows), move_start_idx=4, move_end_idx=10, entry_idx=5, direction="short"
    )
    assert m["total_move_pct"] == pytest.approx(20.0)
    assert m["capture_ratio"] == pytest.approx(1.0)
    assert m["max_adverse_excursion_pct"] == 0.0


def test_zero_movement_is_flagged():
    rows = [(100, 100, 100, 100)] * 12
    m = entry_timing_metrics(
        _candles(rows), move_start_idx=4, move_end_idx=10, entry_idx=5, direction="long"
    )
    assert m["zero_movement"] is True
    assert m["capture_ratio"] == 0.0
    assert m["missed_move_pct"] == 0.0


def test_invalid_index_raises():
    candles = _candles(_LONG_ROWS)
    with pytest.raises(ValueError):
        entry_timing_metrics(candles, move_start_idx=4, move_end_idx=10, entry_idx=999, direction="long")
    with pytest.raises(ValueError):
        entry_timing_metrics(candles, move_start_idx=10, move_end_idx=4, entry_idx=5, direction="long")
    with pytest.raises(ValueError):
        entry_timing_metrics(candles, move_start_idx=4, move_end_idx=10, entry_idx=5, direction="sideways")
