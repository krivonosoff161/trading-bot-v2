from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


DEFAULT_LISTENER_PROBE_TIMEOUT_SECONDS = 8.0
_WINDOWS_POWERSHELL = (
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)
_LISTENER_COMMAND = (
    "Get-NetTCPConnection -State Listen -ErrorAction Stop | "
    "Select-Object LocalAddress,LocalPort,OwningProcess | "
    "ConvertTo-Json -Compress"
)


@dataclass(frozen=True, order=True)
class ListeningSocket:
    pid: int
    host: str
    port: int


class WindowsListenerProbeError(RuntimeError):
    """The bounded Windows listener inventory could not be proven."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _decode_rows(payload: str) -> Sequence[dict[str, Any]]:
    if not payload.strip():
        return ()
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise WindowsListenerProbeError("listener_probe_invalid_json") from exc
    if isinstance(decoded, dict):
        return (decoded,)
    if isinstance(decoded, list) and all(isinstance(row, dict) for row in decoded):
        return decoded
    raise WindowsListenerProbeError("listener_probe_invalid_shape")


def collect_windows_listeners(
    *,
    timeout_seconds: float = DEFAULT_LISTENER_PROBE_TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
) -> tuple[ListeningSocket, ...]:
    """Return a bounded, identity-carrying snapshot of Windows TCP listeners.

    The global ``psutil.net_connections()`` path can remain inside the Windows
    TCP provider for an unbounded interval on a busy Ollama host.  Startup and
    canary monitors must use this bounded provider so a listener inventory
    cannot hide the independent fast-safety lane.

    The function is side-effect free apart from invoking the read-only Windows
    networking provider.  Tests inject ``runner`` and never inspect the host.
    """

    timeout = float(timeout_seconds)
    if timeout <= 0:
        raise ValueError("listener probe timeout must be positive")
    arguments = (
        _WINDOWS_POWERSHELL,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        _LISTENER_COMMAND,
    )
    try:
        completed = runner(
            arguments,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise WindowsListenerProbeError("listener_probe_timeout") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise WindowsListenerProbeError("listener_probe_failed") from exc

    rows = _decode_rows(completed.stdout)
    listeners: set[ListeningSocket] = set()
    for row in rows:
        try:
            listener = ListeningSocket(
                pid=int(row["OwningProcess"]),
                host=str(row["LocalAddress"]),
                port=int(row["LocalPort"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WindowsListenerProbeError("listener_probe_invalid_row") from exc
        if (
            listener.pid <= 0
            or not listener.host
            or not 1 <= listener.port <= 65_535
        ):
            raise WindowsListenerProbeError("listener_probe_invalid_row")
        listeners.add(listener)
    return tuple(sorted(listeners))


__all__ = [
    "DEFAULT_LISTENER_PROBE_TIMEOUT_SECONDS",
    "ListeningSocket",
    "WindowsListenerProbeError",
    "collect_windows_listeners",
]
