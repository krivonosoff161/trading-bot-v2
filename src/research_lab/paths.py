# -*- coding: utf-8 -*-
"""Path safety helpers for private Strategy Research Lab outputs."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRIVATE_ROOT = Path.home() / "github_projects" / "trading-bot-research" / "strategy-lab"


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


def strategy_lab_label(*parts: str) -> str:
    return "/".join(("strategy-lab", *[p.strip("/\\") for p in parts if p]))


def one_minute_data_dir(private_root: str | Path) -> Path:
    """Where demand-prepared 1m candles live (under the private research root)."""
    return Path(private_root).expanduser() / "market_data" / "1m"


def one_minute_glob(private_root: str | Path) -> str:
    """Glob (with a {symbol} placeholder) for prepared 1m files under the private root."""
    return str(one_minute_data_dir(private_root) / "{symbol}_*_1m*.json")
