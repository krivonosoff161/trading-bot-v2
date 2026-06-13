# -*- coding: utf-8 -*-
"""Shared config IO for Strategy Lab loaders.

Centralizes YAML reading and clear failure messages so every loader fails the
same way on a missing file, invalid YAML, or a non-mapping top level.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.research_lab.paths import PROJECT_ROOT

CONFIG_DIR = PROJECT_ROOT / "configs" / "strategy_lab"


def load_yaml_mapping(path: str | Path, *, what: str) -> dict[str, Any]:
    """Load a YAML file that must contain a mapping at the top level."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{what} config not found: {p.name}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{what} config is not valid YAML ({p.name}): {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{what} config must be a mapping at the top level ({p.name})")
    return data


def require(data: dict[str, Any], key: str, *, what: str) -> Any:
    """Return data[key] or raise a clear error naming the config and key."""
    if key not in data:
        raise ValueError(f"{what} config missing required key: {key}")
    return data[key]
