# -*- coding: utf-8 -*-

import json

from src.research_lab.data_inventory import (
    build_inventory,
    infer_symbol,
    infer_timeframe,
    inspect_file,
    write_inventory,
)


def _candles(n: int, step_ms: int = 86_400_000) -> list[dict]:
    return [
        {
            "ts": 1_700_000_000_000 + i * step_ms,
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "vol": 1000 + i,
        }
        for i in range(n)
    ]


def test_inspect_file_usable_daily(tmp_path):
    path = tmp_path / "BTC_USDT_SWAP_430d_1Dutc.json"
    path.write_text(json.dumps(_candles(80)), encoding="utf-8")

    row = inspect_file(path)

    assert row["symbol"] == "BTC_USDT_SWAP"
    assert row["rows"] == 80
    assert row["timeframe"] == "1D"
    assert row["has_ohlcv"] is True
    assert row["quality_status"] == "usable"
    assert row["start_ts"] < row["end_ts"]


def test_inspect_file_too_short_and_malformed(tmp_path):
    short = tmp_path / "ABC_USDT_SWAP_5d.json"
    short.write_text(json.dumps(_candles(5)), encoding="utf-8")
    funding = tmp_path / "ABC_USDT_SWAP_funding_100.json"
    funding.write_text(json.dumps([{"ts": 1, "rate": 0.0001}] * 100), encoding="utf-8")
    broken = tmp_path / "BROKEN_USDT_SWAP_1d.json"
    broken.write_text("{not json", encoding="utf-8")

    assert inspect_file(short)["quality_status"] == "too_short"
    funding_row = inspect_file(funding)
    assert funding_row["quality_status"] == "malformed"
    assert funding_row["has_ohlcv"] is False
    assert inspect_file(broken)["quality_status"] == "malformed"


def test_build_inventory_reports_missing_symbols(tmp_path):
    (tmp_path / "BTC_USDT_SWAP_80d.json").write_text(json.dumps(_candles(80)), encoding="utf-8")

    inventory = build_inventory(
        str(tmp_path / "{symbol}_*.json"),
        ["BTC_USDT_SWAP", "GHOST_USDT_SWAP"],
    )

    statuses = {r["symbol"]: r["quality_status"] for r in inventory["rows"]}
    assert statuses["BTC_USDT_SWAP"] == "usable"
    assert statuses["GHOST_USDT_SWAP"] == "missing"
    assert inventory["quality_counts"]["usable"] == 1
    assert inventory["quality_counts"]["missing"] == 1


def test_write_inventory_has_no_absolute_paths(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "ETH_USDT_SWAP_90d.json").write_text(json.dumps(_candles(90)), encoding="utf-8")
    inventory = build_inventory(str(data_dir / "*.json"))

    out = write_inventory(inventory, tmp_path / "private" / "inventory")

    written = out.read_text(encoding="utf-8")
    rows_text = json.dumps(json.loads(written)["rows"])
    assert str(tmp_path).replace("\\", "\\\\") not in rows_text
    assert "ETH_USDT_SWAP_90d.json" in rows_text
    assert (out.parent / "data_inventory.csv").exists()


def test_build_inventory_empty_glob(tmp_path):
    inventory = build_inventory(str(tmp_path / "{symbol}_*.json"), ["GHOST_USDT_SWAP"])

    assert inventory["file_count"] == 0
    assert inventory["quality_counts"] == {"missing": 1}


def test_build_inventory_keeps_duplicate_symbol_files(tmp_path):
    (tmp_path / "BTC_USDT_SWAP_30d.json").write_text(json.dumps(_candles(70)), encoding="utf-8")
    (tmp_path / "BTC_USDT_SWAP_80d.json").write_text(json.dumps(_candles(80)), encoding="utf-8")

    inventory = build_inventory(str(tmp_path / "*.json"))

    assert inventory["file_count"] == 2
    assert all(r["symbol"] == "BTC_USDT_SWAP" for r in inventory["rows"])


def test_infer_helpers():
    assert infer_timeframe([0, 900_000, 1_800_000, 2_700_000]) == "15m"
    assert infer_timeframe([0]) == "unknown"

    class FakePath:
        stem = "PENGU_USDT_SWAP_569d_1Dutc"

    assert infer_symbol(FakePath()) == "PENGU_USDT_SWAP"
