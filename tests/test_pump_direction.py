from __future__ import annotations

import json

from scripts.archive.ws_pump_orchestrator import PumpOrchestrator


def _make_orchestrator(tmp_path, monkeypatch, payload: dict) -> PumpOrchestrator:
    path = tmp_path / "active_universe.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("scripts.archive.ws_pump_orchestrator.ACTIVE_UNIVERSE_PATH", path)
    orch = PumpOrchestrator.__new__(PumpOrchestrator)
    orch.config = {}
    return orch


def test_reads_direction_from_symbols_key(tmp_path, monkeypatch) -> None:
    orch = _make_orchestrator(
        tmp_path,
        monkeypatch,
        {
            "active": ["AAA-USDT-SWAP", "BBB-USDT-SWAP"],
            "symbols": {
                "AAA-USDT-SWAP": {"direction": "up"},
                "BBB-USDT-SWAP": {"direction": "down"},
            },
        },
    )
    result = orch._read_universe()
    assert result["AAA-USDT-SWAP"]["direction"] == "up"
    assert result["BBB-USDT-SWAP"]["direction"] == "down"


def test_fallback_active_list_defaults_to_up(tmp_path, monkeypatch) -> None:
    orch = _make_orchestrator(
        tmp_path,
        monkeypatch,
        {"active": ["AAA-USDT-SWAP"]},
    )
    result = orch._read_universe()
    assert result["AAA-USDT-SWAP"]["direction"] == "up"


def test_missing_file_returns_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.archive.ws_pump_orchestrator.ACTIVE_UNIVERSE_PATH",
        tmp_path / "nonexistent.json",
    )
    orch = PumpOrchestrator.__new__(PumpOrchestrator)
    orch.config = {}
    result = orch._read_universe()
    assert result == {}
