from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

from src.utils.runtime_root import DOTENV_AUTOLOAD_ENV, load_runtime_dotenv


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DOTENV_CALL_GRAPH = (
    "scripts/analyze_chart.py",
    "scripts/auto_execute.py",
    "scripts/build_journal.py",
    "scripts/public_channel_publisher.py",
    "scripts/strategy_lab/paper_telegram_transport.py",
    "scripts/telegram_bot.py",
    "src/scout/scanner_v0.py",
)
REPOSITORY_IMPORT_ISOLATION_MODULES = (
    "scripts.analysis.analysis_query",
    "scripts.analysis.label_outcomes",
    "scripts.analysis.label_signals",
    "scripts.analysis.tape_recorder",
    "scripts.archive.backtest_simulate",
    "scripts.archive.ws_pump_orchestrator",
    "scripts.backtest.backtest_entries",
    "scripts.dump_trades",
    "scripts.get_chat_id",
    "scripts.get_group_chat_id",
    "scripts.llm_provider_ab",
    "scripts.mcp_okx_server",
    "scripts.strategy_lab.agent_role_provider_bench",
    "scripts.strategy_lab.operational_health",
    "scripts.strategy_lab.paper_telegram_sender",
    "scripts.strategy_lab.vip_vision_provider_smoke",
    "scripts.telegram_delivery_smoke",
    "scripts.ws.ws_bb_fade",
    "scripts.ws.ws_impulse_pump",
    "scripts.ws.ws_main_impulse",
    "scripts.ws.ws_main_screener",
    "scripts.ws.ws_scanner",
    "scripts.ws.ws_smart_pump",
    "src.config",
    "src.scout.llm_health_report",
)


def _safe_subprocess_env(tmp_path: Path) -> dict[str, str]:
    allowed = ("PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "WINDIR")
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            DOTENV_AUTOLOAD_ENV: "0",
            "AUTO_TRADE": "0",
            "TELEGRAM_BOT_ALLOW_AUTO_EXECUTE": "0",
            "TRADING_BOT_RUNTIME_ROOT": str(tmp_path / "runtime"),
            "TRADING_BOT_RESEARCH_ROOT": str(tmp_path / "research"),
        }
    )
    return env


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


def test_dotenv_autoload_production_default_reads_isolated_runtime_file(
    monkeypatch, tmp_path,
) -> None:
    runtime_root = tmp_path / "runtime-default"
    runtime_root.mkdir()
    (runtime_root / ".env").write_text(
        "SYNTHETIC_DOTENV_DEFAULT_VALUE=isolated\n",
        encoding="utf-8",
    )
    monkeypatch.delenv(DOTENV_AUTOLOAD_ENV, raising=False)
    monkeypatch.setenv("TRADING_BOT_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.delenv("SYNTHETIC_DOTENV_DEFAULT_VALUE", raising=False)

    assert load_runtime_dotenv(tmp_path) is True
    assert os.environ["SYNTHETIC_DOTENV_DEFAULT_VALUE"] == "isolated"


def test_dotenv_test_environment_changes_are_restored(monkeypatch, tmp_path) -> None:
    before = {
        DOTENV_AUTOLOAD_ENV: os.environ.get(DOTENV_AUTOLOAD_ENV),
        "TRADING_BOT_RUNTIME_ROOT": os.environ.get("TRADING_BOT_RUNTIME_ROOT"),
    }
    with monkeypatch.context() as isolated:
        isolated.setenv(DOTENV_AUTOLOAD_ENV, "1")
        isolated.setenv("TRADING_BOT_RUNTIME_ROOT", str(tmp_path))
    after = {
        DOTENV_AUTOLOAD_ENV: os.environ.get(DOTENV_AUTOLOAD_ENV),
        "TRADING_BOT_RUNTIME_ROOT": os.environ.get("TRADING_BOT_RUNTIME_ROOT"),
    }

    assert after == before


def test_direct_canonical_env_open_fails_closed_in_isolated_process(tmp_path) -> None:
    canonical_env = Path.home() / "trading-bot-v2" / ".env"
    code = """
import ntpath
import os
import sys

target = ntpath.normcase(ntpath.normpath(sys.argv[1]))

def guard(event, args):
    if event != "open" or not args:
        return
    raw = args[0]
    if not isinstance(raw, (str, bytes, os.PathLike)):
        return
    candidate = ntpath.normcase(ntpath.normpath(os.fsdecode(raw)))
    if candidate == target:
        raise PermissionError("canonical dotenv blocked")

sys.addaudithook(guard)
open(sys.argv[1], "rb")
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(canonical_env)],
        cwd=ROOT,
        env=_safe_subprocess_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "canonical dotenv blocked" in result.stderr


def test_canonical_rcc_import_graph_is_hermetic_and_side_effect_free(tmp_path) -> None:
    code = """
import importlib
import os
import platform
import sqlite3
import subprocess
import sys

import dotenv
import dotenv.main

# Avoid the Windows stdlib `cmd /c ver` probe performed by platform.system()
# during aiohttp import; all process launches remain forbidden after this.
platform.system = lambda: "Windows"

def forbidden(label):
    def fail(*args, **kwargs):
        raise AssertionError(label)
    return fail

def audit(event, args):
    if event in {"socket.connect", "subprocess.Popen"}:
        raise AssertionError(event)
    if event == "open" and args:
        raw = args[0]
        if isinstance(raw, (str, bytes, os.PathLike)):
            if os.path.basename(os.fsdecode(raw)).lower() == ".env":
                raise AssertionError("dotenv file open")

sys.addaudithook(audit)
dotenv.load_dotenv = forbidden("direct dotenv loader")
dotenv.main.load_dotenv = dotenv.load_dotenv
sqlite3.connect = forbidden("sqlite connection")

modules = (
    "scripts.research_control_center",
    "scripts.public_channel_publisher",
    "src.scout.scanner_v0",
    "src.scout.resolve_outcomes",
    "scripts.strategy_lab.farm_loop",
    "scripts.build_journal",
    "scripts.strategy_lab.paper_telegram_transport",
    "scripts.telegram_bot",
    "scripts.analyze_chart",
)
for module in modules:
    importlib.import_module(module)

from scripts.research_control_center import contour_specs
from scripts import telegram_bot

specs = {spec.key: spec for spec in contour_specs()}
assert {"ollama", "public_news", "scanner", "paper_cards", "telegram_bot"} <= set(specs)
assert specs["telegram_bot"].env["AUTO_TRADE"] == "0"
assert specs["telegram_bot"].env["TELEGRAM_BOT_ALLOW_AUTO_EXECUTE"] == "0"
assert telegram_bot._auto_execute_opt_in() is False
assert "scripts.auto_execute" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=_safe_subprocess_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-1000:]


def test_canonical_rcc_call_graph_has_no_direct_python_dotenv_imports() -> None:
    violations: list[str] = []
    for relative_path in CANONICAL_DOTENV_CALL_GRAPH:
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "dotenv":
                violations.append(f"{relative_path}:{node.lineno}")
            elif isinstance(node, ast.Import):
                if any(alias.name == "dotenv" for alias in node.names):
                    violations.append(f"{relative_path}:{node.lineno}")

    assert violations == []


def test_repository_python_modules_delegate_dotenv_to_runtime_root() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    violations: list[str] = []
    loader_names = {"load_dotenv", "dotenv_values", "find_dotenv"}

    for relative_path in tracked:
        normalized = relative_path.replace("\\", "/")
        if normalized.startswith("tests/") or normalized == "src/utils/runtime_root.py":
            continue
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8-sig"))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "dotenv":
                violations.append(f"{normalized}:{node.lineno}:direct-import")
            elif isinstance(node, ast.Import) and any(
                alias.name == "dotenv" or alias.name.startswith("dotenv.")
                for alias in node.names
            ):
                violations.append(f"{normalized}:{node.lineno}:direct-import")
            elif isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in loader_names:
                    violations.append(f"{normalized}:{node.lineno}:{name}")
                if name not in {"open", "read_text", "read_bytes"}:
                    continue
                if not any(
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and child.value.replace("\\", "/").lower().endswith(".env")
                    for child in ast.walk(node)
                ):
                    continue
                ancestor = parents.get(node)
                while ancestor is not None and not isinstance(
                    ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
                ):
                    ancestor = parents.get(ancestor)
                if ancestor is None:
                    violations.append(f"{normalized}:{node.lineno}:import-time-env-io")

    assert violations == []


def test_archive_import_does_not_invoke_dotenv_loader(tmp_path) -> None:
    code = """
import importlib
import os

import dotenv
import dotenv.main

def forbidden(*args, **kwargs):
    raise AssertionError("archive import invoked dotenv")

dotenv.load_dotenv = forbidden
dotenv.main.load_dotenv = forbidden
os.environ["TRADING_BOT_DOTENV_AUTOLOAD"] = "1"
importlib.import_module("scripts.archive.ws_pump_orchestrator")
importlib.import_module("scripts.archive.backtest_simulate")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=_safe_subprocess_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-1000:]


def test_repository_import_isolation_surfaces_have_no_runtime_side_effects(tmp_path) -> None:
    modules = repr(REPOSITORY_IMPORT_ISOLATION_MODULES)
    code = f"""
import importlib
import os
import platform
import sqlite3
import sys

import dotenv
import dotenv.main

platform.system = lambda: "Windows"

def forbidden(label):
    def fail(*args, **kwargs):
        raise AssertionError(label)
    return fail

def audit(event, args):
    if event in {{"socket.connect", "subprocess.Popen"}}:
        raise AssertionError(event)
    if event == "import" and args and str(args[0]).split(".", 1)[0] == "mcp":
        raise AssertionError("optional MCP dependency imported during module import")
    if event == "open" and args:
        raw = args[0]
        if isinstance(raw, (str, bytes, os.PathLike)):
            if os.path.basename(os.fsdecode(raw)).lower() == ".env":
                raise AssertionError("dotenv file open")

sys.addaudithook(audit)
dotenv.load_dotenv = forbidden("direct dotenv loader")
dotenv.main.load_dotenv = dotenv.load_dotenv
sqlite3.connect = forbidden("sqlite connection")

for module in {modules}:
    importlib.import_module(module)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=_safe_subprocess_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:]
