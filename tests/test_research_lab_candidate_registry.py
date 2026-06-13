# -*- coding: utf-8 -*-

import json
from dataclasses import dataclass, field

from src.research_lab.candidate_registry import (
    build_entry,
    load_entries,
    registry_path,
    registry_summary,
    upsert_entries,
)


@dataclass(frozen=True)
class FakeResult:
    run_id: str = "abc123"
    symbol: str = "BTC_USDT_SWAP"
    family: str = "momentum_breakout"
    params: dict = field(default_factory=lambda: {"lookback": 20})
    metrics: dict = field(default_factory=lambda: {"n_trades": 30, "avg_net_pct": 0.5, "profit_factor": 1.4})
    decision: str = "PROMOTE_FOR_PRESSURE_TEST"
    reasons: list = field(default_factory=lambda: ["passed_basic_gates"])
    validation_status: str = "FORWARD_PAPER"
    validation_reasons: list = field(default_factory=lambda: ["passed_lite_validation"])
    risk_flags: list = field(default_factory=lambda: ["parameter_fragility_unknown"])
    next_action: str = "track paper-forward only"
    regime_summary: dict = field(default_factory=lambda: {"dominant_bucket": "medium|up|normal"})


def test_build_entry_fields():
    entry = build_entry("exp1", FakeResult(), "experiments/completed/run1")

    assert entry["candidate_id"] == "abc123"
    assert entry["experiment_id"] == "exp1"
    assert entry["strategy_id"] == "momentum_breakout"
    assert entry["validation_status"] == "FORWARD_PAPER"
    assert entry["metrics_summary"]["n_trades"] == 30
    assert entry["artifact_label"] == "experiments/completed/run1"
    assert entry["next_review"] > entry["created_at"][:10]


def test_upsert_is_idempotent_and_preserves_created_at(tmp_path):
    path = registry_path(tmp_path)
    first_entry = build_entry("exp1", FakeResult(), "run1", created_at="2026-06-01T00:00:00+00:00")

    stats1 = upsert_entries(path, [first_entry])
    again = build_entry("exp1", FakeResult(), "run2", created_at="2026-06-10T00:00:00+00:00")
    stats2 = upsert_entries(path, [again])

    assert stats1 == {"added": 1, "updated": 0, "total": 1}
    assert stats2 == {"added": 0, "updated": 1, "total": 1}
    entries = load_entries(path)
    assert len(entries) == 1
    assert entries[0]["created_at"] == "2026-06-01T00:00:00+00:00"
    assert entries[0]["artifact_label"] == "run2"


def test_reject_entries_have_no_next_review(tmp_path):
    entry = build_entry("exp1", FakeResult(validation_status="REJECT"), "run1")
    assert entry["next_review"] == ""


def test_registry_summary_counts_without_payload(tmp_path):
    path = registry_path(tmp_path)
    upsert_entries(path, [
        build_entry("exp1", FakeResult(run_id="a"), "run1"),
        build_entry("exp1", FakeResult(run_id="b", validation_status="REJECT"), "run1"),
    ])

    summary = registry_summary(path)

    assert summary["entries"] == 2
    assert summary["unique_candidates"] == 2
    assert summary["by_validation_status"] == {"FORWARD_PAPER": 1, "REJECT": 1}
    assert "BTC" not in json.dumps(summary)
    assert summary["registry_label"].startswith("strategy-lab/")


def test_registry_summary_separates_rows_from_unique_candidates(tmp_path):
    path = registry_path(tmp_path)
    upsert_entries(path, [
        build_entry("exp1", FakeResult(run_id="same"), "run1"),
        build_entry("exp2", FakeResult(run_id="same", validation_status="OBSERVE"), "run2"),
    ])

    summary = registry_summary(path)

    assert summary["entries"] == 2
    assert summary["unique_candidates"] == 1


def test_registry_file_is_sorted_jsonl(tmp_path):
    path = registry_path(tmp_path)
    upsert_entries(path, [
        build_entry("exp2", FakeResult(run_id="z"), "run1"),
        build_entry("exp1", FakeResult(run_id="a"), "run1"),
    ])

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    keys = [(json.loads(line)["experiment_id"], json.loads(line)["candidate_id"]) for line in lines]
    assert keys == sorted(keys)
