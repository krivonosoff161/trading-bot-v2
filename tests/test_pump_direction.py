from __future__ import annotations

import json

from scripts.ws import ws_pump_engine_v2 as engine_mod


def test_engine_reads_direction_from_active_universe(tmp_path, monkeypatch) -> None:
    path = tmp_path / "active_universe.json"
    path.write_text(
        json.dumps(
            {
                "active": ["AAA-USDT-SWAP", "BBB-USDT-SWAP"],
                "symbols": {
                    "AAA-USDT-SWAP": {"direction": "up"},
                    "BBB-USDT-SWAP": {"direction": "down"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(engine_mod, "ACTIVE_UNIVERSE_PATH", path)

    engine = engine_mod.PumpEngineV2()
    engine.universe_meta = engine._read_active_universe()

    assert engine._side_from_universe("AAA-USDT-SWAP") == "buy"
    assert engine._side_from_universe("BBB-USDT-SWAP") == "sell"


def test_engine_fallback_keeps_existing_direction_stable(tmp_path, monkeypatch) -> None:
    path = tmp_path / "active_universe.json"
    path.write_text(json.dumps({"active": ["AAA-USDT-SWAP"]}), encoding="utf-8")
    monkeypatch.setattr(engine_mod, "ACTIVE_UNIVERSE_PATH", path)

    engine = engine_mod.PumpEngineV2()
    meta = engine._read_active_universe()
    engine.universe_meta = meta

    assert meta["AAA-USDT-SWAP"]["direction"] == "up"
    assert engine._side_from_universe("AAA-USDT-SWAP") == "buy"
