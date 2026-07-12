# -*- coding: utf-8 -*-
"""Path safety helpers for private Strategy Research Lab outputs."""

from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRIVATE_ROOT = Path.home() / "github_projects" / "trading-bot-research" / "strategy-lab"
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}


def _validate_portable_part(raw: str) -> None:
    for name in Path(raw).parts:
        if name in {"", "."}:
            continue
        if ":" in name or name.endswith((".", " ")):
            raise ValueError("unsafe or non-portable private child path")
        base = name.split(".", 1)[0].upper()
        if base in _WINDOWS_RESERVED or re.search(r"[<>\"|?*]", name):
            raise ValueError("unsafe or non-portable private child path")


def resolve_private_root(path: str | Path, *, allow_public_output: bool = False) -> Path:
    """Resolve a private output root and keep it outside this public repository."""
    resolved = Path(path).expanduser().resolve()
    project_root = PROJECT_ROOT.resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError:
        return resolved
    if allow_public_output:
        return resolved
    raise ValueError(
        "private Strategy Lab output root points inside the public trading-bot-v2 repo; "
        "set TRADING_BOT_RESEARCH_ROOT to a private directory or pass --allow-public-output"
    )


def resolve_private_child(root: str | Path, *parts: str) -> Path:
    """Resolve a child path while rejecting traversal and existing links/reparse points."""
    base = resolve_private_root(root)
    current = base
    for raw in parts:
        _validate_portable_part(str(raw))
        part = Path(raw)
        if part.is_absolute() or ".." in part.parts:
            raise ValueError("unsafe private child path")
        current = current / part
        probe = current
        if probe.exists():
            stat = probe.lstat()
            reparse = bool(getattr(stat, "st_file_attributes", 0) & 0x400)
            if probe.is_symlink() or reparse:
                raise ValueError("private child path uses a link or reparse point")
    resolved = current.resolve(strict=False)
    base_text = str(base).removeprefix("\\\\?\\")
    resolved_text = str(resolved).removeprefix("\\\\?\\")
    try:
        contained = os.path.commonpath([base_text, resolved_text]) == os.path.normpath(base_text)
    except ValueError:
        contained = False
    if not contained:
        raise ValueError("private child path escapes private root")
    return Path(resolved_text)


def strategy_lab_label(*parts: str) -> str:
    return "/".join(("strategy-lab", *[p.strip("/\\") for p in parts if p]))


def one_minute_data_dir(private_root: str | Path) -> Path:
    """Where demand-prepared 1m candles live (under the private research root)."""
    return Path(private_root).expanduser() / "market_data" / "1m"


def one_minute_glob(private_root: str | Path) -> str:
    """Glob (with a {symbol} placeholder) for prepared 1m files under the private root."""
    return str(one_minute_data_dir(private_root) / "{symbol}_*_1m*.json")


def market_data_dir(private_root: str | Path, timeframe: str = "1m") -> Path:
    """Where demand-prepared candles live for any timeframe."""
    return Path(private_root).expanduser() / "market_data" / timeframe


def market_data_glob(private_root: str | Path, timeframe: str = "1m") -> str:
    """Glob (with a {symbol} placeholder) for prepared files for any timeframe."""
    return str(market_data_dir(private_root, timeframe) / "{symbol}_*_*.json")
