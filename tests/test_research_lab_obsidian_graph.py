# -*- coding: utf-8 -*-

from src.research_lab.obsidian_graph import (
    build_candidate_note,
    count_notes,
    obsidian_dir,
    write_candidate_notes,
)
from src.research_lab.graph_viewer import build_lineage_graph_data
from src.research_lab.graph_viewer import write_lineage_graph_viewer


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


def test_write_notes_count_matches_files_when_candidate_regraded(tmp_path):
    # The registry keys rows by (experiment_id, candidate_id), so the same
    # candidate can appear twice with different verdicts. Both map to one note
    # file, so notes_written must equal the files on disk and the last grading wins.
    entries = [
        _entry("dup", "REGIME_SPECIFIC"),
        _entry("dup", "FORWARD_PAPER"),
    ]
    result = write_candidate_notes(tmp_path, entries)
    assert result["notes_written"] == 1
    assert count_notes(tmp_path) == result["notes_written"]
    note = next(obsidian_dir(tmp_path).glob("*.md")).read_text(encoding="utf-8")
    assert "forward-paper" in note  # last grading wins


def test_build_lineage_graph_data_links_full_cycle():
    graph = build_lineage_graph_data([
        {
            "source": "farm",
            "symbol": "BTC_USDT_SWAP",
            "instrument": "BTC-USDT-SWAP",
            "timeframe": "15m",
            "scanner_event_id": "se1",
            "data_packet_id": "mdp1",
            "feature_packet_id": "fp1",
            "setup_candidate_id": "setup1",
            "validation_id": "val1",
            "paper_signal_id": "sig1",
            "outcome_id": "out1",
            "training_row_id": "train1",
        }
    ])

    kinds = {node["kind"] for node in graph["nodes"]}
    relations = {edge["relation"] for edge in graph["edges"]}
    assert {"event", "data_packet", "feature_packet", "paper_signal", "outcome", "training"} <= kinds
    assert {"data", "features", "paper", "outcome", "training"} <= relations
    assert graph["summary"]["execution_allowed"] is False


def test_write_lineage_graph_viewer_writes_private_html(tmp_path):
    lineage = tmp_path / "state" / "lineage"
    lineage.mkdir(parents=True)
    (lineage / "cycle_links.jsonl").write_text(
        "\n".join([
            '{"source":"farm","symbol":"BTC_USDT_SWAP","scanner_event_id":"se1",'
            '"data_packet_id":"mdp1","feature_packet_id":"fp1","paper_signal_id":"sig1"}',
        ]),
        encoding="utf-8",
    )

    result = write_lineage_graph_viewer(tmp_path, allow_public_output=True)

    assert result["viewer_label"] == "strategy-lab/graph-viewer/lineage.html"
    assert result["execution_allowed"] is False
    assert (tmp_path / "graph-viewer" / "lineage.html").exists()
