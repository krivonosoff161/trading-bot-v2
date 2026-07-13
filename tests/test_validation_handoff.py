# -*- coding: utf-8 -*-
"""Schema v4 columns + migration, v4 population, and hard-validation handoff readback."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.research_lab.state_db import connect, default_db_path, import_run_dir, init_db
from src.research_lab.validation_handoff import refresh_from_artifacts, validation_state

V3_FARM_RESULTS = """
CREATE TABLE farm_results (
    run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, symbol TEXT NOT NULL,
    asset_group TEXT DEFAULT '', timeframe TEXT DEFAULT '', family TEXT NOT NULL,
    decision TEXT NOT NULL, validation_status TEXT DEFAULT '', backend TEXT DEFAULT '',
    n_trades INTEGER DEFAULT 0, win_rate REAL DEFAULT 0, avg_net_pct REAL DEFAULT 0,
    test_avg_net_pct REAL DEFAULT 0, profit_factor REAL DEFAULT 0, data_file TEXT DEFAULT '',
    data_quality TEXT DEFAULT '', next_action TEXT DEFAULT '', created_at TEXT DEFAULT '',
    PRIMARY KEY (run_id, candidate_id)
);
"""


def _cols(conn, table) -> set[str]:
    return {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})")}


def test_schema_v4_migration_adds_columns(tmp_path):
    db = default_db_path(tmp_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(db))
    raw.executescript(
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE runs(run_id TEXT PRIMARY KEY, experiment_id TEXT, created_at TEXT, "
        "artifact_label TEXT, candidate_count INTEGER DEFAULT 0, promote_count INTEGER DEFAULT 0, "
        "observe_count INTEGER DEFAULT 0, reject_count INTEGER DEFAULT 0, imported_at TEXT);"
        + V3_FARM_RESULTS + "INSERT INTO meta VALUES('schema_version','3');")
    raw.commit()
    raw.close()
    conn = connect(db)
    init_db(conn)  # migrate v3 -> v4
    cols = _cols(conn, "farm_results")
    assert {
        "max_drawdown_pct", "gpu_signal_supported", "hard_status",
        "validation_exported", "paper_status",
    } <= cols
    assert "paper_outcomes" in {
        str(r["name"]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == 5
    conn.close()


def _write_run(private_root: Path, family: str) -> Path:
    run_dir = private_root / "experiments" / "completed" / "20260101_000000_000000_plan_v4"
    run_dir.mkdir(parents=True)
    metrics = {
        "experiment_id": "plan_v4", "created_at": "2026-01-01T00:00:00+00:00", "timeframe": "1h",
        "runtime": {"effective_backend": "gpu", "signal_backend": "gpu"},
        "results": [{
            "run_id": "cand_v4", "symbol": "BTC_USDT_SWAP", "family": family, "params": {},
            "decision": "OBSERVE", "reasons": [], "validation_status": "REGIME_SPECIFIC",
            "validation_reasons": [], "risk_flags": [], "next_action": "x", "regime_summary": {},
            "metrics": {"n_trades": 30, "min_trades": 20, "win_rate": 0.5, "avg_net_pct": 0.1,
                        "test_avg_net_pct": 0.05, "profit_factor": 1.3, "max_drawdown_pct": 4.2,
                        "data_file_label": "BTC.json", "data_file_timeframe": "1h"}}]}
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    return run_dir


def test_import_populates_v4_columns(tmp_path):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    import_run_dir(conn, tmp_path, _write_run(tmp_path, "range_volume_breakout"))
    conn.commit()
    row = dict(conn.execute("SELECT * FROM farm_results").fetchone())
    assert row["max_drawdown_pct"] == 4.2
    assert row["gpu_signal_supported"] == 1   # range_volume_breakout has a GPU kernel
    conn.close()


def test_import_marks_non_gpu_family(tmp_path):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    import_run_dir(conn, tmp_path, _write_run(tmp_path, "vwap_reclaim_reject"))
    conn.commit()
    row = dict(conn.execute("SELECT * FROM farm_results").fetchone())
    assert row["gpu_signal_supported"] == 0   # vwap has no GPU signal kernel
    conn.close()


def test_refresh_handoff_marks_exported_and_verdict(tmp_path):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    conn.execute("INSERT INTO runs(run_id, experiment_id, created_at, artifact_label, imported_at) "
                 "VALUES('r','e','t','l','t')")
    conn.execute("INSERT INTO farm_results(run_id, candidate_id, symbol, family, decision) "
                 "VALUES('r','cand_1','BTC_USDT_SWAP','main_fast_swing_regime','FORWARD_PAPER')")
    conn.commit()
    base = tmp_path / "hard_validation"
    (base / "requests").mkdir(parents=True)
    (base / "verdicts").mkdir(parents=True)
    (base / "requests" / "cand_1.json").write_text("{}", encoding="utf-8")
    (base / "verdicts" / "cand_1.json").write_text(
        json.dumps({"candidate_id": "cand_1", "hard_status": "PAPER_FORWARD_READY"}), encoding="utf-8")
    result = refresh_from_artifacts(conn, tmp_path)
    assert result["rows_marked_exported"] == 1
    assert result["rows_stamped_verdict"] == 1
    row = dict(conn.execute("SELECT validation_exported, hard_status FROM farm_results").fetchone())
    assert row["validation_exported"] == 1
    assert row["hard_status"] == "PAPER_FORWARD_READY"
    conn.close()


def test_refresh_handoff_can_limit_writer_work_to_current_batch(tmp_path):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    conn.execute("INSERT INTO runs(run_id, experiment_id, created_at, artifact_label, imported_at) "
                 "VALUES('r','e','t','l','t')")
    for candidate_id in ("current", "historical"):
        conn.execute(
            "INSERT INTO farm_results(run_id, candidate_id, symbol, family, decision, hard_status) "
            "VALUES('r',?,'BTC_USDT_SWAP','range_breakout','OBSERVE',?)",
            (candidate_id, "OLD" if candidate_id == "historical" else ""),
        )
    conn.commit()
    base = tmp_path / "hard_validation"
    (base / "requests").mkdir(parents=True)
    (base / "verdicts").mkdir(parents=True)
    for candidate_id, status in (("current", "HARD_REJECT"), ("historical", "FAILED_COSTS")):
        (base / "requests" / f"{candidate_id}.json").write_text("{}", encoding="utf-8")
        (base / "verdicts" / f"{candidate_id}.json").write_text(
            json.dumps({"candidate_id": candidate_id, "hard_status": status}), encoding="utf-8",
        )

    result = refresh_from_artifacts(conn, tmp_path, candidate_ids=["current"])

    assert result["request_files"] == 1
    assert result["verdict_files"] == 1
    current = conn.execute(
        "SELECT validation_exported, hard_status FROM farm_results WHERE candidate_id='current'"
    ).fetchone()
    historical = conn.execute(
        "SELECT validation_exported, hard_status FROM farm_results WHERE candidate_id='historical'"
    ).fetchone()
    assert tuple(current) == (1, "HARD_REJECT")
    assert tuple(historical) == (0, "OLD")
    conn.close()


def test_validation_state_mapping():
    assert validation_state("REGIME_SPECIFIC", "PAPER_FORWARD_READY", True) == "VALIDATION_PASSED"
    assert validation_state("OBSERVE", "FAILED_COSTS", True) == "VALIDATION_FAILED"
    assert validation_state("OBSERVE", "NEEDS_MORE_DATA", True) == "NEEDS_MORE_DATA"
    assert validation_state("OBSERVE", "", True) == "VALIDATION_EXPORTED"
    assert validation_state("OBSERVE", "", False) == "OBSERVE"
