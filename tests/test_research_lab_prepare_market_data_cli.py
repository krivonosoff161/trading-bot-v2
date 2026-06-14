# -*- coding: utf-8 -*-

import json
from argparse import Namespace

import pytest

from scripts.strategy_lab.prepare_market_data import build_items, main
from src.research_lab.data_prepare import read_market_data_prepare_report
from src.research_lab.paths import market_data_dir
from src.research_lab.strategy_requirements import derive_requirement


def _args(**overrides):
    base = dict(
        timeframe="15m",
        universe="core_market",
        symbol="",
        symbols="BTC_USDT_SWAP,ETH_USDT_SWAP,XRP_USDT_SWAP",
        start="2026-06-10T00:00:00Z",
        end="2026-06-10T03:00:00Z",
        days=None,
        max_symbols=2,
    )
    base.update(overrides)
    return Namespace(**base)


def test_build_items_caps_symbols_and_uses_requested_timeframe():
    items, skipped = build_items(_args())
    assert [i.symbol for i in items] == ["BTC_USDT_SWAP", "ETH_USDT_SWAP"]
    assert {i.timeframe for i in items} == {"15m"}
    assert skipped == ["symbol_cap:3->2"]


def test_build_items_rejects_1m_trigger_only():
    with pytest.raises(ValueError, match="1m is trigger-only"):
        build_items(_args(timeframe="1m"))


def test_prepare_market_data_cli_dry_run_writes_private_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare_market_data",
            "--timeframe", "1h",
            "--symbol", "BTC_USDT_SWAP",
            "--start", "2026-06-10T00:00:00Z",
            "--end", "2026-06-10T03:00:00Z",
            "--private-root", str(tmp_path),
            "--dry-run",
        ],
    )
    main()
    out = capsys.readouterr().out
    report = read_market_data_prepare_report(tmp_path, "1h")
    assert report["mode"] == "dry_run"
    assert report["would_download"] == 1
    assert "no orders" in out


def test_prepare_market_data_cli_synthetic_apply_writes_tf_file(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_ALLOW_SYNTHETIC", "1")
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare_market_data",
            "--timeframe", "4h",
            "--symbol", "BTC_USDT_SWAP",
            "--start", "2026-06-10T00:00:00Z",
            "--end", "2026-06-10T12:00:00Z",
            "--provider", "synthetic",
            "--private-root", str(tmp_path),
            "--apply",
        ],
    )
    main()
    report = read_market_data_prepare_report(tmp_path, "4h")
    assert report["mode"] == "apply"
    assert report["downloaded"] == 1
    files = list(market_data_dir(tmp_path, "4h").glob("*.json"))
    assert len(files) == 1
    rows = json.loads(files[0].read_text(encoding="utf-8"))
    assert len(rows) >= 4
    assert all(row.get("synthetic") is True for row in rows)


def test_readiness_hint_points_to_market_data_prepare_for_non_1m():
    from src.research_lab.data_readiness import _prepare_hint

    req = derive_requirement("momentum_breakout", "BTC_USDT_SWAP", "15m")
    hint = _prepare_hint(req)
    assert "scripts.strategy_lab.prepare_market_data" in hint
    assert "--timeframe 15m" in hint
    assert "--symbol BTC_USDT_SWAP" in hint
