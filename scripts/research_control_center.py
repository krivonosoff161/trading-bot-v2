"""Russian operator UI for independent paper/research contours.

The control center is deliberately a process supervisor, not a trading brain.
It never imports project runtimes, reads ``.env``, or exposes an execution path.
All switches start the existing paper/research entrypoints as isolated child
processes and keep their output visible in one window.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
from functools import partial
import json
import os
from pathlib import Path
import queue
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import messagebox, ttk
from typing import Any, Callable, TypeVar

from src.research_lab.canary_checkpoint_policy import (
    CanaryLaneSample,
    CanaryMonitorHardFailure,
    CanaryMonitoringService,
    CanaryWatchdogAssessment,
)
from src.research_lab.compute_pipeline_health import assess_compute_pipeline
from src.research_lab.ownership import current_process_identity, probe_process_identity
from src.research_lab.rcc_runtime_safety import CanonicalOwnerSafetyMonitor
from src.research_lab.windows_listener_probe import (
    ListenerProbeStageEvent,
    WindowsListenerProbeError,
    collect_windows_listeners,
)

msvcrt: Any = None
fcntl: Any = None
_WINDLL: Any = getattr(ctypes, "windll", None)
_CREATE_NEW_PROCESS_GROUP = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
_CREATE_NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
_CTRL_BREAK_EVENT = int(getattr(signal, "CTRL_BREAK_EVENT", 0))
_SnapshotT = TypeVar("_SnapshotT")
if os.name == "nt":
    import msvcrt as _msvcrt

    msvcrt = _msvcrt
else:  # pragma: no cover - exercised by the Linux CI import path
    import fcntl as _fcntl

    fcntl = _fcntl


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(
    os.environ.get("TRADING_BOT_RUNTIME_ROOT", "").strip()
    or Path.home() / "trading-bot-v2"
)
GPU_MASK_ENV_NAMES = ("CUDA_VISIBLE_DEVICES", "GGML_VK_VISIBLE_DEVICES")
_configured_private_root = os.environ.get("TRADING_BOT_RESEARCH_ROOT", "").strip()
PRIVATE_ROOT = (
    Path(_configured_private_root)
    if _configured_private_root
    else Path.home() / "github_projects" / "trading-bot-research" / "strategy-lab"
)
STATE_DIR = PRIVATE_ROOT / "state" / "control-center"
STOP_FARM = PRIVATE_ROOT / "state" / "STOP_FARM_FULL_CYCLE.txt"
CANONICAL_STOP_INTENTS = (
    STOP_FARM,
    PRIVATE_ROOT / "state" / "STOP_NEWS_SCANNER.txt",
    PRIVATE_ROOT / "state" / "STOP_PUBLIC_NEWS.txt",
)
REQUIRED_RESEARCH_CONTOURS = frozenset(
    {"ollama", "public_news", "scanner", "farm", "paper_cards", "telegram_bot"}
)
CANONICAL_PAPER_PROFILE = frozenset(
    {"ollama", "public_news", "scanner", "paper_cards", "telegram_bot"}
)
STATUS_CACHE_SECONDS = 1.0
HEARTBEAT_INTERVAL_SECONDS = 5.0
RUNTIME_STARTUP_BUDGET_SECONDS = 600.0
PROCESS_LEASE_SUPERVISOR_MAX_AGE_SECONDS = 50.0


def _python(*args: str) -> tuple[str, ...]:
    return (sys.executable, "-X", "utf8", *args)


def format_age(seconds: int | float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total} с"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} мин {sec} с"
    hours, minute = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {minute} мин"
    days, hour = divmod(hours, 24)
    return f"{days} д {hour} ч"


@dataclass(frozen=True)
class ContourSpec:
    key: str
    title: str
    description: str
    command: tuple[str, ...]
    network: bool = False
    telegram: bool = False
    browser: bool = False
    graceful_stop: Callable[[], None] | None = None
    env: dict[str, str] = field(default_factory=dict)
    owner_group: str = ""
    graceful_seconds: float = 15.0
    signal_fallback_seconds: float = 15.0


@dataclass(frozen=True)
class _CachedJson:
    sampled_at: float
    payload: dict


class _JsonStatusCache:
    """Bound repeated UI reads without making cached data authoritative."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.entries: dict[Path, _CachedJson] = {}

    def read(
        self,
        path: Path,
        reader: Callable[[Path], dict],
        *,
        max_age_seconds: float = STATUS_CACHE_SECONDS,
    ) -> dict:
        now = self.clock()
        cached = self.entries.get(path)
        if cached and now - cached.sampled_at < max(0.0, max_age_seconds):
            return dict(cached.payload)
        payload = reader(path)
        self.entries[path] = _CachedJson(sampled_at=now, payload=dict(payload))
        return dict(payload)


class _HeartbeatPublisher:
    """Publish minimal RCC liveness independently from the Tk status loop."""

    def __init__(
        self,
        target: Path,
        snapshot: Callable[[], dict[str, object]],
        *,
        interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self.target = target
        self.snapshot = snapshot
        self.interval_seconds = interval_seconds
        self.on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def thread_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def publish_once(self) -> None:
        payload = self.snapshot()
        self.target.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.target)

    def start(self) -> None:
        if self.thread_alive:
            raise RuntimeError("RCC heartbeat publisher already started")
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="rcc-liveness-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 10.0) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=max(0.0, timeout))
        return not thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.publish_once()
            except Exception as exc:
                if self.on_error is not None:
                    self.on_error(exc)
            elapsed = max(0.0, time.monotonic() - started)
            if self._stop.wait(max(0.0, self.interval_seconds - elapsed)):
                return


def _request_farm_stop() -> None:
    STOP_FARM.parent.mkdir(parents=True, exist_ok=True)
    STOP_FARM.write_text(
        f"control center stop requested at {time.time()}\n", encoding="utf-8"
    )


def _request_named_stop(filename: str) -> Callable[[], None]:
    def request() -> None:
        target = PRIVATE_ROOT / "state" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"control center stop requested at {time.time()}\n", encoding="utf-8"
        )

    return request


REQUEST_SCANNER_STOP = _request_named_stop("STOP_NEWS_SCANNER.txt")
REQUEST_PUBLIC_NEWS_STOP = _request_named_stop("STOP_PUBLIC_NEWS.txt")


def contour_specs() -> tuple[ContourSpec, ...]:
    """Return the allowlisted surface. There is intentionally no live entry."""
    ollama = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
    return (
        ContourSpec(
            "ollama",
            "Ollama / CPU-калькулятор",
            "Локальная LLM на CPU; видеокарта оставлена числовому перебору",
            (str(ollama), "serve"),
            env={
                "OLLAMA_LLM_LIBRARY": "cpu",
                "CUDA_VISIBLE_DEVICES": "-1",
                "GGML_VK_VISIBLE_DEVICES": "-1",
                "OLLAMA_HOST": "127.0.0.1:11434",
                "OLLAMA_KEEP_ALIVE": "5m",
                "OLLAMA_NUM_PARALLEL": "1",
            },
        ),
        ContourSpec(
            "public_news",
            "Публичный новостной канал",
            "Собирает новости и публикует редакционные карточки",
            ("cmd.exe", "/d", "/c", str(ROOT / "bat" / "public_news_loop.bat")),
            network=True,
            telegram=True,
            graceful_stop=REQUEST_PUBLIC_NEWS_STOP,
            graceful_seconds=300.0,
        ),
        ContourSpec(
            "scanner",
            "Сканер → очередь фермы",
            "Собирает сигнальный контекст; Telegram-отправка отключена",
            (
                "cmd.exe",
                "/d",
                "/c",
                str(ROOT / "bat" / "news_scanner_loop.bat"),
            ),
            network=True,
            env={"SCANNER_SEND_TELEGRAM": "0"},
            graceful_stop=REQUEST_SCANNER_STOP,
            graceful_seconds=300.0,
        ),
        ContourSpec(
            "farm",
            "Ферма / валидатор / paper-наблюдение",
            "Канонический исследовательский цикл с калькулятором",
            (
                "cmd.exe",
                "/d",
                "/c",
                str(ROOT / "bat" / "paper_product_headless_loop.bat"),
            ),
            network=True,
            graceful_stop=_request_farm_stop,
            owner_group="canonical_farm",
            graceful_seconds=120.0,
            signal_fallback_seconds=30.0,
            env={
                "STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM": "0",
                "STRATEGY_LAB_CALCULATOR_BASE_URL": "http://127.0.0.1:11434/v1",
            },
        ),
        ContourSpec(
            "paper_cards",
            "Бумажные карточки Telegram",
            "Та же ферма, но с доставкой готовых paper-карточек",
            (
                "cmd.exe",
                "/d",
                "/c",
                str(ROOT / "bat" / "paper_product_headless_send_loop.bat"),
            ),
            network=True,
            telegram=True,
            graceful_stop=_request_farm_stop,
            owner_group="canonical_farm",
            graceful_seconds=120.0,
            signal_fallback_seconds=30.0,
        ),
        ContourSpec(
            "telegram_bot",
            "Интерактивный Telegram-бот",
            "Анализ, VIP, обучение и статусы; без исполнения сделок",
            _python("-u", "scripts/telegram_bot.py"),
            network=True,
            telegram=True,
            env={
                "AUTO_TRADE": "0",
                "TELEGRAM_BOT_ALLOW_AUTO_EXECUTE": "0",
                "PRODUCT_ANALYZER_LLM_ROUTER": "llm_client",
                "LLM_PROVIDER": "alibaba",
                "PREMIUM_VISION_PROVIDER": "alibaba",
            },
        ),
        ContourSpec(
            "dashboard",
            "Dashboard",
            "Локальная страница состояния на 127.0.0.1:8765",
            _python(
                "scripts/strategy_lab/serve_dashboard.py",
                "--host",
                "127.0.0.1",
                "--port",
                "8765",
            ),
            browser=True,
        ),
        ContourSpec(
            "graphs",
            "Графы",
            "Однократно перестраивает и открывает локальный граф",
            (
                "cmd.exe",
                "/d",
                "/c",
                str(ROOT / "bat" / "strategy_lab_graph_viewer.bat"),
            ),
            browser=True,
        ),
    )


class ManagedContour:
    def __init__(
        self, spec: ContourSpec, events: queue.Queue[tuple[str, str, str]]
    ) -> None:
        self.spec = spec
        self.events = events
        self.process: subprocess.Popen[str] | None = None
        self.started_at = 0.0
        self.stopping = False
        self.expected_running = False
        self.unexpected_exit_reported = False

    @property
    def running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def start(self) -> None:
        if self.running:
            return
        self.stopping = False
        self.expected_running = False
        self.unexpected_exit_reported = False
        env = os.environ.copy()
        for name in GPU_MASK_ENV_NAMES:
            env.pop(name, None)
        env.update(self.spec.env)
        # Inherited host state must never upgrade the paper RCC into an
        # execution-capable child process.
        env["AUTO_TRADE"] = "0"
        env["TELEGRAM_BOT_ALLOW_AUTO_EXECUTE"] = "0"
        env["TRADING_BOT_RUNTIME_ROOT"] = str(RUNTIME_ROOT)
        env["PYTHONUTF8"] = "1"
        env["TRADING_BOT_RESEARCH_ROOT"] = str(PRIVATE_ROOT)
        # CTRL_BREAK is the documented graceful stop for owned console
        # contours. CREATE_NO_WINDOW would detach them from a console and make
        # that signal unreliable on Windows.
        flags = _CREATE_NEW_PROCESS_GROUP
        self.process = subprocess.Popen(
            self.spec.command,
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
        try:
            identity = probe_process_identity(self.process.pid)
        except Exception as exc:
            self._abort_unidentified_start()
            raise RuntimeError(
                "owned contour process identity probe failed"
            ) from exc
        if identity is None:
            self._abort_unidentified_start()
            raise RuntimeError("owned contour process identity unavailable")
        self.started_at = identity.started_at
        self.expected_running = True
        self.events.put((self.spec.key, "state", f"работает · PID {self.process.pid}"))
        threading.Thread(target=self._read_output, daemon=True).start()

    def _abort_unidentified_start(self) -> None:
        """Best-effort graceful rollback when a new child cannot be identified."""

        process = self.process
        self.expected_running = False
        self.stopping = True
        if process is None or process.poll() is not None:
            return
        try:
            process.send_signal(_CTRL_BREAK_EVENT)
            process.wait(timeout=max(1.0, self.spec.signal_fallback_seconds))
        except (OSError, subprocess.SubprocessError):
            return

    def _read_output(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            self.events.put((self.spec.key, "log", line.rstrip()))
        code = self.process.wait()
        self.events.put((self.spec.key, "state", f"остановлен · код {code}"))

    def consume_unexpected_exit(self) -> dict[str, int | float] | None:
        if (
            not self.expected_running
            or self.stopping
            or self.unexpected_exit_reported
            or self.process is None
        ):
            return None
        return_code = self.process.poll()
        if return_code is None:
            return None
        self.unexpected_exit_reported = True
        return {
            "pid": int(self.process.pid),
            "started_at": float(self.started_at),
            "exit_code": int(return_code),
        }

    def stop(self, timeout: float | None = None) -> bool:
        self.expected_running = False
        if not self.running or not self.process:
            return True
        self.stopping = True
        try:
            graceful_timeout = (
                self.spec.graceful_seconds
                if timeout is None
                else max(0.0, float(timeout))
            )
            signal_timeout = (
                self.spec.signal_fallback_seconds
                if timeout is None
                else max(0.0, float(timeout))
            )
            if self.spec.graceful_stop:
                self.spec.graceful_stop()
                deadline = time.monotonic() + graceful_timeout
                while self.running and time.monotonic() < deadline:
                    time.sleep(0.2)
                if not self.running:
                    return True
                if not _same_live_process(self.process.pid, self.started_at):
                    self.events.put(
                        (
                            self.spec.key,
                            "state",
                            "owned process identity changed before graceful fallback",
                        )
                    )
                    return False
                self.events.put(
                    (
                        self.spec.key,
                        "state",
                        "stop marker deadline exhausted; sending documented "
                        "CTRL_BREAK to the exact owned process group",
                    )
                )
                try:
                    self.process.send_signal(_CTRL_BREAK_EVENT)
                except (OSError, ValueError) as exc:
                    self.events.put(
                        (
                            self.spec.key,
                            "state",
                            "graceful process-group signal failed: "
                            f"{type(exc).__name__}; process remains owned",
                        )
                    )
                    return False
                deadline = time.monotonic() + signal_timeout
            else:
                try:
                    self.process.send_signal(_CTRL_BREAK_EVENT)
                except (OSError, ValueError) as exc:
                    self.events.put(
                        (
                            self.spec.key,
                            "state",
                            "graceful stop signal failed: "
                            f"{type(exc).__name__}; process remains owned",
                        )
                    )
                    return False
                deadline = time.monotonic() + graceful_timeout
            while self.running and time.monotonic() < deadline:
                time.sleep(0.2)
            if self.running:
                self.events.put(
                    (
                        self.spec.key,
                        "state",
                        "graceful stop deadline exhausted; "
                        f"owned PID {self.process.pid} remains",
                    )
                )
                return False
            return True
        finally:
            self.stopping = False


class SingleInstance:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+b")
        self.handle.seek(0)
        self.handle.write(b"0")
        self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("Центр управления уже открыт") from exc

    def close(self) -> None:
        if self.handle.closed:
            return
        if os.name == "nt":
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def _request_windows_close(pid: int) -> int:
    """Post WM_CLOSE only to top-level windows owned by the exact PID."""

    if os.name != "nt" or _WINDLL is None or pid <= 0:
        return 0
    user32 = getattr(_WINDLL, "user32", None)
    if user32 is None:
        return 0
    callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    closed = 0

    def visit(hwnd, _lparam) -> bool:
        nonlocal closed
        window_pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if int(window_pid.value) == int(pid):
            if user32.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE
                closed += 1
        return True

    callback = callback_type(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(visit)
    user32.EnumWindows(callback, 0)
    return closed


def _process_started_at(pid: int) -> float | None:
    """Return process creation time without invoking another shell process."""
    if pid <= 0:
        return None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return 0.0
        except OSError:
            return None
    process_query_limited_information = 0x1000
    if _WINDLL is None:  # pragma: no cover - guarded by the Windows call path
        return None
    handle = _WINDLL.kernel32.OpenProcess(
        process_query_limited_information, False, int(pid)
    )
    if not handle:
        return None
    try:
        creation = ctypes.wintypes.FILETIME()
        exit_time = ctypes.wintypes.FILETIME()
        kernel = ctypes.wintypes.FILETIME()
        user = ctypes.wintypes.FILETIME()
        if not _WINDLL.kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return ticks / 10_000_000.0 - 11_644_473_600.0
    finally:
        _WINDLL.kernel32.CloseHandle(handle)


def _process_executable(pid: int) -> Path | None:
    """Return an executable path without reading process arguments."""
    if os.name != "nt" or pid <= 0:
        return None
    process_query_limited_information = 0x1000
    if _WINDLL is None:  # pragma: no cover - guarded by the Windows call path
        return None
    handle = _WINDLL.kernel32.OpenProcess(
        process_query_limited_information, False, int(pid)
    )
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32_768)
        size = ctypes.wintypes.DWORD(len(buffer))
        if not _WINDLL.kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return None
        return Path(buffer.value)
    finally:
        _WINDLL.kernel32.CloseHandle(handle)


def _listening_pid(
    port: int,
    *,
    stage_callback: Callable[[ListenerProbeStageEvent], None] | None = None,
) -> int | None:
    """Return the unique Windows PID listening on a known local TCP port."""
    if os.name != "nt" or port <= 0:
        return None
    pids = {
        listener.pid
        for listener in collect_windows_listeners(stage_callback=stage_callback)
        if listener.port == port
    }
    if len(pids) > 1:
        raise WindowsListenerProbeError(
            "listener_probe_ambiguous_port", stage="decode"
        )
    return next(iter(pids), None)


def _same_live_process(pid: int, started_at: float | int | None) -> bool:
    expected = float(started_at or 0.0)
    actual = _process_started_at(pid)
    return bool(actual is not None and expected > 0 and abs(actual - expected) <= 5.0)


def _process_descends_from(pid: int, ancestor_pid: int) -> bool:
    """Prove process ancestry without reading command lines or environment."""

    if pid <= 0 or ancestor_pid <= 0:
        return False
    if pid == ancestor_pid:
        return True
    try:
        import psutil  # type: ignore[import-untyped]

        current = psutil.Process(pid)
        seen: set[int] = set()
        while current.pid > 0 and current.pid not in seen:
            seen.add(current.pid)
            parent = current.parent()
            if parent is None:
                return False
            if parent.pid == ancestor_pid:
                return True
            current = parent
    except Exception:
        return False
    return False


def _external_process_descriptor(
    *,
    key: str,
    pid: int | None,
    started_at: float | None,
    executable: str | None = None,
    executable_matches: bool = False,
    owned_child: bool = False,
    source: str = "external",
) -> dict[str, Any]:
    """Describe observed processes without upgrading observation into authority."""
    del (
        executable,
        executable_matches,
    )  # identity evidence is useful, but is not ownership
    stoppable = bool(owned_child)
    return {
        "key": key,
        "pid": int(pid or 0),
        "started_at": started_at,
        "source": source,
        "stoppable": stoppable,
        "authority": "owned_child" if stoppable else "display_only",
    }


def validate_owner_group_start(
    specs: tuple[ContourSpec, ...], keys: tuple[str, ...]
) -> None:
    """Reject an entire multi-start request before starting any conflicting contour."""
    by_key = {spec.key: spec for spec in specs}
    groups: dict[str, str] = {}
    for key in keys:
        spec = by_key.get(key)
        if spec is None:
            raise ValueError(f"unknown contour: {key}")
        if not spec.owner_group:
            continue
        prior = groups.get(spec.owner_group)
        if prior is not None and prior != key:
            raise ValueError(
                f"owner group {spec.owner_group!r} requested by both {prior!r} and {key!r}"
            )
        groups[spec.owner_group] = key


def _load_external_contours(path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    rows = payload.get("contours") if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        return {}
    external: dict[str, dict] = {}
    for key, row in rows.items():
        if not isinstance(row, dict):
            continue
        pid = int(row.get("pid") or 0)
        expected = float(row.get("started_at") or 0.0)
        actual = _process_started_at(pid)
        if actual is None or expected <= 0 or abs(actual - expected) > 5.0:
            continue
        external[str(key)] = _external_process_descriptor(
            key=str(key), pid=pid, started_at=actual, source="heartbeat"
        )
    return external


class ControlCenter(tk.Tk):
    def __init__(
        self, instance: SingleInstance, autostart: tuple[str, ...] = ()
    ) -> None:
        super().__init__()
        self.title("Исследовательский центр trading-bot-v2")
        self.geometry("1180x760")
        self.minsize(900, 600)
        self.configure(background="#0f172a")
        self._configure_style()
        self.events: queue.Queue[tuple[str, str, str]] = queue.Queue()
        self.instance = instance
        self._process_identity = current_process_identity()
        self.external_contours = _load_external_contours(STATE_DIR / "heartbeat.json")
        self.contours = {
            spec.key: ManagedContour(spec, self.events) for spec in contour_specs()
        }
        self.status_vars: dict[str, tk.StringVar] = {}
        self.buttons: dict[str, ttk.Button] = {}
        self.selected_key = "ollama"
        self._closing = False
        self._hard_fail_stop_started = False
        self._shutdown_state_lock = threading.Lock()
        self._shutdown_state: dict[str, object] = {
            "state": "running",
            "reason_code": None,
            "started_at": None,
        }
        self._requested_profile_keys: set[str] = set()
        self._runtime_monitor: CanaryMonitoringService | None = None
        self._runtime_owner_monitor: CanonicalOwnerSafetyMonitor | None = None
        self._runtime_monitor_started_at: float | None = None
        self._runtime_ready = False
        self._runtime_lane_states: dict[str, str] = {}
        self._runtime_probe_stage_lock = threading.Lock()
        self._runtime_probe_stage: dict[str, object] = {
            "stage": "not_started",
            "state": "idle",
            "monotonic_at": None,
            "elapsed_seconds": None,
        }
        self._logs: dict[str, list[str]] = {key: [] for key in self.contours}
        self._json_status_cache = _JsonStatusCache()
        self._ui_snapshot_lock = threading.Lock()
        self._ui_snapshot_state: dict[str, object] = {
            "stage": "not_started",
            "stage_started_at": None,
            "last_completed_at": None,
            "last_duration_seconds": None,
            "last_error_type": None,
        }
        self._last_compute_pipeline: dict[str, object] = {
            "state": "unknown",
            "reason": "ui_snapshot_pending",
            "hard_fail": False,
            "execution_allowed": False,
        }
        self.system_var = tk.StringVar(value="Состояние фермы загружается…")
        self.learning_var = tk.StringVar(value="Контур обучения загружается…")
        self.candles_var = tk.StringVar(value="Библиотека свечей загружается…")
        self.manual_symbol = tk.StringVar(value="BTC")
        self.manual_timeframe = tk.StringVar(value="15m")
        self.manual_reason = tk.StringVar(value="срочная ручная проверка")
        self._build()
        self._heartbeat_publisher = _HeartbeatPublisher(
            STATE_DIR / "heartbeat.json",
            self._heartbeat_payload,
            on_error=self._record_heartbeat_publish_error,
        )
        self._heartbeat_publisher.start()
        self.after(150, self._poll)
        self.after(1000, self._heartbeat)
        if autostart:
            self.after(500, lambda: self._start_authorized(autostart))
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#0f172a")
        style.configure(
            "TLabel", background="#0f172a", foreground="#dbeafe", font=("Segoe UI", 10)
        )
        style.configure(
            "Header.TLabel", foreground="#f8fafc", font=("Segoe UI", 22, "bold")
        )
        style.configure("Muted.TLabel", foreground="#93a4b8", font=("Segoe UI", 9))
        style.configure(
            "Status.TLabel",
            background="#132238",
            foreground="#a7f3d0",
            font=("Segoe UI", 10, "bold"),
            padding=(10, 8),
        )
        style.configure(
            "Card.TLabelframe",
            background="#172033",
            foreground="#e2e8f0",
            borderwidth=1,
        )
        style.configure(
            "Card.TLabelframe.Label",
            background="#172033",
            foreground="#7dd3fc",
            font=("Segoe UI", 10, "bold"),
        )
        style.configure("Card.TLabel", background="#172033", foreground="#dbeafe")
        style.configure(
            "Accent.TButton",
            background="#0284c7",
            foreground="#ffffff",
            padding=(12, 7),
        )
        style.map("Accent.TButton", background=[("active", "#0369a1")])
        style.configure(
            "Danger.TButton",
            background="#b91c1c",
            foreground="#ffffff",
            padding=(12, 7),
        )
        style.map("Danger.TButton", background=[("active", "#991b1b")])
        style.configure(
            "TButton", background="#334155", foreground="#f8fafc", padding=(9, 5)
        )
        style.map("TButton", background=[("active", "#475569")])

    def _build(self) -> None:
        ttk.Label(self, text="Исследовательский центр", style="Header.TLabel").pack(
            anchor="w", padx=18, pady=(14, 2)
        )
        ttk.Label(
            self,
            text="Paper/research only · сделки, AUTO_TRADE и private endpoints отсутствуют",
        ).pack(anchor="w", padx=18, pady=(0, 6))
        ttk.Label(
            self, text=f"Приватные данные: {PRIVATE_ROOT}", style="Muted.TLabel"
        ).pack(anchor="w", padx=18, pady=(0, 10))
        ttk.Label(
            self, textvariable=self.system_var, style="Status.TLabel", wraplength=1120
        ).pack(anchor="w", fill=tk.X, padx=18, pady=(0, 4))
        ttk.Label(
            self, textvariable=self.learning_var, style="Status.TLabel", wraplength=1120
        ).pack(anchor="w", fill=tk.X, padx=18, pady=(0, 4))
        ttk.Label(
            self, textvariable=self.candles_var, style="Status.TLabel", wraplength=1120
        ).pack(anchor="w", fill=tk.X, padx=18, pady=(0, 10))
        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, padx=18, pady=(0, 10))
        ttk.Button(
            actions,
            text="Запустить рабочий комплекс",
            command=self._start_research_profile,
            style="Accent.TButton",
        ).pack(side=tk.LEFT)
        ttk.Button(
            actions,
            text="Остановить рабочий комплекс",
            command=self._stop_research_profile,
            style="Danger.TButton",
        ).pack(side=tk.LEFT, padx=8)
        urgent = ttk.LabelFrame(
            self,
            text="Срочный ручной расчёт (paper-only)",
            padding=9,
            style="Card.TLabelframe",
        )
        urgent.pack(fill=tk.X, padx=18, pady=(0, 8))
        ttk.Label(urgent, text="Монета", style="Card.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Entry(urgent, textvariable=self.manual_symbol, width=14).grid(
            row=0, column=1, padx=(6, 12)
        )
        ttk.Label(urgent, text="Таймфрейм", style="Card.TLabel").grid(
            row=0, column=2, sticky="w"
        )
        ttk.Combobox(
            urgent,
            textvariable=self.manual_timeframe,
            values=("15m", "1h", "4h", "1d"),
            state="readonly",
            width=7,
        ).grid(row=0, column=3, padx=(6, 12))
        ttk.Label(urgent, text="Причина", style="Card.TLabel").grid(
            row=0, column=4, sticky="w"
        )
        ttk.Entry(urgent, textvariable=self.manual_reason).grid(
            row=0, column=5, padx=6, sticky="ew"
        )
        ttk.Button(
            urgent,
            text="Поставить первой",
            command=self._enqueue_manual_urgent,
            style="Accent.TButton",
        ).grid(row=0, column=6, padx=(8, 0))
        urgent.columnconfigure(5, weight=1)
        body = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=8)
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=2)
        body.add(right, weight=3)
        canvas = tk.Canvas(left, background="#0f172a", highlightthickness=0)
        scrollbar = ttk.Scrollbar(left, orient=tk.VERTICAL, command=canvas.yview)
        cards = ttk.Frame(canvas)
        cards.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        card_window = canvas.create_window((0, 0), window=cards, anchor="nw")
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(card_window, width=event.width),
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        for spec in contour_specs():
            frame = ttk.LabelFrame(
                cards, text=spec.title, padding=9, style="Card.TLabelframe"
            )
            frame.pack(fill=tk.X, pady=4)
            ttk.Label(frame, text=spec.description, style="Card.TLabel").grid(
                row=0, column=0, sticky="w"
            )
            var = tk.StringVar(value="выключен")
            self.status_vars[spec.key] = var
            ttk.Label(frame, textvariable=var, style="Card.TLabel").grid(
                row=1, column=0, sticky="w"
            )
            button = ttk.Button(
                frame,
                text="Включить",
                command=partial(self._toggle, spec.key),
            )
            button.grid(row=0, column=1, rowspan=2, padx=8)
            self.buttons[spec.key] = button
            ttk.Button(
                frame,
                text="Журнал",
                command=partial(self._select, spec.key),
            ).grid(row=0, column=2, rowspan=2)
            frame.columnconfigure(0, weight=1)
        ttk.Label(
            right, text="Журнал выбранного контура", font=("Segoe UI", 11, "bold")
        ).pack(anchor="w", padx=6, pady=(2, 6))
        self.log = tk.Text(
            right,
            wrap="word",
            state="disabled",
            font=("Cascadia Mono", 9),
            background="#08101f",
            foreground="#cbd5e1",
            insertbackground="#f8fafc",
            relief=tk.FLAT,
            padx=10,
            pady=10,
        )
        self.log.pack(fill=tk.BOTH, expand=True, padx=6)
        ttk.Button(right, text="Обновить статусы", command=self._refresh).pack(
            anchor="e", padx=6, pady=8
        )

    def _select(self, key: str) -> None:
        self.selected_key = key
        self._render_log()

    def _enqueue_manual_urgent(self) -> None:
        symbol = self.manual_symbol.get().strip()
        timeframe = self.manual_timeframe.get().strip()
        reason = self.manual_reason.get().strip()
        if not symbol:
            messagebox.showwarning("Срочный расчёт", "Укажи монету, например BTC.")
            return

        def enqueue() -> None:
            command = _python(
                "-m",
                "scripts.strategy_lab.enqueue_manual_urgent",
                symbol,
                "--timeframe",
                timeframe,
                "--reason",
                reason,
                "--private-root",
                str(PRIVATE_ROOT),
            )
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["TRADING_BOT_RESEARCH_ROOT"] = str(PRIVATE_ROOT)
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=15,
                check=False,
                creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if completed.returncode == 0:
                self.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Срочный расчёт",
                        f"{symbol.upper()} {timeframe} поставлен первым в очередь. Сделки не исполняются.",
                    ),
                )
            else:
                error = (
                    completed.stderr or completed.stdout or "неизвестная ошибка"
                ).strip()[:300]
                self.after(0, lambda: messagebox.showerror("Срочный расчёт", error))

        threading.Thread(target=enqueue, daemon=True).start()

    def _toggle(self, key: str) -> None:
        item = self.contours[key]
        if item.running:
            self.status_vars[key].set("останавливается…")
            threading.Thread(target=item.stop, daemon=True).start()
            return
        external = self._external_descriptor(key)
        if external:
            self._request_external_stop(key, external)
            return
        if item.spec.owner_group:
            conflict = next(
                (
                    other
                    for other in self.contours.values()
                    if other.running
                    and other.spec.key != key
                    and other.spec.owner_group == item.spec.owner_group
                ),
                None,
            )
            if conflict:
                messagebox.showwarning(
                    "Двойной запуск запрещён",
                    f"Уже работает «{conflict.spec.title}». Сначала выключи его.",
                )
                return
        if item.spec.telegram and not messagebox.askyesno(
            "Подтверждение Telegram",
            f"Включить «{item.spec.title}»? Контур может отправлять сообщения в Telegram.",
        ):
            return
        try:
            item.start()
        except OSError as exc:
            self.status_vars[key].set(f"ошибка запуска: {exc}")

    def _start_authorized(self, keys: tuple[str, ...]) -> None:
        """Start only contours explicitly named on this process command line."""
        active_owned = tuple(key for key, item in self.contours.items() if item.running)
        active_external = tuple(
            key
            for key in getattr(self, "external_contours", {})
            if key in self.contours
        )
        active = active_owned + active_external
        try:
            validate_owner_group_start(contour_specs(), keys + active)
        except ValueError as exc:
            for key in keys:
                if key in self.status_vars:
                    self.status_vars[key].set(f"start rejected: {exc}")
            return
        requested = self.__dict__.get("_requested_profile_keys", set())
        requested.update(keys)
        self._requested_profile_keys = requested
        for key in keys:
            item = self.contours[key]
            if not item.running and not self._external_running(key):
                try:
                    item.start()
                except OSError as exc:
                    self.status_vars[key].set(f"ошибка запуска: {exc}")

        self._maybe_start_runtime_monitor()

    def _start_research_profile(self) -> None:
        if not messagebox.askyesno(
            "Запуск рабочего комплекса",
            "Запустить public-news, scanner, paper-ферму с карточками и интерактивный бот? "
            "Будет разрешена Telegram-доставка, но не сделки.",
        ):
            return
        if not self.contours["ollama"].running and not self._external_running("ollama"):
            self.contours["ollama"].start()
        self._requested_profile_keys.add("ollama")
        self.after(
            2000,
            lambda: self._start_authorized(
                ("public_news", "scanner", "paper_cards", "telegram_bot")
            ),
        )

    def _stop_research_profile(self) -> None:
        owned = [
            self.contours[key]
            for key in (
                "telegram_bot",
                "paper_cards",
                "farm",
                "scanner",
                "public_news",
                "ollama",
            )
            if self.contours[key].running
        ]
        external = self._external_profile_contours()
        if not owned and not external:
            return
        if not messagebox.askyesno(
            "Остановка рабочего комплекса",
            "Остановить все работающие контуры, включая обнаруженные внешние процессы?",
        ):
            return
        self._stop_runtime_monitor()

        def stop_owned() -> None:
            # Consumers stop before shared providers; the previous parallel
            # stop could tear down Ollama while farm/Telegram were still
            # finishing their own graceful paths.
            for item in owned:
                item.stop()
            for key, descriptor in external:
                self._stop_external(key, descriptor)

        threading.Thread(target=stop_owned, daemon=True).start()

    def _initiate_hard_fail_stop(self, reason: str) -> bool:
        """Stop the exact owned canonical profile once, without a UI prompt."""

        if self._hard_fail_stop_started:
            return False
        self._hard_fail_stop_started = True
        self._set_shutdown_state("stopping", reason_code="runtime_hard_fail")
        self._closing = True
        self._stop_runtime_monitor()
        evidence_written = self._persist_hard_fail_alert(reason)
        owned = [
            self.contours[key]
            for key in (
                "telegram_bot",
                "paper_cards",
                "farm",
                "scanner",
                "public_news",
                "ollama",
            )
            if self.contours[key].running
        ]
        self.events.put(
            (
                self.selected_key,
                "log",
                f"HARD FAIL: {str(reason)[:160]}; documented graceful stop started",
            )
        )
        if not evidence_written:
            self.events.put(
                (
                    self.selected_key,
                    "log",
                    "hard-fail alert evidence write failed",
                )
            )

        def stop_owned() -> None:
            results: list[bool] = []
            for item in owned:
                try:
                    results.append(bool(item.stop()))
                except Exception:  # noqa: BLE001 - fail closed without payloads
                    results.append(False)
            stopped = all(results)
            if not stopped:
                self._set_shutdown_state(
                    "stop_failed", reason_code="owned_contour_stop_failed"
                )
            self.events.put(
                (
                    "__app__",
                    "close" if stopped else "stop_failed",
                    (
                        ""
                        if stopped
                        else "hard-fail graceful stop left an owned contour running"
                    ),
                )
            )

        threading.Thread(target=stop_owned, daemon=True).start()
        return True

    @staticmethod
    def _safe_shutdown_reason(reason_code: str | None) -> str | None:
        if reason_code is None:
            return None
        safe = "".join(
            char
            for char in str(reason_code).lower()
            if char.isascii() and (char.isalnum() or char in "_:-")
        )[:80]
        return safe or "unspecified"

    def _set_shutdown_state(
        self,
        state: str,
        *,
        reason_code: str | None,
    ) -> None:
        if state not in {"running", "stopping", "stop_failed"}:
            raise ValueError("unsupported RCC shutdown state")
        lock = self.__dict__.get("_shutdown_state_lock")
        if lock is None:
            lock = threading.Lock()
            self._shutdown_state_lock = lock
        with lock:
            prior = dict(self.__dict__.get("_shutdown_state", {}))
            started_at = prior.get("started_at")
            if state == "running":
                started_at = None
            elif started_at is None:
                started_at = time.time()
            self._shutdown_state = {
                "state": state,
                "reason_code": self._safe_shutdown_reason(reason_code),
                "started_at": started_at,
            }

    def _record_listener_probe_stage(self, event: ListenerProbeStageEvent) -> None:
        lock = self.__dict__.get("_runtime_probe_stage_lock")
        if lock is None:
            lock = threading.Lock()
            self._runtime_probe_stage_lock = lock
        with lock:
            self._runtime_probe_stage = {
                "stage": event.stage,
                "state": event.state,
                "monotonic_at": event.monotonic_at,
                "elapsed_seconds": event.elapsed_seconds,
            }

    @staticmethod
    def _persist_hard_fail_alert(reason: str) -> bool:
        alert = {
            "schema": "ResearchControlCenterAlert.v1",
            "alert_id": f"rcc_hard_fail:{time.time_ns()}",
            "reason": str(reason)[:160],
            "detected_at": time.time(),
            "paper_only": True,
            "execution_allowed": False,
            "automatic_restart": False,
        }
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            with (STATE_DIR / "alerts.jsonl").open(
                "a",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                handle.write(
                    json.dumps(alert, ensure_ascii=True, sort_keys=True) + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            return False
        return True

    def _read_active_authority_rows(self) -> tuple[dict[str, object], ...]:
        path = (PRIVATE_ROOT / "state" / "ownership.sqlite").resolve()
        if not path.is_file():
            return ()
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro",
            uri=True,
            timeout=1.0,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 1000")
            rows = connection.execute(
                """
                SELECT resource_id, role_id, owner_id, pid, started_at,
                       executable, command_digest, lease_expires_at, next_fence
                FROM ownership_resources
                WHERE owner_id IS NOT NULL
                """
            ).fetchall()
            return tuple(dict(row) for row in rows)
        finally:
            connection.close()

    def _fast_runtime_safety_probe(self) -> dict[str, object]:
        started_at = self._runtime_monitor_started_at
        owner_monitor = self._runtime_owner_monitor
        if started_at is None or owner_monitor is None:
            raise RuntimeError("runtime monitor was not initialized")
        elapsed = max(0.0, time.monotonic() - started_at)
        contours_ready = True
        for key in CANONICAL_PAPER_PROFILE:
            item = self.contours[key]
            if item.process is None:
                contours_ready = False
                if elapsed >= RUNTIME_STARTUP_BUDGET_SECONDS:
                    raise CanaryMonitorHardFailure(
                        f"required_contour_startup_timeout:{key}"
                    )
                continue
            if not item.running or not _same_live_process(
                item.process.pid,
                item.started_at,
            ):
                raise CanaryMonitorHardFailure(
                    f"required_contour_unavailable:{key}"
                )

        owner = owner_monitor.sample()
        supervisor = self._read_cached_json(
            PRIVATE_ROOT / "state" / "farm_process_lease_status.json"
        )
        supervisor_state = str(supervisor.get("state") or "")
        supervisor_updated_at = float(supervisor.get("updated_at") or 0.0)
        supervisor_age = (
            max(0.0, time.time() - supervisor_updated_at)
            if supervisor_updated_at > 0
            else None
        )
        supervisor_ready = bool(
            owner.ready
            and supervisor.get("schema") == "ProcessLeaseSupervisorStatus.v1"
            and supervisor_state == "running"
            and supervisor.get("paper_only") is True
            and supervisor.get("execution_allowed") is False
            and supervisor_age is not None
            and supervisor_age <= PROCESS_LEASE_SUPERVISOR_MAX_AGE_SECONDS
            and owner.process_identity is not None
            and int(supervisor.get("owner_pid") or 0)
            == owner.process_identity.pid
            and abs(
                float(supervisor.get("owner_started_at") or 0.0)
                - owner.process_identity.started_at
            )
            <= 0.001
            and int(supervisor.get("fencing_token") or 0)
            == int(owner.canonical_fence or 0)
            and float(supervisor.get("lease_expires_at") or 0.0) > time.time()
        )
        if owner.ready and not supervisor_ready and (
            self._runtime_ready or elapsed >= RUNTIME_STARTUP_BUDGET_SECONDS
        ):
            reason = (
                "farm_process_lease_supervisor_failed"
                if supervisor_state == "failed"
                else "farm_process_lease_supervisor_stale"
                if supervisor_age is not None
                and supervisor_age > PROCESS_LEASE_SUPERVISOR_MAX_AGE_SECONDS
                else "farm_process_lease_supervisor_unavailable"
            )
            raise CanaryMonitorHardFailure(reason)
        paper = self.contours["paper_cards"]
        if owner.process_identity is not None and (
            paper.process is None
            or not _process_descends_from(
                owner.process_identity.pid,
                paper.process.pid,
            )
        ):
            raise CanaryMonitorHardFailure("owner_not_in_canonical_rcc_tree")
        listener_pid = _listening_pid(
            11434,
            stage_callback=self._record_listener_probe_stage,
        )
        ollama = self.contours["ollama"]
        listener_ready = bool(
            listener_pid
            and ollama.process is not None
            and listener_pid == ollama.process.pid
            and _same_live_process(listener_pid, ollama.started_at)
        )
        if listener_pid and not listener_ready:
            raise CanaryMonitorHardFailure("foreign_ollama_listener")
        if not listener_ready and (
            self._runtime_ready or elapsed >= RUNTIME_STARTUP_BUDGET_SECONDS
        ):
            raise CanaryMonitorHardFailure("ollama_listener_unavailable")

        wall_started_at = time.time() - elapsed
        try:
            stop_intent_fresh = any(
                path.is_file() and path.stat().st_mtime >= wall_started_at
                for path in CANONICAL_STOP_INTENTS
            )
        except OSError as exc:
            raise CanaryMonitorHardFailure("stop_intent_probe_failed") from exc
        if stop_intent_fresh and not self._closing:
            raise CanaryMonitorHardFailure("canonical_stop_intent_during_runtime")

        ready = contours_ready and owner.ready and listener_ready and supervisor_ready
        if ready and not self._runtime_ready:
            self._runtime_ready = True
            self.events.put(
                (
                    self.selected_key,
                    "log",
                    "T+0 READY: canonical profile, Ollama listener, and "
                    "identity-matched fenced farm owner are green",
                )
            )
        state = (
            "ready"
            if ready
            else "process_starting"
            if not contours_ready
            else "listener_starting"
            if not listener_ready
            else "process_starting"
            if not supervisor_ready
            else owner.state
        )
        return {
            "state": state,
            "ready": ready,
            "owner_fence": owner.canonical_fence,
            "owner_resources": owner.resources,
            "lease_supervisor_state": supervisor_state or "pending",
            "lease_supervisor_age_seconds": supervisor_age,
            "ollama_listener_ready": listener_ready,
            "paper_only": True,
            "execution_allowed": False,
        }

    @staticmethod
    def _deep_runtime_safety_probe() -> dict[str, object]:
        sizes: dict[str, int] = {}
        database_paths = {
            "ownership.sqlite": PRIVATE_ROOT / "state" / "ownership.sqlite",
            "farm_tasks.sqlite": PRIVATE_ROOT / "state" / "farm_tasks.sqlite",
            "strategy_lab.sqlite": PRIVATE_ROOT / "state" / "strategy_lab.sqlite",
            "scanner_farm_loop.sqlite": (
                PRIVATE_ROOT / "state" / "scanner_farm_loop.sqlite"
            ),
            "candles.sqlite3": PRIVATE_ROOT / "market_data" / "candles.sqlite3",
        }
        for name, configured_path in database_paths.items():
            path = configured_path.resolve()
            if not path.is_file():
                raise CanaryMonitorHardFailure(f"canonical_database_missing:{name}")
            connection = sqlite3.connect(
                f"{path.as_uri()}?mode=ro",
                uri=True,
                timeout=1.0,
            )
            try:
                connection.execute("PRAGMA query_only = ON")
                connection.execute("PRAGMA busy_timeout = 1000")
                connection.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
                sizes[name] = int(path.stat().st_size)
            finally:
                connection.close()
        return {"database_count": len(sizes), "database_sizes": sizes}

    def _on_runtime_monitor_sample(self, sample: CanaryLaneSample) -> None:
        prior = self._runtime_lane_states.get(sample.lane)
        self._runtime_lane_states[sample.lane] = sample.state
        if prior != sample.state:
            self.events.put(
                (
                    self.selected_key,
                    "log",
                    f"runtime monitor {sample.lane}: {sample.state}",
                )
            )

    def _on_runtime_monitor_failure(
        self,
        lane: str,
        assessment: CanaryWatchdogAssessment,
    ) -> None:
        reason = assessment.failure_reason or "monitor_lane_failed"
        self.events.put(("__app__", "runtime_hard_fail", f"{lane}:{reason}"))

    def _maybe_start_runtime_monitor(self) -> None:
        if self.__dict__.get("_runtime_monitor") is not None:
            return
        if not CANONICAL_PAPER_PROFILE.issubset(
            self.__dict__.get("_requested_profile_keys", set())
        ):
            return
        self._runtime_monitor_started_at = time.monotonic()
        self._runtime_owner_monitor = CanonicalOwnerSafetyMonitor(
            rows_reader=self._read_active_authority_rows,
            identity_probe=probe_process_identity,
            startup_budget_seconds=RUNTIME_STARTUP_BUDGET_SECONDS,
        )
        monitor = CanaryMonitoringService(
            fast_probe=self._fast_runtime_safety_probe,
            deep_probe=self._deep_runtime_safety_probe,
            on_sample=self._on_runtime_monitor_sample,
            on_failure=self._on_runtime_monitor_failure,
            fast_interval_seconds=5.0,
            deep_interval_seconds=60.0,
        )
        self._runtime_monitor = monitor
        monitor.start()

    def _stop_runtime_monitor(self) -> None:
        monitor = self.__dict__.get("_runtime_monitor")
        self._runtime_monitor = None
        self._runtime_owner_monitor = None
        self._runtime_monitor_started_at = None
        self._runtime_ready = False
        self._runtime_lane_states = {}
        self._requested_profile_keys = set()
        if monitor is None:
            return
        residual = monitor.stop(timeout=2.0)
        if residual:
            self.events.put(
                (
                    self.selected_key,
                    "log",
                    "runtime monitor stop left blocked lanes: "
                    + ",".join(sorted(residual)),
                )
            )

    def _poll(self) -> None:
        while True:
            try:
                key, kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if key == "__app__" and kind == "close":
                self._stop_heartbeat_publisher()
                self.instance.close()
                self.destroy()
                return
            if key == "__app__" and kind == "stop_failed":
                self._set_shutdown_state(
                    "stop_failed", reason_code="owned_contour_stop_failed"
                )
                self._closing = False
                self.deiconify()
                self.events.put(
                    (
                        self.selected_key,
                        "log",
                        "RCC remains open because at least one owned contour "
                        "did not stop gracefully",
                    )
                )
                continue
            if key == "__app__" and kind == "runtime_hard_fail":
                self._initiate_hard_fail_stop(value)
                continue
            if kind == "state":
                self.status_vars[key].set(value)
            else:
                rows = self._logs[key]
                rows.append(value)
                del rows[:-500]
            if key == self.selected_key:
                self._render_log()
        self._refresh_buttons()
        self.after(250, self._poll)

    def _refresh_buttons(self) -> None:
        hard_fail_contours: list[str] = []
        for key, item in self.contours.items():
            if self._record_required_contour_exit(key, item):
                hard_fail_contours.append(key)
            if item.stopping:
                self.buttons[key].configure(text="Останавливается…", state="disabled")
                self.status_vars[key].set("штатная остановка…")
                continue
            external = None if item.running else self._external_descriptor(key)
            if external:
                pid = external.get("pid")
                if pid and external.get("stoppable"):
                    self.buttons[key].configure(
                        text=f"Остановить внешний PID {pid}", state="normal"
                    )
                    self.status_vars[key].set(f"работает вне центра · PID {pid}")
                elif pid:
                    self.buttons[key].configure(
                        text=f"Внешний PID {pid}: владелец не подтверждён",
                        state="disabled",
                    )
                    self.status_vars[key].set(
                        "порт занят неизвестным процессом; автоматическая остановка запрещена"
                    )
                else:
                    self.buttons[key].configure(
                        text="Внешний процесс: PID не найден", state="disabled"
                    )
                    self.status_vars[key].set(
                        "порт занят внешним процессом; безопасная остановка недоступна"
                    )
            else:
                self.buttons[key].configure(
                    text="Выключить" if item.running else "Включить",
                    state="normal",
                )
                if item.running:
                    self.status_vars[key].set(self._health_text(key, item))
        if hard_fail_contours:
            self._initiate_hard_fail_stop(
                "required_contour_unexpected_exit:"
                + ",".join(sorted(hard_fail_contours))
            )

    @staticmethod
    def _canonical_stop_intent_active(key: str, started_at: float) -> bool:
        fresh: dict[str, bool] = {}
        for path in CANONICAL_STOP_INTENTS:
            try:
                fresh[path.name] = (
                    path.is_file() and path.stat().st_mtime >= started_at
                )
            except OSError:
                fresh[path.name] = False
        direct_marker = {
            "farm": "STOP_FARM_FULL_CYCLE.txt",
            "paper_cards": "STOP_FARM_FULL_CYCLE.txt",
            "scanner": "STOP_NEWS_SCANNER.txt",
            "public_news": "STOP_PUBLIC_NEWS.txt",
        }.get(key)
        if direct_marker and fresh.get(direct_marker, False):
            return True
        coordinated = {
            "STOP_FARM_FULL_CYCLE.txt",
            "STOP_NEWS_SCANNER.txt",
            "STOP_PUBLIC_NEWS.txt",
        }
        return coordinated.issubset(
            {name for name, is_fresh in fresh.items() if is_fresh}
        )

    def _record_required_contour_exit(
        self,
        key: str,
        item: ManagedContour,
    ) -> bool:
        if key not in REQUIRED_RESEARCH_CONTOURS:
            return False
        process = item.consume_unexpected_exit()
        if process is None:
            return False
        if self._canonical_stop_intent_active(key, float(process["started_at"])):
            item.expected_running = False
            message = (
                "STOPPED: canonical stop intent observed "
                f"(code={process['exit_code']}); automatic restart is disabled"
            )
            rows = self._logs[key]
            rows.append(message)
            del rows[:-500]
            self.status_vars[key].set(message)
            if key == self.selected_key:
                self._render_log()
            return False
        farm_status = (
            self._read_cached_json(PRIVATE_ROOT / "state" / "farm_loop_status.json")
            if key in {"farm", "paper_cards"}
            else {}
        )
        updated_at = float(farm_status.get("updated_at") or 0.0)
        alert = {
            "schema": "ResearchControlCenterAlert.v1",
            "alert_id": (
                f"required_contour_exit:{key}:{process['pid']}:{process['started_at']}"
            ),
            "reason": "required_contour_unexpected_exit",
            "detected_at": time.time(),
            "contour": key,
            "pid": process["pid"],
            "process_started_at": process["started_at"],
            "exit_code": process["exit_code"],
            "last_stage": str(farm_status.get("stage") or ""),
            "progress_age_seconds": (
                max(0.0, time.time() - updated_at) if updated_at > 0 else None
            ),
            "log_pointer": f"rcc_contour_log:{key}",
            "paper_only": True,
            "execution_allowed": False,
            "automatic_restart": False,
        }
        evidence_written = True
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            with (STATE_DIR / "alerts.jsonl").open(
                "a",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                handle.write(
                    json.dumps(alert, ensure_ascii=True, sort_keys=True) + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            evidence_written = False
        message = (
            "FAILED: required contour exited unexpectedly "
            f"(code={process['exit_code']}); automatic restart is disabled"
        )
        if not evidence_written:
            message += "; alert evidence write failed"
        rows = self._logs[key]
        rows.append(message)
        del rows[:-500]
        self.status_vars[key].set(message)
        if key == self.selected_key:
            self._render_log()
        return True

    @staticmethod
    def _file_age(path: Path) -> int | None:
        try:
            return max(0, int(time.time() - path.stat().st_mtime))
        except OSError:
            return None

    def _health_text(self, key: str, item: ManagedContour) -> str:
        pid = item.process.pid if item.process else "?"
        if key == "ollama":
            return (
                f"готов · API 11434 · PID {pid}"
                if self._port_open(11434)
                else f"запускается · PID {pid}"
            )
        if key == "public_news":
            age = self._file_age(
                ROOT / "logs" / "scout" / "public_channel" / "publisher_audit.jsonl"
            )
            return (
                f"цикл работает · audit {format_age(age)} назад · PID {pid}"
                if age is not None
                else f"первый проход · PID {pid}"
            )
        if key == "scanner":
            age = self._file_age(ROOT / "logs" / "scout" / "scanner_journal.jsonl")
            return (
                f"очередь работает · journal {format_age(age)} назад · PID {pid}"
                if age is not None
                else f"первый проход · PID {pid}"
            )
        if key in {"farm", "paper_cards"}:
            status_path = PRIVATE_ROOT / "state" / "farm_loop_status.json"
            try:
                data = self._read_cached_json(status_path)
                stage = str(data.get("stage") or "неизвестно")
                age = max(0, int(time.time() - float(data.get("updated_at") or 0)))
                base = f"этап {stage} · heartbeat {format_age(age)} назад · PID {pid}"
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                base = f"первый цикл · PID {pid}"
            if key == "paper_cards":
                compute = self._compute_pipeline_health()
                base += f" · compute {compute['state']} ({compute['reason']})"
                delivery_path = (
                    PRIVATE_ROOT / "state" / "derived" / "paper_telegram_delivery.json"
                )
                try:
                    delivery = self._read_cached_json(delivery_path)
                    base += (
                        f" · Telegram: sent={int(delivery.get('sent_cards') or 0)}"
                        f" errors={int(delivery.get('errors') or 0)}"
                    )
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    pass
            return base
        if key == "telegram_bot":
            age = self._file_age(ROOT / "logs" / "telegram_bot.log")
            return (
                f"онлайн · log {format_age(age)} назад · PID {pid}"
                if age is not None
                else f"онлайн · PID {pid}"
            )
        if key == "dashboard":
            return (
                f"готов · http://127.0.0.1:8765 · PID {pid}"
                if self._port_open(8765)
                else f"запускается · PID {pid}"
            )
        return f"работает · PID {pid}"

    def _compute_pipeline_health(self) -> dict:
        priority = self._read_cached_json(
            PRIVATE_ROOT / "state" / "farm_priority_worker_status.json"
        )
        worker = self._read_cached_json(PRIVATE_ROOT / "state" / "worker_status.json")
        process_lease = self._read_cached_json(
            PRIVATE_ROOT / "state" / "farm_process_lease_status.json"
        )
        farm_item = self.contours.get("farm")
        farm_running = bool(farm_item and farm_item.running)
        owned_started_at = getattr(farm_item, "started_at", None)
        farm_started_at = (
            float(owned_started_at)
            if isinstance(owned_started_at, (int, float))
            else None
        )
        if not farm_running:
            descriptor = self._external_descriptor("farm")
            farm_running = descriptor is not None
            external_started_at = descriptor.get("started_at") if descriptor else None
            farm_started_at = (
                float(external_started_at)
                if isinstance(external_started_at, (int, float))
                else None
            )
        return assess_compute_pipeline(
            priority_status=priority,
            worker_status=worker,
            process_lease_status=process_lease,
            farm_running=farm_running,
            farm_started_at=farm_started_at,
        )

    @staticmethod
    def _port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.15):
                return True
        except OSError:
            return False

    def _external_running(self, key: str) -> bool:
        return self._external_descriptor(key) is not None

    def _external_descriptor(self, key: str) -> dict[str, Any] | None:
        """Return verified metadata for a contour owned by another center."""
        related = ("farm", "paper_cards") if key in {"farm", "paper_cards"} else (key,)
        for candidate in related:
            external_contours = getattr(self, "external_contours", {})
            row = external_contours.get(candidate)
            if not row:
                continue
            pid = int(row.get("pid") or 0)
            if _same_live_process(pid, row.get("started_at")):
                return _external_process_descriptor(
                    key=candidate,
                    pid=pid,
                    started_at=float(row["started_at"]),
                    source="heartbeat",
                )
            external_contours.pop(candidate, None)
        port = {"ollama": 11434, "dashboard": 8765}.get(key)
        if port and self._port_open(port):
            listener_pid = _listening_pid(port)
            started_at = _process_started_at(listener_pid or 0)
            executable = _process_executable(listener_pid or 0)
            expected_ollama = (
                Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
            )
            executable_matches = bool(
                key == "ollama"
                and executable
                and os.path.normcase(str(executable))
                == os.path.normcase(str(expected_ollama))
            )
            return _external_process_descriptor(
                key=key,
                pid=listener_pid,
                started_at=started_at,
                executable=str(executable) if executable else None,
                executable_matches=executable_matches,
                source="port",
            )
        return None

    def _external_profile_contours(self) -> list[tuple[str, dict[str, Any]]]:
        rows: list[tuple[str, dict[str, Any]]] = []
        seen: set[int] = set()
        for key in (
            "telegram_bot",
            "paper_cards",
            "farm",
            "scanner",
            "public_news",
            "ollama",
        ):
            if self.contours[key].running:
                continue
            descriptor = self._external_descriptor(key)
            pid = int((descriptor or {}).get("pid") or 0)
            if (
                descriptor
                and descriptor.get("stoppable")
                and pid > 0
                and pid not in seen
            ):
                rows.append((key, descriptor))
                seen.add(pid)
        return rows

    def _request_external_stop(self, key: str, descriptor: dict[str, Any]) -> None:
        pid = int(descriptor.get("pid") or 0)
        started_at = descriptor.get("started_at")
        if (
            not descriptor.get("stoppable")
            or pid <= 0
            or not _same_live_process(pid, started_at)
        ):
            messagebox.showwarning(
                "Внешний процесс",
                "PID внешнего процесса не подтверждён. Центр не будет останавливать неизвестный процесс.",
            )
            return
        if not messagebox.askyesno(
            "Остановка внешнего процесса",
            f"Остановить «{self.contours[key].spec.title}» · PID {pid}?",
        ):
            return
        self.buttons[key].configure(text="Останавливается…", state="disabled")
        threading.Thread(
            target=self._stop_external, args=(key, descriptor), daemon=True
        ).start()

    def _stop_external(self, key: str, descriptor: dict[str, Any]) -> None:
        pid = int(descriptor.get("pid") or 0)
        started_at = descriptor.get("started_at")
        if (
            not descriptor.get("stoppable")
            or pid <= 0
            or not _same_live_process(pid, started_at)
        ):
            self.events.put(
                (key, "state", "внешний процесс уже завершён или PID изменился")
            )
            return
        spec = self.contours[key].spec
        if spec.graceful_stop:
            spec.graceful_stop()
            deadline = time.monotonic() + spec.graceful_seconds
            while _same_live_process(pid, started_at) and time.monotonic() < deadline:
                time.sleep(0.2)
        elif key == "ollama" and _request_windows_close(pid) > 0:
            deadline = time.monotonic() + spec.graceful_seconds
            while _same_live_process(pid, started_at) and time.monotonic() < deadline:
                time.sleep(0.2)
        if not _same_live_process(pid, started_at):
            self.external_contours.pop(key, None)
            self.events.put((key, "state", f"внешний PID {pid} остановлен"))
        else:
            self.events.put((key, "state", f"не удалось остановить внешний PID {pid}"))

    def _render_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.insert(tk.END, "\n".join(self._logs[self.selected_key][-500:]))
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _refresh(self) -> None:
        for key, item in self.contours.items():
            if item.running and item.process:
                age = int(time.time() - item.started_at)
                self.status_vars[key].set(
                    f"работает · PID {item.process.pid} · {format_age(age)}"
                )

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _read_cached_json(
        self,
        path: Path,
        *,
        max_age_seconds: float = STATUS_CACHE_SECONDS,
    ) -> dict:
        cache = getattr(self, "_json_status_cache", None)
        if cache is None:
            cache = _JsonStatusCache()
            self._json_status_cache = cache
        return cache.read(path, self._read_json, max_age_seconds=max_age_seconds)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        try:
            return [
                row
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and isinstance((row := json.loads(line)), dict)
            ]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []

    def _learning_snapshot(self) -> str:
        state = PRIVATE_ROOT / "state"
        role_counts: dict[str, int] = {}
        work_counts: dict[str, dict[str, int]] = {}
        max_generation = 0
        for recipient in ("farm", "validator", "trader"):
            env_dir = state / "role_environments" / recipient
            role_counts[recipient] = (
                len(list(env_dir.glob("env_*.json"))) if env_dir.exists() else 0
            )
            counters = {"waiting": 0, "queued": 0, "completed": 0, "deduped": 0}
            work_dir = state / "role_work_queue" / recipient
            if work_dir.exists():
                for path in work_dir.glob("env_*.json"):
                    row = self._read_json(path)
                    status = str(row.get("status") or "waiting")
                    counters[status] = counters.get(status, 0) + 1
                    raw_spec = row.get("task_spec")
                    spec = raw_spec if isinstance(raw_spec, dict) else {}
                    max_generation = max(
                        max_generation, int(spec.get("generation") or 0)
                    )
            work_counts[recipient] = counters
        results = self._read_jsonl(
            state / "derived" / "system_analyst_result_inbox.jsonl"
        )
        drafts = self._read_jsonl(state / "llm_advice" / "system_analyst_drafts.jsonl")
        reviewed = {
            str(row.get("source_ref") or "")
            for row in drafts
            if row.get("accepted") and str(row.get("role_id") or "") == "system_analyst"
        }
        result_ids = {str(row.get("result_id") or "") for row in results}
        pending_review = len(result_ids - reviewed)
        labels = {
            "farm": "ферма",
            "validator": "валидатор",
            "trader": "paper-наблюдатель",
        }
        role_text = []
        for recipient in ("farm", "validator", "trader"):
            counts = work_counts[recipient]
            role_text.append(
                f"{labels[recipient]}: заданий {role_counts[recipient]}, "
                f"в очереди {counts.get('queued', 0)}, ждут данных {counts.get('waiting', 0)}, "
                f"готово {counts.get('completed', 0)}"
            )
        return (
            "Обучение · Alibaba · "
            + " | ".join(role_text)
            + f" | вернулось аналитику {len(results)}, ждут разбора {pending_review}, "
            f"поколение {max_generation}/2"
        )

    @staticmethod
    def _open_readonly_db(path: Path) -> sqlite3.Connection | None:
        if not path.is_file():
            return None
        try:
            conn = sqlite3.connect(
                f"file:{path.as_posix()}?mode=ro", uri=True, timeout=0.2
            )
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error:
            return None

    def _queue_snapshot(self) -> dict:
        conn = self._open_readonly_db(PRIVATE_ROOT / "state" / "farm_tasks.sqlite")
        if conn is None:
            return {}
        try:
            row = conn.execute(
                """SELECT
                     SUM(CASE WHEN state IN ('queued','running') AND priority<=0 THEN 1 ELSE 0 END) manual,
                     SUM(CASE WHEN state IN ('queued','running') AND priority BETWEEN 1 AND 10 THEN 1 ELSE 0 END) go_n,
                     SUM(CASE WHEN state IN ('queued','running') AND priority BETWEEN 11 AND 20 THEN 1 ELSE 0 END) watch_n,
                     SUM(CASE WHEN state='queued' THEN 1 ELSE 0 END) queued,
                     SUM(CASE WHEN state='running' THEN 1 ELSE 0 END) running
                   FROM tasks"""
            ).fetchone()
            current = conn.execute(
                "SELECT task_type, symbol, timeframe, family, priority FROM tasks "
                "WHERE state='running' ORDER BY priority, updated_at LIMIT 1"
            ).fetchone()
            waiting_manual = conn.execute(
                "SELECT COUNT(*) FROM intake_events WHERE consumed=0 AND priority<=0"
            ).fetchone()[0]
            return {
                "manual": int(row["manual"] or 0) + int(waiting_manual or 0),
                "go": int(row["go_n"] or 0),
                "watch": int(row["watch_n"] or 0),
                "queued": int(row["queued"] or 0),
                "running": int(row["running"] or 0),
                "current": dict(current) if current else {},
            }
        except sqlite3.Error:
            return {}
        finally:
            conn.close()

    def _backend_snapshot(self) -> dict:
        conn = self._open_readonly_db(PRIVATE_ROOT / "state" / "strategy_lab.sqlite")
        if conn is None:
            return {}
        try:
            row = conn.execute(
                "SELECT effective_backend, signal_backend, simulation_backend, accelerated_runs, created_at "
                "FROM runtime_stats ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else {}
        except sqlite3.Error:
            return {}
        finally:
            conn.close()

    @staticmethod
    def _gpu_snapshot() -> str:
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=2,
                check=False,
                creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            parts = [part.strip() for part in completed.stdout.strip().split(",")]
            if completed.returncode == 0 and len(parts) >= 4:
                return f"GPU {parts[0]}% · VRAM {parts[1]}/{parts[2]} МБ · {parts[3]}°C"
        except (OSError, subprocess.SubprocessError):
            pass
        return "GPU: нет данных"

    def _system_snapshot(self) -> str:
        queue_state = self._queue_snapshot()
        backend = self._backend_snapshot()
        farm = self._read_cached_json(PRIVATE_ROOT / "state" / "farm_loop_status.json")
        priority_worker = self._read_cached_json(
            PRIVATE_ROOT / "state" / "farm_priority_worker_status.json"
        )
        stage = (
            str(farm.get("stage") or "работает")
            if self.contours.get("farm") and self.contours["farm"].running
            else "остановлена"
        )
        current = queue_state.get("current") or {}
        current_text = "нет активного слота"
        if current:
            target = "/".join(
                str(current.get(key) or "") for key in ("symbol", "timeframe", "family")
            ).strip("/")
            current_text = f"{current.get('task_type')} {target} · приоритет {current.get('priority')}"
        queue_text = (
            f"очередь {queue_state.get('queued', 0)} · работа {queue_state.get('running', 0)} · "
            f"ручные {queue_state.get('manual', 0)} · GO {queue_state.get('go', 0)} · "
            f"WATCH {queue_state.get('watch', 0)}"
        )
        backend_text = (
            f"backend {backend.get('effective_backend') or '?'} "
            f"(сигналы {backend.get('signal_backend') or '?'}, "
            f"симуляция {backend.get('simulation_backend') or '?'})"
        )
        return (
            f"{self._gpu_snapshot()}  |  {queue_text}  |  этап: {stage}  |  "
            f"priority worker: {priority_worker.get('stage') or 'остановлен'}  |  "
            f"сейчас: {current_text}  |  {backend_text}"
        )

    def _candle_snapshot(self) -> str:
        path = PRIVATE_ROOT / "market_data" / "candles.sqlite3"
        conn = self._open_readonly_db(path)
        if conn is None:
            return "Свечи · единая библиотека ещё не создана · используется переходный JSON"
        try:
            total = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(row_count),0), COALESCE(SUM(gap_count),0) FROM series"
            ).fetchone()
            by_tf = conn.execute(
                "SELECT timeframe, COUNT(*), COALESCE(SUM(row_count),0) "
                "FROM series GROUP BY timeframe ORDER BY timeframe"
            ).fetchall()
        except sqlite3.Error:
            return "Свечи · библиотека недоступна для чтения"
        finally:
            conn.close()
        parts = [
            f"{row[0]}: серий {int(row[1])}, свечей {int(row[2])}" for row in by_tf
        ]
        size_mb = sum(
            self._optional_file_size(p)
            for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        ) / (1024 * 1024)
        return (
            f"Свечи · единая SQLite-библиотека · серий {int(total[0])} · "
            f"свечей {int(total[1])} · разрывов {int(total[2])} · {size_mb:.1f} МБ"
            + (" | " + " | ".join(parts) if parts else "")
        )

    @staticmethod
    def _optional_file_size(path: Path) -> int:
        """Return a transient SQLite file size without racing its deletion."""
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _heartbeat_payload(self) -> dict[str, object]:
        contour_rows: dict[str, dict[str, object]] = {}
        for key, item in self.contours.items():
            process = item.process
            running = bool(process and process.poll() is None)
            contour_rows[key] = {
                "running": running,
                "owned": running,
                "external": False,
                "pid": int(process.pid) if running and process else None,
                "started_at": float(item.started_at) if running else None,
                "status": (
                    "running"
                    if running
                    else (
                        "failed_unexpected_exit"
                        if item.unexpected_exit_reported
                        else "stopped"
                    )
                ),
            }
        with self._ui_snapshot_lock:
            ui_snapshot = dict(self._ui_snapshot_state)
        shutdown_lock = self.__dict__.get("_shutdown_state_lock")
        shutdown: dict[str, object]
        if shutdown_lock is None:
            shutdown = {
                "state": "running",
                "reason_code": None,
                "started_at": None,
            }
        else:
            with shutdown_lock:
                shutdown = dict(self._shutdown_state)
        probe_lock = self.__dict__.get("_runtime_probe_stage_lock")
        runtime_probe: dict[str, object]
        if probe_lock is None:
            runtime_probe = {
                "stage": "not_started",
                "state": "idle",
                "monotonic_at": None,
                "elapsed_seconds": None,
            }
        else:
            with probe_lock:
                runtime_probe = dict(self._runtime_probe_stage)
        stage_started_at = ui_snapshot.get("stage_started_at")
        ui_snapshot["stage_age_seconds"] = (
            max(0.0, time.time() - float(stage_started_at))
            if isinstance(stage_started_at, (int, float))
            else None
        )
        return {
            "schema": "ResearchControlCenterHeartbeat.v3",
            "updated_at": time.time(),
            "pid": self._process_identity.pid,
            "started_at": self._process_identity.started_at,
            "paper_only": True,
            "execution_allowed": False,
            "shutdown": shutdown,
            "runtime_probe": runtime_probe,
            "compute_pipeline": dict(self._last_compute_pipeline),
            "ui_snapshot": ui_snapshot,
            "contours": contour_rows,
        }

    def _record_heartbeat_publish_error(self, exc: Exception) -> None:
        self.events.put(
            (
                self.selected_key,
                "log",
                f"heartbeat publish error: {type(exc).__name__}",
            )
        )

    def _run_ui_snapshot_stage(
        self,
        stage: str,
        producer: Callable[[], _SnapshotT],
        consumer: Callable[[_SnapshotT], None],
    ) -> None:
        started_at = time.time()
        with self._ui_snapshot_lock:
            self._ui_snapshot_state.update(
                {
                    "stage": stage,
                    "stage_started_at": started_at,
                    "last_error_type": None,
                }
            )
        error_type: str | None = None
        try:
            consumer(producer())
        except Exception as exc:
            error_type = type(exc).__name__
            self.events.put(
                (
                    self.selected_key,
                    "log",
                    f"UI status probe failed at {stage}: {error_type}",
                )
            )
        finally:
            completed_at = time.time()
            with self._ui_snapshot_lock:
                self._ui_snapshot_state.update(
                    {
                        "stage": "idle",
                        "stage_started_at": None,
                        "last_completed_at": completed_at,
                        "last_duration_seconds": max(0.0, completed_at - started_at),
                        "last_error_type": error_type,
                    }
                )

    def _heartbeat(self) -> None:
        try:
            self._run_ui_snapshot_stage(
                "system_snapshot",
                self._system_snapshot,
                self.system_var.set,
            )
            self._run_ui_snapshot_stage(
                "learning_snapshot",
                self._learning_snapshot,
                self.learning_var.set,
            )
            self._run_ui_snapshot_stage(
                "candle_snapshot",
                self._candle_snapshot,
                self.candles_var.set,
            )
            self._run_ui_snapshot_stage(
                "compute_pipeline",
                self._compute_pipeline_health,
                lambda value: setattr(self, "_last_compute_pipeline", dict(value)),
            )
        finally:
            self.after(int(HEARTBEAT_INTERVAL_SECONDS * 1000), self._heartbeat)

    def _stop_heartbeat_publisher(self) -> None:
        publisher = getattr(self, "_heartbeat_publisher", None)
        if publisher is not None and not publisher.stop():
            self.events.put(
                (
                    self.selected_key,
                    "log",
                    "heartbeat publisher did not stop within its bounded timeout",
                )
            )

    def _close(self) -> None:
        if self._closing:
            return
        shutdown_order = (
            "telegram_bot",
            "paper_cards",
            "farm",
            "scanner",
            "public_news",
            "ollama",
            "dashboard",
            "graphs",
        )
        running = [
            self.contours[key]
            for key in shutdown_order
            if key in self.contours and self.contours[key].running
        ]
        external = self._external_profile_contours()
        if (running or external) and not messagebox.askyesno(
            "Закрыть центр",
            "Есть работающие контуры. Остановить их, включая подтверждённые внешние процессы, и закрыть окно?",
        ):
            return
        if not running and not external:
            self._stop_runtime_monitor()
            self._stop_heartbeat_publisher()
            self.instance.close()
            self.destroy()
            return
        self._set_shutdown_state("stopping", reason_code="operator_close")
        self._closing = True
        self._stop_runtime_monitor()
        self.withdraw()

        def stop_all() -> None:
            stop_results = [item.stop() for item in running]
            stopped = all(stop_results)
            for key, descriptor in external:
                self._stop_external(key, descriptor)
            external_stopped = all(
                not _same_live_process(
                    int(descriptor.get("pid") or 0),
                    descriptor.get("started_at"),
                )
                for _key, descriptor in external
            )
            self.events.put(
                (
                    "__app__",
                    "close" if stopped and external_stopped else "stop_failed",
                    "",
                )
            )

        threading.Thread(target=stop_all, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--start",
        action="append",
        choices=tuple(spec.key for spec in contour_specs()),
        default=[],
        help="explicitly start one allowlisted contour after opening the UI",
    )
    args = parser.parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        instance = SingleInstance(STATE_DIR / "control-center.lock")
    except RuntimeError as exc:
        messagebox.showerror("Исследовательский центр", str(exc))
        return 2
    app = ControlCenter(instance, tuple(args.start))
    try:
        app.mainloop()
    finally:
        app._stop_heartbeat_publisher()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
