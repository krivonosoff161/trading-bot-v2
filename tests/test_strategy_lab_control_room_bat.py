from pathlib import Path


def _read(name: str) -> str:
    return Path("bat", name).read_text(encoding="utf-8")


def test_control_room_starts_visible_strategy_lab_surfaces():
    text = _read("strategy_lab_control_room.bat")

    assert "strategy_lab_farm_full_cycle_loop.bat" in text
    assert "strategy_lab_dashboard.bat" in text
    assert "strategy_lab_graph_viewer.bat" in text
    assert "strategy_lab_status_monitor.bat" in text
    assert "scripts.strategy_lab.operational_health" in text
    assert "--pfr-db-path" in text
    assert text.count('start "Strategy Lab -') >= 4


def test_control_room_does_not_start_live_or_secret_paths():
    text = (_read("strategy_lab_control_room.bat") + "\n" + _read("strategy_lab_status_monitor.bat")).lower()

    forbidden = [
        "start_all.bat",
        "main.py",
        "auto_trade=true",
        "src.exchange.okx_client",
        "place_order",
        "place_market_order",
        "telegram_bot.py",
    ]
    for token in forbidden:
        assert token not in text


def test_status_monitor_is_read_only_and_stop_file_bounded():
    text = _read("strategy_lab_status_monitor.bat")

    assert "scripts.strategy_lab.farm_status_report" in text
    assert "STOP_FARM_FULL_CYCLE.txt" in text
    assert "while (-not (Test-Path" in text


def test_full_cycle_bat_points_to_fast_health_and_detailed_status():
    text = (_read("strategy_lab_farm_full_cycle_loop.bat") + "\n" + _read("strategy_lab_farm_full_cycle_stop.bat"))

    assert "scripts.strategy_lab.operational_health" in text
    assert "scripts.strategy_lab.farm_status_report" in text
