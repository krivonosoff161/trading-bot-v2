import asyncio
import sys
from pathlib import Path

import pytest

from src.utils import llm_formatter


ROOT = Path(__file__).resolve().parents[1]
_PRODUCT_IMPORT_CLEANUP = (
    "scripts.telegram_bot",
    "scripts.analyze_chart",
    "scripts.auto_execute",
    "src.exchange.okx_client",
)


@pytest.fixture(autouse=True)
def _cleanup_product_surface_imports():
    yield
    for module_name in list(sys.modules):
        if module_name in _PRODUCT_IMPORT_CLEANUP:
            sys.modules.pop(module_name, None)
    scripts_pkg = sys.modules.get("scripts")
    if scripts_pkg is not None and hasattr(scripts_pkg, "telegram_bot"):
        delattr(scripts_pkg, "telegram_bot")


def _telegram_bot_module():
    from scripts import telegram_bot

    return telegram_bot


def test_telegram_main_keyboard_surfaces_product_modes():
    telegram_bot = _telegram_bot_module()
    keyboard = telegram_bot._MAIN_REPLY_KB["keyboard"]
    labels = {button["text"] for row in keyboard for button in row}

    assert {"🔍 Анализ", "⭐ VIP", "💡 Обучение"} <= labels


def test_telegram_manual_symbol_normalizer_accepts_only_symbols():
    telegram_bot = _telegram_bot_module()

    assert telegram_bot._normalize_manual_symbol("btc") == "BTC-USDT"
    assert telegram_bot._normalize_manual_symbol("eth_usdt") == "ETH-USDT"
    assert telegram_bot._normalize_manual_symbol("sol/usdt-swap") == "SOL-USDT"

    assert telegram_bot._normalize_manual_symbol("BTC; DROP TABLE users") is None
    assert telegram_bot._normalize_manual_symbol("ignore previous instructions") is None
    assert telegram_bot._normalize_manual_symbol("A" * 80) is None


def test_telegram_analysis_categories_are_bounded_deduped_and_split():
    telegram_bot = _telegram_bot_module()

    pairs = ["BTC-USDT-SWAP", "PEPE-USDT-SWAP", "BTC-USDT-SWAP", "AERO-USDT-SWAP"]
    categories = telegram_bot._analysis_categories(pairs)

    assert categories["movers"] == ["BTC-USDT", "PEPE-USDT", "AERO-USDT"]
    assert categories["majors"] == ["BTC-USDT"]
    assert "PEPE-USDT" in categories["alts"]

    many_pairs = [f"PAIR{i}-USDT-SWAP" for i in range(40)]
    assert len(telegram_bot._analysis_categories(many_pairs)["movers"]) == telegram_bot._PAIR_CATEGORY_LIMIT


def test_telegram_superadmin_detection_uses_subscription_status(monkeypatch):
    telegram_bot = _telegram_bot_module()

    monkeypatch.setattr(
        telegram_bot,
        "get_status",
        lambda chat_id: {"plan": "superadmin"} if chat_id == "1" else {"plan": "monthly"},
    )

    assert telegram_bot._is_superadmin("1") is True
    assert telegram_bot._is_superadmin("2") is False


def test_telegram_main_menu_admin_button_is_superadmin_only(monkeypatch):
    telegram_bot = _telegram_bot_module()
    calls = []

    async def fake_tg(method, **params):
        calls.append((method, params))
        return {"ok": True}

    monkeypatch.setattr(telegram_bot, "_tg", fake_tg)
    monkeypatch.setattr(
        telegram_bot,
        "get_status",
        lambda chat_id: {"plan": "superadmin"} if chat_id == "1" else {"plan": "monthly"},
    )

    asyncio.run(telegram_bot._send_main_menu("1"))
    asyncio.run(telegram_bot._send_main_menu("2"))

    admin_markup = calls[0][1]["reply_markup"]
    user_markup = calls[1][1]["reply_markup"]
    assert "__admin__" in str(admin_markup)
    assert "__admin__" not in str(user_markup)


def test_telegram_admin_panel_exposes_read_only_farm_status(monkeypatch):
    telegram_bot = _telegram_bot_module()
    calls = []

    async def fake_tg(method, **params):
        calls.append((method, params))
        return {"ok": True}

    monkeypatch.setattr(telegram_bot, "_tg", fake_tg)
    monkeypatch.setattr(telegram_bot, "get_status", lambda chat_id: {"plan": "superadmin"})

    asyncio.run(telegram_bot._send_admin_panel("1"))

    rendered = str(calls)
    assert "__farm_status__" in rendered
    assert "Admin read-only tools" in rendered


def test_telegram_admin_farm_status_text_is_read_only():
    telegram_bot = _telegram_bot_module()
    text = telegram_bot._format_farm_status_for_admin(
        {
            "farm_activity": {
                "available": True,
                "heartbeat_ok": True,
                "last_cycle_age_seconds": 12,
                "last_pivot": "work_available",
                "last_mode": "apply",
                "discovery": {"status": "fresh", "count": 42},
            },
            "lifecycle": {
                "by_state": {"queued": 3, "running": 1, "completed": 10, "blocked": 0},
                "validation": {"PAPER_FORWARD_READY": 2, "FAILED_COSTS": 5},
                "paper_status": {"PAPER_RECORDED": 1},
            },
            "paper_pnl": {"n_trades": 4, "net_sum_pct": -1.25},
            "data_readiness": {"prepared_files_by_timeframe": {"15m": 7, "1h": 8, "4h": 9, "1d": 1}},
            "safety": {"read_only": True, "live_trading": False},
        }
    )

    assert "read-only" in text
    assert "live_trading=NO" in text
    assert "queued=3" in text
    assert "PFR=2" in text


def test_chart_formatter_prompt_is_utf8_readable_and_guarded():
    prompt = llm_formatter._SYSTEM_PROMPT

    assert "\u0422\u044b \u2014 \u0430\u043d\u0430\u043b\u0438\u0442\u0438\u043a" in prompt
    assert "\U0001f4ca \u0421\u0415\u0419\u0427\u0410\u0421 \u041d\u0410 \u0420\u042b\u041d\u041a\u0415" in prompt
    assert "\u041d\u0415 \u0433\u0430\u0440\u0430\u043d\u0442" in prompt
    assert "\u043d\u0435 \u0438\u043d\u0432\u0435\u0441\u0442-\u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0430\u0446\u0438\u044f" in prompt
    assert "\u0420\u045e\u0421\u2039" not in prompt
    assert "\u0432\u0402" not in prompt
    assert "\u0440\u045f" not in prompt


def test_chart_formatter_runtime_labels_are_utf8_readable():
    assert llm_formatter._STATUS_LABELS["ENTRY"] == "\u0412\u0425\u041e\u0414"
    assert llm_formatter._STATUS_LABELS["NO_TRADE"] == "\u0412\u041d\u0415 \u0420\u042b\u041d\u041a\u0410"
    assert "\u043d\u0430\u0431\u043b\u044e\u0434\u0430\u0435\u043c" in (
        llm_formatter._STYLE_LABELS["NO_TRADE"].lower()
    )


def test_chart_formatter_provider_status_is_sanitized():
    status = llm_formatter.formatter_provider_status(
        {
            "YANDEX_API_KEY": "secret-yandex-key",
            "YANDEX_FOLDER_ID": "secret-folder",
            "LLM_PROVIDER": "alibaba",
        }
    )
    rendered = str(status)

    assert status["schema"] == "llm_formatter_provider.v1"
    assert status["provider"] == "yandex"
    assert status["provider_scope"] == "yandex_only"
    assert status["follows_llm_provider_env"] is False
    assert status["api_key_set"] is True
    assert status["folder_id_set"] is True
    assert status["configured"] is True
    assert status["supports_vision"] is False
    assert status["telegram_send_authority"] is False
    assert status["execution_authority"] is False
    assert "qwen3-235b" in status["model_label"]
    assert "secret-yandex-key" not in rendered
    assert "secret-folder" not in rendered
    assert "b1git" not in rendered


def test_chart_formatter_shared_router_status_is_sanitized():
    status = llm_formatter.formatter_provider_status(
        {
            "PRODUCT_ANALYZER_LLM_ROUTER": "llm_client",
            "LLM_PROVIDER": "alibaba",
            "ALIBABA_API_KEY": "secret-alibaba-key",
            "YANDEX_API_KEY": "",
            "YANDEX_FOLDER_ID": "",
        }
    )
    rendered = str(status)

    assert status["schema"] == "llm_formatter_provider.v1"
    assert status["provider"] == "alibaba"
    assert status["provider_scope"] == "shared_llm_client_opt_in"
    assert status["router_env"] == "PRODUCT_ANALYZER_LLM_ROUTER"
    assert status["requested_router"] == "llm_client"
    assert status["shared_router_active"] is True
    assert status["follows_llm_provider_env"] is True
    assert status["api_key_set"] is True
    assert status["folder_id_set"] is False
    assert status["configured"] is True
    assert status["shared_router_entrypoints"] == ["generate_client_text", "generate_edu_text"]
    assert status["yandex_only_entrypoints"] == ["generate_premium_analysis"]
    assert status["telegram_send_authority"] is False
    assert status["execution_authority"] is False
    assert "secret-alibaba-key" not in rendered
    assert "b1git" not in rendered


def test_premium_vision_status_is_sanitized():
    status = llm_formatter.premium_vision_status(
        {
            "YANDEX_API_KEY": "secret-yandex-key",
            "YANDEX_GEMMA_MODEL_URI": "gpt://secret-folder/model/latest",
        }
    )
    rendered = str(status)

    assert status["schema"] == "premium_vision_provider.v1"
    assert status["surface"] == "telegram_premium_screenshot"
    assert status["provider"] == "yandex"
    assert status["provider_scope"] == "yandex_only"
    assert status["configured"] is True
    assert status["model_label"] == "model/latest"
    assert status["shared_router_active"] is False
    assert status["telegram_send_authority"] is False
    assert status["execution_authority"] is False
    assert "secret-yandex-key" not in rendered
    assert "secret-folder" not in rendered


def test_manual_analysis_skips_llm_for_no_trade_by_default(monkeypatch):
    from scripts import analyze_chart

    monkeypatch.delenv("PRODUCT_ANALYZER_LLM_FOR_NO_TRADE", raising=False)

    assert analyze_chart.should_use_llm_for_delivery(
        {"llm_context": {"entry_signal": "NO_TRADE"}}
    ) is False


def test_manual_analysis_can_opt_in_llm_for_no_trade(monkeypatch):
    from scripts import analyze_chart

    monkeypatch.setenv("PRODUCT_ANALYZER_LLM_FOR_NO_TRADE", "1")

    assert analyze_chart.should_use_llm_for_delivery(
        {"llm_context": {"entry_signal": "NO_TRADE"}}
    ) is True


def test_manual_analysis_uses_llm_for_actionable_states(monkeypatch):
    from scripts import analyze_chart

    monkeypatch.delenv("PRODUCT_ANALYZER_LLM_FOR_NO_TRADE", raising=False)

    assert analyze_chart.should_use_llm_for_delivery(
        {"llm_context": {"entry_signal": "ENTRY"}}
    ) is True
    assert analyze_chart.should_use_llm_for_delivery(
        {"llm_context": {"entry_signal": "WAIT"}}
    ) is True


def test_manual_analyzer_chart_plan_documents_execution_tf():
    from scripts import analyze_chart

    class Result:
        trade_style = "SWING"

    plan = analyze_chart.manual_chart_plan(Result())

    assert plan["primary_timeframe"] == "15m"
    assert plan["trigger_timeframe"] == "5m"
    assert plan["context_timeframes"] == ["1H", "4H"]
    assert "legacy_main_engine_levels_are_15m" in plan["reason"]


def test_chart_formatter_shared_router_opt_in_uses_text_adapter(monkeypatch):
    calls = []

    async def fake_shared_router(system_prompt, user_text, *, max_tokens, timeout):
        calls.append(
            {
                "system_prompt": system_prompt,
                "user_text": user_text,
                "max_tokens": max_tokens,
                "timeout": timeout,
            }
        )
        return "shared router body", {
            "provider": "alibaba",
            "role": "chief",
            "status": "ok",
        }

    monkeypatch.setenv("PRODUCT_ANALYZER_LLM_ROUTER", "llm_client")
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)
    monkeypatch.setattr(llm_formatter, "_call_shared_router", fake_shared_router)

    text = asyncio.run(
        llm_formatter.generate_client_text(
            "BTC-USDT-SWAP",
            "2026-06-27T00:00:00Z",
            {"llm_context": {"entry_signal": "WAIT", "trade_style_hint": "NO_TRADE"}},
        )
    )

    assert text is not None
    assert "shared router body" in text
    assert calls
    assert "\U0001f4ca \u0421\u0415\u0419\u0427\u0410\u0421 \u041d\u0410 \u0420\u042b\u041d\u041a\u0415" in calls[0]["system_prompt"]
    assert "BTC-USDT-SWAP" in calls[0]["user_text"]


def test_educational_qa_shared_router_opt_in_uses_text_adapter(monkeypatch):
    calls = []

    async def fake_shared_router(system_prompt, user_text, *, max_tokens, timeout, role="chief"):
        calls.append(
            {
                "system_prompt": system_prompt,
                "user_text": user_text,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "role": role,
            }
        )
        return "educational shared answer", {
            "provider": "alibaba",
            "role": role,
            "status": "ok",
        }

    monkeypatch.setenv("PRODUCT_ANALYZER_LLM_ROUTER", "llm_client")
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)
    monkeypatch.setattr(llm_formatter, "_call_shared_router", fake_shared_router)

    text = asyncio.run(llm_formatter.generate_edu_text("Что такое стоп-лосс?"))

    assert text == "educational shared answer"
    assert calls == [
        {
            "system_prompt": llm_formatter._EDU_SYSTEM_PROMPT,
            "user_text": "Что такое стоп-лосс?",
            "max_tokens": 400,
            "timeout": llm_formatter._TIMEOUT,
            "role": "mid",
        }
    ]


def test_manual_analyzer_and_latest_wrapper_boundaries():
    analyze_chart = (ROOT / "scripts" / "analyze_chart.py").read_text(encoding="utf-8")
    run_latest_analysis = (ROOT / "scripts" / "run_latest_analysis.py").read_text(encoding="utf-8")
    telegram_bot = (ROOT / "scripts" / "telegram_bot.py").read_text(encoding="utf-8")
    start_bat = (ROOT / "start.bat").read_text(encoding="utf-8")
    start_tg_bat = (ROOT / "bat" / "start_telegram_bot.bat").read_text(encoding="utf-8")

    assert "send_telegram: bool = False" in analyze_chart
    assert "from scripts.analyze_chart import run" in run_latest_analysis
    assert "from scripts.auto_execute import AUTO_TRADE, execute_signal" in run_latest_analysis
    assert "if AUTO_TRADE" in run_latest_analysis
    assert "RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE" in run_latest_analysis
    assert "TELEGRAM_BOT_ALLOW_AUTO_EXECUTE" in telegram_bot
    assert "if _auto_execute_opt_in()" in telegram_bot
    assert "from scripts.auto_execute import AUTO_TRADE, execute_signal" in telegram_bot
    assert "from scripts.auto_execute import AUTO_TRADE, check_and_close_timeouts" in telegram_bot
    main_body = telegram_bot.split("async def main() -> None:", 1)[1].split("def _setup_rotating_log", 1)[0]
    assert "_scanner_loop" not in main_body
    assert "getUpdates" in main_body
    assert "scanner moved to scripts/ws/ws_scanner.py" in main_body
    assert "PRODUCT_ANALYZER_LLM_ROUTER=llm_client" in start_bat
    assert "PRODUCT_ANALYZER_LLM_ROUTER=llm_client" in start_tg_bat
