# -*- coding: utf-8 -*-
"""Farm cockpit snapshot: works on empty + populated DBs, no secrets/absolute paths."""

from __future__ import annotations

import json
from pathlib import Path

from src.research_lab.farm_cockpit import build_cockpit
from src.research_lab.instrument_discovery import build_snapshot, save_snapshot
from src.research_lab.state_db import connect, default_db_path, import_run_dir, init_db

SECTIONS = ("loop_state", "data_readiness", "gpu_cpu", "results", "universe_coverage", "safety")


def test_cockpit_on_empty_root_does_not_crash(tmp_path):
    cockpit = build_cockpit(tmp_path)
    assert cockpit["schema"] == "strategy_lab_farm_cockpit.v1"
    for section in SECTIONS:
        assert section in cockpit
    assert cockpit["results"]["available"] is False
    assert cockpit["safety"]["read_only"] is True


def _write_run(private_root: Path) -> Path:
    run_dir = private_root / "experiments" / "completed" / "20260101_000000_000000_plan_cp"
    run_dir.mkdir(parents=True)
    metrics = {
        "experiment_id": "plan_cp", "created_at": "2026-01-01T00:00:00+00:00", "timeframe": "1h",
        "runtime": {"effective_backend": "gpu", "signal_backend": "gpu", "simulation_backend": "gpu",
                    "gpu_available": True},
        "results": [{
            "run_id": "cp1", "symbol": "BTC_USDT_SWAP", "family": "range_volume_breakout", "params": {},
            "decision": "OBSERVE", "reasons": [], "validation_status": "OBSERVE", "validation_reasons": [],
            "risk_flags": [], "next_action": "x", "regime_summary": {},
            "metrics": {"n_trades": 30, "min_trades": 20, "win_rate": 0.5, "avg_net_pct": 0.1,
                        "test_avg_net_pct": 0.05, "profit_factor": 1.3, "max_drawdown_pct": 4.2,
                        "data_file_label": "BTC.json", "data_file_timeframe": "1h"}}]}
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    return run_dir


def test_cockpit_with_results_and_discovery(tmp_path):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    import_run_dir(conn, tmp_path, _write_run(tmp_path))
    conn.commit()
    conn.close()
    raw = [{"instType": "SWAP", "instId": "BTC-USDT-SWAP", "settleCcy": "USDT", "state": "live"},
           {"instType": "SWAP", "instId": "FOO-USDT-SWAP", "settleCcy": "USDT", "state": "live"}]
    save_snapshot(tmp_path, build_snapshot(raw, generated_at="2026-01-01T00:00:00+00:00"))

    cockpit = build_cockpit(tmp_path)
    assert cockpit["results"]["available"] is True
    assert cockpit["results"]["decisions"].get("OBSERVE") == 1
    assert cockpit["results"]["unique_symbols"] == 1
    assert cockpit["gpu_cpu"]["available"] is True
    assert cockpit["gpu_cpu"]["gpu_signal_rows"] == 1     # range_volume_breakout has a GPU kernel
    cov = cockpit["universe_coverage"]
    assert cov["discovered"]["count"] == 2
    assert cov["symbols_processed"] == 1
    assert cov["discovered_not_yet_processed"] == 1


def test_cockpit_data_readiness_counts_prepared_files(tmp_path):
    d = tmp_path / "market_data" / "1h"
    d.mkdir(parents=True)
    (d / "BTC_USDT_SWAP_x_1h.json").write_text("[]", encoding="utf-8")
    cockpit = build_cockpit(tmp_path)
    assert cockpit["data_readiness"]["prepared_files_by_timeframe"]["1h"] == 1


def test_cockpit_has_no_absolute_paths_or_secrets(tmp_path):
    _write_run(tmp_path)
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    conn.close()
    blob = json.dumps(build_cockpit(tmp_path))
    # the private root path must not leak into the cockpit payload
    assert str(tmp_path) not in blob
    assert "TRADING_BOT_RESEARCH_ROOT" not in blob
