# -*- coding: utf-8 -*-
"""Manual / recorded open-interest slot — OI is never faked.

A clean keyless historical OI series per OKX instrument is not reliably public (the
public open-interest endpoint is a single current snapshot), so OI is supplied by a
recorded slot the operator populates: a JSON or CSV file under the private research
root with rows of {ts, oi[, symbol, source]}. This module loads + validates that
slot and exposes the points for a no-lookahead merge (flow_merge.merge_oi). When the
slot is empty the OI families are data-starved and the farm classifies them
NEEDS_OI_DATA rather than presenting them as active. Pure: no network, no faking.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def oi_slot_dir(private_root: Path) -> Path:
    return Path(private_root) / "market_data" / "oi"


def _norm(symbol: str) -> str:
    return str(symbol).replace("-", "_").replace("/", "_").upper()


def oi_slot_path(private_root: Path, symbol: str) -> Path | None:
    """Resolve <symbol>_oi.json or <symbol>_oi.csv under the slot dir, or None."""
    base = oi_slot_dir(private_root) / f"{_norm(symbol)}_oi"
    for ext in (".json", ".csv"):
        candidate = base.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


def _coerce_row(raw: dict[str, Any], symbol: str) -> tuple[int, float] | None:
    try:
        ts = int(raw["ts"])
        oi = float(raw["oi"])
    except (KeyError, TypeError, ValueError):
        return None
    if oi <= 0:
        return None
    row_symbol = raw.get("symbol")
    if row_symbol and _norm(str(row_symbol)) != _norm(symbol):
        return None
    return ts, oi


def _read_json(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _read_csv(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    except OSError:
        return []


def load_oi_series(private_root: Path, symbol: str) -> dict[str, Any]:
    """Load + validate the recorded OI series for one symbol.

    Returns {"points": [(ts, oi)] sorted, "source": str, "rows": int, "rejected": int,
    "path": label}. Missing slot -> empty points with source 'none' (never invented).
    """
    path = oi_slot_path(private_root, symbol)
    if path is None:
        return {"points": [], "source": "none", "rows": 0, "rejected": 0, "path": ""}
    raw_rows = _read_json(path) if path.suffix == ".json" else _read_csv(path)
    points: dict[int, float] = {}
    rejected = 0
    sources: set[str] = set()
    for raw in raw_rows:
        coerced = _coerce_row(raw, symbol)
        if coerced is None:
            rejected += 1
            continue
        points[coerced[0]] = coerced[1]
        src = raw.get("source")
        if src:
            sources.add(str(src))
    source = "+".join(sorted(sources)) if sources else "recorded_slot"
    return {
        "points": [(ts, points[ts]) for ts in sorted(points)],
        "source": source if points else "none",
        "rows": len(points),
        "rejected": rejected,
        "path": path.name,
    }
