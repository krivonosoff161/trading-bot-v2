# -*- coding: utf-8 -*-

import json
from dataclasses import dataclass, field

import pytest

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


def test_runtime_append_only_segment_does_not_rewrite_large_base(tmp_path):
    path = registry_path(tmp_path)
    base = build_entry(
        "exp1",
        FakeResult(run_id="same"),
        "run1",
        created_at="2026-06-01T00:00:00+00:00",
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(base) + "\n", encoding="utf-8")
    original = path.read_bytes()

    update = build_entry(
        "exp1",
        FakeResult(run_id="same"),
        "run2",
        created_at="2026-06-10T00:00:00+00:00",
    )
    stats = upsert_entries(path, [update], append_only=True)

    assert stats["appended"] == 1
    assert path.read_bytes().startswith(original)
    assert path.stat().st_size > len(original)
    segments = list((path.parent / "segments").glob("*.jsonl"))
    assert segments == []
    entries = load_entries(path)
    assert len(entries) == 1
    assert entries[0]["created_at"] == "2026-06-01T00:00:00+00:00"
    assert entries[0]["artifact_label"] == "run2"


def test_runtime_segment_publication_is_atomic_and_deterministic_to_read(tmp_path):
    path = registry_path(tmp_path)
    rows = [
        build_entry("exp2", FakeResult(run_id="z"), "run-z"),
        build_entry("exp1", FakeResult(run_id="a"), "run-a"),
    ]

    upsert_entries(path, rows, append_only=True)

    assert path.exists()
    assert path.read_bytes()
    assert not list((path.parent / "segments").glob("*.tmp"))
    assert not list((path.parent / "segments").glob("*.jsonl"))
    entries = load_entries(path)
    keys = [(row["experiment_id"], row["candidate_id"]) for row in entries]
    assert keys == sorted(keys)
    summary = registry_summary(path)
    assert summary["exists"] is True
    assert summary["base_exists"] is True
    assert summary["segment_files"] == 0


def test_compact_rewrite_fails_closed_when_runtime_segments_exist(tmp_path):
    path = registry_path(tmp_path)
    upsert_entries(
        path,
        [build_entry("exp1", FakeResult(run_id="a"), "run-a")],
        append_only=True,
    )
    segment_dir = path.parent / "segments"
    segment_dir.mkdir(exist_ok=True)
    (segment_dir / "retained_after_interruption.jsonl").write_text(
        json.dumps(
            build_entry("exp1", FakeResult(run_id="a"), "run-a"),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="dedicated transactional compactor"):
        upsert_entries(
            path,
            [build_entry("exp1", FakeResult(run_id="b"), "run-b")],
        )

    entries = load_entries(path)
    assert [(row["experiment_id"], row["candidate_id"]) for row in entries] == [
        ("exp1", "a")
    ]


def test_interrupted_partial_append_is_isolated_and_wal_segment_recovers(
    tmp_path,
):
    path = registry_path(tmp_path)
    base = build_entry("exp1", FakeResult(run_id="base"), "base")
    recovered = build_entry("exp1", FakeResult(run_id="recovered"), "wal")
    path.parent.mkdir(parents=True)
    path.write_bytes(
        (json.dumps(base, sort_keys=True) + "\n").encode("utf-8")
        + b'{"schema":"interrupted"'
    )
    segment_dir = path.parent / "segments"
    segment_dir.mkdir()
    (segment_dir / "0001_recovery.jsonl").write_text(
        json.dumps(recovered, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    entries = load_entries(path)

    assert [(row["experiment_id"], row["candidate_id"]) for row in entries] == [
        ("exp1", "base"),
        ("exp1", "recovered"),
    ]
