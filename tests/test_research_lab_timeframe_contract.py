# -*- coding: utf-8 -*-
"""Tests for the explicit timeframe contract in ExperimentSpec (Phase 1).

Verifies that:
- 15m spec with only 1d data does not execute (DATA_NOT_READY).
- 1d spec with 1d data executes normally.
- same symbol with both 1d and 15m files: each spec picks the matching file.
- legacy spec without timeframe works as before (picks largest).
"""

import json
from pathlib import Path

from src.research_lab.experiment import ExperimentSpec, choose_symbol_file, evaluate_spec
from scripts.strategy_lab.generate_event_sweeps import _exp_to_dict as event_exp_to_dict
from scripts.strategy_lab.queue_validated_proposals import _exp_to_dict as queued_exp_to_dict


def _write_candles(path: Path, interval_ms: int, count: int, price: float = 100.0) -> None:
    rows = []
    p = price
    for i in range(count):
        if i in {count // 4, count // 2, 3 * count // 4}:
            p *= 1.12
            vol = 5000
        else:
            p *= 1.01 if i % 3 else 0.995
            vol = 1000 + i
        rows.append({
            "ts": 1_700_000_000_000 + i * interval_ms,
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": round(p * 0.99, 4),
            "high": round(p * 1.03, 4),
            "low": round(p * 0.97, 4),
            "close": round(p, 4),
            "vol": vol,
        })
    path.write_text(json.dumps(rows), encoding="utf-8")


DAY_MS = 86_400_000
MIN15_MS = 900_000


def test_15m_spec_with_only_1d_file_returns_empty(tmp_path):
    _write_candles(tmp_path / "ABC_USDT_SWAP_80d.json", DAY_MS, 80)
    spec = ExperimentSpec(
        experiment_id="tf_no_match",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 5, "threshold_pct": 0, "hold_bars": 2}]},
        min_trades=1,
        timeframe="15m",
    )
    results = evaluate_spec(spec)
    assert results == [], "15m spec must not execute on 1d data"


def test_1d_spec_with_1d_file_executes(tmp_path):
    _write_candles(tmp_path / "ABC_USDT_SWAP_80d.json", DAY_MS, 80)
    spec = ExperimentSpec(
        experiment_id="tf_match",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 5, "threshold_pct": 0, "hold_bars": 2}]},
        min_trades=1,
        timeframe="1d",
    )
    results = evaluate_spec(spec)
    assert len(results) == 1
    assert results[0].metrics["data_file_timeframe"] == "1d"


def test_mixed_files_each_spec_picks_matching(tmp_path):
    _write_candles(tmp_path / "ABC_USDT_SWAP_180d.json", DAY_MS, 180)
    _write_candles(tmp_path / "ABC_USDT_SWAP_300bars_15m.json", MIN15_MS, 300)
    spec_1d = ExperimentSpec(
        experiment_id="tf_1d",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 5, "threshold_pct": 0, "hold_bars": 2}]},
        min_trades=1,
        timeframe="1d",
    )
    spec_15m = ExperimentSpec(
        experiment_id="tf_15m",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 5, "threshold_pct": 0, "hold_bars": 2}]},
        min_trades=1,
        timeframe="15m",
    )
    r1d = evaluate_spec(spec_1d)
    r15m = evaluate_spec(spec_15m)
    assert len(r1d) == 1
    assert len(r15m) == 1
    assert "180d" in r1d[0].metrics["data_file_label"]
    assert "15m" in r15m[0].metrics["data_file_label"]
    assert r1d[0].metrics["data_file_timeframe"] == "1d"
    assert r15m[0].metrics["data_file_timeframe"] == "15m"


def test_legacy_spec_without_timeframe_picks_largest(tmp_path):
    small = tmp_path / "ABC_USDT_SWAP_10d.json"
    big = tmp_path / "ABC_USDT_SWAP_80d.json"
    _write_candles(small, DAY_MS, 10)
    _write_candles(big, DAY_MS, 80)
    chosen = choose_symbol_file(str(tmp_path / "{symbol}_*.json"), "ABC_USDT_SWAP")
    assert chosen == big
    spec = ExperimentSpec(
        experiment_id="tf_legacy",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 5, "threshold_pct": 0, "hold_bars": 2}]},
        min_trades=1,
    )
    results = evaluate_spec(spec)
    assert len(results) == 1
    assert results[0].metrics["data_file_timeframe"] == "1d"


def test_choose_symbol_file_returns_none_when_no_timeframe_match(tmp_path):
    _write_candles(tmp_path / "ABC_USDT_SWAP_80d.json", DAY_MS, 80)
    result = choose_symbol_file(str(tmp_path / "{symbol}_*.json"), "ABC_USDT_SWAP", timeframe="15m")
    assert result is None


def test_experiment_spec_from_json_loads_timeframe(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({
        "experiment_id": "x",
        "data_glob": "data/{symbol}.json",
        "symbols": ["BTC_USDT_SWAP"],
        "families": ["momentum_breakout"],
        "parameter_grid": {"momentum_breakout": [{"lookback": 3}]},
        "timeframe": "15m",
    }), encoding="utf-8")
    spec = ExperimentSpec.from_json(spec_path)
    assert spec.timeframe == "15m"


def test_experiment_spec_from_json_defaults_timeframe(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({
        "experiment_id": "x",
        "data_glob": "data/{symbol}.json",
        "symbols": ["BTC_USDT_SWAP"],
        "families": ["momentum_breakout"],
        "parameter_grid": {"momentum_breakout": [{"lookback": 3}]},
    }), encoding="utf-8")
    spec = ExperimentSpec.from_json(spec_path)
    assert spec.timeframe == "1d"


def test_experiment_spec_json_roundtrip_preserves_timeframe(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({
        "experiment_id": "x",
        "data_glob": "data/{symbol}.json",
        "symbols": ["BTC_USDT_SWAP"],
        "families": ["momentum_breakout"],
        "parameter_grid": {"momentum_breakout": [{"lookback": 3}]},
        "timeframe": "4h",
    }), encoding="utf-8")
    spec = ExperimentSpec.from_json(spec_path)
    assert spec.timeframe == "4h"
    out_path = tmp_path / "spec_out.json"
    out_path.write_text(json.dumps({
        "experiment_id": spec.experiment_id,
        "data_glob": spec.data_glob,
        "symbols": spec.symbols,
        "families": spec.families,
        "parameter_grid": spec.parameter_grid,
        "timeframe": spec.timeframe,
    }), encoding="utf-8")
    spec2 = ExperimentSpec.from_json(out_path)
    assert spec2.timeframe == "4h"


def test_queue_and_event_spec_serializers_preserve_timeframe():
    spec = ExperimentSpec(
        experiment_id="tf_serialize",
        data_glob="data/{symbol}.json",
        symbols=["BTC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 3}]},
        timeframe="15m",
        backend="auto",
    )
    assert queued_exp_to_dict(spec)["timeframe"] == "15m"
    assert event_exp_to_dict(spec)["timeframe"] == "15m"
    assert queued_exp_to_dict(spec)["backend"] == "auto"
    assert event_exp_to_dict(spec)["backend"] == "auto"
