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


def test_manual_analyzer_and_latest_wrapper_boundaries():
    analyze_chart = (ROOT / "scripts" / "analyze_chart.py").read_text(encoding="utf-8")
    run_latest_analysis = (ROOT / "scripts" / "run_latest_analysis.py").read_text(encoding="utf-8")
    telegram_bot = (ROOT / "scripts" / "telegram_bot.py").read_text(encoding="utf-8")

    assert "send_telegram: bool = False" in analyze_chart
    assert "from scripts.analyze_chart import run" in run_latest_analysis
    assert "from scripts.auto_execute import AUTO_TRADE, execute_signal" in run_latest_analysis
    assert "if AUTO_TRADE" in run_latest_analysis
    assert "RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE" in run_latest_analysis
    assert "TELEGRAM_BOT_ALLOW_AUTO_EXECUTE" in telegram_bot
    assert "if _auto_execute_opt_in()" in telegram_bot
    assert "from scripts.auto_execute import AUTO_TRADE, execute_signal" in telegram_bot
    assert "from scripts.auto_execute import AUTO_TRADE, check_and_close_timeouts" in telegram_bot
