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
    assert "--fail-on-blocked" in text
    assert text.count('start "Strategy Lab -') >= 4


def test_paper_product_control_room_wraps_canonical_control_room_with_product_cadence():
    text = _read("paper_product_control_room.bat")

    assert "strategy_lab_control_room.bat" in text
    assert "STRATEGY_LAB_FARM_SLEEP_SECONDS=180" in text
    assert "STRATEGY_LAB_STATUS_SLEEP_SECONDS=120" in text
    assert "STRATEGY_LAB_PAPER_TELEGRAM_SEND=0" in text
    assert "old main.py" in text
    assert "AUTO_TRADE" in text
    assert "place_order" not in text.lower()
    assert "main.py" in text


def test_paper_product_send_launcher_requires_explicit_wrapper():
    text = _read("paper_product_control_room_send.bat")

    assert "STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM=1" in text
    assert "STRATEGY_LAB_PAPER_TELEGRAM_FETCH_CHART_CANDLES=1" in text
    assert "paper_product_control_room.bat" in text
    assert "AUTO_TRADE" in text
    assert "private exchange endpoints" in text


def test_paper_product_headless_loop_skips_dashboard_and_graph():
    text = _read("paper_product_headless_loop.bat")

    assert "strategy_lab_farm_full_cycle_loop.bat" in text
    assert "strategy_lab_control_room.bat" not in text
    assert "strategy_lab_dashboard.bat" not in text
    assert "strategy_lab_graph_viewer.bat" not in text
    assert "strategy_lab_status_monitor.bat" not in text
    assert "dashboard=off" in text
    assert "graph=off" in text
    assert "STRATEGY_LAB_RUN_AGENT_ROLE_REVIEWS=1" in text
    assert "STRATEGY_LAB_AGENT_ROLE_MAX_OUTCOMES=2" in text
    assert "STRATEGY_LAB_PAPER_TELEGRAM_SEND=0" in text
    assert "AUTO_TRADE" in text
    assert "private endpoints" in text
    assert "place_order" not in text.lower()


def test_paper_product_headless_send_launcher_is_explicit():
    text = _read("paper_product_headless_send_loop.bat")

    assert "STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM=1" in text
    assert "STRATEGY_LAB_PAPER_TELEGRAM_FETCH_CHART_CANDLES=1" in text
    assert "paper_product_headless_loop.bat" in text
    assert "strategy_lab_control_room.bat" not in text
    assert "dashboard" in text
    assert "graph-viewer" in text
    assert "AUTO_TRADE" in text
    assert "private exchange endpoints" in text


def test_control_room_keeps_paper_sender_single_writer_inside_farm_loop():
    text = _read("strategy_lab_control_room.bat")
    loop = _read("strategy_lab_farm_full_cycle_loop.bat")
    sender = _read("strategy_lab_paper_telegram_sender_loop.bat")

    assert "STRATEGY_LAB_PAPER_TELEGRAM_SEND" in text
    assert "Telegram paper delivery runs inside Farm Full Cycle Loop" in text
    assert "STRATEGY_LAB_PAPER_TELEGRAM_SEND_ARG=--send-paper-telegram" in loop
    assert "%STRATEGY_LAB_PAPER_TELEGRAM_SEND_ARG%" in loop
    assert "start \"Strategy Lab - Paper Telegram Sender\"" not in text
    assert "&& bat\\strategy_lab_paper_telegram_sender_loop.bat" not in text
    assert "manual fallback only" in text
    assert "scripts.strategy_lab.paper_telegram_sender" in sender
    assert "--send" in sender
    assert "active subscription users" in sender
    assert "AUTO_TRADE" in sender


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
    assert "--fail-on-blocked" in text
    assert "scripts.strategy_lab.farm_status_report" in text


def test_full_cycle_bat_preserves_paper_pfr_runtime_contract():
    text = _read("strategy_lab_farm_full_cycle_loop.bat")

    required_flags = [
        "--run-worker",
        "--run-validation",
        "--run-paper",
        "--run-paper-signals",
        "--enrich-funding",
        "--enrich-oi",
        "--pfr-db-path",
        "--paper-signals-max-observe",
        "--paper-signals-max-pfr-scan",
        "--paper-signals-pfr-reserved",
        "--paper-signals-fetch-timeout",
        "--paper-signals-timeframes",
        "--main-paper-runtime-limit",
        "--calculator-advisor-max-calls",
        "--calculator-provider",
        "--calculator-model",
        "--calculator-base-url",
        "--calculator-timeout",
        "--run-journal-export",
        "--private-root",
        "--stop-file",
    ]
    for flag in required_flags:
        assert flag in text

    required_defaults = [
        "STRATEGY_LAB_PFR_DB_PATH=%TRADING_BOT_RESEARCH_ROOT%\\state\\strategy_lab.sqlite",
        "STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE=20",
        "STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_SCAN=30",
        "STRATEGY_LAB_PAPER_SIGNALS_PFR_RESERVED=2",
        "STRATEGY_LAB_PAPER_SIGNALS_TIMEFRAMES=15m,1h,4h",
        "STRATEGY_LAB_MAIN_PAPER_RUNTIME_LIMIT=50",
        "STRATEGY_LAB_RUN_CALCULATOR_ADVISOR=1",
        "STRATEGY_LAB_CALCULATOR_ADVISOR_MAX_CALLS=1",
        "STRATEGY_LAB_RUN_JOURNAL_EXPORT=1",
    ]
    for default in required_defaults:
        assert default in text


def test_full_cycle_bat_keeps_notifications_and_money_paths_out():
    text = _read("strategy_lab_farm_full_cycle_loop.bat").lower()

    forbidden = [
        "auto_trade=true",
        "telegram_bot.py",
        "paper_chat_id",
        "telegram_bot_token",
        "place_order",
        "place_market_order",
        "set_leverage",
        "src.exchange.okx_client",
        "main.py",
        "start_all.bat",
    ]
    for token in forbidden:
        assert token not in text


def test_visible_launchers_stop_on_blocked_preflight():
    text = (_read("strategy_lab_control_room.bat") + "\n" + _read("strategy_lab_farm_full_cycle_loop.bat"))

    assert "Preflight blocked" in text
    assert "if errorlevel 1" in text
