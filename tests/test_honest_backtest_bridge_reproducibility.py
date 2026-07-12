# -*- coding: utf-8 -*-
"""Phase 0.1 — honest-backtest reproducibility and fail-loud behavior.

These tests guard against the silent-degradation hole: when the statistical
engine is absent, validation must FAIL LOUD (raise) rather than masquerade as
an ordinary NEEDS_MORE_DATA verdict — unless degraded mode is explicitly opted in.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.research_lab import honest_backtest_bridge as bridge
from src.research_lab.hard_validation_contract import CandidateForValidation, trade_evidence_hash
from src.research_lab.honest_backtest_bridge import (
    BridgeUnavailableError,
    bridge_available,
    ensure_bridge_available,
    run_validation,
    run_validation_batch,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_CANDIDATE_DICT = {
    "contract_version": "1.1.0",
    "candidate_id": "c-repro",
    "source_run_id": "run-repro",
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
    "metrics": {
        "n_trades": 5, "data_fingerprint": "sha256:evaluation",
        "returns_basis": "net_pct", "costs_applied": True,
        "validation_epoch": {
            "schema": "ValidationEpoch.v1", "evidence_stage": "untouched_evaluation",
            "selection_data_fingerprint": "sha256:selection",
            "evaluation_data_fingerprint": "sha256:evaluation",
            "hypothesis_frozen_at": "2026-06-19T00:00:00+00:00",
            "evaluation_started_at": "2026-06-20T00:00:00+00:00",
        },
    },
    "trades": [
        {"side": "long", "net_pct": 1.0,
         "entry_ts": f"2026-06-20T00:0{i}:00+00:00",
         "exit_ts": f"2026-06-20T00:0{i}:30+00:00"}
        for i in range(5)
    ],
    "equity_curve": [],
    "data_window": {"start_ts": 0, "end_ts": 5, "n_bars": 5},
    "created_at": "2026-06-20T00:00:00Z",
}
_CANDIDATE_DICT["metrics"]["validation_epoch"]["selection_evidence"] = [
    {"side": "short", "net_pct": 0.5,
     "entry_ts": "2026-06-18T23:00:00+00:00",
     "exit_ts": "2026-06-19T00:00:00+00:00"}
]
_CANDIDATE_DICT["metrics"]["validation_epoch"]["selection_evidence_hash"] = trade_evidence_hash(
    _CANDIDATE_DICT["metrics"]["validation_epoch"]["selection_evidence"]
)
_CANDIDATE_DICT["metrics"]["validation_epoch"]["evaluation_evidence_hash"] = trade_evidence_hash(
    _CANDIDATE_DICT["trades"]
)


def _candidate() -> CandidateForValidation:
    return CandidateForValidation.from_dict(_CANDIDATE_DICT)


class TestVendoredCopy:
    def test_vendored_package_exists_in_repo(self) -> None:
        init = REPO_ROOT / "vendor" / "honest-backtest" / "src" / "backtest_sanity" / "__init__.py"
        assert init.exists(), "vendored backtest_sanity must be committed for reproducibility"

    def test_vendored_package_imports_standalone(self) -> None:
        # Importing directly from the vendored path must work with no editable install.
        import importlib
        import sys

        vendored_src = REPO_ROOT / "vendor" / "honest-backtest" / "src"
        added = False
        if str(vendored_src) not in sys.path:
            sys.path.insert(0, str(vendored_src))
            added = True
        try:
            mod = importlib.import_module("backtest_sanity")
            # The multiple-testing helpers Phase 1.2 relies on must be present.
            for name in ("deflated_sharpe_ratio", "benjamini_hochberg", "bonferroni"):
                assert hasattr(mod, name)
        finally:
            if added:
                sys.path.remove(str(vendored_src))

    def test_bridge_reports_available_in_this_env(self) -> None:
        status = bridge_available()
        assert status["available"] is True
        assert status["numpy"] is True
        assert status["backtest_sanity"] is True


class TestFailLoud:
    def test_ensure_raises_when_engine_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("STRATEGY_LAB_ALLOW_DEGRADED_VALIDATION", raising=False)
        monkeypatch.setattr(bridge, "_HAS_BACKTEST_SANITY", False)
        with pytest.raises(BridgeUnavailableError):
            ensure_bridge_available()

    def test_ensure_strict_true_raises_even_with_override(self, monkeypatch) -> None:
        monkeypatch.setattr(bridge, "_HAS_BACKTEST_SANITY", False)
        with pytest.raises(BridgeUnavailableError):
            ensure_bridge_available(strict=True)

    def test_run_validation_fails_loud(self, monkeypatch) -> None:
        monkeypatch.delenv("STRATEGY_LAB_ALLOW_DEGRADED_VALIDATION", raising=False)
        monkeypatch.setattr(bridge, "_HAS_BACKTEST_SANITY", False)
        with tempfile.TemporaryDirectory() as td:
            with pytest.raises(BridgeUnavailableError):
                run_validation(_candidate(), Path(td), dry_run=True)

    def test_run_validation_batch_fails_loud(self, monkeypatch) -> None:
        monkeypatch.delenv("STRATEGY_LAB_ALLOW_DEGRADED_VALIDATION", raising=False)
        monkeypatch.setattr(bridge, "_HAS_BACKTEST_SANITY", False)
        with tempfile.TemporaryDirectory() as td:
            with pytest.raises(BridgeUnavailableError):
                run_validation_batch(Path(td), Path(td), dry_run=True)


class TestDegradedOptIn:
    def test_override_allows_needs_more_data(self, monkeypatch) -> None:
        monkeypatch.setenv("STRATEGY_LAB_ALLOW_DEGRADED_VALIDATION", "1")
        monkeypatch.setattr(bridge, "_HAS_BACKTEST_SANITY", False)
        # No raise expected now.
        ensure_bridge_available()
        with tempfile.TemporaryDirectory() as td:
            result = run_validation(_candidate(), Path(td), dry_run=True)
        assert result["hard_status"] == "NEEDS_MORE_DATA"
        assert result.get("bridge_unavailable") is True
