# -*- coding: utf-8 -*-

from src.research_lab.proposal_schema import QUEUED, VALIDATED, coerce_proposal
from src.research_lab.proposal_store import (
    load_proposals,
    proposals_path,
    set_status,
    status_counts,
    upsert_proposals,
)


def _p(pid_seed, status=VALIDATED, created_at="2026-06-13T00:00:00+00:00"):
    return coerce_proposal({
        "proposal_id": pid_seed,
        "created_by": "rule_based",
        "hypothesis": "h",
        "requested_timeframe": "15m",
        "setup_family": "momentum_breakout",
        "symbols": ["SOL_USDT_SWAP"],
        "parameter_grid": {"momentum_breakout": [{"lookback": 20}]},
        "status": status,
        "created_at": created_at,
    })


def test_upsert_idempotent_and_preserves_created_at(tmp_path):
    path = proposals_path(tmp_path)
    s1 = upsert_proposals(path, [_p("a", created_at="2026-06-01T00:00:00+00:00")])
    assert s1["added"] == 1
    # re-upsert same id with different created_at -> updated, original created_at kept
    s2 = upsert_proposals(path, [_p("a", created_at="2026-06-13T00:00:00+00:00")])
    assert s2["added"] == 0 and s2["updated"] == 1
    loaded = load_proposals(path)
    assert len(loaded) == 1
    assert loaded[0].created_at == "2026-06-01T00:00:00+00:00"


def test_set_status(tmp_path):
    path = proposals_path(tmp_path)
    upsert_proposals(path, [_p("a", status=VALIDATED)])
    assert set_status(path, "a", QUEUED) is True
    assert load_proposals(path)[0].status == QUEUED
    assert set_status(path, "missing", QUEUED) is False


def test_status_counts(tmp_path):
    path = proposals_path(tmp_path)
    upsert_proposals(path, [_p("a", VALIDATED), _p("b", VALIDATED), _p("c", "REJECTED")])
    counts = status_counts(load_proposals(path))
    assert counts["VALIDATED"] == 2
    assert counts["REJECTED"] == 1
