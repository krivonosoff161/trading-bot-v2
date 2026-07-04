import asyncio

from src.utils import telegram


def test_telegram_status_reads_environment_lazily(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert telegram.telegram_status() == {
        "token_set": False,
        "chat_env": "TELEGRAM_CHAT_ID",
        "chat_ids_count": 0,
        "configured": False,
    }

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "'token-after-import'")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111, 222")

    assert telegram.bot_token() == "token-after-import"
    assert telegram.chat_ids() == ["111", "222"]
    assert telegram.telegram_status()["configured"] is True
    assert telegram.telegram_status()["chat_ids_count"] == 2


def test_send_message_to_noops_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    assert asyncio.run(telegram.send_message_to("123", "hello")) is None
