# -*- coding: utf-8 -*-
"""Tests for setup_library.py — Phase 5."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.research_lab.setup_library import (
    _card_to_markdown,
    _entry_exit_summary,
    _extract_regime_tags,
    build_setup_card,
    write_setup_library,
)


def _make_report_dict(
    hard_status: str = "PAPER_FORWARD_READY",
    failed_checks: list[str] | None = None,
) -> dict:
    return {
        "candidate_id": "c-001",
        "source_run_id": "run-abc",
        "symbol": "BTC-USDT-SWAP",
        "timeframe": "15m",
        "strategy_id": "momentum_breakout",
        "verdict": {
            "candidate_id": "c-001",
            "hard_status": hard_status,
            "checks": [],
            "failed_checks": failed_checks or [],
        },
        "checks_summary": {"total": 5, "passed": 3, "failed": 2},
    }


def _make_candidate_dict() -> dict:
    return {
        "params": {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        "filters": {"min_vol_ratio": 1.0},
        "data_window": {"start_ts": 0, "end_ts": 1000, "n_bars": 50},
        "lite_status": "FORWARD_PAPER",
        "risk_flags": [],
        "regime_summary": {"dominant_bucket": "trending"},
    }


class TestBuildSetupCard:
    def test_basic_card(self) -> None:
        report = _make_report_dict()
        card = build_setup_card(report)
        assert card.candidate_id == "c-001"
        assert card.hard_status == "PAPER_FORWARD_READY"
        assert card.main_engine_ready is False
        assert card.paper_forward_ready is False

    def test_with_candidate(self) -> None:
        report = _make_report_dict()
        candidate = _make_candidate_dict()
        card = build_setup_card(report, candidate)
        assert card.params["lookback"] == 20
        assert card.regime_tags == ["trending"]
        assert card.paper_forward_ready is True

    def test_main_engine_ready_always_false(self) -> None:
        report = _make_report_dict()
        card = build_setup_card(report)
        d = card.to_dict()
        card2 = type(card).from_dict(d)
        assert card2.main_engine_ready is False

    def test_idempotent_upsert(self) -> None:
        report = _make_report_dict()
        card1 = build_setup_card(report)
        card2 = build_setup_card(report)
        assert card1.setup_id == card2.setup_id


class TestWriteSetupLibrary:
    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            card = build_setup_card(_make_report_dict())
            summary = write_setup_library(Path(td), [card], dry_run=True)
            assert summary["cards_written"] == 0

    def test_apply_writes_cards(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            card = build_setup_card(_make_report_dict())
            summary = write_setup_library(Path(td), [card], dry_run=False)
            assert summary["cards_written"] == 1
            lib_dir = Path(td) / "setup_library"
            assert (lib_dir / "cards" / "setup-c-001.json").exists()
            assert (lib_dir / "reports" / "setup-c-001.md").exists()
            assert (lib_dir / "setup_index.jsonl").exists()

    def test_groups_by_symbol_timeframe_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            card = build_setup_card(_make_report_dict())
            write_setup_library(Path(td), [card], dry_run=False)
            lib_dir = Path(td) / "setup_library"
            assert (lib_dir / "by_symbol" / "BTC-USDT-SWAP").exists()
            assert (lib_dir / "by_timeframe" / "15m").exists()
            assert (lib_dir / "by_strategy" / "momentum_breakout").exists()

    def test_no_absolute_paths_in_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            card = build_setup_card(_make_report_dict())
            write_setup_library(Path(td), [card], dry_run=False)
            lib_dir = Path(td) / "setup_library"
            raw = (lib_dir / "cards" / "setup-c-001.json").read_text()
            assert "C:\\" not in raw
            assert "krivo" not in raw

    def test_markdown_readable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            card = build_setup_card(_make_report_dict())
            write_setup_library(Path(td), [card], dry_run=False)
            md_path = Path(td) / "setup_library" / "reports" / "setup-c-001.md"
            md = md_path.read_text()
            assert "c-001" in md
            assert "not imply profitability" in md

    def test_index_entry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            card = build_setup_card(_make_report_dict())
            write_setup_library(Path(td), [card], dry_run=False)
            index = Path(td) / "setup_library" / "setup_index.jsonl"
            lines = index.read_text().strip().split("\n")
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["setup_id"] == "setup-c-001"
            assert entry["main_engine_ready"] is False

    def test_index_upserts_same_setup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            card = build_setup_card(_make_report_dict())
            write_setup_library(Path(td), [card], dry_run=False)
            write_setup_library(Path(td), [card], dry_run=False)
            index = Path(td) / "setup_library" / "setup_index.jsonl"
            lines = index.read_text().strip().split("\n")
            assert len(lines) == 1


class TestHelpers:
    def test_entry_exit_summary_all_pass(self) -> None:
        summary = _entry_exit_summary({"failed_checks": []})
        assert "passed" in summary.lower()

    def test_entry_exit_summary_failures(self) -> None:
        summary = _entry_exit_summary({"failed_checks": ["costs"]})
        assert "costs" in summary

    def test_regime_tags(self) -> None:
        tags = _extract_regime_tags({"regime_summary": {"dominant_bucket": "trending"}})
        assert tags == ["trending"]

    def test_regime_tags_empty(self) -> None:
        assert _extract_regime_tags(None) == []

    def test_card_to_markdown(self) -> None:
        card = build_setup_card(_make_report_dict())
        md = _card_to_markdown(card)
        assert "BTC-USDT-SWAP" in md
        assert "Main Engine Ready" in md
