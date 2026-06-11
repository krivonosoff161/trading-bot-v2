from __future__ import annotations

import json

from scripts.analysis.build_watch_queue import build_queue
from src.scout import scanner_journal as J


def _row(url: str, verdict: str) -> dict:
    return J.build_row(
        source_url=url,
        source_ts="2026-06-11T10:00:00Z",
        layer=1,
        asset="BTC",
        trigger_type="rss_headline",
        headline="BTC ETF flow shifts",
        verdict=verdict,
        horizon_hours=24,
        price_at_decision=100000.0,
        okx_inst="BTC-USDT-SWAP",
        event_type="etf_flow",
        event_phase="realized",
        lead_class="COINCIDENT",
        source="etf_flow",
        side="long",
        summary="Synthetic source row",
    )


def test_build_watch_queue_backfills_only_watch_and_go(tmp_path):
    journal = tmp_path / "scanner_journal.jsonl"
    out = tmp_path / "watch_queue.jsonl"
    rows = [
        _row("https://example.com/watch", "WATCH"),
        _row("https://example.com/go", "GO"),
        _row("https://example.com/no-go", "NO_GO"),
    ]
    journal.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

    dry = build_queue(journal_path=journal, out_path=out, dry_run=True)
    assert dry["journal_rows"] == 3
    assert dry["eligible"] == 2
    assert not out.exists()

    first = build_queue(journal_path=journal, out_path=out)
    second = build_queue(journal_path=journal, out_path=out)

    assert first["written_or_updated"] == 2
    assert second["unchanged"] == 2
    assert len(out.read_text(encoding="utf-8").splitlines()) == 2
