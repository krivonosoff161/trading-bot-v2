# -*- coding: utf-8 -*-

import json

from src.research_lab.candidate_registry import registry_path, upsert_entries
from src.research_lab.proposals import (
    generate_and_write_from_registry,
    generate_proposals,
    load_proposal_log,
    proposal_log_path,
    spec_dir,
    write_proposals,
)


def _entry(
    *,
    candidate_id: str = "run1",
    symbol: str = "BTC_USDT_SWAP",
    strategy_id: str = "momentum_breakout",
    status: str = "FORWARD_PAPER",
    params: dict | None = None,
    metrics: dict | None = None,
    reasons: list[str] | None = None,
    regime_summary: dict | None = None,
) -> dict:
    if regime_summary is None:
        regime_summary = {"dominant_bucket": "medium|up|normal"}
    return {
        "schema": "strategy_lab_candidate_registry.v1",
        "candidate_id": candidate_id,
        "experiment_id": "source_exp",
        "symbol": symbol,
        "strategy_id": strategy_id,
        "params": params or {"lookback": 20, "threshold": 1.5, "hold_bars": 6},
        "metrics_summary": metrics or {"n_trades": 30, "avg_net_pct": 0.7, "test_avg_net_pct": 0.4},
        "decision": "PROMOTE_FOR_PRESSURE_TEST",
        "validation_status": status,
        "validation_reasons": reasons or ["passed_lite_validation"],
        "risk_flags": [],
        "next_action": "paper-forward only",
        "regime_summary": regime_summary,
        "artifact_label": "strategy-lab/experiments/completed/source",
        "created_at": "2026-06-12T00:00:00+00:00",
        "next_review": "2026-06-19",
    }


def test_generate_forward_paper_parameter_neighborhood():
    proposals = generate_proposals([_entry()], max_proposals=1)

    assert len(proposals) == 1
    spec = proposals[0]["spec"]
    variants = spec["parameter_grid"]["momentum_breakout"]
    assert proposals[0]["reason"] == "parameter_neighborhood_sweep"
    assert spec["symbols"] == ["BTC_USDT_SWAP"]
    assert spec["families"] == ["momentum_breakout"]
    assert variants[0] == {"lookback": 20, "threshold": 1.5, "hold_bars": 6}
    assert {"lookback": 16, "threshold": 1.5, "hold_bars": 6} in variants
    assert {"lookback": 24, "threshold": 1.5, "hold_bars": 6} in variants
    assert spec["max_runs"] == len(variants)


def test_generate_regime_specific_rerun_uses_filter_reason():
    proposals = generate_proposals([
        _entry(status="REGIME_SPECIFIC", reasons=["strong_regime_bucket:high|down|normal"])
    ])

    assert len(proposals) == 1
    spec = proposals[0]["spec"]
    assert proposals[0]["reason"] == "regime_specific_rerun"
    assert spec["filters"] == {
        "volatility": ["high"],
        "trend": ["down"],
        "volume": ["normal"],
    }
    assert spec["min_trades"] == 8


def test_generate_regime_specific_without_bucket_is_skipped():
    entry = _entry(status="REGIME_SPECIFIC", reasons=["fragility_unknown"])
    entry["regime_summary"] = {}
    proposals = generate_proposals([
        entry
    ])

    assert proposals == []


def test_generate_prioritizes_forward_paper_and_limits_count():
    entries = [
        _entry(candidate_id="observe", status="OBSERVE", metrics={"test_avg_net_pct": 99}),
        _entry(candidate_id="paper", status="FORWARD_PAPER", metrics={"test_avg_net_pct": 1}),
    ]

    proposals = generate_proposals(entries, max_proposals=1)

    assert proposals[0]["source_candidate_id"] == "paper"


def test_write_proposals_is_idempotent_and_private(tmp_path):
    proposals = generate_proposals([_entry()], max_proposals=1)

    first = write_proposals(tmp_path, proposals)
    second = write_proposals(tmp_path, proposals)

    assert first["written"] == 1
    assert second["written"] == 0
    rows = load_proposal_log(proposal_log_path(tmp_path))
    assert len(rows) == 1
    files = list(spec_dir(tmp_path).glob("*.json"))
    assert len(files) == 1
    spec = json.loads(files[0].read_text(encoding="utf-8"))
    assert spec["experiment_id"].startswith("auto_parameter_neighborhood_sweep_")


def test_generate_and_write_from_registry_counts_entries(tmp_path):
    upsert_entries(
        registry_path(tmp_path),
        [
            _entry(candidate_id="a"),
            _entry(candidate_id="b", status="REJECT"),
        ],
    )

    result = generate_and_write_from_registry(tmp_path, max_proposals=4)

    assert result["registry_entries"] == 2
    assert result["generated"] == 1
    assert result["written"] == 1
