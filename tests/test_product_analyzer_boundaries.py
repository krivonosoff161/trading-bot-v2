import asyncio
from pathlib import Path

from src.utils import llm_formatter


ROOT = Path(__file__).resolve().parents[1]


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
    assert status["shared_router_entrypoints"] == ["generate_client_text"]
    assert status["yandex_only_entrypoints"] == ["generate_premium_analysis", "generate_edu_text"]
    assert status["telegram_send_authority"] is False
    assert status["execution_authority"] is False
    assert "secret-alibaba-key" not in rendered
    assert "b1git" not in rendered


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
