# -*- coding: utf-8 -*-
"""End-to-end smoke test — Phase 10.

Full pipeline with temp private root and synthetic data.
Tests the complete product path from candidate to setup card.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.research_lab.hard_validation_contract import (
    HardValidationReport,
)
from src.research_lab.hard_validation_export import export_requests
from src.research_lab.honest_backtest_bridge import run_validation_batch
from src.research_lab.setup_library import build_setup_card, write_setup_library
from src.research_lab.validation_feedback import generate_feedback, write_feedback


def _seed_candidate(private_root: Path, status: str = "FORWARD_PAPER") -> dict:
    """Create a synthetic candidate in the registry."""
    reg_dir = private_root / "candidate-registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "candidate_id": "smoke-001",
        "experiment_id": "smoke-exp",
        "symbol": "BTC-USDT-SWAP",
        "strategy_id": "trend",
        "params": {"ma_window": 20, "hold_bars": 10},
        "metrics_summary": {"n_trades": 30, "profit_factor": 1.8, "avg_net_pct": 0.5},
        "decision": "PROMOTE_FOR_PRESSURE_TEST",
        "validation_status": status,
        "validation_reasons": ["passed_lite_validation"],
        "risk_flags": [],
        "artifact_label": "20260614_smoke-exp",
        "created_at": "2026-06-14T00:00:00Z",
    }
    (reg_dir / "candidates.jsonl").write_text(json.dumps(entry))
    return entry


def _seed_experiment_output(private_root: Path) -> None:
    """Create synthetic experiment output with trades."""
    run_dir = private_root / "experiments" / "completed" / "20260614_smoke-exp"
    run_dir.mkdir(parents=True)
    trades = [
        {"entry_ts": i * 1000, "exit_ts": (i + 1) * 1000,
         "net_pct": 0.5 if i % 3 != 0 else -0.3, "side": "long"}
        for i in range(30)
    ]
    metrics = {
        "schema": "strategy_lab_results.v1",
        "results": [{
            "run_id": "smoke-001",
            "symbol": "BTC-USDT-SWAP",
            "family": "trend",
            "params": {"ma_window": 20, "hold_bars": 10},
            "metrics": {"n_trades": 30, "profit_factor": 1.8,
                        "data_file_timeframe": "15m"},
            "trades": trades,
        }],
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics))


def test_full_pipeline_smoke() -> None:
    """End-to-end: seed → export → validate → feedback → setup card → report."""
    with tempfile.TemporaryDirectory() as td:
        private = Path(td)

        # Seed data
        _seed_candidate(private, "FORWARD_PAPER")
        _seed_experiment_output(private)

        # Step 1: Export
        export_summary = export_requests(private, dry_run=False, limit=5)
        assert export_summary["exported"] == 1
        req_file = private / "hard_validation" / "requests" / "smoke-001.json"
        assert req_file.exists()

        # Step 2: Validate
        val_summary = run_validation_batch(
            private / "hard_validation" / "requests",
            private, dry_run=False, limit=5,
        )
        assert val_summary["validated"] == 1
        verdict_file = private / "hard_validation" / "verdicts" / "smoke-001.json"
        report_file = private / "hard_validation" / "reports" / "smoke-001.json"
        assert verdict_file.exists()
        assert report_file.exists()

        # Verify report has disclaimer
        report_md = private / "hard_validation" / "reports" / "smoke-001.md"
        md_text = report_md.read_text()
        assert "not imply profitability" in md_text

        # Step 3: Feedback
        report_data = json.loads(report_file.read_text())
        report = HardValidationReport.from_dict(report_data)
        fb = generate_feedback(report)
        if fb:
            write_feedback(private, fb, dry_run=False)
            fb_file = private / "hard_validation" / "feedback" / "feedback.jsonl"
            assert fb_file.exists()

        # Step 4: Setup card
        card = build_setup_card(report_data)
        assert card.main_engine_ready is False
        lib_summary = write_setup_library(private, [card], dry_run=False)
        assert lib_summary["cards_written"] == 1

        # Verify setup library structure
        lib_dir = private / "setup_library"
        assert (lib_dir / "cards" / "setup-smoke-001.json").exists()
        assert (lib_dir / "reports" / "setup-smoke-001.md").exists()
        assert (lib_dir / "setup_index.jsonl").exists()
        assert (lib_dir / "by_symbol" / "BTC-USDT-SWAP").exists()
        assert (lib_dir / "by_timeframe" / "15m").exists()
        assert (lib_dir / "by_strategy" / "trend").exists()

        # Verify no private artifacts in public repo
        # (we only wrote to temp dir, not to trading-bot-v2)
        public_files = list(
            Path(__file__).resolve().parents[1].glob(
                "hard_validation/**/*.json"
            )
        )
        # Should find nothing in public repo
        assert len(public_files) == 0


def test_smoke_no_candidates() -> None:
    """Smoke test with empty registry — should handle gracefully."""
    with tempfile.TemporaryDirectory() as td:
        private = Path(td)
        reg_dir = private / "candidate-registry"
        reg_dir.mkdir(parents=True)
        (reg_dir / "candidates.jsonl").write_text("")

        export_summary = export_requests(private, dry_run=False)
        assert export_summary["eligible_found"] == 0

        val_summary = run_validation_batch(
            private / "hard_validation" / "requests",
            private, dry_run=False,
        )
        assert val_summary["total"] == 0


def test_smoke_reject_candidate_not_exported() -> None:
    """REJECT candidates should not be exported."""
    with tempfile.TemporaryDirectory() as td:
        private = Path(td)
        _seed_candidate(private, "REJECT")

        export_summary = export_requests(private, dry_run=False)
        assert export_summary["eligible_found"] == 0


def test_smoke_dry_run_writes_nothing() -> None:
    """Full pipeline in dry-run mode writes nothing."""
    with tempfile.TemporaryDirectory() as td:
        private = Path(td)
        _seed_candidate(private)
        _seed_experiment_output(private)

        export_summary = export_requests(private, dry_run=True)
        assert export_summary["exported"] == 0

        # No hard_validation directory should exist
        assert not (private / "hard_validation").exists()
