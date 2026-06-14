# -*- coding: utf-8 -*-
"""Tests for proposal quality scoring (Phase 7).

Verifies:
- Each major rejection reason appears in summary.
- rejection_reason_counts tallies correctly.
- status_counts still works.
"""

from src.research_lab.proposal_schema import Proposal, REJECTED, VALIDATED
from src.research_lab.proposal_store import rejection_reason_counts, status_counts


def _make_proposal(status: str, rejection_reason: str = "", **kw) -> Proposal:
    return Proposal(
        proposal_id=kw.get("proposal_id", "p1"),
        created_by="rule_based",
        hypothesis="test",
        setup_family="momentum_breakout",
        requested_timeframe="1d",
        status=status,
        rejection_reason=rejection_reason,
    )


def test_rejection_reason_counts_empty():
    assert rejection_reason_counts([]) == {}


def test_rejection_reason_counts_groups():
    proposals = [
        _make_proposal(REJECTED, "unknown_family"),
        _make_proposal(REJECTED, "unknown_family"),
        _make_proposal(REJECTED, "too_many_variants"),
        _make_proposal(REJECTED, "unknown_family,disallowed_timeframe"),
        _make_proposal(VALIDATED),
    ]
    counts = rejection_reason_counts(proposals)
    assert counts["unknown_family"] == 3
    assert counts["too_many_variants"] == 1
    assert counts["disallowed_timeframe"] == 1
    assert "VALIDATED" not in counts


def test_major_reasons_all_appear():
    reasons = [
        "unknown_family", "unknown_symbol", "disallowed_timeframe",
        "one_minute_full_sweep_blocked", "too_many_variants",
        "heavy_job_not_allowed", "unsafe_wording", "output_boundary_violation",
        "missing_hypothesis", "not_compilable",
    ]
    proposals = [_make_proposal(REJECTED, r) for r in reasons]
    counts = rejection_reason_counts(proposals)
    for r in reasons:
        assert r in counts, f"reason {r!r} missing from counts"
        assert counts[r] == 1


def test_status_counts():
    proposals = [
        _make_proposal(VALIDATED),
        _make_proposal(VALIDATED),
        _make_proposal(REJECTED, "x"),
    ]
    counts = status_counts(proposals)
    assert counts[VALIDATED] == 2
    assert counts[REJECTED] == 1


def test_empty_proposals():
    assert status_counts([]) == {}
    assert rejection_reason_counts([]) == {}


def test_rejection_reason_with_whitespace():
    proposals = [_make_proposal(REJECTED, " unknown_family , too_many_variants ")]
    counts = rejection_reason_counts(proposals)
    assert counts["unknown_family"] == 1
    assert counts["too_many_variants"] == 1
