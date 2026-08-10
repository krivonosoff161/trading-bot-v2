import asyncio

import pytest

from src.utils import telegram


def test_recipient_ref_is_stable_and_does_not_expose_chat_id():
    chat_id = "123456789"
    value = telegram.recipient_ref(chat_id)
    assert value == telegram.recipient_ref(chat_id)
    assert len(value) == 12
    assert chat_id not in value


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


def test_send_photo_to_noops_without_token(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    image = tmp_path / "chart.png"
    image.write_bytes(b"fake-png")

    assert asyncio.run(telegram.send_photo_to("123", str(image))) is None


def test_send_photo_bytes_to_noops_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    assert asyncio.run(telegram.send_photo_bytes_to("123", b"fake-png")) is None


def test_send_photo_bytes_to_posts_exact_payload(monkeypatch):
    captured = {}

    class FakeFormData:
        def __init__(self):
            self.fields = []

        def add_field(self, name, value, **kwargs):
            self.fields.append((name, value, kwargs))

    class FakeResponse:
        status = 200

        async def text(self):
            return '{"ok": true, "result": {"message_id": 321}}'

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, data, timeout):
            captured["url"] = url
            captured["fields"] = data.fields
            captured["timeout"] = timeout.total
            return FakeResponse()

    monkeypatch.setattr(telegram, "bot_token", lambda: "token")
    monkeypatch.setattr(telegram.aiohttp, "FormData", FakeFormData)
    monkeypatch.setattr(telegram.aiohttp, "ClientSession", FakeSession)

    result = asyncio.run(telegram.send_photo_bytes_to("123", b"captured-png"))

    assert result == 321
    assert captured["url"] == "https://api.telegram.org/bottoken/sendPhoto"
    assert captured["timeout"] == 30
    assert ("chat_id", "123", {}) in captured["fields"]
    assert (
        "photo",
        b"captured-png",
        {"filename": "paper_chart.png", "content_type": "image/png"},
    ) in captured["fields"]


def test_successful_photo_ack_survives_runtime_log_sink_failure(monkeypatch):
    class FakeResponse:
        status = 200

        async def text(self):
            return '{"ok": true, "result": {"message_id": 654}}'

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(telegram, "bot_token", lambda: "synthetic-token")
    monkeypatch.setattr(telegram.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(
        telegram.logger,
        "info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("synthetic runtime-storage sink failure")
        ),
    )

    assert asyncio.run(telegram.send_photo_bytes_to("synthetic-recipient", b"png")) == 654


@pytest.mark.parametrize(
    ("status", "body", "problem"),
    [
        (500, "upstream failure", "Telegram photo HTTP 500"),
        (200, '{"ok": false}', "Telegram photo ok=false"),
    ],
)
def test_send_photo_bytes_to_surfaces_transport_failures(monkeypatch, status, body, problem):
    class FakeResponse:
        async def text(self):
            return body

    FakeResponse.status = status

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, data, timeout):
            return FakeResponse()

    monkeypatch.setattr(telegram, "bot_token", lambda: "token")
    monkeypatch.setattr(telegram.aiohttp, "ClientSession", FakeSession)

    with pytest.raises(RuntimeError, match=problem):
        asyncio.run(telegram.send_photo_bytes_to("123", b"captured-png"))
