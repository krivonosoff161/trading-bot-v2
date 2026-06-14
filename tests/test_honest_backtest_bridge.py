# -*- coding: utf-8 -*-
"""Tests for honest_backtest_bridge.py — Phase 3."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.research_lab.hard_validation_contract import (
    CandidateForValidation,
)
from src.research_lab.honest_backtest_bridge import (
    _build_verdict,
    _check_costs,
    _check_data_quality,
    _check_forward_readiness,
    _check_overfit,
    _check_robustness,
    _check_significance,
    _check_splits,
    _extract_returns,
    _map_failed_to_status,
    bridge_available,
    run_validation,
    run_validation_batch,
)

CANDIDATE_DICT = {
    "candidate_id": "c-001",
    "source_run_id": "run-abc",
    "symbol": "BTC-USDT-SWAP",
    "normalized_symbol": "BTC_USDT_SWAP",
    "timeframe": "15m",
    "strategy_id": "trend",
    "params": {"ma_window": 20},
    "filters": {},
    "fees_bps": 7.0,
    "slippage_bps": 3.0,
    "lite_status": "FORWARD_PAPER",
    "lite_reasons": ["passed_lite_validation"],
    "risk_flags": [],
    "metrics": {"n_trades": 20, "profit_factor": 1.5},
    "trades": [
        {"side": "long", "entry_price": 100, "exit_price": 103,
         "entry_ts": 1000, "exit_ts": 2000, "net_pct": 2.0},
        {"side": "short", "entry_price": 50, "exit_price": 48,
         "entry_ts": 3000, "exit_ts": 4000, "net_pct": 3.5},
    ] + [
        {"side": "long", "entry_price": 100, "exit_price": 99,
         "entry_ts": i * 1000, "exit_ts": (i + 1) * 1000,
         "net_pct": -0.5}
        for i in range(2, 20)
    ],
    "equity_curve": [],
    "data_window": {"start_ts": 0, "end_ts": 20000, "n_bars": 20},
    "created_at": "2026-06-14T00:00:00Z",
}


def _make_candidate(**overrides) -> CandidateForValidation:
    d = {**CANDIDATE_DICT, **overrides}
    return CandidateForValidation.from_dict(d)


class TestBridgeAvailable:
    def test_returns_status(self) -> None:
        status = bridge_available()
        assert "numpy" in status
        assert "backtest_sanity" in status


class TestExtractReturns:
    def test_from_trades(self) -> None:
        c = _make_candidate()
        returns = _extract_returns(c)
        assert len(returns) == 20
        assert returns[0] == 2.0

    def test_from_equity_curve(self) -> None:
        c = _make_candidate(
            trades=[],
            equity_curve=[
                {"ts": 0, "value": 10000},
                {"ts": 1, "value": 10100},
                {"ts": 2, "value": 9900},
            ],
        )
        returns = _extract_returns(c)
        assert len(returns) == 2
        assert abs(returns[0] - 1.0) < 0.01

    def test_empty(self) -> None:
        c = _make_candidate(trades=[], equity_curve=[])
        returns = _extract_returns(c)
        assert returns == []


class TestCheckCosts:
    def test_positive_survives(self) -> None:
        c = _make_candidate(fees_bps=0, slippage_bps=0)
        returns = [1.0] * 10
        result = _check_costs(c, returns)
        assert result["passed"] is True

    def test_negative_fails(self) -> None:
        c = _make_candidate(fees_bps=100, slippage_bps=100)
        returns = [0.01] * 10
        result = _check_costs(c, returns)
        assert result["passed"] is False


class TestCheckSplits:
    def test_positive_oos(self) -> None:
        returns = [0.5] * 20
        result = _check_splits(returns)
        assert result["passed"] is True

    def test_too_few(self) -> None:
        result = _check_splits([1.0, 2.0])
        assert result["passed"] is False


class TestCheckSignificance:
    def test_strong_signal(self) -> None:
        returns = [2.0] * 20
        result = _check_significance(returns)
        assert result["passed"] is True

    def test_weak_signal(self) -> None:
        returns = [0.01, -0.01, 0.02, -0.02, 0.01]
        result = _check_significance(returns)
        assert "permutation_p" in result["details"]


class TestCheckRobustness:
    def test_consistent(self) -> None:
        returns = [0.5] * 20
        result = _check_robustness(returns)
        assert result["passed"] is True


class TestCheckOverfit:
    def test_strong_psr(self) -> None:
        returns = [0.5, 1.0, 0.8, 1.2, 0.9, 1.1, 0.7, 1.3, 0.6, 1.0,
                   0.8, 1.1, 0.9, 1.2, 0.7, 1.0, 0.8, 1.1, 0.9, 1.0]
        result = _check_overfit(_make_candidate(), returns)
        assert result["passed"] is True

    def test_too_few(self) -> None:
        result = _check_overfit(_make_candidate(), [1.0, 2.0])
        assert result["passed"] is False


class TestCheckForwardReadiness:
    def test_forward_paper(self) -> None:
        c = _make_candidate(lite_status="FORWARD_PAPER")
        result = _check_forward_readiness(c)
        assert result["passed"] is True

    def test_reject(self) -> None:
        c = _make_candidate(lite_status="REJECT")
        result = _check_forward_readiness(c)
        assert result["passed"] is False


class TestCheckDataQuality:
    def test_clean(self) -> None:
        c = _make_candidate(metrics={"n_trades": 2})
        returns = [1.0, 2.0]
        result = _check_data_quality(c, returns)
        assert result["passed"] is True

    def test_nan_fails(self) -> None:
        c = _make_candidate(metrics={"n_trades": 2})
        result = _check_data_quality(c, [1.0, float("nan")])
        assert result["passed"] is False


class TestMapFailedToStatus:
    def test_cost_fail(self) -> None:
        c = _make_candidate()
        assert _map_failed_to_status(["costs"], c) == "FAILED_COSTS"

    def test_oos_fail(self) -> None:
        c = _make_candidate()
        assert _map_failed_to_status(["oos_split"], c) == "FAILED_OOS"

    def test_robustness_fail(self) -> None:
        c = _make_candidate()
        assert _map_failed_to_status(["robustness"], c) == "FAILED_FRAGILITY"

    def test_overfit_fail(self) -> None:
        c = _make_candidate()
        assert _map_failed_to_status(["overfit_psr"], c) == "FAILED_OVERFIT"

    def test_data_quality_fail(self) -> None:
        c = _make_candidate()
        assert _map_failed_to_status(["data_quality"], c) == "FAILED_DATA_QUALITY"

    def test_all_pass(self) -> None:
        c = _make_candidate()
        assert _map_failed_to_status([], c) == "PAPER_FORWARD_READY"


class TestBuildVerdict:
    def test_all_pass(self) -> None:
        c = _make_candidate()
        checks = [{"check_name": "costs", "passed": True}]
        v = _build_verdict(c, checks)
        assert v.hard_status == "PAPER_FORWARD_READY"

    def test_cost_fails(self) -> None:
        c = _make_candidate()
        checks = [{"check_name": "costs", "passed": False}]
        v = _build_verdict(c, checks)
        assert v.hard_status == "FAILED_COSTS"
        assert "costs" in v.failed_checks


class TestRunValidation:
    def test_bridge_unavailable(self) -> None:
        with patch(
            "src.research_lab.honest_backtest_bridge._HAS_BACKTEST_SANITY",
            False,
        ):
            c = _make_candidate()
            result = run_validation(c, Path("/tmp"), dry_run=True)
            assert result.get("bridge_unavailable") is True

    def test_dry_run_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = _make_candidate()
            result = run_validation(c, Path(td), dry_run=True)
            assert "hard_status" in result
            assert result["dry_run"] is True

    def test_apply_writes_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = _make_candidate()
            result = run_validation(c, Path(td), dry_run=False)
            assert result["dry_run"] is False
            report = Path(td) / "hard_validation" / "reports" / "c-001.json"
            verdict = Path(td) / "hard_validation" / "verdicts" / "c-001.json"
            assert report.exists()
            assert verdict.exists()

    def test_insufficient_data_writes_report_for_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = _make_candidate(trades=[], equity_curve=[])
            result = run_validation(c, Path(td), dry_run=False)
            assert result["hard_status"] == "NEEDS_MORE_DATA"
            report = Path(td) / "hard_validation" / "reports" / "c-001.json"
            verdict = Path(td) / "hard_validation" / "verdicts" / "c-001.json"
            assert report.exists()
            assert verdict.exists()
            data = json.loads(report.read_text())
            assert data["symbol"] == "BTC-USDT-SWAP"
            assert data["timeframe"] == "15m"

    def test_report_has_disclaimer(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = _make_candidate()
            run_validation(c, Path(td), dry_run=False)
            md_path = Path(td) / "hard_validation" / "reports" / "c-001.md"
            md = md_path.read_text()
            assert "not imply profitability" in md

    def test_regime_specific_maps_to_hard_status(self) -> None:
        c = _make_candidate(lite_status="REGIME_SPECIFIC")
        assert _map_failed_to_status(["forward_readiness"], c) == "REGIME_ONLY"


class TestRunValidationBatch:
    def test_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_validation_batch(
                Path(td) / "nonexistent", Path(td),
            )
            assert result["total"] == 0

    def test_processes_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            req_dir = Path(td) / "requests"
            req_dir.mkdir()
            c = _make_candidate()
            (req_dir / "c-001.json").write_text(json.dumps(c.to_dict()))
            result = run_validation_batch(req_dir, Path(td), dry_run=True)
            assert result["total"] == 1
            assert result["validated"] == 1
