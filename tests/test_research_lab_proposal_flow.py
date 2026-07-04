# -*- coding: utf-8 -*-

import json
import sys

import scripts.strategy_lab.import_llm_proposals as importer
import scripts.strategy_lab.queue_validated_proposals as queuer
from src.research_lab.proposal_schema import QUEUED, REJECTED, VALIDATED, coerce_proposal
from src.research_lab.proposal_store import load_proposals, proposals_path, upsert_proposals


def _run(module, argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", argv)
    module.main()


def test_import_llm_proposals_handles_valid_and_invalid(tmp_path, monkeypatch):
    payload = [
        {  # valid
            "created_by": "llm_review",
            "hypothesis": "probe neighbors for stability",
            "requested_timeframe": "15m",
            "setup_family": "momentum_breakout",
            "symbols": ["SOL_USDT_SWAP"],
            "parameter_grid": {"momentum_breakout": [{"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}]},
            "max_variants": 6,
        },
        {"created_by": "llm_review", "hypothesis": "no family here"},  # invalid: missing setup_family
        {  # rejected by validator: unsafe wording
            "created_by": "llm_review",
            "hypothesis": "guaranteed live-tradable profit",
            "requested_timeframe": "15m",
            "setup_family": "momentum_breakout",
            "symbols": ["SOL_USDT_SWAP"],
            "parameter_grid": {"momentum_breakout": [{"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}]},
        },
    ]
    f = tmp_path / "llm_out.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    _run(importer, ["import_llm_proposals", "--file", str(f), "--apply", "--private-root", str(tmp_path)], monkeypatch)

    stored = load_proposals(proposals_path(tmp_path))
    statuses = {p.status for p in stored}
    assert len(stored) == 3
    assert VALIDATED in statuses
    assert REJECTED in statuses


def test_import_bad_json_does_not_crash_store(tmp_path, monkeypatch, capsys):
    f = tmp_path / "broken.json"
    f.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["import_llm_proposals", "--file", str(f), "--apply", "--private-root", str(tmp_path)])
    try:
        importer.main()
    except SystemExit as exc:
        assert exc.code == 2
    assert not proposals_path(tmp_path).exists()


def _validated(pid):
    return coerce_proposal({
        "proposal_id": pid,
        "created_by": "rule_based",
        "hypothesis": "probe neighbors",
        "requested_timeframe": "15m",
        "setup_family": "momentum_breakout",
        "symbols": ["SOL_USDT_SWAP"],
        "parameter_grid": {"momentum_breakout": [{"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}]},
        "status": VALIDATED,
        "created_at": "2026-06-13T00:00:00+00:00",
    })


def test_queue_validated_proposals_idempotent(tmp_path, monkeypatch, capsys):
    upsert_proposals(proposals_path(tmp_path), [_validated("p1")])
    _run(queuer, ["queue_validated_proposals", "--apply", "--private-root", str(tmp_path)], monkeypatch)
    out1 = capsys.readouterr().out
    assert "queued=1" in out1
    # after queueing, proposal flips to QUEUED -> nothing left to queue
    assert load_proposals(proposals_path(tmp_path))[0].status == QUEUED
    _run(queuer, ["queue_validated_proposals", "--apply", "--private-root", str(tmp_path)], monkeypatch)
    out2 = capsys.readouterr().out
    assert "ready=0" in out2
