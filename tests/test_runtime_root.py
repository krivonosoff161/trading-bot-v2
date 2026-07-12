from pathlib import Path

from src.utils.runtime_root import runtime_env_file, runtime_root


def test_runtime_root_defaults_to_code_root(monkeypatch, tmp_path):
    monkeypatch.delenv("TRADING_BOT_RUNTIME_ROOT", raising=False)
    assert runtime_root(tmp_path) == tmp_path
    assert runtime_env_file(tmp_path) == tmp_path / ".env"


def test_runtime_root_can_reference_existing_machine_local_assets(monkeypatch, tmp_path):
    private = tmp_path / "runtime"
    monkeypatch.setenv("TRADING_BOT_RUNTIME_ROOT", str(private))
    assert runtime_root(Path("ignored")) == private
    assert runtime_env_file(Path("ignored")) == private / ".env"
