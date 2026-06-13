# -*- coding: utf-8 -*-

import json

from src.research_lab.candidate_registry import registry_path
from src.research_lab.review_export import (
    build_registry_review_pack,
    export_review_pack,
    review_pack_dir,
)


def _entry(cid, status, avg, test_avg):
    return {
        "candidate_id": cid,
        "symbol": "BTC_USDT_SWAP",
        "strategy_id": "momentum_breakout",
        "params": {"lookback": 20},
        "validation_status": status,
        "metrics_summary": {"n_trades": 25, "avg_net_pct": avg, "test_avg_net_pct": test_avg, "profit_factor": 1.3},
        "risk_flags": ["parameter_fragility_unknown"],
        "next_action": "keep in registry",
        "artifact_label": "experiments/completed/run_x",
        "next_review": "2026-07-01",
    }


def _write_registry(tmp_path, entries):
    path = registry_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def test_build_pack_excludes_reject_and_has_framing():
    entries = [
        _entry("a", "FORWARD_PAPER", 1.0, 0.8),
        _entry("b", "OBSERVE", 0.4, 0.2),
        _entry("c", "REJECT", -1.0, -1.0),
    ]
    pack = build_registry_review_pack(entries, limit=10)
    statuses = {c["validation_status"] for c in pack["candidates"]}
    assert "REJECT" not in statuses
    assert pack["candidate_count"] == 2
    # strongest (FORWARD_PAPER) ranked first
    assert pack["candidates"][0]["validation_status"] == "FORWARD_PAPER"
    assert "not a profitability claim" in pack["disclaimer"].lower()
    assert "next tests only" in pack["disclaimer"].lower()


def test_export_works_without_api_key_and_no_path_leak(tmp_path, monkeypatch):
    monkeypatch.delenv("STRATEGY_LAB_LLM_ENABLED", raising=False)
    _write_registry(tmp_path, [_entry("a", "FORWARD_PAPER", 1.0, 0.8), _entry("c", "REJECT", -1, -1)])

    result = export_review_pack(tmp_path, limit=5)

    assert result["candidate_count"] == 1
    packs = list(review_pack_dir(tmp_path).glob("*.json"))
    assert len(packs) == 1
    text = packs[0].read_text(encoding="utf-8")
    assert str(tmp_path) not in text  # no absolute private path
    assert str(tmp_path).replace("\\", "/") not in text.replace("\\", "/")
    pack = json.loads(text)
    # summaries only — no raw trade lists
    assert "trades" not in pack["candidates"][0]
    assert "metrics" in pack["candidates"][0]


def test_export_empty_registry_is_safe(tmp_path):
    result = export_review_pack(tmp_path, limit=5)
    assert result["registry_entries"] == 0
    assert result["candidate_count"] == 0
