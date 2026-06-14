# -*- coding: utf-8 -*-
"""Tests for price_provider.py — instrument validation + price reasons."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import price_provider as PP  # noqa: E402


def test_no_instrument():
    price, reason = PP.get_price(None)
    assert price is None
    assert reason == "no_instrument"


def test_classify_instrument_crypto():
    inst, reason = PP.classify_instrument("crypto_major", "BTC-USDT-SWAP")
    assert inst == "BTC-USDT-SWAP"
    assert reason == "supported"


def test_classify_instrument_equity():
    inst, reason = PP.classify_instrument("equity", "NVDA-USDT-SWAP")
    assert inst == "NVDA-USDT-SWAP"
    assert reason == "unsupported_asset_class"


def test_classify_instrument_pre_ipo():
    inst, reason = PP.classify_instrument("pre_ipo_equity", "SPACEX-USDT-SWAP")
    assert inst == "SPACEX-USDT-SWAP"
    assert reason == "unsupported_asset_class"


def test_classify_instrument_unknown():
    inst, reason = PP.classify_instrument("unknown", "G7-USDT-SWAP")
    assert inst == "G7-USDT-SWAP"
    assert reason == "unsupported_asset_class"


def test_classify_instrument_no_inst():
    inst, reason = PP.classify_instrument("crypto_major", None)
    assert inst is None
    assert reason == "no_instrument"


def test_classify_instrument_liquidation_flow():
    inst, reason = PP.classify_instrument("liquidation_flow", "ETH-USDT-SWAP")
    assert inst == "ETH-USDT-SWAP"
    assert reason == "supported"


def test_get_price_okx_403():
    PP.reset_cache()
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.json.return_value = {}
    with patch("src.scout.price_provider.requests.get", return_value=mock_resp):
        price, reason = PP.get_price("BTC-USDT-SWAP")
    assert price is None
    assert reason == "provider_403"


def test_get_price_okx_success():
    PP.reset_cache()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"code": "0", "data": [{"last": "64000.5"}]}
    with patch("src.scout.price_provider.requests.get", return_value=mock_resp):
        price, reason = PP.get_price("BTC-USDT-SWAP")
    assert price == 64000.5
    assert reason == "supported"


def test_get_price_instrument_not_found():
    PP.reset_cache()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"code": "51004", "msg": "Instrument does not exist"}
    with patch("src.scout.price_provider.requests.get", return_value=mock_resp):
        price, reason = PP.get_price("FAKE-USDT-SWAP")
    assert price is None
    assert reason == "instrument_not_listed"


def test_get_price_caches_result():
    PP.reset_cache()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"code": "0", "data": [{"last": "64000.5"}]}
    with patch("src.scout.price_provider.requests.get", return_value=mock_resp) as m:
        price1, reason1 = PP.get_price("BTC-USDT-SWAP")
        price2, reason2 = PP.get_price("BTC-USDT-SWAP")
    assert m.call_count == 1  # second call uses cache
    assert price1 == price2 == 64000.5


def test_get_price_for_card_unsupported_asset():
    row = {"asset_class": "equity", "okx_inst": "NVDA-USDT-SWAP"}
    price, reason = PP.get_price_for_card(row)
    assert price is None
    assert reason == "unsupported_asset_class"


def test_get_price_for_card_no_instrument():
    row = {"asset_class": "unknown", "okx_inst": None}
    price, reason = PP.get_price_for_card(row)
    assert price is None
    assert reason == "no_instrument"


def test_price_stats_from_rows():
    rows = [
        {"asset_class": "crypto_major", "okx_inst": "BTC-USDT-SWAP"},
        {"asset_class": "equity", "okx_inst": "NVDA-USDT-SWAP"},
        {"asset_class": "unknown", "okx_inst": None},
    ]
    PP.reset_cache()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"code": "0", "data": [{"last": "64000"}]}
    with patch("src.scout.price_provider.requests.get", return_value=mock_resp):
        stats = PP.price_stats_from_rows(rows)
    assert stats.get("supported", 0) == 1
    assert stats.get("unsupported_asset_class", 0) == 1
    assert stats.get("no_instrument", 0) == 1
