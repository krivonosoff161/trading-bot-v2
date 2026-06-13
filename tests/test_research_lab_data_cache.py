# -*- coding: utf-8 -*-

import json

from src.research_lab.data_cache import check_requirement
from src.research_lab.data_requirements import DataRequirement

MINUTE = 60_000
START = 1_700_000_000_000


def _req(symbol, start, end):
    return DataRequirement(symbol, "1m", start, end, "r", "manual", 360, "candidate")


def _write_1m(path, start_ms, n):
    rows = [
        {"ts": start_ms + i * MINUTE, "date": "d", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "vol": 1.0}
        for i in range(n)
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_present_when_window_covered(tmp_path):
    _write_1m(tmp_path / "BTC_USDT_SWAP_a_1m.json", START, 400)
    c = check_requirement(_req("BTC_USDT_SWAP", START, START + 359 * MINUTE), data_dir=tmp_path)
    assert c.requirement.status == "present"
    assert c.bars_in_window >= 360
    assert c.file_label == "BTC_USDT_SWAP_a_1m.json"  # name only, no abs path


def test_missing_when_no_file(tmp_path):
    c = check_requirement(_req("GHOST_USDT_SWAP", START, START + MINUTE), data_dir=tmp_path)
    assert c.requirement.status == "missing"


def test_missing_when_file_outside_window(tmp_path):
    _write_1m(tmp_path / "BTC_USDT_SWAP_old_1m.json", START - 1_000_000_000, 400)
    c = check_requirement(_req("BTC_USDT_SWAP", START, START + 10 * MINUTE), data_dir=tmp_path)
    assert c.requirement.status == "missing"  # file exists & is 1m, but doesn't cover the window


def test_too_short_when_partial(tmp_path):
    _write_1m(tmp_path / "ETH_USDT_SWAP_p_1m.json", START, 100)  # only 100 of ~360 needed
    c = check_requirement(_req("ETH_USDT_SWAP", START, START + 359 * MINUTE), data_dir=tmp_path)
    assert c.requirement.status == "too_short"
    assert c.bars_in_window == 100


def test_malformed_file(tmp_path):
    (tmp_path / "BAD_USDT_SWAP_x_1m.json").write_text("{not valid json", encoding="utf-8")
    c = check_requirement(_req("BAD_USDT_SWAP", START, START + MINUTE), data_dir=tmp_path)
    assert c.requirement.status == "malformed"


def test_outside_policy_left_untouched(tmp_path):
    req = DataRequirement("X_USDT_SWAP", "1m", START, START + MINUTE, "r", "manual", 360, "outside_policy")
    c = check_requirement(req, data_dir=tmp_path)
    assert c.requirement.status == "outside_policy"
