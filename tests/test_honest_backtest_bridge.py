# -*- coding: utf-8 -*-
"""Tests for honest_backtest_bridge.py — Phase 3."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.research_lab.hard_validation_contract import (
    CandidateForValidation,
    trade_evidence_hash,
    validation_evidence_hash,
)
from src.research_lab.honest_backtest_bridge import (
    _build_verdict,
    _artifact_stem,
    _candidate_contract_errors,
    _check_costs,
    _check_data_quality,
    _check_forward_readiness,
    _check_independent_evaluation,
    _check_overfit,
    _check_return_concentration,
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
    "contract_version": "1.1.0",
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
    "metrics": {"n_trades": 20, "profit_factor": 1.5,
                "data_fingerprint": "sha256:evaluation", "returns_basis": "net_pct",
                "costs_applied": True,
                "validation_epoch": {
                    "schema": "ValidationEpoch.v1",
                    "evidence_stage": "untouched_evaluation",
                    "selection_data_fingerprint": "sha256:selection",
                    "evaluation_data_fingerprint": "sha256:evaluation",
                    "hypothesis_frozen_at": "2026-07-01T00:00:00+00:00",
                    "evaluation_started_at": "2026-07-02T00:00:00+00:00",
                }},
    "trades": [
        {"side": "long", "entry_price": 100, "exit_price": 103,
         "entry_ts": "2026-07-02T00:00:00+00:00",
         "exit_ts": "2026-07-02T00:00:30+00:00", "net_pct": 2.0},
        {"side": "short", "entry_price": 50, "exit_price": 48,
         "entry_ts": "2026-07-02T00:01:00+00:00",
         "exit_ts": "2026-07-02T00:01:30+00:00", "net_pct": 3.5},
    ] + [
        {"side": "long", "entry_price": 100, "exit_price": 99,
         "entry_ts": f"2026-07-02T00:{i:02d}:00+00:00",
         "exit_ts": f"2026-07-02T00:{i:02d}:30+00:00",
         "net_pct": -0.5}
        for i in range(2, 20)
    ],
    "equity_curve": [],
    "data_window": {"start_ts": 0, "end_ts": 20000, "n_bars": 20},
    "created_at": "2026-06-14T00:00:00Z",
}
CANDIDATE_DICT["metrics"]["validation_epoch"]["selection_evidence_hash"] = "0" * 64
CANDIDATE_DICT["metrics"]["validation_epoch"]["selection_evidence"] = [
    {"entry_ts": "2026-06-30T23:00:00+00:00",
     "exit_ts": "2026-07-01T00:00:00+00:00", "net_pct": 0.25, "side": "long"}
]
CANDIDATE_DICT["metrics"]["validation_epoch"]["selection_evidence_hash"] = trade_evidence_hash(
    CANDIDATE_DICT["metrics"]["validation_epoch"]["selection_evidence"]
)
CANDIDATE_DICT["metrics"]["validation_epoch"]["evaluation_evidence_hash"] = trade_evidence_hash(
    CANDIDATE_DICT["trades"]
)


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
        metrics = {**CANDIDATE_DICT["metrics"], "returns_basis": "gross_pct",
                   "costs_applied": False}
        c = _make_candidate(fees_bps=100, slippage_bps=100, metrics=metrics)
        returns = [0.01] * 10
        result = _check_costs(c, returns)
        assert result["passed"] is False

    def test_net_returns_do_not_pay_costs_twice(self) -> None:
        c = _make_candidate(fees_bps=7, slippage_bps=3)
        result = _check_costs(c, [0.05] * 10)
        assert result["passed"] is True
        assert result["details"]["net_mean"] == pytest.approx(0.05)
        assert result["details"]["gross_mean"] == pytest.approx(0.15)

    def test_zero_net_return_does_not_fall_back_to_gross_pnl(self) -> None:
        candidate = _make_candidate(trades=[{"net_pct": 0.0, "pnl_pct": 0.1}])
        assert _extract_returns(candidate) == [0.0]

    def test_gross_returns_pay_declared_cost_once(self) -> None:
        metrics = {**CANDIDATE_DICT["metrics"], "returns_basis": "gross_pct",
                   "costs_applied": False}
        result = _check_costs(_make_candidate(metrics=metrics), [0.15] * 10)
        assert result["details"]["net_mean"] == pytest.approx(0.05)


class TestIndependentEvaluation:
    def test_distinct_later_epoch_passes(self) -> None:
        assert _check_independent_evaluation(_make_candidate())["passed"] is True

    def test_selection_series_cannot_validate_itself(self) -> None:
        metrics = {**CANDIDATE_DICT["metrics"], "validation_epoch": {
            "schema": "ValidationEpoch.v1", "evidence_stage": "selection_only",
            "selection_data_fingerprint": "same", "evaluation_data_fingerprint": "same",
            "selection_evidence_hash": "same-hash", "evaluation_evidence_hash": "same-hash",
            "hypothesis_frozen_at": "2026-07-01T00:00:00+00:00",
            "evaluation_started_at": "2026-07-01T00:00:00+00:00",
        }}
        result = _check_independent_evaluation(_make_candidate(metrics=metrics))
        assert result["passed"] is False
        assert "evaluation_reuses_selection_data" in result["details"]["errors"]

    def test_evaluation_hash_must_match_actual_trade_evidence(self) -> None:
        epoch = {**CANDIDATE_DICT["metrics"]["validation_epoch"],
                 "evaluation_evidence_hash": "f" * 64}
        metrics = {**CANDIDATE_DICT["metrics"], "validation_epoch": epoch}
        result = _check_independent_evaluation(_make_candidate(metrics=metrics))
        assert result["passed"] is False
        assert "evaluation_evidence_hash_mismatch" in result["details"]["errors"]

    def test_selection_hash_must_bind_embedded_selection_evidence(self) -> None:
        epoch = {**CANDIDATE_DICT["metrics"]["validation_epoch"],
                 "selection_evidence_hash": "0" * 64}
        metrics = {**CANDIDATE_DICT["metrics"], "validation_epoch": epoch}
        result = _check_independent_evaluation(_make_candidate(metrics=metrics))
        assert result["passed"] is False
        assert "selection_evidence_hash_mismatch" in result["details"]["errors"]

    def test_equity_only_evaluation_hash_binds_equity_content(self) -> None:
        curve = [
            {"ts": "2026-07-02T00:00:00+00:00", "value": 100.0},
            {"ts": "2026-07-02T00:01:00+00:00", "value": 101.0},
        ]
        epoch = {
            **CANDIDATE_DICT["metrics"]["validation_epoch"],
            "evaluation_evidence_hash": validation_evidence_hash([], curve),
        }
        metrics = {**CANDIDATE_DICT["metrics"], "validation_epoch": epoch}
        candidate = _make_candidate(trades=[], equity_curve=curve, metrics=metrics)
        assert _check_independent_evaluation(candidate)["passed"] is True
        changed = _make_candidate(
            trades=[], equity_curve=[
                {"ts": "2026-07-02T00:00:00+00:00", "value": 100.0},
                {"ts": "2026-07-02T00:01:00+00:00", "value": 99.0},
            ],
            metrics=metrics,
        )
        assert _check_independent_evaluation(changed)["passed"] is False

    def test_return_extraction_honors_gross_basis_before_costs(self) -> None:
        metrics = {**CANDIDATE_DICT["metrics"], "returns_basis": "gross_pct",
                   "costs_applied": False}
        candidate = _make_candidate(metrics=metrics, trades=[{"pnl_pct": 0.15, "net_pct": 0.05}])
        assert _extract_returns(candidate) == [0.15]

    @pytest.mark.parametrize(
        ("basis", "trade"),
        [("gross_pct", {"net_pct": 0.05}), ("net_pct", {"pnl_pct": 0.15})],
    )
    def test_missing_basis_owned_return_field_fails_closed(self, basis, trade) -> None:
        metrics = {
            **CANDIDATE_DICT["metrics"], "returns_basis": basis,
            "costs_applied": basis == "net_pct",
        }
        candidate = _make_candidate(metrics=metrics, trades=[trade])
        assert "returns_basis_field_missing" in _candidate_contract_errors(candidate)


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

    def test_malformed_available_dsr_evidence_fails_closed(self) -> None:
        metrics = {
            **CANDIDATE_DICT["metrics"],
            "trial_sharpes": [1.0, float("nan")],
        }
        returns = [0.5, 1.0, 0.8, 1.2, 0.9, 1.1] * 4
        result = _check_overfit(_make_candidate(metrics=metrics), returns)
        assert result["passed"] is False
        assert "dsr_error" in result["details"]

    def test_malformed_available_pbo_evidence_fails_closed(self) -> None:
        metrics = {
            **CANDIDATE_DICT["metrics"],
            "trial_returns": [[0.1, 0.2]],
        }
        returns = [0.5, 1.0, 0.8, 1.2, 0.9, 1.1] * 4
        result = _check_overfit(_make_candidate(metrics=metrics), returns)
        assert result["passed"] is False
        assert "pbo_error" in result["details"]


class TestReturnConcentration:
    def test_diversified_edge_passes(self) -> None:
        assert _check_return_concentration([0.2] * 10)["passed"] is True

    def test_single_trade_dominance_fails(self) -> None:
        result = _check_return_concentration([10.0] + [-0.5] * 9)
        assert result["passed"] is False
        assert result["details"]["leave_best_out_mean"] < 0


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
    def test_untrusted_candidate_id_is_encoded_for_artifact_paths(self) -> None:
        stem = _artifact_stem(r"..\\..\\outside")
        assert ".." not in stem
        assert "\\" not in stem
        assert "/" not in stem

    def test_long_candidate_ids_keep_collision_resistant_suffix(self) -> None:
        first = _artifact_stem("a" * 180 + "x")
        second = _artifact_stem("a" * 180 + "y")
        assert first != second
        assert len(first) < 180

    def test_windows_reserved_path_forms_are_digest_only(self) -> None:
        assert _artifact_stem("candidate:stream").startswith("candidate_")
        assert _artifact_stem("CON").startswith("candidate_")
        assert _artifact_stem("A") != _artifact_stem("a")
        assert _artifact_stem("abc") != _artifact_stem("abc.")

    def test_missing_provenance_fails_closed(self) -> None:
        c = _make_candidate(metrics={"n_trades": 20, "returns_basis": "net_pct",
                                     "costs_applied": True})
        result = run_validation(c, Path("/tmp"), dry_run=True)
        assert result["hard_status"] == "FAILED_DATA_QUALITY"
        assert result["checks_failed"] == 1

    def test_bridge_unavailable_fails_loud_by_default(self) -> None:
        # Phase 0.1: missing engine must raise, not silently degrade.
        import pytest

        from src.research_lab.honest_backtest_bridge import BridgeUnavailableError

        with patch.dict("os.environ", {}, clear=False) as _env:
            os.environ.pop("STRATEGY_LAB_ALLOW_DEGRADED_VALIDATION", None)
            with patch(
                "src.research_lab.honest_backtest_bridge._HAS_BACKTEST_SANITY",
                False,
            ):
                c = _make_candidate()
                with pytest.raises(BridgeUnavailableError):
                    run_validation(c, Path("/tmp"), dry_run=True)

    def test_bridge_unavailable_degraded_opt_in(self) -> None:
        with patch.dict(
            "os.environ",
            {"STRATEGY_LAB_ALLOW_DEGRADED_VALIDATION": "1"},
            clear=False,
        ):
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
            stem = _artifact_stem("c-001")
            report = Path(td) / "hard_validation" / "reports" / f"{stem}.json"
            verdict = Path(td) / "hard_validation" / "verdicts" / f"{stem}.json"
            assert report.exists()
            assert verdict.exists()

    def test_insufficient_data_writes_report_for_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = _make_candidate(trades=[], equity_curve=[])
            result = run_validation(c, Path(td), dry_run=False)
            assert result["hard_status"] == "NEEDS_MORE_DATA"
            stem = _artifact_stem("c-001")
            report = Path(td) / "hard_validation" / "reports" / f"{stem}.json"
            verdict = Path(td) / "hard_validation" / "verdicts" / f"{stem}.json"
            assert report.exists()
            assert verdict.exists()
            data = json.loads(report.read_text())
            assert data["symbol"] == "BTC-USDT-SWAP"
            assert data["timeframe"] == "15m"

    def test_report_has_disclaimer(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = _make_candidate()
            run_validation(c, Path(td), dry_run=False)
            md_path = (
                Path(td) / "hard_validation" / "reports" / f"{_artifact_stem('c-001')}.md"
            )
            md = md_path.read_text()
            assert "not imply profitability" in md

    def test_regime_specific_maps_to_hard_status(self) -> None:
        c = _make_candidate(lite_status="REGIME_SPECIFIC")
        assert _map_failed_to_status(["forward_readiness"], c) == "REGIME_ONLY"


class TestRunValidationBatch:
    def test_missing_contract_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            req_dir = Path(td) / "requests"
            req_dir.mkdir()
            data = _make_candidate().to_dict()
            data.pop("contract_version")
            (req_dir / "c-001.json").write_text(json.dumps(data))
            result = run_validation_batch(req_dir, Path(td), dry_run=True)
            assert result["validated"] == 0
            assert result["errors"] == 1
            assert "contract_version" in result["results"][0]["error"]

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
