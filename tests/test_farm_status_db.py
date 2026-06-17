# -*- coding: utf-8 -*-
"""Schema v3 (farm_results / runtime_stats) migration, import hook, and status report."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.strategy_lab.farm_status_report import collect
from src.research_lab.state_db import connect, default_db_path, import_run_dir, init_db


def _table_names(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_v3_tables_created_and_idempotent(tmp_path):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    init_db(conn)  # idempotent
    names = _table_names(conn)
    assert {"runs", "candidates", "queue", "farm_results", "runtime_stats"} <= names
    assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == 3
    conn.close()


def _write_run(private_root: Path) -> Path:
    run_dir = private_root / "experiments" / "completed" / "20260101_000000_000000_plan_test"
    run_dir.mkdir(parents=True)
    metrics = {
        "experiment_id": "plan_test",
        "created_at": "2026-01-01T00:00:00+00:00",
        "timeframe": "1h",
        "requested_backend": "auto",
        "runtime": {
            "requested_backend": "auto", "effective_backend": "cpu", "signal_backend": "cpu",
            "simulation_backend": "cpu", "gpu_available": False, "fallback_reason": "no_gpu_backend",
            "accelerated_runs": 0, "elapsed_ms": 12.3,
        },
        "results": [
            {"run_id": "abc", "symbol": "BTC_USDT_SWAP", "family": "main_fast_swing_regime",
             "params": {}, "decision": "PROMOTE_FOR_PRESSURE_TEST", "reasons": ["passed_basic_gates"],
             "validation_status": "FORWARD_PAPER", "validation_reasons": [], "risk_flags": [],
             "next_action": "track paper-forward only", "regime_summary": {},
             "metrics": {"n_trades": 25, "min_trades": 20, "win_rate": 0.6, "avg_net_pct": 0.3,
                         "test_avg_net_pct": 0.2, "profit_factor": 1.4,
                         "data_file_label": "BTC_USDT_SWAP_1h.json", "data_file_timeframe": "1h"}},
            {"run_id": "def", "symbol": "DOGE_USDT_SWAP", "family": "pump_dump_scalp",
             "params": {}, "decision": "REJECT", "reasons": ["too_few_trades"],
             "validation_status": "REJECT", "validation_reasons": [], "risk_flags": [],
             "next_action": "archive", "regime_summary": {},
             "metrics": {"n_trades": 0, "min_trades": 20, "win_rate": 0.0, "avg_net_pct": 0.0,
                         "test_avg_net_pct": 0.0, "profit_factor": 0.0,
                         "data_file_label": "DOGE_USDT_SWAP_1h.json", "data_file_timeframe": "1h"}},
        ],
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    return run_dir


def test_import_populates_farm_results_and_runtime(tmp_path):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    run_dir = _write_run(tmp_path)
    import_run_dir(conn, tmp_path, run_dir)
    conn.commit()

    fr = {r["symbol"]: dict(r) for r in conn.execute("SELECT * FROM farm_results")}
    assert len(fr) == 2
    btc = fr["BTC_USDT_SWAP"]
    assert btc["timeframe"] == "1h"
    assert btc["backend"] == "cpu"
    assert btc["data_quality"] == "ok"          # 25 >= 20 min_trades
    assert btc["validation_status"] == "FORWARD_PAPER"
    assert btc["asset_group"] in ("core_market", "btc_eth_tactical")  # BTC is in both
    assert fr["DOGE_USDT_SWAP"]["data_quality"] == "no_trades"
    assert fr["DOGE_USDT_SWAP"]["asset_group"] == "meme_flow"

    rt = dict(conn.execute("SELECT * FROM runtime_stats").fetchone())
    assert rt["effective_backend"] == "cpu"
    assert rt["gpu_available"] == 0
    assert rt["fallback_reason"] == "no_gpu_backend"
    assert rt["n_results"] == 2
    conn.close()


def test_status_report_collect(tmp_path):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    import_run_dir(conn, tmp_path, _write_run(tmp_path))
    conn.commit()
    conn.close()

    report = collect(default_db_path(tmp_path))
    assert report["exists"] is True
    assert report["totals"]["farm_results"] == 2
    assert "FORWARD_PAPER" in report["validation"]
    assert report["by_timeframe"].get("1h") == 2
    ready = {r["symbol"] for r in report["ready_for_validation"]}
    assert "BTC_USDT_SWAP" in ready


def test_status_report_missing_db(tmp_path):
    report = collect(default_db_path(tmp_path / "nope"))
    assert report["exists"] is False
