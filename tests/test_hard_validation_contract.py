# -*- coding: utf-8 -*-
"""Tests for hard_validation_contract.py — Phase 1."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.research_lab.hard_validation_contract import (
    CONTRACT_VERSION,
    HARD_STATUSES,
    CandidateForValidation,
    DataWindow,
    EquityPoint,
    FailureFeedback,
    HardValidationReport,
    HardValidationVerdict,
    SetupCard,
    TradeRecord,
    append_jsonl,
    read_json,
    write_json,
)

CANDIDATE_DICT = {
    "candidate_id": "c-001",
    "source_run_id": "run-abc",
    "symbol": "BTC-USDT-SWAP",
    "normalized_symbol": "BTC_USDT_SWAP",
    "timeframe": "15m",
    "strategy_id": "trend",
    "params": {"ma_window": 20},
    "filters": {"min_vol_ratio": 1.0},
    "fees_bps": 7.0,
    "slippage_bps": 3.0,
    "lite_status": "FORWARD_PAPER",
    "lite_reasons": ["passed_lite_validation"],
    "risk_flags": [],
    "metrics": {"n_trades": 42, "profit_factor": 1.5, "avg_net_pct": 0.3},
    "trades": [
        {"side": "long", "entry_price": 100, "exit_price": 103,
         "entry_ts": 1000, "exit_ts": 2000, "pnl_pct": 3.0}
    ],
    "equity_curve": [
        {"ts": 1000, "value": 10000},
        {"ts": 2000, "value": 10300},
    ],
    "data_window": {"start_ts": 0, "end_ts": 3000, "n_bars": 200},
    "created_at": "2026-06-14T00:00:00Z",
}


def _make_candidate() -> CandidateForValidation:
    return CandidateForValidation.from_dict(CANDIDATE_DICT)


def _make_verdict(status: str = "PAPER_FORWARD_READY") -> HardValidationVerdict:
    return HardValidationVerdict(
        candidate_id="c-001",
        hard_status=status,
        checks=[
            {"check_name": "costs", "passed": True, "message": "ok"},
        ],
        failed_checks=[] if status == "PAPER_FORWARD_READY" else ["costs"],
        reason_codes=[],
        created_at="2026-06-14T00:00:00Z",
    )


class TestCandidateForValidation:
    def test_roundtrip(self) -> None:
        c = _make_candidate()
        d = c.to_dict()
        c2 = CandidateForValidation.from_dict(d)
        assert c2.candidate_id == "c-001"
        assert c2.strategy_id == "trend"
        assert c2.params == {"ma_window": 20}
        assert c2.trades[0]["side"] == "long"
        assert c2.contract_version == CONTRACT_VERSION

    def test_json_roundtrip(self) -> None:
        c = _make_candidate()
        raw = json.dumps(c.to_dict())
        c2 = CandidateForValidation.from_dict(json.loads(raw))
        assert c2.candidate_id == c.candidate_id

    def test_missing_required_field(self) -> None:
        d = {k: v for k, v in CANDIDATE_DICT.items() if k != "candidate_id"}
        with pytest.raises(KeyError):
            CandidateForValidation.from_dict(d)

    def test_no_absolute_private_paths_in_dict(self) -> None:
        c = _make_candidate()
        raw = json.dumps(c.to_dict())
        assert "C:\\" not in raw
        assert "krivo" not in raw
        assert "github_projects" not in raw

    def test_contract_version_stable(self) -> None:
        c = _make_candidate()
        assert c.contract_version == CONTRACT_VERSION

    def test_lite_status_forward_paper(self) -> None:
        c = _make_candidate()
        assert c.lite_status == "FORWARD_PAPER"


class TestHardValidationVerdict:
    def test_valid_status(self) -> None:
        v = _make_verdict("PAPER_FORWARD_READY")
        assert v.hard_status == "PAPER_FORWARD_READY"

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="hard_status"):
            HardValidationVerdict(
                candidate_id="x",
                hard_status="PROFITABLE",
                checks=[],
                failed_checks=[],
                reason_codes=[],
            )

    def test_all_hard_statuses_accepted(self) -> None:
        for status in HARD_STATUSES:
            v = _make_verdict(status)
            assert v.hard_status == status

    def test_roundtrip(self) -> None:
        v = _make_verdict("FAILED_COSTS")
        d = v.to_dict()
        v2 = HardValidationVerdict.from_dict(d)
        assert v2.hard_status == "FAILED_COSTS"
        assert v2.failed_checks == ["costs"]

    def test_json_roundtrip(self) -> None:
        v = _make_verdict()
        raw = json.dumps(v.to_dict())
        v2 = HardValidationVerdict.from_dict(json.loads(raw))
        assert v2.contract_version == CONTRACT_VERSION


class TestHardValidationReport:
    def test_to_markdown(self) -> None:
        report = HardValidationReport(
            candidate_id="c-001",
            source_run_id="run-abc",
            symbol="BTC-USDT-SWAP",
            timeframe="15m",
            strategy_id="trend",
            verdict=_make_verdict("FAILED_COSTS").to_dict(),
            checks_summary={"total": 1, "passed": 0, "failed": 1},
            created_at="2026-06-14T00:00:00Z",
        )
        md = report.to_markdown()
        assert "c-001" in md
        assert "FAILED_COSTS" in md
        assert "FAIL" in md
        assert "not imply profitability" in md
        assert "not readiness for live trading" in md.lower() or "not imply" in md

    def test_roundtrip(self) -> None:
        report = HardValidationReport(
            candidate_id="c-001",
            source_run_id="run-abc",
            symbol="BTC-USDT-SWAP",
            timeframe="15m",
            strategy_id="trend",
            verdict=_make_verdict().to_dict(),
            checks_summary={"total": 1, "passed": 1, "failed": 0},
        )
        d = report.to_dict()
        r2 = HardValidationReport.from_dict(d)
        assert r2.candidate_id == "c-001"
        assert r2.contract_version == CONTRACT_VERSION


class TestSetupCard:
    def test_main_engine_ready_always_false(self) -> None:
        card = SetupCard(
            setup_id="s-001",
            candidate_id="c-001",
            symbol="BTC-USDT-SWAP",
            timeframe="15m",
            strategy_id="trend",
            params={},
            filters={},
            data_window={},
            lite_status="FORWARD_PAPER",
            hard_status="PAPER_FORWARD_READY",
            checks_summary={},
            failed_checks=[],
            risk_flags=[],
            entry_exit_summary="",
            regime_tags=[],
            main_engine_ready=True,
        )
        d = card.to_dict()
        card2 = SetupCard.from_dict(d)
        assert card2.main_engine_ready is False

    def test_roundtrip(self) -> None:
        card = SetupCard(
            setup_id="s-001",
            candidate_id="c-001",
            symbol="BTC-USDT-SWAP",
            timeframe="15m",
            strategy_id="trend",
            params={"ma": 20},
            filters={},
            data_window={},
            lite_status="FORWARD_PAPER",
            hard_status="PAPER_FORWARD_READY",
            checks_summary={"total": 3, "passed": 3, "failed": 0},
            failed_checks=[],
            risk_flags=[],
            entry_exit_summary="trend following",
            regime_tags=["trending"],
            paper_forward_ready=True,
        )
        d = card.to_dict()
        card2 = SetupCard.from_dict(d)
        assert card2.setup_id == "s-001"
        assert card2.paper_forward_ready is True
        assert card2.main_engine_ready is False


class TestFailureFeedback:
    def test_roundtrip(self) -> None:
        fb = FailureFeedback(
            candidate_id="c-001",
            symbol="BTC-USDT-SWAP",
            timeframe="15m",
            strategy_id="trend",
            hard_status="FAILED_FRAGILITY",
            failed_checks=["robustness"],
            reason_codes=["lucky_spike"],
            suggested_next_test_constraints=["wider_neighborhood"],
            blocked_parameter_regions=[{"ma_window": [19, 21]}],
            required_data=["15m_30d"],
            priority="high",
        )
        d = fb.to_dict()
        fb2 = FailureFeedback.from_dict(d)
        assert fb2.hard_status == "FAILED_FRAGILITY"
        assert fb2.priority == "high"


class TestIOHelpers:
    def test_write_read_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "test.json"
            write_json(p, {"a": 1})
            assert read_json(p) == {"a": 1}

    def test_append_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "log.jsonl"
            append_jsonl(p, {"x": 1})
            append_jsonl(p, {"x": 2})
            lines = p.read_text().strip().split("\n")
            assert len(lines) == 2
            assert json.loads(lines[1])["x"] == 2


class TestTradeRecordEquityPointDataWindow:
    def test_trade_record_roundtrip(self) -> None:
        t = TradeRecord(
            side="long", entry_price=100, exit_price=103,
            entry_ts=1000, exit_ts=2000, pnl_pct=3.0,
        )
        d = t.to_dict()
        assert d["side"] == "long"
        assert d["pnl_pct"] == 3.0

    def test_equity_point_roundtrip(self) -> None:
        ep = EquityPoint(ts=1000, value=10000)
        d = ep.to_dict()
        assert d["value"] == 10000

    def test_data_window_roundtrip(self) -> None:
        dw = DataWindow(start_ts=0, end_ts=3000, n_bars=200, timeframe="15m")
        d = dw.to_dict()
        assert d["n_bars"] == 200
