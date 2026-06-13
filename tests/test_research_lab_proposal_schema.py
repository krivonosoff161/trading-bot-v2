# -*- coding: utf-8 -*-

import pytest

from src.research_lab.proposal_schema import (
    PROPOSED,
    Proposal,
    coerce_proposal,
    proposal_id_for,
)


def _valid_payload(**over):
    base = {
        "created_by": "rule_based",
        "hypothesis": "probe neighbors",
        "requested_timeframe": "15m",
        "setup_family": "momentum_breakout",
        "symbols": ["SOL_USDT_SWAP"],
        "parameter_grid": {"momentum_breakout": [{"lookback": 20}]},
        "max_variants": 6,
    }
    base.update(over)
    return base


def test_coerce_valid_fills_defaults():
    p = coerce_proposal(_valid_payload())
    assert isinstance(p, Proposal)
    assert p.status == PROPOSED
    assert p.proposal_id  # auto-generated
    assert p.to_dict()["schema"].startswith("strategy_lab_proposal")


def test_proposal_id_is_deterministic():
    a = proposal_id_for(_valid_payload())
    b = proposal_id_for(_valid_payload())
    assert a == b
    c = proposal_id_for(_valid_payload(symbols=["BTC_USDT_SWAP"]))
    assert c != a


def test_missing_required_raises():
    for key in ("created_by", "hypothesis", "requested_timeframe", "setup_family"):
        bad = _valid_payload()
        bad[key] = ""
        with pytest.raises(ValueError):
            coerce_proposal(bad)


def test_invalid_created_by_and_status():
    with pytest.raises(ValueError):
        coerce_proposal(_valid_payload(created_by="hacker"))
    with pytest.raises(ValueError):
        coerce_proposal(_valid_payload(status="LIVE"))


def test_non_dict_raises():
    with pytest.raises(ValueError):
        coerce_proposal(["not", "a", "dict"])
