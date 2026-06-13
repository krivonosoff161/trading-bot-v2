# -*- coding: utf-8 -*-

import pytest

from src.research_lab.universe import DEFAULT_PATH, Universe, load_universe

VALID = """
schema: strategy_lab_universe.v1
groups:
  core:
    - BTC_USDT_SWAP
    - ETH_USDT_SWAP
  alts:
    - SOL_USDT_SWAP
    - WLD_USDT_SWAP
relations:
  market_beta:
    SOL_USDT_SWAP: [BTC_USDT_SWAP, ETH_USDT_SWAP]
    WLD_USDT_SWAP: [ETH_USDT_SWAP]
"""


def _write(tmp_path, text):
    path = tmp_path / "universe.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_config_loads():
    universe = load_universe(DEFAULT_PATH)
    assert "core_market" in universe.groups
    assert "BTC_USDT_SWAP" in universe.all_symbols()
    assert universe.group_of("ETH_USDT_SWAP") == "core_market"


def test_valid_config_relations_and_peers(tmp_path):
    universe = load_universe(_write(tmp_path, VALID))
    assert isinstance(universe, Universe)
    assert universe.group_of("SOL_USDT_SWAP") == "alts"
    assert universe.peers("SOL_USDT_SWAP") == ("WLD_USDT_SWAP",)
    related = universe.related("SOL_USDT_SWAP")
    assert "BTC_USDT_SWAP" in related and "ETH_USDT_SWAP" in related
    # peers are included and self is excluded
    assert "WLD_USDT_SWAP" in related
    assert "SOL_USDT_SWAP" not in related


def test_relation_filter_and_normalization(tmp_path):
    universe = load_universe(_write(tmp_path, VALID))
    # lookup is case/separator insensitive
    only_beta = universe.related("sol-usdt-swap", relation="market_beta", include_peers=False)
    assert only_beta == ("BTC_USDT_SWAP", "ETH_USDT_SWAP")
    assert universe.related("BTC_USDT_SWAP", relation="market_beta", include_peers=False) == ()


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_universe(tmp_path / "nope.yaml")


def test_invalid_shape_raises(tmp_path):
    bad = _write(tmp_path, "groups:\n  core: not_a_list\n")
    with pytest.raises(ValueError):
        load_universe(bad)


def test_missing_groups_key_raises(tmp_path):
    bad = _write(tmp_path, "schema: x\nrelations: {}\n")
    with pytest.raises(ValueError):
        load_universe(bad)


def test_top_level_not_mapping_raises(tmp_path):
    bad = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ValueError):
        load_universe(bad)
