# -*- coding: utf-8 -*-
"""Tests for the OKX public instrument resolver (no real network; injected http_get)."""
import sys
import urllib.parse
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.okx_instrument_resolver import (  # noqa: E402
    OkxInstrumentResolver,
    base_ticker,
    resolve_instrument,
)

# A small fake OKX public-instruments universe, by instType.
_UNIVERSE = {
    "SWAP": [
        {"instId": "BTC-USDT-SWAP", "instType": "SWAP", "state": "live", "listTime": "1600000000000"},
        {"instId": "RE-USDT-SWAP", "instType": "SWAP", "state": "live", "listTime": "1780000000000"},
        {"instId": "GRAM-USDT-SWAP", "instType": "SWAP", "state": "live", "listTime": "1781000000000"},
        {"instId": "XAU-USDT-SWAP", "instType": "SWAP", "state": "live", "listTime": "1700000000000"},
        {"instId": "NVDA-USDT-SWAP", "instType": "SWAP", "state": "live", "listTime": "1700000000000"},
    ],
    "SPOT": [
        {"instId": "BTC-USDT", "instType": "SPOT", "state": "live"},
        {"instId": "ONLYSPOT-USDT", "instType": "SPOT", "state": "live"},
    ],
}


def _make_http_get(universe=None, *, calls=None):
    universe = universe if universe is not None else _UNIVERSE

    def http_get(url, timeout):
        if calls is not None:
            calls.append(url)
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        inst_type = (qs.get("instType") or ["SWAP"])[0]
        return {"code": "0", "msg": "", "data": universe.get(inst_type, [])}

    return http_get


def _resolver(http_get, tmp_path):
    return OkxInstrumentResolver(http_get=http_get, hot_root=tmp_path)


def test_base_ticker_strips_pair_and_type():
    assert base_ticker("RE") == "RE"
    assert base_ticker("RE-USDT-SWAP") == "RE"
    assert base_ticker("re_usdt_swap") == "RE"
    assert base_ticker("XAU/USDT") == "XAU"
    assert base_ticker("") == ""


def test_geod_is_not_resolved(tmp_path):
    r = _resolver(_make_http_get(), tmp_path).resolve("GEOD")
    assert r["resolved"] is False
    assert r["primary_inst_id"] is None
    assert r["inst_type"] is None
    assert r["reason"] == "no_okx_instrument"
    assert r["confidence"] == 0.0


def test_re_resolves_to_swap(tmp_path):
    r = _resolver(_make_http_get(), tmp_path).resolve("RE")
    assert r["resolved"] is True
    assert r["primary_inst_id"] == "RE-USDT-SWAP"
    assert r["inst_type"] == "SWAP"
    assert r["asset_class"] == "crypto_alt"
    assert r["reason"] == "exact_swap_match"
    assert r["confidence"] == 0.95


def test_gram_resolves_to_swap(tmp_path):
    r = _resolver(_make_http_get(), tmp_path).resolve("GRAM")
    assert r["resolved"] is True
    assert r["primary_inst_id"] == "GRAM-USDT-SWAP"


def test_xau_resolves_as_commodity(tmp_path):
    r = _resolver(_make_http_get(), tmp_path).resolve("XAU")
    assert r["resolved"] is True
    assert r["primary_inst_id"] == "XAU-USDT-SWAP"
    assert r["asset_class"] == "commodity"


def test_nvda_resolves_as_equity(tmp_path):
    r = _resolver(_make_http_get(), tmp_path).resolve("NVDA")
    assert r["resolved"] is True
    assert r["primary_inst_id"] == "NVDA-USDT-SWAP"
    assert r["asset_class"] == "equity"


def test_prefers_usdt_linear_over_coin_margined_perp(tmp_path):
    # OKX lists both BTC-USD-SWAP (inverse) and BTC-USDT-SWAP (linear). The whole
    # pipeline is USDT-linear, so the resolver must pick -USDT-SWAP, never -USD-SWAP.
    universe = {"SWAP": [
        {"instId": "BTC-USD-SWAP", "instType": "SWAP", "state": "live"},
        {"instId": "BTC-USDT-SWAP", "instType": "SWAP", "state": "live"},
    ], "SPOT": []}
    r = OkxInstrumentResolver(http_get=_make_http_get(universe), hot_root=tmp_path).resolve("BTC")
    assert r["primary_inst_id"] == "BTC-USDT-SWAP"


def test_prefers_swap_over_spot(tmp_path):
    r = _resolver(_make_http_get(), tmp_path).resolve("BTC")
    assert r["inst_type"] == "SWAP"
    assert r["primary_inst_id"] == "BTC-USDT-SWAP"


def test_spot_only_falls_back_to_spot(tmp_path):
    r = _resolver(_make_http_get(), tmp_path).resolve("ONLYSPOT")
    assert r["resolved"] is True
    assert r["inst_type"] == "SPOT"
    assert r["primary_inst_id"] == "ONLYSPOT-USDT"
    assert r["reason"] == "exact_spot_match"
    assert r["confidence"] == 0.85


def test_ambiguous_freetext_is_not_an_unsafe_guess(tmp_path):
    # A descriptive name, not a tradable ticker -> base 'SOME' has no instrument.
    r = _resolver(_make_http_get(), tmp_path).resolve("Some New Project")
    assert r["resolved"] is False
    assert r["primary_inst_id"] is None


def test_provider_error_is_reported_not_raised(tmp_path):
    def boom(url, timeout):
        raise OSError("network down")

    r = _resolver(boom, tmp_path).resolve("BTC")
    assert r["resolved"] is False
    assert r["reason"] == "provider_error"


def test_empty_universe_is_provider_error_not_missing(tmp_path):
    # A code==0 response with empty data everywhere is non-authoritative -> provider_error,
    # NOT a false "no_okx_instrument" (we never actually saw the universe).
    empty = {"SWAP": [], "SPOT": []}
    r = OkxInstrumentResolver(http_get=_make_http_get(empty), hot_root=tmp_path).resolve("RE")
    assert r["resolved"] is False
    assert r["reason"] == "provider_error"


def test_missing_symbol_with_one_empty_quote_type_still_no_instrument(tmp_path):
    # SWAP universe is authoritative (non-empty) and lacks GEOD; SPOT is empty.
    # Seeing one real universe => genuine no_okx_instrument, not provider_error.
    r = _resolver(_make_http_get(), tmp_path).resolve("GEOD")
    assert r["resolved"] is False
    assert r["reason"] == "no_okx_instrument"


def test_disk_fallback_serves_after_network_blip(tmp_path):
    # First resolve populates the disk snapshot.
    ok = _make_http_get()
    res1 = OkxInstrumentResolver(http_get=ok, hot_root=tmp_path)
    assert res1.resolve("RE")["resolved"] is True
    # A fresh resolver whose network is down still resolves from the disk snapshot.
    def boom(url, timeout):
        raise OSError("network down")

    res2 = OkxInstrumentResolver(http_get=boom, hot_root=tmp_path)
    assert res2.resolve("RE")["primary_inst_id"] == "RE-USDT-SWAP"


def test_instruments_universe_fetched_once_per_type(tmp_path):
    calls: list[str] = []
    resolver = OkxInstrumentResolver(http_get=_make_http_get(calls=calls), hot_root=tmp_path)
    resolver.resolve("RE")
    resolver.resolve("GRAM")
    resolver.resolve("XAU")
    # All three are SWAP hits -> SWAP universe fetched exactly once (cached).
    swap_calls = [u for u in calls if "instType=SWAP" in u]
    assert len(swap_calls) == 1


def test_resolve_instrument_convenience(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_HOT_ROOT", str(tmp_path))
    r = resolve_instrument("RE", http_get=_make_http_get())
    assert r["primary_inst_id"] == "RE-USDT-SWAP"


def test_resolver_source_has_no_private_or_order_paths():
    src = (_ROOT / "src" / "scout" / "okx_instrument_resolver.py").read_text(encoding="utf-8")
    low = src.lower()
    for forbidden in ("/account", "/trade", "place-order", "place_order", "ok-access",
                      "passphrase", "secretkey", "secret_key", "apikey", "api_key",
                      "okx_client", "auto_execute"):
        assert forbidden not in low, f"forbidden token in resolver source: {forbidden}"
    assert "/api/v5/public/instruments" in src
