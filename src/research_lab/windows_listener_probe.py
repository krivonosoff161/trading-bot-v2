from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


DEFAULT_LISTENER_PROBE_TIMEOUT_SECONDS = 8.0
DEFAULT_LISTENER_CLEANUP_TIMEOUT_SECONDS = 1.0
_NATIVE_LISTENER_SCRIPT = (
    "import json,psutil;"
    "rows=[];"
    "connections=psutil.net_connections(kind='tcp');"
    "rows=[{'LocalAddress':str(item.laddr.ip),"
    "'LocalPort':int(item.laddr.port),'OwningProcess':int(item.pid)} "
    "for item in connections if item.status==psutil.CONN_LISTEN "
    "and item.pid is not None and int(item.pid)>0 and item.laddr];"
    "print(json.dumps(rows,separators=(',',':')))"
)
_CREATE_NEW_PROCESS_GROUP = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
_CREATE_NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
_CREATE_SUSPENDED = int(getattr(subprocess, "CREATE_SUSPENDED", 0x00000004))
_WINDLL_FACTORY: Any = getattr(ctypes, "WinDLL", None)
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operation_count", ctypes.c_uint64),
        ("write_operation_count", ctypes.c_uint64),
        ("other_operation_count", ctypes.c_uint64),
        ("read_transfer_count", ctypes.c_uint64),
        ("write_transfer_count", ctypes.c_uint64),
        ("other_transfer_count", ctypes.c_uint64),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_int64),
        ("per_job_user_time_limit", ctypes.c_int64),
        ("limit_flags", ctypes.wintypes.DWORD),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", ctypes.wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.wintypes.DWORD),
        ("scheduling_class", ctypes.wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", _BasicLimitInformation),
        ("io_info", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


@dataclass(frozen=True, order=True)
class ListeningSocket:
    pid: int
    host: str
    port: int


@dataclass(frozen=True)
class ListenerProbeStageEvent:
    """Secret-free monotonic progress emitted by the bounded probe."""

    stage: str
    state: str
    monotonic_at: float
    elapsed_seconds: float


class WindowsListenerProbeError(RuntimeError):
    """The bounded Windows listener inventory could not be proven."""

    def __init__(self, code: str, *, stage: str = "unknown") -> None:
        super().__init__(code)
        self.code = code
        self.stage = stage


class _ProcessLike(Protocol):
    pid: int
    returncode: int | None

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class _OwnedTreeGuard(Protocol):
    def resume_process(self) -> None: ...

    def terminate_tree(self) -> None: ...

    def close(self) -> None: ...


class _WindowsJobTreeGuard:
    """Kill-on-close job containing only the exact listener probe process tree."""

    def __init__(self, process: _ProcessLike) -> None:
        if os.name != "nt":
            raise OSError("windows_job_unavailable")
        if _WINDLL_FACTORY is None:
            raise OSError("windows_job_unavailable")
        kernel32 = _WINDLL_FACTORY("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = ctypes.wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = ctypes.wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = ctypes.wintypes.BOOL
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError("listener_probe_job_create_failed")
        self._kernel32 = kernel32
        self._handle = handle
        self._process_handle: Any = getattr(process, "_handle", None)
        try:
            limits = _ExtendedLimitInformation()
            limits.basic_limit_information.limit_flags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            if not kernel32.SetInformationJobObject(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise OSError("listener_probe_job_policy_failed")
            if self._process_handle is None or not kernel32.AssignProcessToJobObject(
                handle,
                int(self._process_handle),
            ):
                raise OSError("listener_probe_job_assign_failed")
        except Exception:
            self.close()
            raise

    def resume_process(self) -> None:
        if _WINDLL_FACTORY is None:
            raise OSError("windows_job_unavailable")
        ntdll = _WINDLL_FACTORY("ntdll", use_last_error=True)
        ntdll.NtResumeProcess.argtypes = [ctypes.wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        if ntdll.NtResumeProcess(int(self._process_handle)) != 0:
            raise OSError("listener_probe_resume_failed")

    def terminate_tree(self) -> None:
        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE makes handle closure the bounded
        # termination request. TerminateJobObject is synchronous and can wait
        # behind the kernel inventory call that this cleanup must contain.
        if self._handle:
            handle = self._handle
            if not self._kernel32.CloseHandle(handle):
                raise OSError("listener_probe_job_close_failed")
            self._handle = None

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


PopenFactory = Callable[..., _ProcessLike]
JobFactory = Callable[[_ProcessLike], _OwnedTreeGuard]
StageCallback = Callable[[ListenerProbeStageEvent], None]


def _decode_rows(payload: str) -> Sequence[dict[str, Any]]:
    if not payload.strip():
        return ()
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise WindowsListenerProbeError(
            "listener_probe_invalid_json", stage="decode"
        ) from exc
    if isinstance(decoded, dict):
        return (decoded,)
    if isinstance(decoded, list) and all(isinstance(row, dict) for row in decoded):
        return decoded
    raise WindowsListenerProbeError("listener_probe_invalid_shape", stage="decode")


def _emit_stage(
    callback: StageCallback | None,
    *,
    stage: str,
    state: str,
    started_at: float,
    monotonic: Callable[[], float],
) -> None:
    if callback is None:
        return
    now = float(monotonic())
    try:
        callback(
            ListenerProbeStageEvent(
                stage=stage,
                state=state,
                monotonic_at=now,
                elapsed_seconds=max(0.0, now - started_at),
            )
        )
    except Exception:
        # Observability must not control the safety result.
        return


def _stop_owned_probe_tree(
    process: _ProcessLike,
    guard: _OwnedTreeGuard,
    *,
    cleanup_timeout_seconds: float,
) -> None:
    """Stop only the spawned probe job and wait for bounded handle cleanup."""

    try:
        guard.terminate_tree()
    except OSError as exc:
        # The root can still be stopped exactly, but descendant cleanup is no
        # longer proven, so the caller must receive a fail-closed result.
        try:
            process.terminate()
            process.wait(timeout=cleanup_timeout_seconds)
        except (OSError, subprocess.SubprocessError):
            pass
        raise OSError("listener_probe_tree_cleanup_unproven") from exc
    try:
        process.wait(timeout=cleanup_timeout_seconds)
        return
    except subprocess.TimeoutExpired:
        process.kill()
    process.wait(timeout=cleanup_timeout_seconds)


def _default_popen(arguments: tuple[str, ...], **kwargs: Any) -> _ProcessLike:
    return subprocess.Popen(arguments, **kwargs)


def _read_probe_output(handle: Any) -> str:
    handle.seek(0)
    return handle.read().decode("utf-8", errors="strict")


def collect_windows_listeners(
    *,
    timeout_seconds: float = DEFAULT_LISTENER_PROBE_TIMEOUT_SECONDS,
    cleanup_timeout_seconds: float = DEFAULT_LISTENER_CLEANUP_TIMEOUT_SECONDS,
    popen_factory: PopenFactory = _default_popen,
    job_factory: JobFactory = _WindowsJobTreeGuard,
    stage_callback: StageCallback | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[ListeningSocket, ...]:
    """Return a wall-clock-bounded snapshot of Windows TCP listeners.

    Output goes to a temporary file rather than a pipe, preventing inherited
    pipe handles from extending ``communicate()`` past the deadline.  The exact
    spawned native provider is assigned to a kill-on-close Windows job; timeout
    cleanup cannot target unrelated processes and includes any descendants it
    created.  Isolated mode prevents project imports and environment-controlled
    Python path changes from affecting the inventory child.
    """

    timeout = float(timeout_seconds)
    cleanup_timeout = float(cleanup_timeout_seconds)
    if timeout <= 0 or cleanup_timeout <= 0:
        raise ValueError("listener probe timeouts must be positive")
    arguments = (
        sys.executable,
        "-I",
        "-c",
        _NATIVE_LISTENER_SCRIPT,
    )
    started_at = float(monotonic())
    _emit_stage(
        stage_callback,
        stage="spawn",
        state="started",
        started_at=started_at,
        monotonic=monotonic,
    )
    with tempfile.TemporaryFile(mode="w+b") as output:
        try:
            process = popen_factory(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW | _CREATE_SUSPENDED
                ),
                close_fds=True,
            )
        except OSError as exc:
            _emit_stage(
                stage_callback,
                stage="spawn",
                state="failed",
                started_at=started_at,
                monotonic=monotonic,
            )
            raise WindowsListenerProbeError(
                "listener_probe_spawn_failed", stage="spawn"
            ) from exc
        try:
            guard = job_factory(process)
        except OSError as exc:
            try:
                process.terminate()
                process.wait(timeout=cleanup_timeout)
            except (OSError, subprocess.SubprocessError):
                pass
            raise WindowsListenerProbeError(
                "listener_probe_job_setup_failed", stage="spawn"
            ) from exc
        try:
            try:
                guard.resume_process()
            except OSError as exc:
                try:
                    _stop_owned_probe_tree(
                        process,
                        guard,
                        cleanup_timeout_seconds=cleanup_timeout,
                    )
                except (OSError, subprocess.SubprocessError):
                    pass
                raise WindowsListenerProbeError(
                    "listener_probe_resume_failed", stage="spawn"
                ) from exc
            _emit_stage(
                stage_callback,
                stage="inventory",
                state="started",
                started_at=started_at,
                monotonic=monotonic,
            )
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _emit_stage(
                    stage_callback,
                    stage="cleanup",
                    state="started",
                    started_at=started_at,
                    monotonic=monotonic,
                )
                try:
                    _stop_owned_probe_tree(
                        process,
                        guard,
                        cleanup_timeout_seconds=cleanup_timeout,
                    )
                except (OSError, subprocess.SubprocessError) as cleanup_exc:
                    _emit_stage(
                        stage_callback,
                        stage="cleanup",
                        state="failed",
                        started_at=started_at,
                        monotonic=monotonic,
                    )
                    raise WindowsListenerProbeError(
                        "listener_probe_cleanup_failed", stage="cleanup"
                    ) from cleanup_exc
                _emit_stage(
                    stage_callback,
                    stage="inventory",
                    state="timed_out",
                    started_at=started_at,
                    monotonic=monotonic,
                )
                raise WindowsListenerProbeError(
                    "listener_probe_timeout", stage="inventory"
                ) from exc
            if returncode != 0:
                _emit_stage(
                    stage_callback,
                    stage="inventory",
                    state="failed",
                    started_at=started_at,
                    monotonic=monotonic,
                )
                raise WindowsListenerProbeError(
                    "listener_probe_provider_failed", stage="inventory"
                )
            _emit_stage(
                stage_callback,
                stage="inventory",
                state="completed",
                started_at=started_at,
                monotonic=monotonic,
            )
            _emit_stage(
                stage_callback,
                stage="decode",
                state="started",
                started_at=started_at,
                monotonic=monotonic,
            )
            try:
                payload = _read_probe_output(output)
            except (OSError, UnicodeError) as exc:
                _emit_stage(
                    stage_callback,
                    stage="decode",
                    state="failed",
                    started_at=started_at,
                    monotonic=monotonic,
                )
                raise WindowsListenerProbeError(
                    "listener_probe_output_failed", stage="decode"
                ) from exc
        finally:
            guard.close()

    try:
        rows = _decode_rows(payload)
    except WindowsListenerProbeError:
        _emit_stage(
            stage_callback,
            stage="decode",
            state="failed",
            started_at=started_at,
            monotonic=monotonic,
        )
        raise
    listeners: set[ListeningSocket] = set()
    for row in rows:
        try:
            listener = ListeningSocket(
                pid=int(row["OwningProcess"]),
                host=str(row["LocalAddress"]),
                port=int(row["LocalPort"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WindowsListenerProbeError(
                "listener_probe_invalid_row", stage="decode"
            ) from exc
        if listener.pid <= 0 or not listener.host or not 1 <= listener.port <= 65_535:
            raise WindowsListenerProbeError(
                "listener_probe_invalid_row", stage="decode"
            )
        listeners.add(listener)
    _emit_stage(
        stage_callback,
        stage="complete",
        state="completed",
        started_at=started_at,
        monotonic=monotonic,
    )
    return tuple(sorted(listeners))


__all__ = [
    "DEFAULT_LISTENER_CLEANUP_TIMEOUT_SECONDS",
    "DEFAULT_LISTENER_PROBE_TIMEOUT_SECONDS",
    "ListenerProbeStageEvent",
    "ListeningSocket",
    "WindowsListenerProbeError",
    "collect_windows_listeners",
]
