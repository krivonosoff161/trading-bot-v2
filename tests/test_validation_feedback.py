# -*- coding: utf-8 -*-
"""Tests for validation_feedback.py — Phase 4."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.research_lab.hard_validation_contract import (
    HardValidationReport,
)
from src.research_lab.validation_feedback import (
    STATUS_TO_FEEDBACK,
    generate_feedback,
    load_feedback_queue,
    write_feedback,
)


def _make_report(
    hard_status: str = "FAILED_COSTS",
    failed_checks: list[str] | None = None,
    reason_codes: list[str] | None = None,
) -> HardValidationReport:
    return HardValidationReport(
        candidate_id="c-001",
        source_run_id="run-abc",
        symbol="BTC-USDT-SWAP",
        timeframe="15m",
        strategy_id="trend",
        verdict={
            "candidate_id": "c-001",
            "hard_status": hard_status,
            "checks": [],
            "failed_checks": failed_checks or [],
            "reason_codes": reason_codes or [],
        },
        checks_summary={"total": 5, "passed": 2, "failed": 3},
    )


class TestGenerateFeedback:
    def test_paper_forward_ready_returns_none(self) -> None:
        report = _make_report(hard_status="PAPER_FORWARD_READY")
        fb = generate_feedback(report)
        assert fb is None

    def test_failed_costs(self) -> None:
        report = _make_report(
            hard_status="FAILED_COSTS",
            failed_checks=["costs"],
            reason_codes=["edge_thinner_than_costs"],
        )
        fb = generate_feedback(report)
        assert fb is not None
        assert fb.hard_status == "FAILED_COSTS"
        assert "costs" in fb.failed_checks
        assert len(fb.suggested_next_test_constraints) > 0

    def test_failed_oos(self) -> None:
        report = _make_report(
            hard_status="FAILED_OOS",
            failed_checks=["oos_split"],
        )
        fb = generate_feedback(report)
        assert fb is not None
        assert fb.hard_status == "FAILED_OOS"
        assert "more_oos_bars" in fb.required_data

    def test_failed_fragility(self) -> None:
        report = _make_report(
            hard_status="FAILED_FRAGILITY",
            failed_checks=["robustness"],
        )
        fb = generate_feedback(report)
        assert fb is not None
        assert "wider_parameter_neighborhood" in fb.suggested_next_test_constraints

    def test_failed_overfit(self) -> None:
        report = _make_report(
            hard_status="FAILED_OVERFIT",
            failed_checks=["overfit_psr"],
        )
        fb = generate_feedback(report)
        assert fb is not None
        assert fb.priority == "high"

    def test_hard_reject(self) -> None:
        report = _make_report(
            hard_status="HARD_REJECT",
            failed_checks=["significance", "forward_readiness"],
        )
        fb = generate_feedback(report)
        assert fb is not None
        assert fb.priority == "low"

    def test_all_statuses_have_templates(self) -> None:
        for status in STATUS_TO_FEEDBACK:
            report = _make_report(
                hard_status=status,
                failed_checks=["costs"] if status != "PAPER_FORWARD_READY" else [],
            )
            fb = generate_feedback(report)
            if status == "PAPER_FORWARD_READY":
                assert fb is None
            else:
                assert fb is not None
                assert fb.hard_status == status

    def test_no_profitability_claim(self) -> None:
        report = _make_report(hard_status="FAILED_COSTS", failed_checks=["costs"])
        fb = generate_feedback(report)
        assert fb is not None
        d = fb.to_dict()
        raw = json.dumps(d)
        assert "profitable" not in raw.lower()
        assert "guaranteed" not in raw.lower()


class TestWriteFeedback:
    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = _make_report()
            fb = generate_feedback(report)
            assert fb is not None
            path = write_feedback(Path(td), fb, dry_run=True)
            assert path is None

    def test_apply_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = _make_report()
            fb = generate_feedback(report)
            assert fb is not None
            path = write_feedback(Path(td), fb, dry_run=False)
            assert path is not None
            assert path.exists()
            lines = path.read_text().strip().split("\n")
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["candidate_id"] == "c-001"

    def test_multiple_appends(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            for status in ["FAILED_COSTS", "FAILED_OOS", "FAILED_FRAGILITY"]:
                report = _make_report(hard_status=status, failed_checks=["costs"])
                fb = generate_feedback(report)
                write_feedback(Path(td), fb, dry_run=False)
            queue = load_feedback_queue(Path(td))
            assert len(queue) == 3


class TestLoadFeedbackQueue:
    def test_empty_when_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            queue = load_feedback_queue(Path(td))
            assert queue == []

    def test_reads_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = _make_report()
            fb = generate_feedback(report)
            write_feedback(Path(td), fb, dry_run=False)
            queue = load_feedback_queue(Path(td))
            assert len(queue) == 1
            assert queue[0]["candidate_id"] == "c-001"
