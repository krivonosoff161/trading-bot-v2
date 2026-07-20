from __future__ import annotations

import os

from src.utils.runtime_root import load_runtime_dotenv


def test_dotenv_autoload_is_disabled_before_collection(monkeypatch, tmp_path) -> None:
    attempted: list[object] = []

    def fail_if_called(path, **_kwargs) -> bool:
        attempted.append(path)
        raise AssertionError("dotenv loader must remain disabled")

    monkeypatch.setattr("dotenv.load_dotenv", fail_if_called)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-test-value")

    assert os.environ["TRADING_BOT_DOTENV_AUTOLOAD"] == "0"
    assert load_runtime_dotenv(tmp_path) is False
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "synthetic-test-value"
    assert attempted == []


def test_dotenv_autoload_enabled_reads_only_isolated_runtime_file(
    monkeypatch, tmp_path,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / ".env").write_text(
        "SYNTHETIC_DOTENV_POLICY_VALUE=isolated\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADING_BOT_DOTENV_AUTOLOAD", "1")
    monkeypatch.setenv("TRADING_BOT_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.delenv("SYNTHETIC_DOTENV_POLICY_VALUE", raising=False)

    assert load_runtime_dotenv(tmp_path) is True
    assert os.environ["SYNTHETIC_DOTENV_POLICY_VALUE"] == "isolated"
