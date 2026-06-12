from __future__ import annotations

import datetime as dt
import json

from src.scout import scanner_journal as J
from src.scout import watch_queue as WQ


def _row(verdict: str = "WATCH", side: str = "long") -> dict:
    row = J.build_row(
        source_url=f"https://example.com/{verdict.lower()}-{side}",
        source_ts="2026-06-11T10:00:00Z",
        layer=2,
        asset="ZEC",
        trigger_type="rss_headline",
        headline="ZEC event requires monitoring",
        verdict=verdict,
        horizon_hours=24,
        price_at_decision=42.0,
        okx_inst="ZEC-USDT-SWAP",
        event_type="security_incident",
        event_phase="realized",
        lead_class="LAGGING",
        source="decrypt",
        side=side,
        agent_direction=side,
        event_key="ZEC::security_incident",
        chief_called=True,
        summary="Synthetic watch row",
    )
    row["ts_utc"] = "2026-06-11T10:00:00Z"
    return row


def test_watch_queue_writes_watch_and_go_but_not_no_go(tmp_path):
    path = tmp_path / "watch_queue.jsonl"

    watch_id, changed = WQ.upsert_watch(_row("WATCH", "long"), path=path)
    assert watch_id.startswith("watch_")
    assert changed is True

    go_id, go_changed = WQ.upsert_watch(_row("GO", "short"), path=path)
    assert go_id.startswith("watch_")
    assert go_changed is True

    no_go_id, no_go_changed = WQ.upsert_watch(_row("NO_GO", "none"), path=path)
    assert no_go_id == ""
    assert no_go_changed is False

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert {row["scanner"]["verdict"] for row in rows} == {"WATCH", "GO"}
    assert all(row["execution_allowed"] is False for row in rows)
    assert all(row["confirm_required"] is True for row in rows)


def test_watch_queue_is_idempotent_and_side_normalized(tmp_path):
    path = tmp_path / "watch_queue.jsonl"
    row = _row("WATCH", "long")

    first = WQ.upsert_watch(row, path=path)
    second = WQ.upsert_watch(row, path=path)

    assert first == (f"watch_{row['card_id']}", True)
    assert second == (f"watch_{row['card_id']}", False)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["scanner"]["normalized_side"] == "buy"
    assert rows[0]["asset"]["okx_inst"] == "ZEC-USDT-SWAP"


def test_open_watches_filters_symbol_and_expiry(tmp_path):
    path = tmp_path / "watch_queue.jsonl"
    row = _row("WATCH", "long")
    WQ.upsert_watch(row, path=path)

    now = dt.datetime(2026, 6, 11, 12, 0, tzinfo=dt.timezone.utc)
    assert len(WQ.open_watches(symbol="ZEC", path=path, now_utc=now)) == 1
    assert len(WQ.open_watches(okx_inst="ZEC-USDT-SWAP", path=path, now_utc=now)) == 1
    assert len(WQ.open_watches(symbol="BTC", path=path, now_utc=now)) == 0

    later = dt.datetime(2026, 6, 13, 12, 0, tzinfo=dt.timezone.utc)
    assert WQ.open_watches(symbol="ZEC", path=path, now_utc=later) == []
    assert WQ.expire_old(now_utc=later, path=path) == 1
