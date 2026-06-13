# -*- coding: utf-8 -*-

import json
from pathlib import Path

from src.research_lab import ExperimentSpec, evaluate_spec, write_run_outputs


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
    assert (out_dir / "llm_review_prompt.md").exists()
    assert "Strategy Lab Run" in (out_dir / "summary.md").read_text(encoding="utf-8")
    vault = tmp_path / "private" / "obsidian-vault"
    assert (vault / "Runs").exists()
    assert list((vault / "Candidates").glob("*.md"))
    assert "[[Symbols/ABC_USDT_SWAP]]" in next((vault / "Candidates").glob("*.md")).read_text(encoding="utf-8")


def test_run_outputs_include_validation_and_registry(tmp_path):
    data = tmp_path / "ABC_USDT_SWAP_80d.json"
    _write_candles(data)
    spec = ExperimentSpec(
        experiment_id="unit_validation",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={
            "momentum_breakout": [
                {"lookback": 5, "threshold_pct": 0, "hold_bars": 2, "stop_pct": 5, "take_pct": 10}
            ],
        },
        min_trades=1,
    )

    results = evaluate_spec(spec)
    out_dir = write_run_outputs(spec, results, tmp_path / "private")

    assert results[0].validation_status in {"REJECT", "OBSERVE", "REGIME_SPECIFIC", "FORWARD_PAPER"}
    assert results[0].next_action
    assert "regime_breakdown" in results[0].metrics
    csv_text = (out_dir / "candidates.csv").read_text(encoding="utf-8")
    assert "validation_status" in csv_text
    registry_file = tmp_path / "private" / "candidate-registry" / "candidates.jsonl"
    assert registry_file.exists()
    entry = json.loads(registry_file.read_text(encoding="utf-8").splitlines()[0])
    assert entry["experiment_id"] == "unit_validation"
    assert entry["validation_status"] == results[0].validation_status
    pack = json.loads((out_dir / "llm_review_pack.json").read_text(encoding="utf-8"))
    assert pack["schema"] == "strategy_lab_llm_review_pack.v1"
    assert "validation_counts" in pack


def test_reject_excluded_from_registry_unless_debug(tmp_path):
    data = tmp_path / "ABC_USDT_SWAP_80d.json"
    _write_candles(data)
    spec = ExperimentSpec(
        experiment_id="unit_reject_filter",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={
            "momentum_breakout": [
                {"lookback": 5, "threshold_pct": 0, "hold_bars": 2, "stop_pct": 5, "take_pct": 10}
            ],
        },
        min_trades=1,
        filters={"volatility": ["no_such_bucket"]},  # eliminates all signals -> REJECT
    )
    results = evaluate_spec(spec)
    assert results[0].validation_status == "REJECT"

    registry_file = tmp_path / "private" / "candidate-registry" / "candidates.jsonl"

    write_run_outputs(spec, results, tmp_path / "private")
    assert not registry_file.exists()  # REJECT not registered by default

    write_run_outputs(spec, results, tmp_path / "private", include_rejects=True)
    assert registry_file.exists()
    entry = json.loads(registry_file.read_text(encoding="utf-8").splitlines()[0])
    assert entry["validation_status"] == "REJECT"


def test_run_outputs_do_not_reuse_same_run_dir(tmp_path):
    data = tmp_path / "ABC_USDT_SWAP_80d.json"
    _write_candles(data)
    spec = ExperimentSpec(
        experiment_id="unit_unique",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 5, "hold_bars": 2}]},
        min_trades=1,
    )
    results = evaluate_spec(spec)

    first = write_run_outputs(spec, results, tmp_path / "private")
    second = write_run_outputs(spec, results, tmp_path / "private")

    assert first != second
    assert first.exists()
    assert second.exists()


def test_regime_filters_reduce_or_keep_signal_count(tmp_path):
    data = tmp_path / "ABC_USDT_SWAP_80d.json"
    _write_candles(data)
    base = dict(
        experiment_id="unit_filters",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={
            "momentum_breakout": [
                {"lookback": 5, "threshold_pct": 0, "hold_bars": 2, "stop_pct": 5, "take_pct": 10}
            ],
        },
        min_trades=1,
    )

    unfiltered = evaluate_spec(ExperimentSpec(**base))
    filtered = evaluate_spec(ExperimentSpec(**base, filters={"trend": ["down"]}))

    assert filtered[0].metrics["n_trades"] <= unfiltered[0].metrics["n_trades"]


def test_filters_eliminating_all_signals_yield_empty_metrics(tmp_path):
    data = tmp_path / "ABC_USDT_SWAP_80d.json"
    _write_candles(data)
    spec = ExperimentSpec(
        experiment_id="unit_empty",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={
            "momentum_breakout": [
                {"lookback": 5, "threshold_pct": 0, "hold_bars": 2, "stop_pct": 5, "take_pct": 10}
            ],
        },
        min_trades=1,
        filters={"volatility": ["no_such_bucket"]},
    )

    results = evaluate_spec(spec)

    assert results[0].metrics["n_trades"] == 0
    assert results[0].metrics["win_rate"] == 0.0
    assert results[0].decision == "REJECT"
    assert results[0].validation_status == "REJECT"


def test_max_runs_caps_results(tmp_path):
    data = tmp_path / "ABC_USDT_SWAP_80d.json"
    _write_candles(data)
    spec = ExperimentSpec(
        experiment_id="unit_cap",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={
            "momentum_breakout": [
                {"lookback": 5, "hold_bars": 2},
                {"lookback": 10, "hold_bars": 2},
                {"lookback": 15, "hold_bars": 2},
            ],
        },
        min_trades=1,
        max_runs=2,
    )

    assert len(evaluate_spec(spec)) == 2


def test_missing_symbol_file_is_skipped(tmp_path):
    data = tmp_path / "ABC_USDT_SWAP_80d.json"
    _write_candles(data)
    spec = ExperimentSpec(
        experiment_id="unit_missing",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["ABC_USDT_SWAP", "GHOST_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 5, "hold_bars": 2}]},
        min_trades=1,
    )

    results = evaluate_spec(spec)

    assert len(results) == 1
    assert results[0].symbol == "ABC_USDT_SWAP"


def test_choose_symbol_file_prefers_largest(tmp_path):
    from src.research_lab.experiment import choose_symbol_file

    small = tmp_path / "ABC_USDT_SWAP_10d.json"
    big = tmp_path / "ABC_USDT_SWAP_80d.json"
    small.write_text(json.dumps([{"ts": 1, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1}]), encoding="utf-8")
    _write_candles(big)

    chosen = choose_symbol_file(str(tmp_path / "{symbol}_*.json"), "ABC_USDT_SWAP")

    assert chosen == big


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
                "filters": {"volatility": ["medium", "high"]},
            }
        ),
        encoding="utf-8",
    )

    spec = ExperimentSpec.from_json(spec_path)

    assert spec.experiment_id == "x"
    assert spec.parameter_grid["momentum_breakout"][0]["lookback"] == 3
    assert spec.filters == {"volatility": ["medium", "high"]}
