# -*- coding: utf-8 -*-
"""Derived setup lifecycle and positive/negative grouping."""

import json
from pathlib import Path

from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
from src.research_lab.hard_validation_export import validation_id_for_unique_candidate
from src.research_lab.setup_lifecycle import derive_setup_lifecycle, summarize_setup_lifecycle


def _unique(tasks: FarmTasksDB, uc_key: str, *, hard_status: str = "", candidate_id: str = "c1") -> str:
    tasks.upsert_unique_candidate({
        "uc_key": uc_key,
        "symbol": "BTC",
        "timeframe": "1h",
        "family": "momentum_breakout",
        "params_hash": "ph",
        "data_fingerprint": "fp",
        "decision": "OBSERVE",
        "validation_status": "FORWARD_PAPER",
        "hard_status": hard_status,
        "candidate_id": candidate_id,
        "params": {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
    }, now=1.0)
    return validation_id_for_unique_candidate({"uc_key": uc_key})


def _write_request(private_root: Path, validation_id: str, uc_key: str) -> None:
    req = private_root / "hard_validation" / "requests"
    req.mkdir(parents=True, exist_ok=True)
    (req / f"{validation_id}.json").write_text(json.dumps({
        "candidate_id": validation_id,
        "symbol": "BTC",
        "timeframe": "1h",
        "strategy_id": "momentum_breakout",
        "metrics": {"uc_key": uc_key},
    }), encoding="utf-8")


def _write_verdict(private_root: Path, validation_id: str, hard_status: str) -> None:
    vdir = private_root / "hard_validation" / "verdicts"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{validation_id}.json").write_text(json.dumps({
        "candidate_id": validation_id,
        "hard_status": hard_status,
    }), encoding="utf-8")


def test_hard_failed_is_not_paper_negative(tmp_path):
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    uc_key = "BTC::1h::momentum_breakout::ph::fp"
    validation_id = _unique(tasks, uc_key)
    tasks.close()
    _write_request(tmp_path, validation_id, uc_key)
    _write_verdict(tmp_path, validation_id, "FAILED_COSTS")

    rows = derive_setup_lifecycle(tmp_path)
    assert rows[0]["derived_lifecycle_state"] == "HARD_FAILED"
    summary = summarize_setup_lifecycle(tmp_path)
    assert summary["negative_setups"] == 0
    assert summary["no_paper_sample"] == 1


def test_paper_jsonl_drives_positive_negative_groups(tmp_path):
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    uc_key = "BTC::1h::momentum_breakout::ph::fp"
    validation_id = _unique(tasks, uc_key)
    tasks.close()
    _write_request(tmp_path, validation_id, uc_key)
    _write_verdict(tmp_path, validation_id, "PAPER_FORWARD_READY")
    paper = tmp_path / "paper"
    paper.mkdir(parents=True)
    (paper / "paper_trades.jsonl").write_text(json.dumps({
        "trade_id": "t1",
        "candidate_id": validation_id,
        "state": "closed_sl",
        "net_pct": -1.5,
    }) + "\n", encoding="utf-8")

    row = derive_setup_lifecycle(tmp_path)[0]
    assert row["paper_outcome_count"] == 1
    assert row["paper_net_sum"] == -1.5
    assert row["derived_lifecycle_state"] == "PAPER_NEGATIVE_OBSERVED"
    summary = summarize_setup_lifecycle(tmp_path)
    assert summary["negative_setups"] == 1
    assert summary["positive_setups"] == 0
