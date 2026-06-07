# -*- coding: utf-8 -*-
import datetime as dt
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import scanner_journal as J  # noqa: E402
from src.scout import pending_store as PS  # noqa: E402


def _patch_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(PS, "OUT_DIR", tmp_path)
    monkeypatch.setattr(PS, "PENDING", tmp_path / "pending_events.jsonl")


def test_pending_store_opens_expected_from_journal(monkeypatch, tmp_path):
    _patch_pending(monkeypatch, tmp_path)
    row = J.build_row(
        source_url="https://example.com/fomc",
        source_ts="2026-06-10",
        layer=3,
        asset="XAU",
        trigger_type="calendar",
        headline="FOMC meeting scheduled for next week",
        verdict="WATCH",
        horizon_hours=48,
        price_at_decision=None,
        event_type="fomc",
        event_phase="expected",
        source="fred_calendar",
        source_class="api",
        lead_class="LEADING",
        event_key="XAU::fomc",
    )
    pending = PS.build_pending_from_journal(row)
    assert pending is not None
    assert pending["kind"] == PS.KIND_CALENDAR
    assert pending["status"] == PS.STATUS_OPEN
    _, changed = PS.upsert_pending(pending)
    assert changed
    assert len(PS.open_items()) == 1


def test_pending_store_matches_realized_event(monkeypatch, tmp_path):
    _patch_pending(monkeypatch, tmp_path)
    pending = PS.build_pending_record(
        asset="BTC",
        layer=1,
        event_type="etf_approval",
        expected_start_ts="2026-06-10T12:00:00Z",
        kind=PS.KIND_CALENDAR,
        source_id="calendar",
        source_ref="https://example.com/calendar",
    )
    PS.upsert_pending(pending)
    row = J.build_row(
        source_url="https://example.com/news",
        source_ts="2026-06-10T13:00:00Z",
        layer=1,
        asset="BTC",
        trigger_type="rss_headline",
        headline="SEC approves spot ETF",
        verdict="GO",
        horizon_hours=24,
        price_at_decision=70000.0,
        event_type="etf_approval",
        event_phase="realized",
        source="cointelegraph",
        source_class="rss",
        lead_class="LEADING",
        event_key="BTC::etf_approval",
    )
    matched = PS.match_realized_event(row)
    assert matched is not None
    assert matched["pending_id"] == pending["pending_id"]
    assert PS.mark_matched(pending["pending_id"], row["card_id"])
    assert PS.open_items() == []


def test_pending_store_expires_old_items(monkeypatch, tmp_path):
    _patch_pending(monkeypatch, tmp_path)
    pending = PS.build_pending_record(
        asset="NVDA",
        layer=5,
        event_type="earnings",
        expected_start_ts="2026-06-01T00:00:00Z",
        expected_end_ts="2026-06-01T00:00:00Z",
        kind=PS.KIND_CALENDAR,
    )
    PS.upsert_pending(pending)
    changed = PS.expire_old(now_utc=dt.datetime(2026, 6, 10, tzinfo=dt.timezone.utc))
    assert changed == 1
    idx = PS.read_index()
    assert idx[pending["pending_id"]]["status"] == PS.STATUS_EXPIRED


def test_pending_store_parses_rss_pubdate(monkeypatch, tmp_path):
    _patch_pending(monkeypatch, tmp_path)
    ts = PS.parse_ts("Fri, 05 Jun 2026 12:52:32 GMT")
    assert ts is not None
    assert ts.year == 2026 and ts.month == 6 and ts.day == 5


def test_pending_store_upsert_is_idempotent(monkeypatch, tmp_path):
    _patch_pending(monkeypatch, tmp_path)
    pending = PS.build_pending_record(
        asset="BTC",
        layer=1,
        event_type="etf_approval",
        expected_start_ts="2026-06-10T12:00:00Z",
        kind=PS.KIND_CALENDAR,
    )
    _, changed1 = PS.upsert_pending(pending)
    _, changed2 = PS.upsert_pending(pending)
    assert changed1 is True
    assert changed2 is False


def test_pending_store_does_not_reopen_expired(monkeypatch, tmp_path):
    _patch_pending(monkeypatch, tmp_path)
    pending = PS.build_pending_record(
        asset="TSLA",
        layer=5,
        event_type="earnings",
        expected_start_ts="2026-04-18T00:00:00Z",
        kind=PS.KIND_CALENDAR,
    )
    PS.upsert_pending(pending)
    PS.expire_old(now_utc=dt.datetime(2026, 6, 10, tzinfo=dt.timezone.utc))
    _, changed = PS.upsert_pending(pending)
    idx = PS.read_index()
    assert changed is False
    assert idx[pending["pending_id"]]["status"] == PS.STATUS_EXPIRED
