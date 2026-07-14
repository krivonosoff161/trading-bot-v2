import json

from src.research_lab.candle_identity import candle_slice_fingerprint
from src.research_lab.candle_library import load_canonical_candles
from src.research_lab.candle_store import CandleStore


def _rows(close_offset=0.0):
    return [
        {"ts": 1_700_000_000_000 + i * 3_600_000, "open": 10 + i,
         "high": 11 + i, "low": 9 + i, "close": 10.5 + i + close_offset, "vol": 2}
        for i in range(3)
    ]


def test_canonical_library_prefers_sqlite_over_conflicting_json(tmp_path):
    folder = tmp_path / "market_data" / "1h"
    folder.mkdir(parents=True)
    (folder / "BTC_USDT_SWAP_history_1h.json").write_text(
        json.dumps(_rows(close_offset=100)), encoding="utf-8",
    )
    store = CandleStore(tmp_path)
    store.upsert_candles("BTC_USDT_SWAP", "1h", _rows(), source="test")

    result = load_canonical_candles(tmp_path, "BTC_USDT_SWAP", "1h")
    assert result.source == "sqlite"
    assert result.rows[0]["close"] == 10.5


def test_identity_changes_when_an_inner_candle_changes():
    rows = _rows()
    original = candle_slice_fingerprint("BTC_USDT_SWAP", "1h", rows)
    changed = [dict(row) for row in rows]
    changed[1]["close"] += 0.01
    assert candle_slice_fingerprint("BTC_USDT_SWAP", "1h", changed) != original
