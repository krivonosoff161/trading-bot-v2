# -*- coding: utf-8 -*-

import json
from argparse import Namespace

from scripts.strategy_lab.research_session import run_session
from src.research_lab.candidate_registry import registry_path
from src.research_lab.paths import market_data_dir, one_minute_data_dir
from src.research_lab.research_session import session_report_path, write_session_report

DAY = 86_400_000


def _args(tmp_path, **over):
    base = dict(apply=False, max_candidates=8, max_queued=None, max_worker_jobs=1,
                prepare_1m=False, prepare_1m_apply=False, provider="null",
                llm_export=False, llm_send=False, session_budget_hours=None,
                no_worker=False, priority=72, private_root=str(tmp_path), allow_public_output=False)
    base.update(over)
    return Namespace(**base)


def _seed_candidates(tmp_path, n=3):
    path = registry_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    symbols = ["BTC_USDT_SWAP", "ETH_USDT_SWAP", "SOL_USDT_SWAP"]
    rows = [{
        "candidate_id": f"c{i}", "symbol": symbols[i % len(symbols)],
        "strategy_id": "momentum_breakout",
        "params": {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        "validation_status": "FORWARD_PAPER",
        "metrics_summary": {"n_trades": 25, "avg_net_pct": 1.0, "test_avg_net_pct": 0.8,
                            "profit_factor": 1.3, "entry_timing": {"avg_capture_ratio": 0.4}},
        "risk_flags": [], "next_action": "keep", "artifact_label": "experiments/completed/run_x",
        "next_review": "2026-07-01",
    } for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    for symbol in symbols:
        _write_daily(market_data_dir(tmp_path, "1d") / f"{symbol}_120d_1d.json", n=120)


def _write_daily(path, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{
        "ts": 1_700_000_000_000 + i * DAY,
        "date": f"d{i}",
        "open": 100.0 + i,
        "high": 101.0 + i,
        "low": 99.0 + i,
        "close": 100.5 + i,
        "vol": 1000.0 + i,
    } for i in range(n)]
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_session_dry_run_writes_only_report(tmp_path):
    _seed_candidates(tmp_path)
    report = run_session(_args(tmp_path, apply=False))
    write_session_report(tmp_path, report)
    assert report.mode == "dry_run"
    assert not (tmp_path / "proposals" / "queued_specs").exists()
    assert not one_minute_data_dir(tmp_path).exists()
    assert session_report_path(tmp_path).exists()


def test_session_report_schema(tmp_path):
    report = run_session(_args(tmp_path, apply=False))
    d = report.to_dict()
    assert d["schema"] == "strategy_lab_research_session.v1"
    assert "cycle" in d and "llm" in d and "readiness" in d
    assert d["llm"]["mode"] == "disabled" and d["llm"]["enabled"] is False


def test_session_apply_queues_ready_jobs(tmp_path):
    _seed_candidates(tmp_path)
    report = run_session(_args(tmp_path, apply=True, no_worker=True, max_queued=5))
    # majors are in the archive -> READY -> queued
    assert report.cycle["proposals_queued"] >= 1
    assert report.readiness["ready_jobs"] >= 1


def test_session_llm_export_dry_run_writes_no_pack(tmp_path):
    _seed_candidates(tmp_path)
    report = run_session(_args(tmp_path, apply=False, llm_export=True))
    assert "dry-run" in report.llm["pack_label"]
    assert not (tmp_path / "reports" / "llm_review").exists()  # no pack written in dry-run


def test_session_llm_send_blocked_by_gates(tmp_path):
    report = run_session(_args(tmp_path, apply=True, no_worker=True, llm_send=True))
    assert report.llm["sent"] is False  # no provider configured -> never sends


def test_session_no_absolute_paths(tmp_path):
    report = run_session(_args(tmp_path, apply=False))
    assert str(tmp_path) not in json.dumps(report.to_dict())


def test_dashboard_includes_session_and_llm_loop(tmp_path, monkeypatch):
    from src.research_lab.dashboard_state import load_dashboard_state
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    report = run_session(_args(tmp_path, apply=False))
    write_session_report(tmp_path, report)
    state = load_dashboard_state(tmp_path)
    assert state["last_session"]["available"] is True
    assert state["llm_loop"]["mode"] == "disabled"
    assert str(tmp_path) not in json.dumps(state)
