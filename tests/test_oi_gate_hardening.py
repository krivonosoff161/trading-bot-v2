# -*- coding: utf-8 -*-
"""Phase 0.5 — OI gate hardening.

A sweep must be released only when OI is ACTUALLY merged onto candles with enough
coverage — not merely because an oi-slot file exists. enrich_oi_one refuses to write a
barely-covered OI field and returns a precise status.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.research_lab.farm_coordinator import _gate_clear
from src.research_lab.flow_enrich import enrich_oi_one

_HOUR = 3_600_000
_START = 1_700_000_000_000


class _FakeOI:
    def __init__(self, points):
        self._points = points

    def fetch_open_interest(self, symbol, timeframe, start_ms, end_ms):
        return self._points


def _write_candles(tmp_path: Path, n: int = 100) -> Path:
    d = tmp_path / "market_data" / "1h"
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"ts": _START + i * _HOUR, "open": 1.0, "high": 1.1, "low": 0.9,
             "close": 1.0, "vol": 5.0} for i in range(n)]
    path = d / f"AAA_USDT_SWAP_{_START}_{_START + (n - 1) * _HOUR}_1h.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


class TestEnrichCoverage:
    def test_full_coverage_enriches(self, tmp_path) -> None:
        path = _write_candles(tmp_path, 100)
        points = [(_START + i * _HOUR, 1000.0 + i) for i in range(100)]
        status, n = enrich_oi_one(path, "AAA-USDT-SWAP", "1h", provider=_FakeOI(points), now_ms=_START)
        assert status == "enriched" and n == 100
        rows = json.loads(path.read_text(encoding="utf-8"))
        assert all(r.get("oi") is not None for r in rows)

    def test_low_coverage_not_written(self, tmp_path) -> None:
        path = _write_candles(tmp_path, 100)
        # OI only for the last 10 candles -> ~10% coverage < 0.5 default
        points = [(_START + i * _HOUR, 1000.0 + i) for i in range(90, 100)]
        status, _n = enrich_oi_one(path, "AAA-USDT-SWAP", "1h", provider=_FakeOI(points), now_ms=_START)
        assert status == "not_enough_coverage"
        rows = json.loads(path.read_text(encoding="utf-8"))
        assert all("oi" not in r for r in rows)  # file untouched, no partial OI written

    def test_no_points_status(self, tmp_path) -> None:
        path = _write_candles(tmp_path, 50)
        status, n = enrich_oi_one(path, "AAA-USDT-SWAP", "1h", provider=_FakeOI([]), now_ms=_START)
        assert status == "no_points" and n == 0

    def test_provider_failure_status(self, tmp_path) -> None:
        path = _write_candles(tmp_path, 50)

        class _Boom:
            def fetch_open_interest(self, *a, **k):
                raise RuntimeError("network")

        status, _n = enrich_oi_one(path, "AAA-USDT-SWAP", "1h", provider=_Boom(), now_ms=_START)
        assert status == "fetch_failed"


class TestGateClear:
    def _state_fn(self, *, enrichment=(), oi_available=False):
        def fn(symbol, timeframe):
            return {"status": "usable", "rows": 200, "fingerprint": "fp",
                    "enrichment": enrichment, "oi_available": oi_available}
        return fn

    def _task(self):
        return {"family": "oi_price_quadrant", "symbol": "BTC-USDT-SWAP", "timeframe": "1h"}

    def test_oi_slot_alone_does_not_clear(self) -> None:
        # The previous weak behavior: an oi-slot file existing unblocked the sweep.
        assert _gate_clear(self._task(), self._state_fn(oi_available=True)) is False

    def test_merged_oi_clears(self) -> None:
        assert _gate_clear(self._task(), self._state_fn(enrichment=("oi",))) is True
