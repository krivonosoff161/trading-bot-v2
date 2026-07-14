# -*- coding: utf-8 -*-

import sqlite3

import pytest

from src.research_lab.candle_store import (
    CandleStore,
    CandleStoreError,
    CandleValidationError,
)

MINUTE = 60_000


def _row(ts: int, close: float = 100.0, **extra) -> dict:
    return {
        "ts": ts,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "vol": 10.0,
        **extra,
    }


def test_store_upserts_deduplicates_and_reads_bounded_range(tmp_path):
    store = CandleStore(tmp_path, max_read_rows=10)
    first = store.upsert_candles(
        "BTC-USDT-SWAP", "1m",
        [_row(0), _row(MINUTE), _row(MINUTE, 101.0)],
        source="fixture",
    )
    assert first.accepted == 2
    assert first.coverage.row_count == 2
    rows = store.read("BTC_USDT_SWAP", "1m", 0, MINUTE)
    assert [row["ts"] for row in rows] == [0, MINUTE]
    assert rows[-1]["close"] == 101.0


def test_store_records_gaps_without_inventing_candles(tmp_path):
    store = CandleStore(tmp_path)
    result = store.upsert_candles(
        "ETH_USDT_SWAP", "1m", [_row(0), _row(3 * MINUTE)], source="fixture",
    )
    assert result.coverage.gap_count == 1
    assert result.coverage.expected_rows == 4
    assert result.coverage.missing_rows == 2
    assert len(store.read("ETH_USDT_SWAP", "1m", 0, 3 * MINUTE)) == 2


def test_store_rejects_impossible_or_unconfirmed_rows(tmp_path):
    store = CandleStore(tmp_path)
    with pytest.raises(CandleValidationError, match="geometry"):
        store.upsert_candles(
            "BAD_USDT_SWAP", "1m",
            [{"ts": 0, "open": 10, "high": 9, "low": 8, "close": 10, "vol": 1}],
        )
    with pytest.raises(CandleValidationError, match="unconfirmed"):
        store.upsert_candles("BAD_USDT_SWAP", "1m", [_row(0, confirm="0")])


def test_store_preserves_optional_research_fields(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "FLOW_USDT_SWAP", "1h",
        [_row(0, funding=0.001, oi=1200, custom_tag="before_decision")],
    )
    row = store.read("FLOW_USDT_SWAP", "1h", 0, 0)[0]
    assert row["funding"] == 0.001
    assert row["oi"] == 1200
    assert row["custom_tag"] == "before_decision"


def test_plain_price_refresh_does_not_erase_existing_enrichment(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "FLOW_USDT_SWAP", "1h", [_row(0, funding=0.001, oi=1200)],
    )
    store.upsert_candles("FLOW_USDT_SWAP", "1h", [_row(0, close=101.0)])
    row = store.read("FLOW_USDT_SWAP", "1h", 0, 0)[0]
    assert row["close"] == 101.0
    assert row["funding"] == 0.001
    assert row["oi"] == 1200


def test_store_enforces_read_cap(tmp_path):
    store = CandleStore(tmp_path, max_read_rows=2)
    store.upsert_candles("CAP_USDT_SWAP", "1m", [_row(i * MINUTE) for i in range(3)])
    with pytest.raises(CandleStoreError, match="bounded read cap"):
        store.read("CAP_USDT_SWAP", "1m", 0, 2 * MINUTE)


def test_instrument_passport_round_trip(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_instrument(
        "NEW-USDT-SWAP", state="live", inst_type="SWAP",
        settle_ccy="USDT", list_time_ms=1234, updated_at_ms=5678,
    )
    assert store.instrument("NEW_USDT_SWAP") == {
        "inst_id": "NEW-USDT-SWAP",
        "state": "live",
        "inst_type": "SWAP",
        "settle_ccy": "USDT",
        "list_time_ms": 1234,
        "updated_at_ms": 5678,
    }


def test_store_uses_wal_and_schema_version(tmp_path):
    store = CandleStore(tmp_path)
    store.initialize()
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0] == "1"
