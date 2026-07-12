"""Resolve machine-local runtime assets without copying them into a task worktree."""

from __future__ import annotations

import os
from pathlib import Path


ENV_NAME = "TRADING_BOT_RUNTIME_ROOT"


def runtime_root(code_root: Path) -> Path:
    configured = os.environ.get(ENV_NAME, "").strip()
    return Path(configured).expanduser() if configured else Path(code_root)


def runtime_env_file(code_root: Path) -> Path:
    return runtime_root(code_root) / ".env"
