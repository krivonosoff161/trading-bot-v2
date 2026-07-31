from __future__ import annotations

import importlib
import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from src.research_lab.windows_listener_probe import (
    ListenerProbeStageEvent,
    ListeningSocket,
    WindowsListenerProbeError,
    collect_windows_listeners,
)


class FakeProcess:
    def __init__(
        self,
        *,
        output: BinaryIO,
        payload: bytes = b"",
        returncode: int = 0,
        blocks: bool = False,
    ) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self._expected_returncode = returncode
        self._blocks = blocks
        self.terminated = False
        self.killed = False
        output.write(payload)
        output.flush()

    def wait(self, timeout: float | None = None) -> int:
        if self._blocks and not (self.terminated or self.killed):
            raise subprocess.TimeoutExpired("synthetic-listener-probe", timeout)
        self.returncode = self._expected_returncode
        return self._expected_returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class FakeJob:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.tree_terminated = False
        self.closed = False
        self.resumed = False

    def resume_process(self) -> None:
        self.resumed = True

    def terminate_tree(self) -> None:
        self.tree_terminated = True
        self.process.terminated = True

    def close(self) -> None:
        self.closed = True


def _factories(
    payload: str,
    *,
    returncode: int = 0,
    blocks: bool = False,
) -> tuple[Any, Any, dict[str, Any]]:
    observed: dict[str, Any] = {}

    def popen_factory(arguments, **kwargs):
        observed["arguments"] = arguments
        observed["kwargs"] = kwargs
        process = FakeProcess(
            output=kwargs["stdout"],
            payload=payload.encode("utf-8"),
            returncode=returncode,
            blocks=blocks,
        )
        observed["process"] = process
        return process

    def job_factory(process):
        job = FakeJob(process)
        observed["job"] = job
        return job

    return popen_factory, job_factory, observed


def _collect(payload: str, **kwargs: Any) -> tuple[ListeningSocket, ...]:
    popen_factory, job_factory, _observed = _factories(payload)
    return collect_windows_listeners(
        popen_factory=popen_factory,
        job_factory=job_factory,
        **kwargs,
    )


def test_probe_is_bounded_and_uses_read_only_windows_listener_provider() -> None:
    popen_factory, job_factory, observed = _factories(
        '{"LocalAddress":"127.0.0.1","LocalPort":11434,"OwningProcess":321}'
    )

    result = collect_windows_listeners(
        timeout_seconds=7.5,
        popen_factory=popen_factory,
        job_factory=job_factory,
    )

    assert result == (ListeningSocket(pid=321, host="127.0.0.1", port=11434),)
    assert observed["arguments"][0].endswith(
        "WindowsPowerShell\\v1.0\\powershell.exe"
    )
    assert observed["arguments"][1:5] == (
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    )
    assert "Get-NetTCPConnection -State Listen" in observed["arguments"][5]
    assert observed["kwargs"]["stdin"] is subprocess.DEVNULL
    assert observed["kwargs"]["stderr"] is subprocess.DEVNULL
    assert observed["kwargs"]["close_fds"] is True
    assert "env" not in observed["kwargs"]
    assert "shell" not in observed["kwargs"]
    assert "cwd" not in observed["kwargs"]
    assert observed["job"].resumed is True
    assert observed["job"].closed is True


def test_multiple_rows_are_deduplicated_and_sorted() -> None:
    payload = """[
      {"LocalAddress":"127.0.0.1","LocalPort":11434,"OwningProcess":9},
      {"LocalAddress":"127.0.0.1","LocalPort":59428,"OwningProcess":11},
      {"LocalAddress":"127.0.0.1","LocalPort":11434,"OwningProcess":9}
    ]"""

    result = _collect(payload)

    assert result == (
        ListeningSocket(pid=9, host="127.0.0.1", port=11434),
        ListeningSocket(pid=11, host="127.0.0.1", port=59428),
    )


def test_empty_listener_inventory_is_valid() -> None:
    assert _collect("") == ()


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
        _collect(payload)


def test_provider_timeout_kills_only_the_owned_job_tree_and_returns_bounded() -> None:
    popen_factory, job_factory, observed = _factories("", blocks=True)
    stages: list[ListenerProbeStageEvent] = []

    with pytest.raises(WindowsListenerProbeError, match="listener_probe_timeout") as caught:
        collect_windows_listeners(
            timeout_seconds=0.25,
            cleanup_timeout_seconds=0.1,
            popen_factory=popen_factory,
            job_factory=job_factory,
            stage_callback=stages.append,
        )

    assert caught.value.stage == "inventory"
    assert observed["job"].tree_terminated is True
    assert observed["job"].closed is True
    assert observed["process"].terminated is True
    assert [(event.stage, event.state) for event in stages] == [
        ("spawn", "started"),
        ("inventory", "started"),
        ("cleanup", "started"),
        ("inventory", "timed_out"),
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows job-object contract")
def test_real_blocked_provider_and_descendant_are_killed_within_bound(
    tmp_path: Path,
) -> None:
    child_pid_path = tmp_path / "child-pid.txt"
    observed: dict[str, subprocess.Popen[bytes]] = {}
    script = (
        "import pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid),encoding='ascii');"
        "time.sleep(30)"
    )

    def blocked_provider(_arguments, **kwargs):
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(child_pid_path)],
            **kwargs,
        )
        observed["process"] = process
        return process

    started_at = time.monotonic()
    with pytest.raises(WindowsListenerProbeError, match="listener_probe_timeout"):
        collect_windows_listeners(
            timeout_seconds=3.0,
            cleanup_timeout_seconds=0.5,
            popen_factory=blocked_provider,
        )
    elapsed = time.monotonic() - started_at

    assert elapsed < 5.0
    assert observed["process"].poll() is not None
    assert child_pid_path.exists()
    child_pid = int(child_pid_path.read_text(encoding="ascii"))
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, child_pid)
    if handle:
        try:
            exit_code = ctypes.wintypes.DWORD()
            assert kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            assert exit_code.value != 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)


def test_provider_nonzero_exit_is_visible_without_private_stderr() -> None:
    popen_factory, job_factory, _observed = _factories("", returncode=7)
    with pytest.raises(
        WindowsListenerProbeError,
        match="listener_probe_provider_failed",
    ):
        collect_windows_listeners(
            popen_factory=popen_factory,
            job_factory=job_factory,
        )


def test_timeout_fails_closed_when_owned_tree_cleanup_cannot_be_proven() -> None:
    popen_factory, _job_factory, observed = _factories("", blocks=True)

    class FailedJob(FakeJob):
        def terminate_tree(self) -> None:
            raise OSError("synthetic job failure")

    with pytest.raises(
        WindowsListenerProbeError,
        match="listener_probe_cleanup_failed",
    ) as caught:
        collect_windows_listeners(
            timeout_seconds=0.1,
            cleanup_timeout_seconds=0.1,
            popen_factory=popen_factory,
            job_factory=FailedJob,
        )

    assert caught.value.stage == "cleanup"
    assert observed["process"].terminated is True


def test_spawn_failure_is_visible() -> None:
    def popen_factory(*_args, **_kwargs):
        raise OSError("synthetic provider failure")

    with pytest.raises(WindowsListenerProbeError, match="listener_probe_spawn_failed"):
        collect_windows_listeners(popen_factory=popen_factory)


def test_job_setup_failure_terminates_exact_spawned_process() -> None:
    popen_factory, _job_factory, observed = _factories("")

    def failing_job(_process):
        raise OSError("synthetic job setup failure")

    with pytest.raises(
        WindowsListenerProbeError,
        match="listener_probe_job_setup_failed",
    ):
        collect_windows_listeners(
            popen_factory=popen_factory,
            job_factory=failing_job,
        )
    assert observed["process"].terminated is True


def test_timeouts_must_be_positive_before_provider_invocation() -> None:
    invoked = False

    def popen_factory(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError

    with pytest.raises(ValueError, match="must be positive"):
        collect_windows_listeners(timeout_seconds=0, popen_factory=popen_factory)
    with pytest.raises(ValueError, match="must be positive"):
        collect_windows_listeners(
            cleanup_timeout_seconds=0,
            popen_factory=popen_factory,
        )
    assert not invoked


def test_stage_callback_failure_cannot_change_probe_result() -> None:
    popen_factory, job_factory, _observed = _factories("")

    result = collect_windows_listeners(
        popen_factory=popen_factory,
        job_factory=job_factory,
        stage_callback=lambda _event: (_ for _ in ()).throw(RuntimeError("private")),
    )

    assert result == ()


def test_module_import_does_not_enumerate_listeners_or_start_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("import attempted a process or listener probe")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    sys.modules.pop("src.research_lab.windows_listener_probe", None)
    importlib.import_module("src.research_lab.windows_listener_probe")
    assert calls == []
