# -*- coding: utf-8 -*-
"""Tests for trigger enrichment (offline; injected resolver/provider/mentions)."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import trigger_enrichment as TE  # noqa: E402
from src.scout.okx_instrument_resolver import OkxInstrumentResolver  # noqa: E402

_NOW_MS = 1_780_000_000_000

_UNIVERSE = {
    "SWAP": [
        {"instId": "RE-USDT-SWAP", "instType": "SWAP", "state": "live", "listTime": "1700000000000"},
        {"instId": "BTC-USDT-SWAP", "instType": "SWAP", "state": "live"},  # GEOD genuinely absent
    ],
    "SPOT": [],
}


def _http_get(url, timeout):
    import urllib.parse
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    inst_type = (qs.get("instType") or ["SWAP"])[0]
    return {"code": "0", "data": _UNIVERSE.get(inst_type, [])}


def _resolver(tmp_path):
    return OkxInstrumentResolver(http_get=_http_get, hot_root=tmp_path)


class _FakeProvider:
    def __init__(self, rows):
        self._rows = rows

    def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
        n = int(self._rows.get(timeframe, 0))
        return [{"ts": start_ts + i, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1} for i in range(n)]


def test_body_status_extracted():
    b = TE.body_status("x" * 400)
    assert b["extracted"] is True and b["status"] == "extracted"


def test_body_status_missing_says_why():
    b = TE.body_status("", extraction_status="paywall_blocked")
    assert b["extracted"] is False
    assert b["reason"] == "paywall_blocked"   # explicit reason, not a placeholder
    b2 = TE.body_status("")
    assert b2["reason"] == "empty"


def test_unresolved_asset_is_not_farm_eligible(tmp_path):
    pkg = TE.build_trigger_package(
        asset="GEOD", okx_inst="GEOD-USDT-SWAP", asset_class="crypto_alt",
        headline="GEOD listing rumor", body_text="x" * 300,
        resolver=_resolver(tmp_path), provider=_FakeProvider({"15m": 200}), now_ms=_NOW_MS,
    )
    assert pkg["resolution"]["resolved"] is False
    assert pkg["farm_eligible"] is False
    assert pkg["farm_pending_reason"] == "no_okx_instrument"


def test_resolved_and_usable_is_farm_eligible(tmp_path):
    pkg = TE.build_trigger_package(
        asset="RE", okx_inst="RE-USDT-SWAP", asset_class="crypto_alt",
        headline="RE listed", body_text="x" * 300,
        resolver=_resolver(tmp_path),
        provider=_FakeProvider({"15m": 200, "1h": 200, "4h": 200}), now_ms=_NOW_MS,
    )
    assert pkg["resolution"]["resolved"] is True
    assert pkg["readiness"]["status"] == "usable"
    assert pkg["farm_eligible"] is True
    assert pkg["farm_pending_reason"] is None


def test_resolved_but_too_short_pending(tmp_path):
    pkg = TE.build_trigger_package(
        asset="RE", okx_inst="RE-USDT-SWAP", asset_class="crypto_alt",
        headline="RE listed", body_text="x" * 300,
        resolver=_resolver(tmp_path),
        provider=_FakeProvider({"15m": 5, "1h": 3, "4h": 1}), now_ms=_NOW_MS,
    )
    assert pkg["farm_eligible"] is False
    assert pkg["farm_pending_reason"] in ("too_short", "fresh_listing_pending")


def test_no_provider_means_readiness_not_assessed(tmp_path):
    pkg = TE.build_trigger_package(
        asset="RE", okx_inst="RE-USDT-SWAP", asset_class="crypto_alt",
        headline="RE listed", body_text="x" * 300,
        resolver=_resolver(tmp_path), provider=None, now_ms=_NOW_MS,
    )
    assert pkg["readiness"]["status"] is None
    assert pkg["farm_eligible"] is None
    assert pkg["farm_pending_reason"] == "readiness_not_assessed"


def test_default_resolution_uses_shared_resolver_facade(monkeypatch):
    calls = []

    def fake_resolve(query):
        calls.append(query)
        return {
            "query": query,
            "resolved": False,
            "primary_inst_id": None,
            "inst_type": None,
            "asset_class": "crypto_alt",
            "candidates": [],
            "reason": "no_okx_instrument",
            "confidence": 0.0,
            "state": None,
            "list_time_ms": None,
        }

    monkeypatch.setattr(TE, "resolve_instrument", fake_resolve)
    pkg = TE.build_trigger_package(
        asset="GEOD", okx_inst="GEOD-USDT-SWAP", asset_class="crypto_alt",
        headline="GEOD rumor", body_text="x" * 300,
        provider=None, now_ms=_NOW_MS,
    )
    assert calls == ["GEOD-USDT-SWAP"]
    assert pkg["farm_eligible"] is False
    assert pkg["farm_pending_reason"] == "no_okx_instrument"


def test_mentions_merged_and_counted(tmp_path):
    calls = []

    def mentions_fn(asset):
        calls.append(asset)
        return [{"title": "RE pump", "source": "decrypt"}, {"title": "RE listing", "source": "okx"}]

    pkg = TE.build_trigger_package(
        asset="RE", okx_inst="RE-USDT-SWAP", asset_class="crypto_alt",
        headline="RE listed", body_text="x" * 300,
        resolver=_resolver(tmp_path), provider=None, mentions_fn=mentions_fn, now_ms=_NOW_MS,
    )
    assert pkg["mention_count"] == 2
    assert calls == ["RE"]


def test_package_to_journal_fields(tmp_path):
    pkg = TE.build_trigger_package(
        asset="RE", okx_inst="RE-USDT-SWAP", asset_class="crypto_alt",
        headline="RE listed", body_text="x" * 300,
        resolver=_resolver(tmp_path),
        provider=_FakeProvider({"15m": 200, "1h": 200, "4h": 200}), now_ms=_NOW_MS,
    )
    fields = TE.package_to_journal_fields(pkg)
    assert fields["okx_resolved"] is True
    assert fields["okx_inst_type"] == "SWAP"
    assert fields["okx_asset_class"] == "crypto_alt"
    assert fields["data_readiness_status"] == "usable"
    assert fields["selected_timeframe"] in ("15m", "1h", "4h")
    assert fields["farm_eligible"] is True
