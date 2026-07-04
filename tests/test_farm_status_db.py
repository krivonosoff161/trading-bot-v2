# -*- coding: utf-8 -*-
"""Schema v3 (farm_results / runtime_stats) migration, import hook, and status report."""

from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path

import scripts.strategy_lab.farm_status_report as farm_status_report
from scripts.strategy_lab.farm_status_report import _print, collect
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
from src.research_lab.state_db import connect, default_db_path, import_run_dir, init_db


def _table_names(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_v3_tables_created_and_idempotent(tmp_path):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    init_db(conn)  # idempotent
    names = _table_names(conn)
    assert {"runs", "candidates", "queue", "farm_results", "runtime_stats", "paper_outcomes"} <= names
    assert int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == 5
    conn.close()


def _write_run(private_root: Path) -> Path:
    run_dir = private_root / "experiments" / "completed" / "20260101_000000_000000_plan_test"
    run_dir.mkdir(parents=True)
    metrics = {
        "experiment_id": "plan_test",
        "created_at": "2026-01-01T00:00:00+00:00",
        "timeframe": "1h",
        "requested_backend": "auto",
        "plan_meta": {"group": "core_market", "timeframe_role": "intraday"},
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
    assert btc["asset_group"] == "core_market"  # plan provenance wins over static duplicate groups
    assert fr["DOGE_USDT_SWAP"]["data_quality"] == "no_trades"
    assert fr["DOGE_USDT_SWAP"]["asset_group"] == "core_market"

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
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    (derived / "main_paper_runtime_observation.json").write_text(
        json.dumps({
            "schema": "main_paper_runtime_observation.v1",
            "rows_read": 2,
            "observed": 2,
            "reviewed": 1,
            "pending": 1,
            "invalid": 0,
            "provider_error": 0,
            "execution_allowed": False,
        }),
        encoding="utf-8",
    )
    (derived / "paper_telegram_delivery.json").write_text(
        json.dumps(
            {
                "schema": "paper_telegram_delivery.v1",
                "eligible_cards": 3,
                "targets": 4,
                "sent_messages": 0,
                "sent_cards": 0,
                "duplicate_messages": 12,
                "duplicate_cards": 3,
                "errors": 0,
                "dry_run": False,
                "sends_network": True,
            }
        ),
        encoding="utf-8",
    )

    report = collect(default_db_path(tmp_path))
    assert report["exists"] is True
    assert report["totals"]["farm_results"] == 2
    assert report["handoff"]["paper_outcomes"] == 0
    assert "FORWARD_PAPER" in report["validation"]
    assert report["by_timeframe"].get("1h") == 2
    assert report["main_paper_runtime_observation"]["observed"] == 2
    assert report["main_paper_runtime_observation"]["execution_allowed"] is False
    assert report["paper_telegram_delivery"]["eligible_cards"] == 3
    assert report["paper_telegram_delivery"]["sends_network"] is True
    ready = {r["symbol"] for r in report["ready_for_validation"]}
    assert "BTC_USDT_SWAP" in ready


def test_status_report_fast_skips_heavy_derived_rebuilds(tmp_path, monkeypatch):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    import_run_dir(conn, tmp_path, _write_run(tmp_path))
    conn.commit()
    conn.close()

    def _boom(*_args, **_kwargs):
        raise AssertionError("fast status must not rebuild heavy derived research views")

    monkeypatch.setitem(
        sys.modules,
        "src.research_lab.setup_lifecycle",
        types.SimpleNamespace(summarize_setup_lifecycle=_boom),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.research_lab.setup_outcome_memory",
        types.SimpleNamespace(
            build_memory_index=_boom,
            summarize_memory=_boom,
            build_gate_index=_boom,
            knowledge_base_counts=_boom,
        ),
    )

    report = collect(default_db_path(tmp_path), fast=True)

    assert report["report_mode"] == "fast"
    assert report["setup_lifecycle"]["skipped"] == "fast_mode"
    assert report["outcome_memory"]["skipped"] == "fast_mode"
    assert report["knowledge_base"]["skipped"] == "fast_mode"
    assert report["handoff"]["paper_outcomes"] == 0
    assert "BTC_USDT_SWAP" in {r["symbol"] for r in report["ready_for_validation"]}


def test_status_report_falls_back_to_readonly_when_db_locked(tmp_path, monkeypatch):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    import_run_dir(conn, tmp_path, _write_run(tmp_path))
    conn.commit()
    conn.close()

    def _locked(_conn):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(farm_status_report, "init_db", _locked)

    report = collect(default_db_path(tmp_path), fast=True)

    assert report["exists"] is True
    assert report["migration_note"] == "database_locked_readonly"
    assert report["totals"]["farm_results"] == 2


def test_status_report_paused_work_mentions_running_loop(tmp_path, capsys):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    conn.close()
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    try:
        tasks.enqueue_task(
            task_type="run_sweep",
            task_key="sweep:btc",
            symbol="BTC_USDT_SWAP",
            timeframe="1h",
            family="momentum_breakout",
            now=1000.0,
        )
    finally:
        tasks.close()
    status = tmp_path / "state" / "farm_loop_status.json"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(
        json.dumps(
            {
                "schema": "FarmLoopStatus.v1",
                "pid": 123,
                "stage": "sleep",
                "updated_at": 1100.0,
                "loop": True,
                "paper_only": True,
                "execution_allowed": False,
                "details": {"sleep_seconds": 120},
            }
        ),
        encoding="utf-8",
    )

    report = collect(default_db_path(tmp_path), fast=True)
    report["farm_loop_status"]["active"] = True
    report["farm_loop_status"]["age_seconds"] = 20
    report["farm_loop_status"]["fresh"] = True

    _print(report)
    out = capsys.readouterr().out
    assert "COMPLETION: PAUSED_WITH_WORK" in out
    assert "loop running stage=sleep" in out
    assert "loop stopped with claimable work" not in out


def test_status_report_prints_paper_telegram_delivery(tmp_path, capsys):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    import_run_dir(conn, tmp_path, _write_run(tmp_path))
    conn.commit()
    conn.close()
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    (derived / "paper_telegram_delivery.json").write_text(
        json.dumps(
            {
                "eligible_cards": 3,
                "targets": 4,
                "sent_messages": 0,
                "sent_cards": 0,
                "duplicate_messages": 12,
                "duplicate_cards": 3,
                "errors": 0,
                "dry_run": False,
                "sends_network": True,
            }
        ),
        encoding="utf-8",
    )

    _print(collect(default_db_path(tmp_path), fast=True))
    out = capsys.readouterr().out

    assert "paper Telegram delivery:" in out
    assert "eligible_cards=3" in out
    assert "duplicate_messages=12" in out
    assert "errors=0" in out
    assert "sends_network=True" in out


def test_status_report_missing_db(tmp_path):
    report = collect(default_db_path(tmp_path / "nope"))
    assert report["exists"] is False


def test_status_report_old_db_without_v3_does_not_crash(tmp_path):
    """A v2-shaped DB (no farm_results/runtime_stats) must be migrated, not crash."""
    db = default_db_path(tmp_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE runs(run_id TEXT PRIMARY KEY, experiment_id TEXT, created_at TEXT, artifact_label TEXT,
            candidate_count INTEGER DEFAULT 0, promote_count INTEGER DEFAULT 0, observe_count INTEGER DEFAULT 0,
            reject_count INTEGER DEFAULT 0, imported_at TEXT);
        CREATE TABLE candidates(run_id TEXT, candidate_id TEXT, symbol TEXT, family TEXT, decision TEXT,
            reasons TEXT, metrics_json TEXT, params_json TEXT, validation_status TEXT DEFAULT '',
            validation_reasons TEXT DEFAULT '', next_action TEXT DEFAULT '', PRIMARY KEY(run_id, candidate_id));
        CREATE TABLE queue(job_id INTEGER PRIMARY KEY AUTOINCREMENT, spec_path TEXT, status TEXT, priority INTEGER,
            created_at TEXT, started_at TEXT, finished_at TEXT, attempts INTEGER DEFAULT 0,
            run_dir_label TEXT, last_error TEXT);
        INSERT INTO meta VALUES('schema_version','2');
        INSERT INTO runs VALUES('r1','e1','2026-01-01T00:00:00+00:00','lbl',1,0,0,1,'2026-01-01T00:00:00+00:00');
        INSERT INTO candidates(run_id,candidate_id,symbol,family,decision,reasons,metrics_json,params_json,validation_status)
            VALUES('r1','c1','BTC_USDT_SWAP','main_fast_swing_regime','OBSERVE','','{}','{}','FORWARD_PAPER');
        """
    )
    conn.commit()
    conn.close()
    report = collect(db)  # init_db inside upgrades to current schema; must not crash
    assert report["exists"] is True
    assert report["schema_version"] == 5
    assert report["totals"]["farm_results"] == 0
    assert "BTC_USDT_SWAP" in {r["symbol"] for r in report["ready_for_validation"]}


def test_ready_for_validation_deduped(tmp_path):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    for run_id in ("run_a", "run_b"):
        conn.execute(
            "INSERT INTO runs(run_id, experiment_id, created_at, artifact_label, imported_at) VALUES(?,?,?,?,?)",
            (run_id, "e", "2026-01-01T00:00:00+00:00", "lbl", "2026-01-01T00:00:00+00:00"))
        conn.execute(
            "INSERT INTO farm_results(run_id, candidate_id, symbol, family, decision, validation_status, "
            "timeframe, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (run_id, "c", "BTC_USDT_SWAP", "main_fast_swing_regime", "PROMOTE_FOR_PRESSURE_TEST",
             "FORWARD_PAPER", "1h", "2026-01-01T00:00:00+00:00"))
    conn.commit()
    conn.close()
    report = collect(default_db_path(tmp_path))
    btc = [r for r in report["ready_for_validation"]
           if r["symbol"] == "BTC_USDT_SWAP" and r["family"] == "main_fast_swing_regime"]
    assert len(btc) == 1  # deduped by (symbol, family, timeframe) across two runs


def test_status_report_json_fields_stable(tmp_path):
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    import_run_dir(conn, tmp_path, _write_run(tmp_path))
    conn.commit()
    conn.close()
    report = collect(default_db_path(tmp_path))
    # the dashboard consumes these keys; keep them present
    for key in ("exists", "schema_version", "totals", "queue", "queue_age", "latest_run",
                "decisions", "validation", "flow_coverage", "ready_for_validation", "recent_runs",
                "handoff"):
        assert key in report, f"missing report key: {key}"
    assert "paper_outcomes" in report["handoff"]
    json.dumps(report)  # must be JSON-serializable
