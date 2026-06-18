# -*- coding: utf-8 -*-
"""Concrete data-state probe for the planner — read-only local readiness facts.

Wraps the inventory + fingerprint + OI-slot helpers into the single ``data_state``
dict the planner consumes: ``{status, rows, fingerprint, enrichment, oi_available}``.
``status`` is the local-file readiness (usable / too_short / missing) — it does NOT
fetch; whether to download is the planner/coordinator's decision. Pure, no network.
"""
from __future__ import annotations

import glob as _glob
from pathlib import Path
from typing import Any

from src.research_lab.data_fingerprint import fingerprint_for_file, present_enrichment
from src.research_lab.data_inventory import inspect_file
from src.research_lab.data_readiness_selector import min_rows_for
from src.research_lab.experiment import choose_symbol_file
from src.research_lab.oi_slot import oi_slot_path
from src.research_lab.paths import market_data_glob


def _best_existing(pattern: str, symbol: str) -> Path | None:
    normalized = symbol.replace("-", "_").replace("/", "_")
    paths = [Path(p) for p in _glob.glob(pattern.format(symbol=normalized))]
    if not paths:
        paths = [Path(p) for p in _glob.glob(pattern.format(symbol=symbol))]
    return max(paths, key=lambda p: p.stat().st_size, default=None) if paths else None


def data_state(private_root: Path, symbol: str, timeframe: str) -> dict[str, Any]:
    """Local readiness facts for one (symbol, timeframe). No fetch."""
    pattern = market_data_glob(private_root, timeframe)
    oi_available = oi_slot_path(private_root, symbol) is not None
    usable = choose_symbol_file(pattern, symbol, timeframe=timeframe)
    if usable:
        return {
            "status": "usable",
            "rows": int(inspect_file(usable).get("rows") or 0),
            "fingerprint": fingerprint_for_file(usable),
            "enrichment": present_enrichment(usable),
            "oi_available": oi_available,
        }
    existing = _best_existing(pattern, symbol)
    if existing is None:
        return {"status": "missing", "rows": 0, "fingerprint": None,
                "enrichment": (), "oi_available": oi_available}
    info = inspect_file(existing)
    rows = int(info.get("rows") or 0)
    status = "too_short" if 0 < rows < min_rows_for(timeframe) else "missing"
    return {"status": status, "rows": rows, "fingerprint": None,
            "enrichment": (), "oi_available": oi_available}
