# -*- coding: utf-8 -*-

import json

from src.research_lab.data_readiness import (
    MALFORMED,
    MISSING_DATA,
    READY,
    TOO_SHORT,
    assess,
    assess_proposal,
)
from src.research_lab.strategy_requirements import derive_requirement

DAY = 86_400_000


def _write_daily(path, n):
    rows = [{"ts": 1_700_000_000_000 + i * DAY, "date": f"d{i}", "open": 100.0,
             "high": 101.0, "low": 99.0, "close": 100.0, "vol": 1000.0} for i in range(n)]
    path.write_text(json.dumps(rows), encoding="utf-8")


def _glob(tmp_path):
    return str(tmp_path / "{symbol}_*.json")


def _req(symbol, **kw):
    return derive_requirement("momentum_breakout", symbol, "1d", **kw)  # min_rows = 70


def test_ready_when_enough_rows(tmp_path):
    _write_daily(tmp_path / "ABC_USDT_SWAP_x.json", 100)
    r = assess(_req("ABC_USDT_SWAP"), data_glob=_glob(tmp_path))
    assert r.status == READY and r.is_ready()


def test_missing_when_no_file(tmp_path):
    r = assess(_req("GHOST_USDT_SWAP"), data_glob=_glob(tmp_path))
    assert r.status == MISSING_DATA and not r.is_ready()


def test_too_short_when_few_rows(tmp_path):
    _write_daily(tmp_path / "ETH_USDT_SWAP_x.json", 40)  # < min_rows 70
    r = assess(_req("ETH_USDT_SWAP"), data_glob=_glob(tmp_path))
    assert r.status == TOO_SHORT


def test_malformed_file(tmp_path):
    (tmp_path / "BAD_USDT_SWAP_x.json").write_text("{not json", encoding="utf-8")
    r = assess(_req("BAD_USDT_SWAP"), data_glob=_glob(tmp_path))
    assert r.status == MALFORMED


def test_timeframe_mismatch_is_missing_not_silently_run(tmp_path):
    # Only daily data on disk; a 15m proposal must NOT run on it.
    _write_daily(tmp_path / "XRP_USDT_SWAP_x.json", 100)
    req = derive_requirement("momentum_breakout", "XRP_USDT_SWAP", "15m")
    r = assess(req, data_glob=_glob(tmp_path))
    assert r.status == MISSING_DATA and not r.is_ready()
    assert any("no_file_for_timeframe_15m" in reason for reason in r.reasons)
    assert "TODO" in r.suggested_command  # honest hint; no false "prepare_1m" claim for a 15m gap
    assert "prepare_1m_data" not in r.suggested_command


def test_needs_1m_missing_gives_missing_with_suggested_command(tmp_path):
    _write_daily(tmp_path / "SOL_USDT_SWAP_x.json", 100)  # primary ok
    r = assess(_req("SOL_USDT_SWAP", needs_1m_microscope=True), data_glob=_glob(tmp_path), private_root=tmp_path)
    assert r.status == MISSING_DATA
    assert "prepare_1m_data" in r.suggested_command


def test_assess_proposal_worst_status_wins(tmp_path):
    _write_daily(tmp_path / "BTC_USDT_SWAP_x.json", 100)  # present
    # GHOST missing -> worst is MISSING_DATA

    class _P:
        setup_family = "momentum_breakout"
        requested_timeframe = "1d"
        symbols = ["BTC_USDT_SWAP", "GHOST_USDT_SWAP"]
        parameter_grid = {"momentum_breakout": [{"lookback": 20}]}

    pr = assess_proposal(_P(), data_glob=_glob(tmp_path), private_root=tmp_path)
    assert pr.status == MISSING_DATA and not pr.is_ready()


def test_readiness_has_no_absolute_paths(tmp_path):
    _write_daily(tmp_path / "ABC_USDT_SWAP_x.json", 100)
    r = assess(_req("ABC_USDT_SWAP"), data_glob=_glob(tmp_path))
    assert str(tmp_path) not in json.dumps(r.to_dict())
