# -*- coding: utf-8 -*-

import sys

import scripts.strategy_lab.status as status


def test_status_runs_on_empty_root(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    monkeypatch.setattr(sys, "argv", ["status", "--private-root", str(tmp_path)])
    status.main()
    out = capsys.readouterr().out
    assert "Strategy Lab status" in out
    assert "Worker" in out
    assert "Queue" in out
    assert "pending:" in out  # queue state is explicit, not just completed counts
    assert "Proposals" in out
    assert "registry rows" in out
    assert "unique" in out
    assert "auto-send disabled" in out
    assert "Proposal apply: manual only" in out
    assert "no live trading" in out
    # no absolute private path leaked into operator output
    assert str(tmp_path) not in out


def test_status_reports_proposal_counts(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    from src.research_lab.proposal_schema import VALIDATED, coerce_proposal
    from src.research_lab.proposal_store import proposals_path, upsert_proposals
    p = coerce_proposal({
        "proposal_id": "p1", "created_by": "rule_based", "hypothesis": "h",
        "requested_timeframe": "15m", "setup_family": "momentum_breakout",
        "symbols": ["SOL_USDT_SWAP"], "parameter_grid": {"momentum_breakout": [{"lookback": 20}]},
        "status": VALIDATED, "created_at": "2026-06-13T00:00:00+00:00",
    })
    upsert_proposals(proposals_path(tmp_path), [p])

    monkeypatch.setattr(sys, "argv", ["status", "--private-root", str(tmp_path)])
    status.main()
    out = capsys.readouterr().out
    assert "validated waiting for queue: 1" in out


def test_status_shows_microscope_and_send_gate(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    monkeypatch.delenv("STRATEGY_LAB_LLM_ENABLED", raising=False)
    monkeypatch.delenv("STRATEGY_LAB_LLM_DAILY_CAP", raising=False)
    monkeypatch.setattr(sys, "argv", ["status", "--private-root", str(tmp_path)])
    status.main()
    out = capsys.readouterr().out
    assert "Microscope" in out
    assert "trigger-only" in out
    assert "send gate" in out
    assert "1m data prep" in out
    assert str(tmp_path) not in out
