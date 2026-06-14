# -*- coding: utf-8 -*-
"""Tests for hard_validation_export.py — Phase 2."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.research_lab.hard_validation_export import (
    _build_candidate,
    _build_data_window,
    _build_equity_curve,
    _deduplicate,
    _filter_entries,
    export_requests,
)


def _make_entry(
    candidate_id: str = "c-001",
    status: str = "FORWARD_PAPER",
    symbol: str = "BTC-USDT-SWAP",
    strategy_id: str = "trend",
    created_at: str = "2026-06-14T00:00:00Z",
    artifact_label: str = "",
) -> dict:
    return {
        "candidate_id": candidate_id,
        "experiment_id": "exp-001",
        "symbol": symbol,
        "strategy_id": strategy_id,
        "params": {"ma_window": 20},
        "metrics_summary": {"n_trades": 42, "profit_factor": 1.5},
        "decision": "PROMOTE_FOR_PRESSURE_TEST",
        "validation_status": status,
        "validation_reasons": ["passed_lite_validation"],
        "risk_flags": [],
        "artifact_label": artifact_label,
        "created_at": created_at,
    }


class TestFilterEntries:
    def test_forward_paper_included(self) -> None:
        entries = [_make_entry(status="FORWARD_PAPER")]
        result = _filter_entries(entries)
        assert len(result) == 1

    def test_regime_specific_excluded_by_default(self) -> None:
        entries = [_make_entry(status="REGIME_SPECIFIC")]
        result = _filter_entries(entries)
        assert len(result) == 0

    def test_regime_specific_included_when_flag(self) -> None:
        entries = [_make_entry(status="REGIME_SPECIFIC")]
        result = _filter_entries(entries, include_regime_specific=True)
        assert len(result) == 1

    def test_reject_excluded(self) -> None:
        entries = [_make_entry(status="REJECT")]
        result = _filter_entries(entries)
        assert len(result) == 0

    def test_observe_excluded(self) -> None:
        entries = [_make_entry(status="OBSERVE")]
        result = _filter_entries(entries)
        assert len(result) == 0

    def test_status_filter_overrides(self) -> None:
        entries = [
            _make_entry(status="FORWARD_PAPER"),
            _make_entry(status="REGIME_SPECIFIC"),
        ]
        result = _filter_entries(entries, status_filter="REGIME_SPECIFIC")
        assert len(result) == 1
        assert result[0]["validation_status"] == "REGIME_SPECIFIC"

    def test_candidate_id_filter(self) -> None:
        entries = [_make_entry(candidate_id="c-001"), _make_entry(candidate_id="c-002")]
        result = _filter_entries(entries, candidate_id="c-001")
        assert len(result) == 1
        assert result[0]["candidate_id"] == "c-001"

    def test_since_filter(self) -> None:
        entries = [
            _make_entry(created_at="2026-06-10T00:00:00Z"),
            _make_entry(created_at="2026-06-14T00:00:00Z"),
        ]
        result = _filter_entries(entries, since="2026-06-12T00:00:00Z")
        assert len(result) == 1


class TestDeduplicate:
    def test_no_dedup_needed(self) -> None:
        entries = [_make_entry(candidate_id="c-001"), _make_entry(candidate_id="c-002")]
        result = _deduplicate(entries)
        assert len(result) == 2

    def test_dedup_by_key(self) -> None:
        entries = [
            _make_entry(candidate_id="c-001", symbol="BTC-USDT-SWAP"),
            _make_entry(candidate_id="c-001", symbol="BTC-USDT-SWAP"),
        ]
        result = _deduplicate(entries)
        assert len(result) == 1

    def test_different_symbols_not_deduped(self) -> None:
        entries = [
            _make_entry(candidate_id="c-001", symbol="BTC-USDT-SWAP"),
            _make_entry(candidate_id="c-001", symbol="ETH-USDT-SWAP"),
        ]
        result = _deduplicate(entries)
        assert len(result) == 2


class TestBuildCandidate:
    def test_builds_candidate_from_entry_no_artifact(self) -> None:
        entry = _make_entry()
        c = _build_candidate(entry, Path("/tmp/nonexistent"))
        assert c is not None
        assert c.candidate_id == "c-001"
        assert c.lite_status == "FORWARD_PAPER"

    def test_builds_candidate_with_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td)
            run_dir = private / "experiments" / "completed" / "20260614_000000_exp-001"
            run_dir.mkdir(parents=True)
            metrics = {
                "schema": "strategy_lab_results.v1",
                "results": [
                    {
                        "run_id": "c-001",
                        "symbol": "BTC-USDT-SWAP",
                        "family": "trend",
                        "params": {"ma_window": 20},
                        "metrics": {
                            "n_trades": 10,
                            "profit_factor": 2.0,
                            "data_file_timeframe": "15m",
                        },
                        "trades": [
                            {"entry_ts": 1000, "exit_ts": 2000,
                             "net_pct": 1.5, "side": "long"},
                            {"entry_ts": 3000, "exit_ts": 4000,
                             "net_pct": -0.5, "side": "short"},
                        ],
                    }
                ],
            }
            (run_dir / "metrics.json").write_text(json.dumps(metrics))
            entry = _make_entry(artifact_label="20260614_000000_exp-001")
            c = _build_candidate(entry, private)
            assert c is not None
            assert len(c.trades) == 2
            assert len(c.equity_curve) == 3
            assert c.timeframe == "15m"


class TestEquityCurve:
    def test_empty_trades(self) -> None:
        assert _build_equity_curve([]) == []

    def test_equity_grows(self) -> None:
        trades = [
            {"net_pct": 1.0, "exit_ts": 100},
            {"net_pct": -0.5, "exit_ts": 200},
        ]
        curve = _build_equity_curve(trades)
        assert len(curve) == 3
        assert curve[0]["value"] == 10000.0
        assert curve[1]["value"] == 10100.0
        assert curve[2]["value"] == 10049.5


class TestDataWindow:
    def test_empty_trades(self) -> None:
        dw = _build_data_window([])
        assert dw["n_bars"] == 0

    def test_window_from_trades(self) -> None:
        trades = [
            {"entry_ts": 100, "exit_ts": 200},
            {"entry_ts": 300, "exit_ts": 400},
        ]
        dw = _build_data_window(trades)
        assert dw["start_ts"] == 100
        assert dw["end_ts"] == 400
        assert dw["n_bars"] == 2


class TestExportRequests:
    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td)
            reg_dir = private / "candidate-registry"
            reg_dir.mkdir(parents=True)
            entry = _make_entry()
            (reg_dir / "candidates.jsonl").write_text(json.dumps(entry))
            summary = export_requests(private, dry_run=True)
            assert summary["exported"] == 0
            assert summary["eligible_found"] == 1

    def test_apply_writes_requests(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td)
            reg_dir = private / "candidate-registry"
            reg_dir.mkdir(parents=True)
            entry = _make_entry()
            (reg_dir / "candidates.jsonl").write_text(json.dumps(entry))
            summary = export_requests(private, dry_run=False)
            assert summary["exported"] == 1
            req_file = private / "hard_validation" / "requests" / "c-001.json"
            assert req_file.exists()
            data = json.loads(req_file.read_text())
            assert data["candidate_id"] == "c-001"
            assert data["contract_version"] == "1.0.0"

    def test_no_candidates_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td)
            reg_dir = private / "candidate-registry"
            reg_dir.mkdir(parents=True)
            (reg_dir / "candidates.jsonl").write_text("")
            summary = export_requests(private)
            assert summary["eligible_found"] == 0

    def test_limit_caps_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td)
            reg_dir = private / "candidate-registry"
            reg_dir.mkdir(parents=True)
            entries = [_make_entry(candidate_id=f"c-{i:03d}") for i in range(5)]
            (reg_dir / "candidates.jsonl").write_text(
                "\n".join(json.dumps(e) for e in entries)
            )
            summary = export_requests(private, dry_run=False, limit=2)
            assert summary["exported"] == 2

    def test_no_absolute_paths_in_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td)
            reg_dir = private / "candidate-registry"
            reg_dir.mkdir(parents=True)
            entry = _make_entry()
            (reg_dir / "candidates.jsonl").write_text(json.dumps(entry))
            export_requests(private, dry_run=False)
            req_file = private / "hard_validation" / "requests" / "c-001.json"
            raw = req_file.read_text()
            assert "C:\\" not in raw
            assert "krivo" not in raw

    def test_only_eligible_exported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td)
            reg_dir = private / "candidate-registry"
            reg_dir.mkdir(parents=True)
            entries = [
                _make_entry(candidate_id="c-fp", status="FORWARD_PAPER"),
                _make_entry(candidate_id="c-rej", status="REJECT"),
                _make_entry(candidate_id="c-obs", status="OBSERVE"),
            ]
            (reg_dir / "candidates.jsonl").write_text(
                "\n".join(json.dumps(e) for e in entries)
            )
            summary = export_requests(private, dry_run=False)
            assert summary["exported"] == 1
            req_dir = private / "hard_validation" / "requests"
            files = list(req_dir.glob("*.json"))
            assert len(files) == 1
