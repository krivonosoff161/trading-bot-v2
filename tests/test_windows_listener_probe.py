from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Any

import pytest

from src.research_lab.windows_listener_probe import (
    ListeningSocket,
    WindowsListenerProbeError,
    collect_windows_listeners,
)


def completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=(), returncode=0, stdout=stdout, stderr="")


def test_probe_is_bounded_and_uses_read_only_windows_listener_provider() -> None:
    observed: dict[str, Any] = {}

    def runner(arguments, **kwargs):
        observed["arguments"] = arguments
        observed["kwargs"] = kwargs
        return completed(
            '{"LocalAddress":"127.0.0.1","LocalPort":11434,"OwningProcess":321}'
        )

    result = collect_windows_listeners(timeout_seconds=7.5, runner=runner)

    assert result == (ListeningSocket(pid=321, host="127.0.0.1", port=11434),)
    assert observed["arguments"][0].endswith("WindowsPowerShell\\v1.0\\powershell.exe")
    assert observed["arguments"][1:5] == (
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    )
    assert "Get-NetTCPConnection -State Listen" in observed["arguments"][5]
    assert observed["kwargs"] == {
        "check": True,
        "capture_output": True,
        "text": True,
        "timeout": 7.5,
    }


def test_multiple_rows_are_deduplicated_and_sorted() -> None:
    payload = """[
      {"LocalAddress":"127.0.0.1","LocalPort":11434,"OwningProcess":9},
      {"LocalAddress":"127.0.0.1","LocalPort":59428,"OwningProcess":11},
      {"LocalAddress":"127.0.0.1","LocalPort":11434,"OwningProcess":9}
    ]"""

    result = collect_windows_listeners(runner=lambda *_args, **_kwargs: completed(payload))

    assert result == (
        ListeningSocket(pid=9, host="127.0.0.1", port=11434),
        ListeningSocket(pid=11, host="127.0.0.1", port=59428),
    )


def test_empty_listener_inventory_is_valid() -> None:
    result = collect_windows_listeners(runner=lambda *_args, **_kwargs: completed(""))

    assert result == ()


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("not-json", "listener_probe_invalid_json"),
        ('"wrong-shape"', "listener_probe_invalid_shape"),
        ('{"LocalAddress":"127.0.0.1","LocalPort":0,"OwningProcess":1}', "listener_probe_invalid_row"),
        ('{"LocalAddress":"","LocalPort":11434,"OwningProcess":1}', "listener_probe_invalid_row"),
        ('{"LocalAddress":"127.0.0.1","LocalPort":11434}', "listener_probe_invalid_row"),
    ],
)
def test_unprovable_listener_rows_fail_closed(payload: str, reason: str) -> None:
    with pytest.raises(WindowsListenerProbeError, match=reason):
        collect_windows_listeners(runner=lambda *_args, **_kwargs: completed(payload))


def test_provider_timeout_is_visible_and_cannot_become_starting_forever() -> None:
    def runner(arguments, **kwargs):
        raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])

    with pytest.raises(WindowsListenerProbeError, match="listener_probe_timeout"):
        collect_windows_listeners(timeout_seconds=0.25, runner=runner)


def test_provider_failure_is_visible() -> None:
    def runner(*_args, **_kwargs):
        raise OSError("synthetic provider failure")

    with pytest.raises(WindowsListenerProbeError, match="listener_probe_failed"):
        collect_windows_listeners(runner=runner)


def test_timeout_must_be_positive_before_provider_invocation() -> None:
    invoked = False

    def runner(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        return completed("")

    with pytest.raises(ValueError, match="must be positive"):
        collect_windows_listeners(timeout_seconds=0, runner=runner)
    assert not invoked


def test_module_import_does_not_enumerate_listeners_or_start_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("import attempted a process or listener probe")

    monkeypatch.setattr(subprocess, "run", forbidden)
    sys.modules.pop("src.research_lab.windows_listener_probe", None)

    module = importlib.import_module("src.research_lab.windows_listener_probe")

    assert module.ListeningSocket is not None
    assert calls == []


def test_runner_receives_no_environment_or_shell_override() -> None:
    observed: dict[str, Any] = {}

    def runner(arguments, **kwargs):
        observed.update(kwargs)
        return completed("")

    collect_windows_listeners(runner=runner)

    assert "env" not in observed
    assert "shell" not in observed
    assert "cwd" not in observed
