# -*- coding: utf-8 -*-
"""Tests for timeframe recovery + hard-validation metadata repair."""
from __future__ import annotations

import json
from pathlib import Path

from src.research_lab.data_inventory import normalize_timeframe, timeframe_from_filename
from src.research_lab.experiment import _derive_timeframe
from src.research_lab.hard_validation_export import _recover_timeframe
from src.research_lab.metadata_repair import build_timeframe_index, repair_metadata


def test_timeframe_from_filename():
    assert timeframe_from_filename("DOGE_USDT_SWAP_430d_1Dutc.json") == "1d"
    assert timeframe_from_filename("BTC_USDT_SWAP_500d_15mutc.json") == "15m"
    assert timeframe_from_filename("x_4Hutc.json") == "4h"
    assert timeframe_from_filename("AAVE_USDT_SWAP_funding_100.json") == ""
    assert timeframe_from_filename("") == ""


def test_normalize_timeframe():
    assert normalize_timeframe("1D") == "1d"
    assert normalize_timeframe("15m") == "15m"
    assert normalize_timeframe("430d") == ""  # history count is not a timeframe
    assert normalize_timeframe(None) == ""


def test_derive_timeframe_from_daily_candles():
    day = 86_400_000
    candles = [{"ts": i * day} for i in range(10)]
    assert _derive_timeframe(candles) == "1d"
    assert _derive_timeframe([]) == ""


def test_recover_timeframe_prefers_explicit_then_label():
    assert _recover_timeframe({"data_file_timeframe": "1D"}, {}) == "1d"
    assert _recover_timeframe({}, {"timeframe": "15m"}) == "15m"
    # legacy: only the data-file label survives
    assert _recover_timeframe({"data_file_label": "DOGE_USDT_SWAP_430d_1Dutc.json"}, {}) == "1d"
    # nothing recoverable -> unknown (honest)
    assert _recover_timeframe({}, {}) == "unknown"


def _seed_private_root(root: Path) -> None:
    cid = "doge1"
    # registry row WITHOUT timeframe (legacy)
    reg = root / "candidate-registry"
    reg.mkdir(parents=True)
    (reg / "candidates.jsonl").write_text(json.dumps({
        "candidate_id": cid, "experiment_id": "exp1", "symbol": "DOGE_USDT_SWAP",
        "strategy_id": "mean_reversion_fade", "timeframe": None,
        "artifact_label": "experiments/completed/20260612_exp1", "params": {},
        "validation_status": "FORWARD_PAPER", "created_at": "2026-06-12T00:00:00Z",
    }) + "\n")
    # request carrying data_file_label but timeframe unknown
    req = root / "hard_validation" / "requests"
    req.mkdir(parents=True)
    (req / f"{cid}.json").write_text(json.dumps({
        "candidate_id": cid, "symbol": "DOGE_USDT_SWAP", "timeframe": "unknown",
        "metrics": {"data_file_label": "DOGE_USDT_SWAP_430d_1Dutc.json", "data_file_timeframe": None},
    }, indent=2))
    # feedback row with unknown
    fb = root / "hard_validation" / "feedback"
    fb.mkdir(parents=True)
    (fb / "feedback.jsonl").write_text(json.dumps({
        "candidate_id": cid, "symbol": "DOGE_USDT_SWAP", "timeframe": "unknown",
        "hard_status": "NEEDS_MORE_DATA",
    }) + "\n")
    # setup card + index (index keyed by setup_id, no candidate_id)
    cards = root / "setup_library" / "cards"
    cards.mkdir(parents=True)
    (cards / f"setup-{cid}.json").write_text(json.dumps({
        "setup_id": f"setup-{cid}", "candidate_id": cid, "symbol": "DOGE_USDT_SWAP",
        "timeframe": "unknown", "strategy_id": "mean_reversion_fade",
    }, indent=2))
    (root / "setup_library" / "setup_index.jsonl").write_text(json.dumps({
        "setup_id": f"setup-{cid}", "symbol": "DOGE_USDT_SWAP", "timeframe": "unknown",
        "strategy_id": "mean_reversion_fade",
    }) + "\n")


def test_repair_metadata_backfills_from_label(tmp_path):
    _seed_private_root(tmp_path)
    index = build_timeframe_index(tmp_path)
    assert index.get("doge1") == "1d"

    summary = repair_metadata(tmp_path, dry_run=False)
    assert summary["total_unresolved"] == 0
    assert summary["total_repaired"] >= 5

    # every artifact now carries 1d
    reg = json.loads((tmp_path / "candidate-registry" / "candidates.jsonl").read_text().strip())
    assert reg["timeframe"] == "1d"
    req = json.loads((tmp_path / "hard_validation" / "requests" / "doge1.json").read_text())
    assert req["timeframe"] == "1d"
    fb = json.loads((tmp_path / "hard_validation" / "feedback" / "feedback.jsonl").read_text().strip())
    assert fb["timeframe"] == "1d"
    idx = json.loads((tmp_path / "setup_library" / "setup_index.jsonl").read_text().strip())
    assert idx["timeframe"] == "1d"


def test_repair_dry_run_writes_nothing(tmp_path):
    _seed_private_root(tmp_path)
    repair_metadata(tmp_path, dry_run=True)
    fb = json.loads((tmp_path / "hard_validation" / "feedback" / "feedback.jsonl").read_text().strip())
    assert fb["timeframe"] == "unknown"  # unchanged on dry-run


def test_repair_unrecoverable_stays_unknown(tmp_path):
    cid = "ghost"
    reg = tmp_path / "candidate-registry"
    reg.mkdir(parents=True)
    (reg / "candidates.jsonl").write_text(json.dumps({
        "candidate_id": cid, "symbol": "X_USDT_SWAP", "timeframe": None,
        "artifact_label": "", "validation_status": "FORWARD_PAPER",
    }) + "\n")
    fb = tmp_path / "hard_validation" / "feedback"
    fb.mkdir(parents=True)
    (fb / "feedback.jsonl").write_text(json.dumps({
        "candidate_id": cid, "symbol": "X_USDT_SWAP", "timeframe": "unknown",
        "hard_status": "HARD_REJECT",
    }) + "\n")
    summary = repair_metadata(tmp_path, dry_run=False)
    # no data_file_label anywhere -> unrecoverable, left unknown, counted honestly
    assert summary["total_unresolved"] >= 1
    row = json.loads((fb / "feedback.jsonl").read_text().strip())
    assert row["timeframe"] == "unknown"
