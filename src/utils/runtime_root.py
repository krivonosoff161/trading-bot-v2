"""Resolve machine-local runtime assets without copying them into a task worktree."""

from __future__ import annotations

import os
from pathlib import Path


ENV_NAME = "TRADING_BOT_RUNTIME_ROOT"
DOTENV_AUTOLOAD_ENV = "TRADING_BOT_DOTENV_AUTOLOAD"


def runtime_root(code_root: Path) -> Path:
    configured = os.environ.get(ENV_NAME, "").strip()
    return Path(configured).expanduser() if configured else Path(code_root)


def runtime_env_file(code_root: Path) -> Path:
    return runtime_root(code_root) / ".env"


def dotenv_autoload_enabled() -> bool:
    value = os.environ.get(DOTENV_AUTOLOAD_ENV, "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def load_runtime_dotenv(code_root: Path, **kwargs: object) -> bool:
    """Load the runtime dotenv file unless the process disabled autoload."""
    if not dotenv_autoload_enabled():
        return False

    from dotenv import load_dotenv

    return bool(load_dotenv(runtime_env_file(code_root), **kwargs))
