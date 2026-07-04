# -*- coding: utf-8 -*-
"""Obsidian farm-memory notes: generated from DB+registry, linked, deterministic, safe."""

from __future__ import annotations

import json
from pathlib import Path

from src.research_lab.candidate_registry import registry_path
from src.research_lab.farm_obsidian import write_farm_notes
from src.research_lab.state_db import connect, default_db_path, import_run_dir, init_db

DAY = "2026-06-18"


def _write_run(private_root: Path) -> Path:
    run_dir = private_root / "experiments" / "completed" / "20260101_000000_000000_plan_obs"
    run_dir.mkdir(parents=True)
    metrics = {
        "experiment_id": "plan_obs", "created_at": "2026-01-01T00:00:00+00:00", "timeframe": "1h",
        "runtime": {"effective_backend": "gpu", "signal_backend": "gpu"},
        "results": [{
            "run_id": "obs1", "symbol": "BTC_USDT_SWAP", "family": "vwap_reclaim_reject", "params": {},
            "decision": "OBSERVE", "reasons": [], "validation_status": "OBSERVE", "validation_reasons": [],
            "risk_flags": [], "next_action": "x", "regime_summary": {},
            "metrics": {"n_trades": 30, "min_trades": 20, "win_rate": 0.5, "avg_net_pct": 0.2,
                        "test_avg_net_pct": 0.1, "profit_factor": 1.3, "max_drawdown_pct": 3.0,
                        "data_file_label": "BTC.json", "data_file_timeframe": "1h"}}]}
    (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    return run_dir


def _populate(private_root: Path) -> None:
    conn = connect(default_db_path(private_root))
    init_db(conn)
    import_run_dir(conn, private_root, _write_run(private_root))
    conn.commit()
    conn.close()
    reg = registry_path(private_root)
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({
        "candidate_id": "obs1", "symbol": "BTC_USDT_SWAP", "strategy_id": "vwap_reclaim_reject",
        "params": {"vwap_period": 20, "ema_fast": 20}, "validation_status": "OBSERVE"}) + "\n", encoding="utf-8")


def test_write_farm_notes_creates_all_note_types(tmp_path):
    _populate(tmp_path)
    res = write_farm_notes(tmp_path, day=DAY, allow_public_output=True)
    c = res["counts"]
    assert c["runs"] == 1 and c["symbols"] == 1 and c["families"] == 1 and c["candidates"] == 1
    base = tmp_path / "obsidian-vault" / "Farm"
    assert (base / "Runs" / f"farm_{DAY}.md").exists()
    assert (base / "Symbols" / "BTC_USDT_SWAP.md").exists()
    assert (base / "Families" / "vwap_reclaim_reject.md").exists()
    assert list((base / "Candidates").glob("*.md"))


def test_notes_have_graph_links_and_params(tmp_path):
    _populate(tmp_path)
    write_farm_notes(tmp_path, day=DAY, allow_public_output=True)
    base = tmp_path / "obsidian-vault" / "Farm"
    symbol_note = (base / "Symbols" / "BTC_USDT_SWAP.md").read_text(encoding="utf-8")
    assert "[[Families/vwap_reclaim_reject]]" in symbol_note
    family_note = (base / "Families" / "vwap_reclaim_reject.md").read_text(encoding="utf-8")
    assert "[[VWAP]]" in family_note          # data tag derived from the family
    assert "GPU signal kernel: no" in family_note  # vwap has no GPU kernel - honest
    cand_note = next((base / "Candidates").glob("*.md")).read_text(encoding="utf-8")
    assert "vwap_period" in cand_note          # exact params from the registry
    assert "[[Symbols/BTC_USDT_SWAP]]" in cand_note


def test_notes_deterministic_and_no_absolute_paths(tmp_path):
    _populate(tmp_path)
    write_farm_notes(tmp_path, day=DAY, allow_public_output=True)
    first = (tmp_path / "obsidian-vault" / "Farm" / "Runs" / f"farm_{DAY}.md").read_text(encoding="utf-8")
    write_farm_notes(tmp_path, day=DAY, allow_public_output=True)  # re-run
    second = (tmp_path / "obsidian-vault" / "Farm" / "Runs" / f"farm_{DAY}.md").read_text(encoding="utf-8")
    assert first == second                      # deterministic
    assert str(tmp_path) not in first           # no absolute private path leaks into notes
