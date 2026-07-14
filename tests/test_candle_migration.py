import json

from src.research_lab.candle_migration import migrate_json_candles
from src.research_lab.candle_store import CandleStore


def _rows():
    return [
        {"ts": 1_700_000_000_000 + i * 3_600_000, "open": 10 + i,
         "high": 11 + i, "low": 9 + i, "close": 10.5 + i, "vol": 2}
        for i in range(3)
    ]


def test_migration_is_dry_by_default_and_never_deletes_json(tmp_path):
    folder = tmp_path / "market_data" / "1h"
    folder.mkdir(parents=True)
    source = folder / "BTC_USDT_SWAP_history_1h.json"
    source.write_text(json.dumps(_rows()), encoding="utf-8")

    dry = migrate_json_candles(tmp_path)
    assert dry["mode"] == "dry_report"
    assert dry["source_files_deleted"] == 0
    assert source.exists()
    assert not CandleStore(tmp_path).exists

    applied = migrate_json_candles(tmp_path, apply=True)
    assert applied["rows_accepted"] == 3
    assert applied["source_files_deleted"] == 0
    assert source.exists()
    assert CandleStore(tmp_path).coverage("BTC_USDT_SWAP", "1h").row_count == 3
