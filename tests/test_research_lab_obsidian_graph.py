# -*- coding: utf-8 -*-

from src.research_lab.obsidian_graph import (
    build_candidate_note,
    count_notes,
    obsidian_dir,
    write_candidate_notes,
)


def _entry(cid, status, symbol="BTC_USDT_SWAP"):
    return {
        "candidate_id": cid,
        "symbol": symbol,
        "strategy_id": "momentum_breakout",
        "validation_status": status,
        "validation_reasons": ["weak_profit_factor"],
        "regime_summary": {"dominant_bucket": "high|up|normal"},
        "artifact_label": "experiments/completed/run_x",
        "next_action": "keep in registry",
        "next_review": "2026-07-01",
    }


def test_build_note_has_links_and_no_profit_claim():
    fname, md = build_candidate_note(_entry("a", "FORWARD_PAPER"), related=("ETH_USDT_SWAP",))
    assert fname.endswith(".md")
    assert "[[Families/momentum_breakout]]" in md
    assert "[[Symbols/BTC_USDT_SWAP]]" in md
    assert "[[Symbols/ETH_USDT_SWAP]]" in md
    assert "forward-paper" in md
    assert "not a profitability claim" in md.lower()


def test_write_notes_skips_reject_and_counts(tmp_path):
    entries = [
        _entry("a", "FORWARD_PAPER"),
        _entry("b", "OBSERVE", symbol="ETH_USDT_SWAP"),
        _entry("c", "REJECT", symbol="SOL_USDT_SWAP"),
    ]
    result = write_candidate_notes(tmp_path, entries)
    assert result["notes_written"] == 2  # REJECT skipped
    assert count_notes(tmp_path) == 2
    # no absolute private path leaks into the notes
    for note in obsidian_dir(tmp_path).glob("*.md"):
        text = note.read_text(encoding="utf-8")
        assert str(tmp_path) not in text


def test_write_notes_idempotent(tmp_path):
    entries = [_entry("a", "FORWARD_PAPER")]
    write_candidate_notes(tmp_path, entries)
    write_candidate_notes(tmp_path, entries)
    assert count_notes(tmp_path) == 1  # same candidate -> same file, not duplicated
