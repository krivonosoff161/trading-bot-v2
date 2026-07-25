from __future__ import annotations

import json


def test_oi_enrichment_records_when_canonical_rows_became_available(tmp_path, monkeypatch):
    from scripts.strategy_lab import enrich_oi_data

    prepared = tmp_path / "prepared.json"
    prepared.write_text(json.dumps([{"ts": 1}]), encoding="utf-8")
    observed: dict = {}

    monkeypatch.setattr(enrich_oi_data, "choose_symbol_file", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(
        enrich_oi_data,
        "load_oi_series",
        lambda *_args: {
            "points": [{"ts": 1, "oi": 2.0}],
            "source": "synthetic",
            "rejected": [],
        },
    )
    monkeypatch.setattr(
        enrich_oi_data,
        "merge_oi",
        lambda rows, _points: [{**row, "oi": 2.0} for row in rows],
    )
    monkeypatch.setattr(enrich_oi_data, "coverage", lambda *_args: 1.0)
    monkeypatch.setattr(enrich_oi_data, "load_candles", lambda *_args: [{"ts": 1, "oi": 2.0}])

    def record_sync(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return 1

    monkeypatch.setattr(enrich_oi_data, "sync_json_to_store", record_sync)
    monkeypatch.setattr(enrich_oi_data.time, "time_ns", lambda: 123_456_000_000)

    result = enrich_oi_data.enrich("BTC_USDT_SWAP", "1h", tmp_path, apply=True)

    assert result["status"] == "enriched"
    assert observed["args"] == (tmp_path, "BTC_USDT_SWAP", "1h", prepared)
    assert observed["kwargs"] == {
        "source": "oi_enrichment",
        "available_at_ms": 123_456,
    }


def test_funding_enrichment_records_when_canonical_rows_became_available(tmp_path, monkeypatch):
    from scripts.strategy_lab import enrich_flow_data

    prepared = tmp_path / "prepared.json"
    prepared.write_text(json.dumps([{"ts": 1}, {"ts": 2}]), encoding="utf-8")
    observed: dict = {}

    class SyntheticProvider:
        def fetch_funding(self, symbol, start_ts, end_ts):
            assert (symbol, start_ts, end_ts) == ("BTC_USDT_SWAP", 1, 2)
            return [{"ts": 1, "funding": 0.001}]

    monkeypatch.setattr(enrich_flow_data, "choose_symbol_file", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(enrich_flow_data, "load_candles", lambda *_args: [{"ts": 1}, {"ts": 2}])
    monkeypatch.setattr(enrich_flow_data, "OkxPublicFundingProvider", SyntheticProvider)
    monkeypatch.setattr(
        enrich_flow_data,
        "merge_funding",
        lambda rows, _points: [{**row, "funding": 0.001} for row in rows],
    )
    monkeypatch.setattr(enrich_flow_data, "coverage", lambda *_args: 1.0)

    def record_sync(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return 1

    monkeypatch.setattr(enrich_flow_data, "sync_json_to_store", record_sync)
    monkeypatch.setattr(enrich_flow_data.time, "time_ns", lambda: 987_654_000_000)

    result = enrich_flow_data.enrich("BTC_USDT_SWAP", "1h", tmp_path, apply=True)

    assert result["status"] == "enriched"
    assert observed["args"] == (tmp_path, "BTC_USDT_SWAP", "1h", prepared)
    assert observed["kwargs"] == {
        "source": "funding_enrichment",
        "available_at_ms": 987_654,
    }
