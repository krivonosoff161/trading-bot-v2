# -*- coding: utf-8 -*-
"""Tiny .env loader for Strategy Lab operator commands.

The lab cannot assume python-dotenv is installed. This parser is intentionally
small: KEY=VALUE lines only, comments skipped, existing environment values kept
unless override=True. Values are never printed by callers.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path, *, override: bool = False) -> dict[str, str]:
    """Load KEY=VALUE pairs into os.environ and return the keys that were set."""
    env_path = Path(path)
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded
    for raw in env_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or any(ch.isspace() for ch in key):
            continue
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded
