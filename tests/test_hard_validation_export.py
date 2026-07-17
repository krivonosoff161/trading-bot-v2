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
    validation_id_for_unique_candidate,
)
from src.research_lab.honest_backtest_bridge import _artifact_stem
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path


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
        entry = {
            **_make_entry(),
            "timeframe": "1h",
            "filters": {"trend": ["up"]},
            "fees_bps": 9.0,
            "slippage_bps": 4.0,
        }
        c = _build_candidate(entry, Path("/tmp/nonexistent"))
        assert c is not None
        assert c.candidate_id == "c-001"
        assert c.lite_status == "FORWARD_PAPER"
        assert c.timeframe == "1h"
        assert c.filters == {"trend": ["up"]}
        assert c.fees_bps == 9.0
        assert c.slippage_bps == 4.0
        assert c.metrics["returns_basis"] == "net_pct"
        assert c.metrics["costs_applied"] is True

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

    def test_build_candidate_preserves_unique_candidate_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td)
            run_dir = private / "experiments" / "completed" / "20260614_000000_exp-001"
            run_dir.mkdir(parents=True)
            metrics = {
                "results": [{
                    "run_id": "raw-1",
                    "symbol": "BTC_USDT_SWAP",
                    "family": "trend",
                    "params": {"ma_window": 20},
                    "metrics": {"n_trades": 4, "data_file_timeframe": "1h"},
                    "trades": [{"entry_ts": 1, "exit_ts": 2, "net_pct": 1.0}],
                }],
            }
            (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
            entry = {
                **_make_entry(candidate_id="fv-1", artifact_label="20260614_000000_exp-001"),
                "source_candidate_id": "raw-1",
                "uc_key": "BTC::1h::trend::ph::fp",
                "params_hash": "ph",
                "data_fingerprint": "fp",
                "timeframe": "1h",
            }
            c = _build_candidate(entry, private)
            assert c is not None
            assert c.metrics["uc_key"] == "BTC::1h::trend::ph::fp"
            assert c.metrics["source_candidate_id"] == "raw-1"
            assert c.metrics["data_fingerprint"] == "fp"

    def test_build_candidate_fails_closed_for_unbound_legacy_aggregate_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td)
            data_dir = private / "market_data" / "1h"
            data_dir.mkdir(parents=True)
            label = "ABC_USDT_SWAP_legacy_1h.json"
            rows = []
            for i in range(80):
                price = 100.0 + i * 1.0
                rows.append({
                    "ts": 1_700_000_000_000 + i * 3_600_000,
                    "open": price,
                    "high": price + 0.2,
                    "low": price - 0.2,
                    "close": price + 0.4,
                    "vol": 1000.0,
                })
            (data_dir / label).write_text(json.dumps(rows), encoding="utf-8")
            run_dir = private / "experiments" / "completed" / "legacy_run"
            run_dir.mkdir(parents=True)
            metrics = {
                "filters": {},
                "fees_bps": 7.0,
                "slippage_bps": 3.0,
                "timeframe": "1h",
                "results": [{
                    "run_id": "raw-legacy",
                    "symbol": "ABC_USDT_SWAP",
                    "family": "momentum_breakout",
                    "params": {"lookback": 5, "hold_bars": 2, "stop_pct": 2, "take_pct": 4},
                    "metrics": {
                        "n_trades": 10,
                        "data_file_label": label,
                        "data_file_timeframe": "1h",
                    },
                }],
            }
            (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
            entry = _make_entry(
                candidate_id="fv-legacy",
                symbol="ABC_USDT_SWAP",
                strategy_id="momentum_breakout",
                artifact_label="legacy_run",
            )
            entry["params"] = {}
            c = _build_candidate(entry, private)
            assert c is not None
            assert c.params["stop_pct"] == 2
            assert c.trades == []
            assert c.data_window == {"start_ts": 0, "end_ts": 0, "n_bars": 0}
            assert c.data_window["n_bars"] == len(c.trades)


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

    def test_zero_net_return_does_not_fall_back_to_gross_pnl(self) -> None:
        curve = _build_equity_curve([{"net_pct": 0.0, "pnl_pct": 10.0, "exit_ts": 1}])
        assert curve[-1]["value"] == 10000.0


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
            req_file = private / "hard_validation" / "requests" / f"{_artifact_stem('c-001')}.json"
            assert req_file.exists()
            data = json.loads(req_file.read_text())
            assert data["candidate_id"] == "c-001"
            assert data["contract_version"] == "1.1.0"

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
            req_file = private / "hard_validation" / "requests" / f"{_artifact_stem('c-001')}.json"
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

    def test_farm_tasks_unique_candidates_are_canonical_source(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td)
            db = FarmTasksDB(tasks_db_path(private))
            uc_key = "BTC::1h::trend::ph::fp"
            db.upsert_unique_candidate({
                "uc_key": uc_key,
                "symbol": "BTC_USDT_SWAP",
                "timeframe": "1h",
                "family": "trend",
                "params_hash": "ph",
                "data_fingerprint": "fp",
                "decision": "PROMOTE_FOR_PRESSURE_TEST",
                "validation_status": "FORWARD_PAPER",
                "hard_status": "",
                "candidate_id": "raw-candidate",
                "run_dir_label": "",
                "n_trades": 12,
                "avg_net_pct": 0.4,
            })
            db.close()
            summary = export_requests(private, dry_run=False, source="auto")
            vid = validation_id_for_unique_candidate({"uc_key": uc_key})
            assert summary["source"] == "farm_tasks"
            assert summary["exported_ids"] == [vid]
            req = json.loads((
                private / "hard_validation" / "requests" / f"{_artifact_stem(vid)}.json"
            ).read_text())
            assert req["candidate_id"] == vid
            assert req["metrics"]["source_candidate_id"] == "raw-candidate"
            assert req["metrics"]["uc_key"] == uc_key

    def test_farm_tasks_validation_id_prevents_candidate_id_collision(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            private = Path(td)
            db = FarmTasksDB(tasks_db_path(private))
            for tf, fp in (("1h", "fp1"), ("4h", "fp2")):
                uc_key = f"BTC::{tf}::trend::ph::{fp}"
                db.upsert_unique_candidate({
                    "uc_key": uc_key,
                    "symbol": "BTC_USDT_SWAP",
                    "timeframe": tf,
                    "family": "trend",
                    "params_hash": "ph",
                    "data_fingerprint": fp,
                    "decision": "PROMOTE_FOR_PRESSURE_TEST",
                    "validation_status": "FORWARD_PAPER",
                    "hard_status": "",
                    "candidate_id": "same-raw-id",
                    "run_dir_label": "",
                })
            db.close()
            summary = export_requests(private, dry_run=False, source="farm_tasks", limit=10)
            assert summary["exported"] == 2
            assert len(set(summary["exported_ids"])) == 2
