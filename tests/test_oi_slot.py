# -*- coding: utf-8 -*-
"""Recorded OI slot loading/validation, OI quadrant A/B opposition, NEEDS_OI_DATA."""

from __future__ import annotations

import json

from src.research_lab.experiment import ExperimentSpec, evaluate_spec, missing_required_data
from src.research_lab.oi_slot import load_oi_series, oi_slot_dir
from src.research_lab.strategies.flow_family import (
    signals_oi_price_quadrant_continuation,
    signals_oi_price_quadrant_trap_fade,
)

HOUR = 3_600_000
BASE = 1_700_000_000_000


def _c(i, oi=None, price=None):
    p = 10.0 + i if price is None else price
    row = {"ts": BASE + i * HOUR, "open": p, "high": p + 1, "low": p - 1, "close": p, "vol": 100.0}
    if oi is not None:
        row["oi"] = oi
    return row


# ---- OI slot ----

def test_load_oi_json_slot(tmp_path):
    d = oi_slot_dir(tmp_path)
    d.mkdir(parents=True)
    rows = [{"ts": BASE, "oi": 1000.0, "symbol": "BTC_USDT_SWAP", "source": "manual"},
            {"ts": BASE + HOUR, "oi": 1100.0}]
    (d / "BTC_USDT_SWAP_oi.json").write_text(json.dumps(rows), encoding="utf-8")
    series = load_oi_series(tmp_path, "BTC_USDT_SWAP")
    assert series["rows"] == 2
    assert series["points"][0] == (BASE, 1000.0)
    assert "manual" in series["source"]


def test_load_oi_csv_slot_and_validation(tmp_path):
    d = oi_slot_dir(tmp_path)
    d.mkdir(parents=True)
    csv_text = "ts,oi,symbol\n%d,2000,BTC_USDT_SWAP\nbad,oops,BTC_USDT_SWAP\n%d,-5,BTC_USDT_SWAP\n" % (
        BASE, BASE + HOUR)
    (d / "BTC_USDT_SWAP_oi.csv").write_text(csv_text, encoding="utf-8")
    series = load_oi_series(tmp_path, "BTC_USDT_SWAP")
    assert series["rows"] == 1            # one valid row
    assert series["rejected"] == 2        # malformed + non-positive oi rejected


def test_missing_oi_slot_is_empty_not_faked(tmp_path):
    series = load_oi_series(tmp_path, "ETH_USDT_SWAP")
    assert series["points"] == [] and series["source"] == "none"


# ---- OI quadrant A/B opposition ----

def test_quadrant_ab_produce_opposite_actions():
    # OI rising + price rising on every bar -> every quadrant is new_longs
    candles = [_c(i, oi=1000.0 * (1 + 0.01 * i)) for i in range(20)]
    params = {"oi_lookback": 5}
    cont = signals_oi_price_quadrant_continuation(candles, params)
    trap = signals_oi_price_quadrant_trap_fade(candles, params)
    assert cont and trap
    cont_by_idx = {s["idx"]: s["side"] for s in cont}
    trap_by_idx = {s["idx"]: s["side"] for s in trap}
    assert cont_by_idx.keys() == trap_by_idx.keys()
    for idx in cont_by_idx:
        assert cont_by_idx[idx] != trap_by_idx[idx], "A/B must take opposite sides on the same quadrant"
    assert set(cont_by_idx.values()) == {"long"} and set(trap_by_idx.values()) == {"short"}


# ---- NEEDS_OI_DATA classification ----

def test_missing_required_data_detects_absent_oi():
    no_oi = [_c(i) for i in range(10)]
    with_oi = [_c(i, oi=1000.0) for i in range(10)]
    assert missing_required_data("oi_price_quadrant_continuation", no_oi) == "NEEDS_OI_DATA"
    assert missing_required_data("oi_price_quadrant_continuation", with_oi) is None
    assert missing_required_data("microstructure_confirmed_breakout", no_oi) == "NEEDS_MICRO_DATA"
    assert missing_required_data("main_fast_swing_regime", no_oi) is None  # no required data


def test_evaluate_spec_marks_needs_oi_data(tmp_path):
    rows = [{"ts": BASE + i * HOUR, "date": "", "open": 10.0, "high": 11.0,
             "low": 9.0, "close": 10.0 + 0.01 * i, "vol": 100.0} for i in range(120)]
    (tmp_path / "BTC_USDT_SWAP_x_1h.json").write_text(json.dumps(rows), encoding="utf-8")
    spec = ExperimentSpec(
        experiment_id="t", data_glob=str(tmp_path / "{symbol}_*_1h.json"),
        symbols=["BTC_USDT_SWAP"], families=["oi_price_quadrant_continuation"],
        parameter_grid={"oi_price_quadrant_continuation": [
            {"oi_lookback": 5, "hold_bars": 4, "stop_pct": 8, "take_pct": 12}]},
        timeframe="1h")
    results = evaluate_spec(spec)
    assert results and results[0].decision == "NEEDS_OI_DATA"
    assert results[0].validation_status == "NEEDS_OI_DATA"
    assert "needs_data" in results[0].risk_flags
