"""Fail-closed listener policy for the canonical paper-only RCC process tree."""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ListenerPolicyAssessment:
    green: bool
    errors: tuple[str, ...]
    public_api_ports: tuple[int, ...]
    internal_runner_ports: tuple[int, ...]


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _same_start(left: Any, right: float) -> bool:
    try:
        return abs(float(left) - float(right)) <= 0.001
    except (TypeError, ValueError):
        return False


def _descends_from(pid: int, ancestor_pid: int, processes: Mapping[int, Mapping[str, Any]]) -> bool:
    seen: set[int] = set()
    current = int(pid)
    while current > 0 and current not in seen:
        seen.add(current)
        row = processes.get(current)
        if row is None:
            return False
        parent = int(row.get("ppid") or 0)
        if parent == ancestor_pid:
            return True
        current = parent
    return False


def _command_has_port(command: Sequence[object], port: int) -> bool:
    parts = [str(part) for part in command]
    expected = str(int(port))
    for index, part in enumerate(parts):
        if part == "--port" and index + 1 < len(parts) and parts[index + 1] == expected:
            return True
        if part == f"--port={expected}":
            return True
    return False


def assess_canonical_rcc_listeners(
    listeners: Sequence[Mapping[str, Any]],
    processes: Mapping[int, Mapping[str, Any]],
    *,
    ollama_pid: int,
    ollama_started_at: float,
    expected_ollama_executable: Path,
) -> ListenerPolicyAssessment:
    """Allow only the loopback Ollama API and its exact internal model runner.

    Ollama's public local API is the RCC-owned ``ollama serve`` process on
    127.0.0.1:11434.  During inference it starts ``llama-server.exe`` beneath
    that exact process and assigns a dynamic loopback ``--port``.  That runner
    is not a second public service, but it is allowed only when executable,
    ancestry, start generation, bind address and command port all match.
    """

    errors: list[str] = []
    public_ports: list[int] = []
    runner_ports: list[int] = []
    root = processes.get(int(ollama_pid))
    expected_root = os.path.normcase(os.path.abspath(str(expected_ollama_executable)))
    if root is None:
        errors.append("ollama_root_missing")
    else:
        actual_root = os.path.normcase(os.path.abspath(str(root.get("exe") or "")))
        if (
            actual_root != expected_root
            or not _same_start(root.get("create_time"), ollama_started_at)
        ):
            errors.append("ollama_root_identity_mismatch")

    trusted_root = os.path.normcase(os.path.abspath(str(expected_ollama_executable.parent)))
    trusted_prefix = trusted_root.rstrip("\\/") + os.sep
    for listener in listeners:
        try:
            pid = int(listener["pid"])
            host = str(listener["host"])
            port = int(listener["port"])
        except (KeyError, TypeError, ValueError):
            errors.append("corrupt_listener")
            continue
        if port <= 0 or not _loopback(host):
            errors.append("non_loopback_or_invalid_listener")
            continue
        if pid == int(ollama_pid) and port == 11434:
            public_ports.append(port)
            continue

        process = processes.get(pid)
        if process is None:
            errors.append("listener_process_missing")
            continue
        executable = os.path.normcase(os.path.abspath(str(process.get("exe") or "")))
        command = process.get("cmdline")
        if (
            Path(executable).name.lower() != "llama-server.exe"
            or not executable.startswith(trusted_prefix)
            or not _descends_from(pid, int(ollama_pid), processes)
            or not _command_has_port(command if isinstance(command, Sequence) else (), port)
        ):
            errors.append("unexpected_owned_listener")
            continue
        runner_ports.append(port)

    if public_ports != [11434]:
        errors.append("ollama_public_api_listener_mismatch")
    unique_errors = tuple(dict.fromkeys(errors))
    return ListenerPolicyAssessment(
        green=not unique_errors,
        errors=unique_errors,
        public_api_ports=tuple(sorted(public_ports)),
        internal_runner_ports=tuple(sorted(runner_ports)),
    )
