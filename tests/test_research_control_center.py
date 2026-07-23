from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "research_control_center.py"
SPEC = importlib.util.spec_from_file_location("research_control_center", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


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


def test_research_profile_methods_are_explicit_ui_actions():
    assert callable(MODULE.ControlCenter._start_research_profile)
    assert callable(MODULE.ControlCenter._stop_research_profile)
    assert callable(MODULE.ControlCenter._health_text)
    assert MODULE.ControlCenter._file_age(Path("missing-file")) is None
    assert hasattr(MODULE.ManagedContour, "stop")
    assert callable(MODULE.ControlCenter._enqueue_manual_urgent)
    assert callable(MODULE.ControlCenter._system_snapshot)
    assert callable(MODULE.ControlCenter._queue_snapshot)
    assert callable(MODULE.ControlCenter._backend_snapshot)
    assert callable(MODULE.ControlCenter._learning_snapshot)


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
