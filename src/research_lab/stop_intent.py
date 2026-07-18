# -*- coding: utf-8 -*-
"""Graceful stop intent for the research loop.

Writes a stop-intent file under the private root that the research loop and
worker loop check between iterations. When stop is requested, the current
iteration finishes cleanly (no job interruption) and the loop exits after that.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from src.research_lab.paths import resolve_private_root

_STOP_FILENAME = "strategy_lab_stop_requested.json"


def _stop_path(private_root: Path) -> Path:
    return private_root / "state" / _STOP_FILENAME


def stop_intent_path(private_root: Path) -> Path:
    """Return the bounded stop-intent path for owner-bound acknowledgement."""
    return _stop_path(Path(private_root))


def request_stop(
    private_root: Path,
    *,
    reason: str = "operator_requested",
    allow_public_output: bool = False,
) -> Path:
    """Write stop-intent file. The loop will exit after its current iteration."""
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    path = _stop_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "requested_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "reason": reason,
        "schema": "strategy_lab_stop_intent.v1",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def is_stop_requested(private_root: Path) -> bool:
    """Check if stop intent is active."""
    return _stop_path(private_root).exists()


def read_stop_intent(private_root: Path) -> dict[str, Any]:
    """Read stop intent details, or {} if not requested."""
    path = _stop_path(private_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def clear_stop(private_root: Path, *, allow_public_output: bool = False) -> None:
    """Remove stop-intent file."""
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    path = _stop_path(private_root)
    if path.exists():
        path.unlink()
