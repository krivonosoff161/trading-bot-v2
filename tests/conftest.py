from __future__ import annotations

import os
import ntpath
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Credential-bearing local files must never influence tests. This is set before
# pytest imports test modules, including modules with import-time dotenv loads.
os.environ["TRADING_BOT_DOTENV_AUTOLOAD"] = "0"


def _normalized_test_path(raw_path: object) -> str | None:
    if not isinstance(raw_path, (str, bytes, os.PathLike)):
        return None
    try:
        value = os.fsdecode(raw_path)
    except (TypeError, ValueError):
        return None
    if not ntpath.isabs(value):
        value = ntpath.join(str(ROOT), value)
    return ntpath.normcase(ntpath.normpath(value))


_CANONICAL_ENV_PATHS = frozenset(
    filter(
        None,
        (
            _normalized_test_path(ROOT / ".env"),
            _normalized_test_path(Path.home() / "trading-bot-v2" / ".env"),
        ),
    )
)
_CANONICAL_ENV_OPEN_ATTEMPTS: list[str] = []


def _deny_canonical_env_open(event: str, args: tuple[object, ...]) -> None:
    if event != "open" or not args:
        return
    candidate = _normalized_test_path(args[0])
    if candidate in _CANONICAL_ENV_PATHS:
        _CANONICAL_ENV_OPEN_ATTEMPTS.append("blocked")
        raise PermissionError("canonical .env access is forbidden during tests")


sys.addaudithook(_deny_canonical_env_open)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _CANONICAL_ENV_OPEN_ATTEMPTS:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter) -> None:
    terminalreporter.write_line(
        f"canonical_env_open_attempts={len(_CANONICAL_ENV_OPEN_ATTEMPTS)}"
    )
