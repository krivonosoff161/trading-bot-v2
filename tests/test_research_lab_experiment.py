# -*- coding: utf-8 -*-

import json
from pathlib import Path

from src.research_lab.experiment import ExperimentSpec, evaluate_spec, write_run_outputs


def _write_candles(path: Path) -> None:
    rows = []
    price = 100.0
    for i in range(80):
        if i in {20, 40, 60}:
            price *= 1.12
            vol = 5000
        else:
            price *= 1.01 if i % 3 else 0.995
            vol = 1000 + i
        rows.append(
            {
                "ts": 1_700_000_000_000 + i * 86_400_000,
                "date": f"2026-01-{(i % 28) + 1:02d}",
                "open": round(price * 0.99, 4),
                "high": round(price * 1.03, 4),
                "low": round(price * 0.97, 4),
                "close": round(price, 4),
                "vol": vol,
            }
        )
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_strategy_lab_evaluates_and_writes_private_outputs(tmp_path):
    data = tmp_path / "ABC_USDT_SWAP_80d.json"
    _write_candles(data)
    spec = ExperimentSpec(
        experiment_id="unit_smoke",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout", "volume_shock_continuation"],
        parameter_grid={
            "momentum_breakout": [
                {"lookback": 5, "threshold_pct": 0, "hold_bars": 2, "stop_pct": 5, "take_pct": 10}
            ],
            "volume_shock_continuation": [
                {"lookback": 5, "vol_mult": 2, "min_body_pct": 3, "hold_bars": 2, "stop_pct": 5, "take_pct": 10}
            ],
        },
        min_trades=1,
    )

    results = evaluate_spec(spec)
    out_dir = write_run_outputs(spec, results, tmp_path / "private")

    assert len(results) == 2
    assert (out_dir / "metrics.json").exists()
    assert (out_dir / "candidates.csv").exists()
    assert (out_dir / "graph_edges.csv").exists()
    assert (out_dir / "llm_review_pack.json").exists()
    assert "Strategy Lab Run" in (out_dir / "summary.md").read_text(encoding="utf-8")


def test_experiment_spec_loads_from_json(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "experiment_id": "x",
                "data_glob": "data/{symbol}.json",
                "symbols": ["BTC_USDT_SWAP"],
                "families": ["momentum_breakout"],
                "parameter_grid": {"momentum_breakout": [{"lookback": 3}]},
            }
        ),
        encoding="utf-8",
    )

    spec = ExperimentSpec.from_json(spec_path)

    assert spec.experiment_id == "x"
    assert spec.parameter_grid["momentum_breakout"][0]["lookback"] == 3

