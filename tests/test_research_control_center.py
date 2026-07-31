from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "research_control_center.py"
SPEC = importlib.util.spec_from_file_location("research_control_center", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_canonical_bat_launches_control_center_as_repo_module():
    text = (ROOT / "bat" / "research_control_center.bat").read_text(encoding="utf-8")

    assert "python -X utf8 -m scripts.research_control_center %*" in text
    assert "python -X utf8 scripts\\research_control_center.py %*" not in text


def test_canonical_bat_help_resolves_repo_packages_without_dotenv():
    if os.name != "nt":
        return
    env = os.environ.copy()
    env["TRADING_BOT_DOTENV_AUTOLOAD"] = "disabled"
    completed = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "call",
            str(ROOT / "bat" / "research_control_center.bat"),
            "--help",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "ModuleNotFoundError" not in completed.stdout + completed.stderr
    assert "--start" in completed.stdout


def test_format_age_uses_human_units():
    assert MODULE.format_age(7) == "7 с"
    assert MODULE.format_age(125) == "2 мин 5 с"
    assert MODULE.format_age(3_660) == "1 ч 1 мин"
    assert MODULE.format_age(90_000) == "1 д 1 ч"


def test_control_center_has_only_allowlisted_paper_research_contours():
    specs = MODULE.contour_specs()
    assert {item.key for item in specs} == {
        "ollama",
        "public_news",
        "scanner",
        "farm",
        "paper_cards",
        "telegram_bot",
        "dashboard",
        "graphs",
    }
    command_text = " ".join(part for item in specs for part in item.command).lower()
    assert "main.py" not in command_text
    assert "auto_trade" not in command_text
    assert "order" not in command_text
    assert all(item.key.isidentifier() for item in specs)


def test_scanner_delivery_is_forced_off_and_telegram_surfaces_are_marked():
    specs = {item.key: item for item in MODULE.contour_specs()}
    assert specs["scanner"].env["SCANNER_SEND_TELEGRAM"] == "0"
    assert specs["scanner"].telegram is False
    assert specs["public_news"].telegram is True
    assert specs["paper_cards"].telegram is True
    assert specs["telegram_bot"].telegram is True
    assert specs["scanner"].graceful_stop is MODULE.REQUEST_SCANNER_STOP
    assert specs["public_news"].graceful_stop is MODULE.REQUEST_PUBLIC_NEWS_STOP
    assert specs["telegram_bot"].env["AUTO_TRADE"] == "0"
    assert specs["telegram_bot"].env["TELEGRAM_BOT_ALLOW_AUTO_EXECUTE"] == "0"
    assert specs["telegram_bot"].command[0] == sys.executable
    assert specs["telegram_bot"].command[-1] == "scripts/telegram_bot.py"
    assert specs["dashboard"].command[0] == sys.executable


def test_ollama_is_local_and_gpu_environment_is_explicit():
    ollama = {item.key: item for item in MODULE.contour_specs()}["ollama"]
    assert ollama.env["OLLAMA_HOST"] == "127.0.0.1:11434"
    assert ollama.env["OLLAMA_LLM_LIBRARY"] == "cpu"
    assert ollama.env["CUDA_VISIBLE_DEVICES"] == "-1"
    assert ollama.env["GGML_VK_VISIBLE_DEVICES"] == "-1"
    assert ollama.env["OLLAMA_NUM_PARALLEL"] == "1"
    assert MODULE.GPU_MASK_ENV_NAMES == ("CUDA_VISIBLE_DEVICES", "GGML_VK_VISIBLE_DEVICES")


def test_canonical_code_root_and_private_runtime_state_are_separate():
    assert MODULE.RUNTIME_ROOT == Path.home() / "trading-bot-v2"
    assert MODULE.PRIVATE_ROOT != ROOT
    assert MODULE.STATE_DIR.is_relative_to(MODULE.PRIVATE_ROOT)


def test_only_known_local_ports_are_probed():
    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center.external_contours = {}
    center._port_open = lambda _port: False
    assert center._external_running("unknown") is False


def test_previous_heartbeat_recovers_only_same_live_process(tmp_path):
    started_at = MODULE._process_started_at(os.getpid())
    assert started_at is not None
    heartbeat = tmp_path / "heartbeat.json"
    heartbeat.write_text(json.dumps({
        "contours": {
            "telegram_bot": {"pid": os.getpid(), "started_at": started_at},
            "public_news": {"pid": os.getpid(), "started_at": started_at - 60},
        }
    }), encoding="utf-8")

    recovered = MODULE._load_external_contours(heartbeat)

    if os.name == "nt":
        assert recovered["telegram_bot"]["pid"] == os.getpid()
        assert "public_news" not in recovered
    else:
        # The control center is a Windows operator surface.  On other systems
        # process liveness is available, but creation-time identity is not, so
        # heartbeat recovery must fail closed instead of trusting a reused PID.
        assert recovered == {}


def test_port_owned_external_service_exposes_pid(monkeypatch):
    monkeypatch.setattr(MODULE, "_listening_pid", lambda port: 4242 if port == 11434 else None)
    monkeypatch.setattr(MODULE, "_process_started_at", lambda pid: 123.0 if pid == 4242 else None)
    monkeypatch.setattr(
        MODULE,
        "_process_executable",
        lambda pid: Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
        if pid == 4242 else None,
    )

    class FakeCenter:
        external_contours = {}

        @staticmethod
        def _port_open(port):
            return port == 11434

    external = MODULE.ControlCenter._external_descriptor(FakeCenter(), "ollama")

    assert external["pid"] == 4242
    assert external["started_at"] == 123.0
    assert external["source"] == "port"
    assert external["stoppable"] is False
    assert external["authority"] == "display_only"
    assert MODULE.ControlCenter._external_descriptor(FakeCenter(), "dashboard") is None


def test_same_live_process_rejects_missing_or_reused_pid(monkeypatch):
    monkeypatch.setattr(MODULE, "_process_started_at", lambda pid: 200.0 if pid == 42 else None)

    assert MODULE._same_live_process(42, 200.0) is True
    assert MODULE._same_live_process(42, 190.0) is False
    assert MODULE._same_live_process(99, 200.0) is False


def test_farm_and_paper_cards_share_graceful_stop_owner():
    specs = {item.key: item for item in MODULE.contour_specs()}
    assert specs["farm"].graceful_stop is not None
    assert specs["paper_cards"].graceful_stop is specs["farm"].graceful_stop
    assert specs["farm"].owner_group == "canonical_farm"
    assert specs["paper_cards"].owner_group == "canonical_farm"
    assert specs["farm"].graceful_seconds == 120.0
    assert specs["paper_cards"].graceful_seconds == 120.0
    assert specs["scanner"].graceful_seconds == 300.0
    assert specs["public_news"].graceful_seconds == 300.0


def test_authorized_multi_start_rejects_owner_group_before_any_start():
    starts: list[str] = []

    class Item:
        def __init__(self, spec):
            self.spec = spec
            self.running = False

        def start(self):
            starts.append(self.spec.key)

    class Status:
        def set(self, _value):
            return None

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center.contours = {spec.key: Item(spec) for spec in MODULE.contour_specs()}
    center.external_contours = {}
    center.status_vars = {key: Status() for key in center.contours}
    MODULE.ControlCenter._start_authorized(center, ("farm", "paper_cards"))
    assert starts == []


def test_forged_recovered_process_cannot_reach_stop_hooks(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(MODULE.subprocess, "run", lambda *_a, **_k: calls.append("taskkill"))

    class Events:
        def put(self, _event):
            return None

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center.events = Events()
    MODULE.ControlCenter._stop_external(
        center,
        "farm",
        {"pid": os.getpid(), "started_at": MODULE._process_started_at(os.getpid()),
         "stoppable": False, "authority": "display_only"},
    )
    assert calls == []


def test_exact_external_ollama_uses_wm_close_without_taskkill(monkeypatch):
    events: list[tuple[str, str, str]] = []
    live_samples = iter((True, True, False, False))
    close_calls: list[int] = []
    taskkill_calls: list[object] = []

    class Events:
        def put(self, event):
            events.append(event)

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center.events = Events()
    center.external_contours = {"ollama": {"pid": 4242}}
    center.contours = {
        "ollama": MODULE.ManagedContour(
            next(spec for spec in MODULE.contour_specs() if spec.key == "ollama"),
            MODULE.queue.Queue(),
        )
    }
    monkeypatch.setattr(
        MODULE,
        "_same_live_process",
        lambda *_args: next(live_samples),
    )
    monkeypatch.setattr(
        MODULE,
        "_request_windows_close",
        lambda pid: close_calls.append(pid) or 1,
    )
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda *_args, **_kwargs: taskkill_calls.append(object()),
    )

    MODULE.ControlCenter._stop_external(
        center,
        "ollama",
        {
            "pid": 4242,
            "started_at": 100.0,
            "stoppable": True,
            "authority": "owned_child",
        },
    )

    assert close_calls == [4242]
    assert taskkill_calls == []
    assert "ollama" not in center.external_contours
    assert any("4242" in event[2] for event in events)


def test_research_profile_methods_are_explicit_ui_actions():
    assert callable(MODULE.ControlCenter._start_research_profile)
    assert callable(MODULE.ControlCenter._stop_research_profile)
    assert callable(MODULE.ControlCenter._health_text)
    assert MODULE.ControlCenter._file_age(Path("missing-file")) is None
    assert hasattr(MODULE.ManagedContour, "stop")
    assert callable(MODULE.ControlCenter._enqueue_manual_urgent)
    assert callable(MODULE.ControlCenter._system_snapshot)
    assert callable(MODULE.ControlCenter._compute_pipeline_health)
    assert callable(MODULE.ControlCenter._queue_snapshot)
    assert callable(MODULE.ControlCenter._backend_snapshot)
    assert callable(MODULE.ControlCenter._learning_snapshot)


def test_compute_health_exposes_fatal_priority_worker_without_private_identity(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(MODULE, "PRIVATE_ROOT", tmp_path)
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "farm_priority_worker_status.json").write_text(
        json.dumps(
            {
                "stage": "worker_failed",
                "updated_at": MODULE.time.time(),
                "details": {"owner_id": "must-not-propagate"},
            }
        ),
        encoding="utf-8",
    )
    (state / "worker_status.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "reason_code": "expired_alive_conflict",
                "updated_at": "2026-07-24T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    class Farm:
        running = True

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center._json_status_cache = MODULE._JsonStatusCache()
    center.contours = {"farm": Farm()}
    center.external_contours = {}

    health = center._compute_pipeline_health()

    assert health["state"] == "failed"
    assert health["hard_fail"] is True
    assert "owner_id" not in json.dumps(health)
    assert health["execution_allowed"] is False


def test_learning_snapshot_explains_closed_loop_in_plain_language(monkeypatch, tmp_path):
    monkeypatch.setattr(MODULE, "PRIVATE_ROOT", tmp_path)
    work = tmp_path / "state" / "role_work_queue" / "farm"
    work.mkdir(parents=True)
    (work / "env_1.json").write_text(json.dumps({
        "status": "queued", "task_spec": {"generation": 1}
    }), encoding="utf-8")
    inbox = tmp_path / "state" / "derived" / "system_analyst_result_inbox.jsonl"
    inbox.parent.mkdir(parents=True)
    inbox.write_text(json.dumps({"result_id": "result-1"}) + "\n", encoding="utf-8")

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    text = center._learning_snapshot()

    assert "Alibaba" in text
    assert "ферма: заданий" in text
    assert "вернулось аналитику 1" in text
    assert "ждут разбора 1" in text
    assert "поколение 1/2" in text


def test_optional_sqlite_sidecar_size_tolerates_disappearance(monkeypatch, tmp_path):
    sidecar = tmp_path / "candles.sqlite3-shm"
    sidecar.write_bytes(b"transient")
    original_stat = Path.stat

    def disappearing_stat(path, *args, **kwargs):
        if path == sidecar:
            raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", disappearing_stat)

    assert MODULE.ControlCenter._optional_file_size(sidecar) == 0


def test_scanner_delivery_environment_gate(monkeypatch):
    from src.scout.delivery_policy import scanner_telegram_enabled

    monkeypatch.delenv("SCANNER_SEND_TELEGRAM", raising=False)
    assert scanner_telegram_enabled() is True
    monkeypatch.setenv("SCANNER_SEND_TELEGRAM", "0")
    assert scanner_telegram_enabled() is False
    monkeypatch.setenv("SCANNER_SEND_TELEGRAM", "1")
    assert scanner_telegram_enabled() is True


def test_status_cache_bounds_duplicate_rcc_reads() -> None:
    now = [100.0]
    reads: list[Path] = []
    path = Path("synthetic") / "farm_loop_status.json"

    def reader(target: Path) -> dict:
        reads.append(target)
        return {"sample": len(reads)}

    cache = MODULE._JsonStatusCache(clock=lambda: now[0])

    assert cache.read(path, reader)["sample"] == 1
    assert cache.read(path, reader)["sample"] == 1
    now[0] += MODULE.STATUS_CACHE_SECONDS
    assert cache.read(path, reader)["sample"] == 2
    assert reads == [path, path]


def test_required_contour_exit_is_latched_once_without_restart(
    monkeypatch,
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private"
    state_dir = private_root / "state" / "control-center"
    farm_status = private_root / "state" / "farm_loop_status.json"
    farm_status.parent.mkdir(parents=True)
    farm_status.write_text(
        json.dumps({"stage": "paper_signals", "updated_at": MODULE.time.time()}),
        encoding="utf-8",
    )
    monkeypatch.setattr(MODULE, "PRIVATE_ROOT", private_root)
    monkeypatch.setattr(MODULE, "STATE_DIR", state_dir)
    monkeypatch.setattr(
        MODULE,
        "CANONICAL_STOP_INTENTS",
        (private_root / "state" / "not-requested.stop",),
    )

    class Process:
        pid = 4242

        @staticmethod
        def poll():
            return 7

    class Status:
        value = ""

        def set(self, value):
            self.value = value

    item = MODULE.ManagedContour(
        next(spec for spec in MODULE.contour_specs() if spec.key == "paper_cards"),
        MODULE.queue.Queue(),
    )
    item.process = Process()
    item.started_at = 123.0
    item.expected_running = True

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center._json_status_cache = MODULE._JsonStatusCache()
    center._logs = {"paper_cards": []}
    center.status_vars = {"paper_cards": Status()}
    center.selected_key = "ollama"
    center._render_log = lambda: None

    assert center._record_required_contour_exit("paper_cards", item) is True
    assert center._record_required_contour_exit("paper_cards", item) is False

    alerts = [
        json.loads(line)
        for line in (state_dir / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(alerts) == 1
    assert alerts[0]["reason"] == "required_contour_unexpected_exit"
    assert alerts[0]["last_stage"] == "paper_signals"
    assert alerts[0]["automatic_restart"] is False
    assert alerts[0]["execution_allowed"] is False
    assert "automatic restart is disabled" in center.status_vars["paper_cards"].value


def test_intentional_stop_disarms_required_contour_exit() -> None:
    class Process:
        pid = 4242

        @staticmethod
        def poll():
            return 0

    item = MODULE.ManagedContour(
        next(spec for spec in MODULE.contour_specs() if spec.key == "paper_cards"),
        MODULE.queue.Queue(),
    )
    item.process = Process()
    item.started_at = 123.0
    item.expected_running = True

    item.stop()

    assert item.expected_running is False
    assert item.consume_unexpected_exit() is None


def test_owned_contour_start_preserves_ctrl_break_process_group(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class Process:
        pid = 4242
        stdout = [""]

        @staticmethod
        def poll():
            return 0

        @staticmethod
        def wait():
            return 0

    def popen(*_args, **kwargs):
        captured.update(kwargs)
        return Process()

    monkeypatch.setattr(MODULE.subprocess, "Popen", popen)
    monkeypatch.setattr(MODULE, "_process_started_at", lambda _pid: 100.0)
    item = MODULE.ManagedContour(
        next(spec for spec in MODULE.contour_specs() if spec.key == "telegram_bot"),
        MODULE.queue.Queue(),
    )

    item.start()

    assert captured["creationflags"] == MODULE._CREATE_NEW_PROCESS_GROUP
    assert not (
        int(captured["creationflags"]) & int(MODULE._CREATE_NO_WINDOW)
    )
    assert captured["env"]["AUTO_TRADE"] == "0"
    assert captured["env"]["TELEGRAM_BOT_ALLOW_AUTO_EXECUTE"] == "0"


def test_owned_contour_stop_fails_closed_without_force_termination(
    monkeypatch,
) -> None:
    events: list[tuple[str, str, str]] = []

    class Events:
        def put(self, event):
            events.append(event)

    class Process:
        pid = 4242
        terminated = False

        @staticmethod
        def poll():
            return None

        @staticmethod
        def send_signal(_signal):
            raise OSError("synthetic ctrl break failure")

        def terminate(self):
            self.terminated = True

    process = Process()
    item = MODULE.ManagedContour(
        next(spec for spec in MODULE.contour_specs() if spec.key == "telegram_bot"),
        Events(),
    )
    item.process = process
    item.expected_running = True
    taskkill_calls: list[object] = []
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda *_args, **_kwargs: taskkill_calls.append(object()),
    )

    assert item.stop(timeout=0.0) is False
    assert process.terminated is False
    assert taskkill_calls == []
    assert any("graceful stop signal failed" in event[2] for event in events)


def test_owned_contour_stop_deadline_surfaces_residual_without_taskkill(
    monkeypatch,
) -> None:
    events: list[tuple[str, str, str]] = []

    class Events:
        def put(self, event):
            events.append(event)

    class Process:
        pid = 4242

        @staticmethod
        def poll():
            return None

        @staticmethod
        def send_signal(_signal):
            return None

    item = MODULE.ManagedContour(
        next(spec for spec in MODULE.contour_specs() if spec.key == "telegram_bot"),
        Events(),
    )
    item.process = Process()
    item.expected_running = True
    taskkill_calls: list[object] = []
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda *_args, **_kwargs: taskkill_calls.append(object()),
    )

    assert item.stop(timeout=0.0) is False
    assert taskkill_calls == []
    assert any("deadline exhausted" in event[2] for event in events)


def test_farm_stop_marker_falls_back_to_owned_process_group_ctrl_break(
    monkeypatch,
) -> None:
    events: list[tuple[str, str, str]] = []
    stop_requests: list[str] = []

    class Events:
        def put(self, event):
            events.append(event)

    class Process:
        pid = 4242

        def __init__(self):
            self.running = True
            self.signals: list[int] = []

        def poll(self):
            return None if self.running else 0

        def send_signal(self, signal):
            self.signals.append(signal)
            self.running = False

    spec = MODULE.ContourSpec(
        key="paper_cards",
        title="paper cards",
        description="synthetic",
        command=("synthetic",),
        graceful_stop=lambda: stop_requests.append("marker"),
        owner_group="canonical_farm",
        graceful_seconds=0.0,
        signal_fallback_seconds=0.1,
    )
    process = Process()
    item = MODULE.ManagedContour(spec, Events())
    item.process = process
    item.started_at = 123.0
    item.expected_running = True
    monkeypatch.setattr(
        MODULE,
        "_same_live_process",
        lambda pid, started_at: pid == 4242 and started_at == 123.0,
    )

    assert item.stop() is True
    assert stop_requests == ["marker"]
    assert process.signals == [MODULE._CTRL_BREAK_EVENT]
    assert any("exact owned process group" in event[2] for event in events)


def test_farm_stop_fallback_refuses_changed_process_identity(monkeypatch) -> None:
    class Process:
        pid = 4242

        @staticmethod
        def poll():
            return None

        @staticmethod
        def send_signal(_signal):
            raise AssertionError("changed PID identity must not receive a signal")

    spec = MODULE.ContourSpec(
        key="paper_cards",
        title="paper cards",
        description="synthetic",
        command=("synthetic",),
        graceful_stop=lambda: None,
        graceful_seconds=0.0,
        signal_fallback_seconds=0.0,
    )
    item = MODULE.ManagedContour(spec, MODULE.queue.Queue())
    item.process = Process()
    item.started_at = 123.0
    monkeypatch.setattr(MODULE, "_same_live_process", lambda *_args: False)

    assert item.stop() is False


def test_profile_stop_is_dependency_ordered_not_parallel(monkeypatch) -> None:
    stopped: list[str] = []
    order = (
        "telegram_bot",
        "paper_cards",
        "farm",
        "scanner",
        "public_news",
        "ollama",
    )

    class Item:
        def __init__(self, key: str):
            self.spec = SimpleNamespace(key=key)
            self.running = True

        def stop(self):
            stopped.append(self.spec.key)
            return True

    class InlineThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            self.target()

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center.contours = {key: Item(key) for key in order}
    center._external_profile_contours = lambda: []
    monkeypatch.setattr(MODULE.messagebox, "askyesno", lambda *_args: True)
    monkeypatch.setattr(MODULE.threading, "Thread", InlineThread)

    center._stop_research_profile()

    assert stopped == list(order)


def test_cancelled_profile_stop_keeps_runtime_monitor_active(monkeypatch) -> None:
    class Item:
        running = True

    class Monitor:
        stopped = False

        def stop(self, *, timeout):
            self.stopped = True
            return ()

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center.contours = {
        key: Item()
        for key in (
            "telegram_bot",
            "paper_cards",
            "farm",
            "scanner",
            "public_news",
            "ollama",
        )
    }
    monitor = Monitor()
    center._runtime_monitor = monitor
    center._external_profile_contours = lambda: []
    monkeypatch.setattr(MODULE.messagebox, "askyesno", lambda *_args: False)

    center._stop_research_profile()

    assert monitor.stopped is False
    assert center._runtime_monitor is monitor


def test_hard_fail_stop_is_dependency_ordered_idempotent_and_prompt_free(
    monkeypatch,
    tmp_path: Path,
) -> None:
    stopped: list[str] = []
    events: list[tuple[str, str, str]] = []
    order = (
        "telegram_bot",
        "paper_cards",
        "farm",
        "scanner",
        "public_news",
        "ollama",
    )

    class Item:
        def __init__(self, key: str):
            self.spec = SimpleNamespace(key=key)
            self.running = True

        def stop(self):
            stopped.append(self.spec.key)
            return True

    class Events:
        def put(self, event):
            events.append(event)

    class InlineThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            self.target()

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center.contours = {key: Item(key) for key in order}
    center.events = Events()
    center.selected_key = "paper_cards"
    center._hard_fail_stop_started = False
    center._closing = False
    monkeypatch.setattr(MODULE, "STATE_DIR", tmp_path)
    monkeypatch.setattr(MODULE.threading, "Thread", InlineThread)
    monkeypatch.setattr(
        MODULE.messagebox,
        "askyesno",
        lambda *_args: pytest.fail("hard fail must not wait for a UI prompt"),
    )

    assert center._initiate_hard_fail_stop("synthetic_authority_failure") is True
    assert center._initiate_hard_fail_stop("duplicate") is False

    assert stopped == list(order)
    assert ("__app__", "close", "") in events
    assert center._closing is True
    assert sum("HARD FAIL" in event[2] for event in events) == 1
    alerts = [
        json.loads(line)
        for line in (tmp_path / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(alerts) == 1
    assert alerts[0]["reason"] == "synthetic_authority_failure"
    assert alerts[0]["automatic_restart"] is False


def test_hard_fail_stop_failure_keeps_rcc_open_and_reports_residual(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[tuple[str, str, str]] = []

    class Item:
        running = True

        def __init__(self, key: str, stopped: bool) -> None:
            self.spec = SimpleNamespace(key=key)
            self.stopped = stopped

        def stop(self) -> bool:
            return self.stopped

    class Events:
        def put(self, event: tuple[str, str, str]) -> None:
            events.append(event)

    class InlineThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self) -> None:
            self.target()

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center.contours = {
        key: Item(key, key != "paper_cards")
        for key in (
            "telegram_bot",
            "paper_cards",
            "farm",
            "scanner",
            "public_news",
            "ollama",
        )
    }
    center.events = Events()
    center.selected_key = "paper_cards"
    center._hard_fail_stop_started = False
    center._closing = False
    center._stop_runtime_monitor = lambda: None
    monkeypatch.setattr(MODULE, "STATE_DIR", tmp_path)
    monkeypatch.setattr(MODULE.threading, "Thread", InlineThread)

    assert center._initiate_hard_fail_stop("synthetic_failure") is True

    assert center._closing is True
    assert ("__app__", "close", "") not in events
    assert (
        "__app__",
        "stop_failed",
        "hard-fail graceful stop left an owned contour running",
    ) in events


def test_hard_fail_stop_exception_is_reported_without_losing_close_control(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[tuple[str, str, str]] = []

    class Item:
        running = True

        def stop(self) -> bool:
            raise OSError("synthetic stop failure")

    class Events:
        def put(self, event: tuple[str, str, str]) -> None:
            events.append(event)

    class InlineThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self) -> None:
            self.target()

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center.contours = {
        key: Item()
        for key in (
            "telegram_bot",
            "paper_cards",
            "farm",
            "scanner",
            "public_news",
            "ollama",
        )
    }
    center.events = Events()
    center.selected_key = "paper_cards"
    center._hard_fail_stop_started = False
    center._closing = False
    center._stop_runtime_monitor = lambda: None
    monkeypatch.setattr(MODULE, "STATE_DIR", tmp_path)
    monkeypatch.setattr(MODULE.threading, "Thread", InlineThread)

    assert center._initiate_hard_fail_stop("synthetic_failure") is True

    assert ("__app__", "close", "") not in events
    assert (
        "__app__",
        "stop_failed",
        "hard-fail graceful stop left an owned contour running",
    ) in events


def test_hard_fail_close_event_stops_publishers_and_releases_rcc_instance() -> None:
    actions: list[str] = []

    class Events:
        def __init__(self) -> None:
            self.items = [("__app__", "close", "")]

        def get_nowait(self) -> tuple[str, str, str]:
            if self.items:
                return self.items.pop(0)
            raise MODULE.queue.Empty

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center.events = Events()
    center._stop_heartbeat_publisher = lambda: actions.append("heartbeat_stopped")
    center.instance = SimpleNamespace(close=lambda: actions.append("instance_closed"))
    center.destroy = lambda: actions.append("ui_destroyed")

    center._poll()

    assert actions == ["heartbeat_stopped", "instance_closed", "ui_destroyed"]


def test_minimal_heartbeat_continues_while_ui_status_probe_is_blocked(
    tmp_path: Path,
) -> None:
    class Process:
        pid = 4242

        @staticmethod
        def poll():
            return None

    class Item:
        process = Process()
        started_at = 123.0
        unexpected_exit_reported = False

    class Events:
        def put(self, _event):
            return None

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center._process_identity = SimpleNamespace(
        pid=os.getpid(),
        started_at=1_700_000_000.0,
        executable="python.exe",
        command_digest="sha256:rcc",
    )
    center.contours = {"paper_cards": Item()}
    center._ui_snapshot_lock = threading.Lock()
    center._ui_snapshot_state = {
        "stage": "not_started",
        "stage_started_at": None,
        "last_completed_at": None,
        "last_duration_seconds": None,
        "last_error_type": None,
    }
    center._last_compute_pipeline = {
        "state": "unknown",
        "reason": "synthetic",
        "hard_fail": False,
        "execution_allowed": False,
    }
    center.events = Events()
    center.selected_key = "paper_cards"

    target = tmp_path / "heartbeat.json"
    publish_count = 0
    published_thrice = threading.Event()

    def snapshot():
        nonlocal publish_count
        publish_count += 1
        if publish_count >= 3:
            published_thrice.set()
        return center._heartbeat_payload()

    publisher = MODULE._HeartbeatPublisher(
        target,
        snapshot,
        interval_seconds=0.02,
    )
    blocked = threading.Event()
    release = threading.Event()

    def blocked_probe():
        blocked.set()
        assert release.wait(2.0)
        return "done"

    ui_thread = threading.Thread(
        target=center._run_ui_snapshot_stage,
        args=("synthetic_blocking_probe", blocked_probe, lambda _value: None),
    )
    publisher.start()
    ui_thread.start()
    assert blocked.wait(1.0)
    deadline = time.monotonic() + 1.0
    while not target.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert target.exists()
    assert published_thrice.wait(1.0)
    payload = json.loads(target.read_text(encoding="utf-8"))

    assert publish_count >= 3
    assert payload["schema"] == "ResearchControlCenterHeartbeat.v3"
    assert payload["pid"] == os.getpid()
    assert payload["started_at"] == 1_700_000_000.0
    assert payload["paper_only"] is True
    assert payload["execution_allowed"] is False
    assert payload["contours"]["paper_cards"]["running"] is True
    assert payload["ui_snapshot"]["stage"] == "synthetic_blocking_probe"
    assert payload["ui_snapshot"]["stage_age_seconds"] > 0

    release.set()
    ui_thread.join(timeout=1.0)
    assert publisher.stop(timeout=1.0) is True
    assert publisher.thread_alive is False


def test_heartbeat_publisher_stops_without_leaving_background_thread(
    tmp_path: Path,
) -> None:
    publisher = MODULE._HeartbeatPublisher(
        tmp_path / "heartbeat.json",
        lambda: {"schema": "synthetic"},
        interval_seconds=0.01,
    )

    publisher.start()
    assert publisher.thread_alive is True
    assert publisher.stop(timeout=1.0) is True
    assert publisher.thread_alive is False


def test_ui_probe_failure_is_sanitized_and_next_refresh_remains_scheduled() -> None:
    events: list[tuple[str, str, str]] = []
    scheduled: list[tuple[int, object]] = []

    class Events:
        def put(self, event):
            events.append(event)

    class Variable:
        def set(self, _value):
            return None

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center._ui_snapshot_lock = threading.Lock()
    center._ui_snapshot_state = {
        "stage": "not_started",
        "stage_started_at": None,
        "last_completed_at": None,
        "last_duration_seconds": None,
        "last_error_type": None,
    }
    center._last_compute_pipeline = {}
    center.selected_key = "paper_cards"
    center.events = Events()
    center.system_var = Variable()
    center.learning_var = Variable()
    center.candles_var = Variable()
    center._system_snapshot = lambda: (_ for _ in ()).throw(
        RuntimeError("synthetic-private-value-must-not-leak")
    )
    center._learning_snapshot = lambda: "learning"
    center._candle_snapshot = lambda: "candles"
    center._compute_pipeline_health = lambda: {
        "state": "healthy",
        "execution_allowed": False,
    }
    center.after = lambda delay, callback: scheduled.append((delay, callback))

    center._heartbeat()

    assert events == [
        (
            "paper_cards",
            "log",
            "UI status probe failed at system_snapshot: RuntimeError",
        )
    ]
    assert "synthetic-private-value" not in json.dumps(events)
    assert center._last_compute_pipeline["state"] == "healthy"
    assert scheduled == [
        (int(MODULE.HEARTBEAT_INTERVAL_SECONDS * 1000), center._heartbeat)
    ]


def test_canonical_stop_intent_disarms_external_fail_closed_exit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state" / "control-center"
    state = tmp_path / "state"
    state.mkdir(parents=True)
    stop_intents = tuple(
        state / name
        for name in (
            "STOP_FARM_FULL_CYCLE.txt",
            "STOP_NEWS_SCANNER.txt",
            "STOP_PUBLIC_NEWS.txt",
        )
    )
    for stop_intent in stop_intents:
        stop_intent.write_text("synthetic coordinated stop\n", encoding="utf-8")
    monkeypatch.setattr(MODULE, "STATE_DIR", state_dir)
    monkeypatch.setattr(MODULE, "CANONICAL_STOP_INTENTS", stop_intents)

    class Process:
        pid = 4242

        @staticmethod
        def poll():
            return 1

    class Status:
        value = ""

        def set(self, value):
            self.value = value

    item = MODULE.ManagedContour(
        next(spec for spec in MODULE.contour_specs() if spec.key == "ollama"),
        MODULE.queue.Queue(),
    )
    item.process = Process()
    item.started_at = min(path.stat().st_mtime for path in stop_intents) - 1.0
    item.expected_running = True

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center._logs = {"ollama": []}
    center.status_vars = {"ollama": Status()}
    center.selected_key = "ollama"
    center._render_log = lambda: None

    assert center._record_required_contour_exit("ollama", item) is False
    assert item.expected_running is False
    assert not (state_dir / "alerts.jsonl").exists()
    assert "canonical stop intent observed" in center.status_vars["ollama"].value
    assert "automatic restart is disabled" in center.status_vars["ollama"].value


def test_stale_stop_intent_does_not_hide_real_required_contour_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state" / "control-center"
    stop_intent = tmp_path / "state" / "STOP_FARM_FULL_CYCLE.txt"
    stop_intent.parent.mkdir(parents=True)
    stop_intent.write_text("stale synthetic stop\n", encoding="utf-8")
    monkeypatch.setattr(MODULE, "PRIVATE_ROOT", tmp_path)
    monkeypatch.setattr(MODULE, "STATE_DIR", state_dir)
    monkeypatch.setattr(MODULE, "CANONICAL_STOP_INTENTS", (stop_intent,))

    class Process:
        pid = 4242

        @staticmethod
        def poll():
            return 9

    class Status:
        value = ""

        def set(self, value):
            self.value = value

    item = MODULE.ManagedContour(
        next(spec for spec in MODULE.contour_specs() if spec.key == "paper_cards"),
        MODULE.queue.Queue(),
    )
    item.process = Process()
    item.started_at = stop_intent.stat().st_mtime + 60.0
    item.expected_running = True

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center._json_status_cache = MODULE._JsonStatusCache()
    center._logs = {"paper_cards": []}
    center.status_vars = {"paper_cards": Status()}
    center.selected_key = "ollama"
    center._render_log = lambda: None

    assert center._record_required_contour_exit("paper_cards", item) is True
    alert = json.loads(
        (state_dir / "alerts.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert alert["reason"] == "required_contour_unexpected_exit"
    assert alert["exit_code"] == 9


def test_single_contour_stop_marker_does_not_hide_unrelated_crash(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state" / "control-center"
    scanner_stop = tmp_path / "state" / "STOP_NEWS_SCANNER.txt"
    scanner_stop.parent.mkdir(parents=True)
    scanner_stop.write_text("synthetic scanner stop\n", encoding="utf-8")
    monkeypatch.setattr(MODULE, "STATE_DIR", state_dir)
    monkeypatch.setattr(MODULE, "CANONICAL_STOP_INTENTS", (scanner_stop,))

    class Process:
        pid = 4242

        @staticmethod
        def poll():
            return 5

    class Status:
        def set(self, _value):
            return None

    item = MODULE.ManagedContour(
        next(spec for spec in MODULE.contour_specs() if spec.key == "ollama"),
        MODULE.queue.Queue(),
    )
    item.process = Process()
    item.started_at = scanner_stop.stat().st_mtime - 1.0
    item.expected_running = True

    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    center._logs = {"ollama": []}
    center.status_vars = {"ollama": Status()}
    center.selected_key = "paper_cards"
    center._render_log = lambda: None

    assert center._record_required_contour_exit("ollama", item) is True
    alert = json.loads(
        (state_dir / "alerts.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert alert["exit_code"] == 5


def _runtime_probe_center() -> tuple[Any, list[tuple[str, str, str]]]:
    events: list[tuple[str, str, str]] = []
    center = MODULE.ControlCenter.__new__(MODULE.ControlCenter)
    owner_pid = 42_424
    owner_started_at = 1_700_000_042.0
    owner_fence = 38
    center.contours = {
        key: SimpleNamespace(
            process=SimpleNamespace(pid=10_000 + index),
            running=True,
            started_at=1_700_000_000.0 + index,
        )
        for index, key in enumerate(sorted(MODULE.CANONICAL_PAPER_PROFILE))
    }
    center.events = SimpleNamespace(put=events.append)
    center.selected_key = "paper_cards"
    center._closing = False
    center._runtime_monitor_started_at = 100.0
    center._runtime_ready = False
    center._runtime_owner_monitor = SimpleNamespace(
        sample=lambda: SimpleNamespace(
            state="ready",
            ready=True,
            canonical_fence=owner_fence,
            process_identity=SimpleNamespace(
                pid=owner_pid,
                started_at=owner_started_at,
            ),
            resources=("canonical_farm", "strategy_lab_worker"),
        )
    )
    center._read_cached_json = lambda _path: {
        "schema": "ProcessLeaseSupervisorStatus.v1",
        "state": "running",
        "updated_at": MODULE.time.time(),
        "owner_pid": owner_pid,
        "owner_started_at": owner_started_at,
        "fencing_token": owner_fence,
        "lease_expires_at": MODULE.time.time() + 90.0,
        "paper_only": True,
        "execution_allowed": False,
    }
    return center, events


def test_runtime_probe_treats_early_missing_listener_as_starting(
    monkeypatch,
    tmp_path: Path,
) -> None:
    center, events = _runtime_probe_center()
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: 103.76)
    monkeypatch.setattr(MODULE, "_same_live_process", lambda *_args: True)
    monkeypatch.setattr(MODULE, "_process_descends_from", lambda *_args: True)
    monkeypatch.setattr(MODULE, "_listening_pid", lambda _port: None)
    monkeypatch.setattr(
        MODULE,
        "CANONICAL_STOP_INTENTS",
        (tmp_path / "absent-stop-intent",),
    )

    sample = center._fast_runtime_safety_probe()

    assert sample["state"] == "listener_starting"
    assert sample["ready"] is False
    assert events == []


def test_runtime_probe_sets_t0_only_after_listener_and_owner_are_ready(
    monkeypatch,
    tmp_path: Path,
) -> None:
    center, events = _runtime_probe_center()
    ollama_pid = center.contours["ollama"].process.pid
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: 120.0)
    monkeypatch.setattr(MODULE, "_same_live_process", lambda *_args: True)
    monkeypatch.setattr(MODULE, "_process_descends_from", lambda *_args: True)
    monkeypatch.setattr(MODULE, "_listening_pid", lambda _port: ollama_pid)
    monkeypatch.setattr(
        MODULE,
        "CANONICAL_STOP_INTENTS",
        (tmp_path / "absent-stop-intent",),
    )

    sample = center._fast_runtime_safety_probe()

    assert sample["state"] == "ready"
    assert sample["ready"] is True
    assert center._runtime_ready is True
    assert len(events) == 1
    assert "T+0 READY" in events[0][2]


def test_runtime_probe_listener_loss_after_t0_is_immediate_hard_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    center, _events = _runtime_probe_center()
    center._runtime_ready = True
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: 125.0)
    monkeypatch.setattr(MODULE, "_same_live_process", lambda *_args: True)
    monkeypatch.setattr(MODULE, "_process_descends_from", lambda *_args: True)
    monkeypatch.setattr(MODULE, "_listening_pid", lambda _port: None)
    monkeypatch.setattr(
        MODULE,
        "CANONICAL_STOP_INTENTS",
        (tmp_path / "absent-stop-intent",),
    )

    with pytest.raises(
        MODULE.CanaryMonitorHardFailure,
        match="ollama_listener_unavailable",
    ):
        center._fast_runtime_safety_probe()


def test_runtime_probe_rejects_owner_outside_canonical_rcc_tree(
    monkeypatch,
    tmp_path: Path,
) -> None:
    center, _events = _runtime_probe_center()
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: 104.0)
    monkeypatch.setattr(MODULE, "_same_live_process", lambda *_args: True)
    monkeypatch.setattr(MODULE, "_process_descends_from", lambda *_args: False)
    monkeypatch.setattr(MODULE, "_listening_pid", lambda _port: None)
    monkeypatch.setattr(
        MODULE,
        "CANONICAL_STOP_INTENTS",
        (tmp_path / "absent-stop-intent",),
    )

    with pytest.raises(
        MODULE.CanaryMonitorHardFailure,
        match="owner_not_in_canonical_rcc_tree",
    ):
        center._fast_runtime_safety_probe()


def test_runtime_probe_keeps_supervisor_pending_before_startup_deadline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    center, events = _runtime_probe_center()
    ollama_pid = center.contours["ollama"].process.pid
    center._read_cached_json = lambda _path: {}
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: 120.0)
    monkeypatch.setattr(MODULE, "_same_live_process", lambda *_args: True)
    monkeypatch.setattr(MODULE, "_process_descends_from", lambda *_args: True)
    monkeypatch.setattr(MODULE, "_listening_pid", lambda _port: ollama_pid)
    monkeypatch.setattr(
        MODULE,
        "CANONICAL_STOP_INTENTS",
        (tmp_path / "absent-stop-intent",),
    )

    sample = center._fast_runtime_safety_probe()

    assert sample["state"] == "process_starting"
    assert sample["ready"] is False
    assert sample["lease_supervisor_state"] == "pending"
    assert events == []


@pytest.mark.parametrize(
    ("override", "expected"),
    (
        ({"state": "failed"}, "farm_process_lease_supervisor_failed"),
        ({"updated_at": 1.0}, "farm_process_lease_supervisor_stale"),
        ({"owner_pid": 99_999}, "farm_process_lease_supervisor_unavailable"),
        (
            {"owner_started_at": 1_700_000_043.0},
            "farm_process_lease_supervisor_unavailable",
        ),
        ({"fencing_token": 39}, "farm_process_lease_supervisor_unavailable"),
    ),
)
def test_runtime_probe_fails_closed_on_invalid_supervisor_after_t0(
    monkeypatch,
    tmp_path: Path,
    override: dict[str, object],
    expected: str,
) -> None:
    center, _events = _runtime_probe_center()
    center._runtime_ready = True
    original_read = center._read_cached_json
    center._read_cached_json = lambda path: {
        **original_read(path),
        **override,
    }
    ollama_pid = center.contours["ollama"].process.pid
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: 125.0)
    monkeypatch.setattr(MODULE, "_same_live_process", lambda *_args: True)
    monkeypatch.setattr(MODULE, "_process_descends_from", lambda *_args: True)
    monkeypatch.setattr(MODULE, "_listening_pid", lambda _port: ollama_pid)
    monkeypatch.setattr(
        MODULE,
        "CANONICAL_STOP_INTENTS",
        (tmp_path / "absent-stop-intent",),
    )

    with pytest.raises(MODULE.CanaryMonitorHardFailure, match=expected):
        center._fast_runtime_safety_probe()


def test_deep_runtime_probe_requires_every_canonical_database(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(MODULE, "PRIVATE_ROOT", tmp_path)

    with pytest.raises(
        MODULE.CanaryMonitorHardFailure,
        match="canonical_database_missing:ownership.sqlite",
    ):
        MODULE.ControlCenter._deep_runtime_safety_probe()

    paths = (
        tmp_path / "state" / "ownership.sqlite",
        tmp_path / "state" / "farm_tasks.sqlite",
        tmp_path / "state" / "strategy_lab.sqlite",
        tmp_path / "state" / "scanner_farm_loop.sqlite",
        tmp_path / "market_data" / "candles.sqlite3",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE synthetic_guard(id INTEGER)")

    sample = MODULE.ControlCenter._deep_runtime_safety_probe()

    assert sample["database_count"] == 5
    assert set(sample["database_sizes"]) == {path.name for path in paths}
