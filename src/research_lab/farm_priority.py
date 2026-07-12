"""Canonical priority classes for the private paper/research farm.

Lower numbers run first.  The classes are deliberately coarse: a running
numeric slot is allowed to finish, then the next slot claims the best waiting
work.  This gives bounded urgent latency without unsafe process preemption.
"""
from __future__ import annotations

from typing import Any

PRIORITY_MANUAL_URGENT = 0
PRIORITY_SCANNER_GO = 10
PRIORITY_SCANNER_WATCH = 20
PRIORITY_OKX_ANNOUNCEMENT = 30
PRIORITY_OKX_MOVER = 40
PRIORITY_ADVISORY = 60
PRIORITY_BACKGROUND = 90


def watch_verdict(watch: dict[str, Any]) -> str:
    scanner = watch.get("scanner") or {}
    return str(scanner.get("verdict") or watch.get("verdict") or "WATCH").strip().upper()


def priority_label(value: int | None) -> str:
    priority = 100 if value is None else int(value)
    if priority <= PRIORITY_MANUAL_URGENT:
        return "manual urgent"
    if priority <= PRIORITY_SCANNER_GO:
        return "strong GO"
    if priority <= PRIORITY_SCANNER_WATCH:
        return "WATCH"
    if priority <= PRIORITY_OKX_ANNOUNCEMENT:
        return "official news"
    if priority <= PRIORITY_OKX_MOVER:
        return "market mover"
    if priority <= PRIORITY_ADVISORY:
        return "role/recheck"
    return "background sweep"


def priority_value(value: Any, default: int = 100) -> int:
    """Preserve priority zero; only a missing value gets the default."""
    priority = int(default if value is None else value)
    return {1: 20, 2: 30, 3: 40, 4: 90}.get(priority, priority)
