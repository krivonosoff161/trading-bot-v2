# -*- coding: utf-8 -*-
"""Cheap, stable fingerprint of prepared candle data.

The fingerprint is the missing identity that lets the farm decide "have I already
computed THIS sweep on THIS data?" without re-downloading or re-running blindly. It
is derived from the data the inventory already produces — symbol, timeframe, row
count, and the first/last bar timestamps — plus which optional enrichment fields
(funding / oi / microstructure) are present, because enriching a file in place
changes its meaning for the flow/OI families without changing the candle count.

A changed fingerprint == legitimately fresh data == a sweep may run again (re-arm).
An unchanged fingerprint == identical data == do not recompute. Pure + read-only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.research_lab.data_inventory import inspect_file
from src.research_lab.experiment import choose_symbol_file
from src.research_lab.candle_identity import candle_slice_fingerprint

# Optional fields whose presence changes a file's meaning for data-gated families.
ENRICHMENT_FIELDS = ("funding", "oi", "obi_top5", "spread_bps", "trade_delta_100")


def compute_fingerprint(
    symbol: str,
    timeframe: str,
    rows: int,
    start_ts: int | None,
    end_ts: int | None,
    *,
    enrichment: tuple[str, ...] = (),
) -> str:
    """Stable short hash of the data identity. Same inputs -> same fingerprint."""
    payload = {
        "symbol": str(symbol).replace("-", "_").upper(),
        "timeframe": str(timeframe).lower(),
        "rows": int(rows or 0),
        "start_ts": int(start_ts) if start_ts is not None else None,
        "end_ts": int(end_ts) if end_ts is not None else None,
        "enrichment": sorted(set(enrichment)),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def fingerprint_for_rows(
    symbol: str, timeframe: str, rows: list[dict[str, Any]],
) -> str | None:
    """Content-sensitive identity for the exact canonical candle slice."""
    return candle_slice_fingerprint(symbol, timeframe, rows)


def present_enrichment(path: Path) -> tuple[str, ...]:
    """Which enrichment fields are present on a prepared file (peek last candle)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(data, list) or not data:
        return ()
    last = data[-1] if isinstance(data[-1], dict) else {}
    return tuple(f for f in ENRICHMENT_FIELDS if last.get(f) is not None)


def fingerprint_for_file(path: Path, *, with_enrichment: bool = True) -> str | None:
    """Fingerprint a specific prepared file, or None if it is not usable."""
    info = inspect_file(Path(path))
    if not info.get("has_ohlcv"):
        return None
    enrichment = present_enrichment(path) if with_enrichment else ()
    return compute_fingerprint(
        info["symbol"], info.get("timeframe") or "", int(info.get("rows") or 0),
        info.get("start_ts"), info.get("end_ts"), enrichment=enrichment,
    )


def fingerprint_for_symbol(
    data_glob: str, symbol: str, timeframe: str, *, with_enrichment: bool = True,
    private_root: str | Path | None = None,
) -> str | None:
    """Fingerprint the prepared file the experiment loader would pick, or None."""
    if private_root is not None:
        from src.research_lab.candle_store import CandleStore
        store = CandleStore(private_root)
        coverage = store.coverage(symbol, timeframe)
        if coverage.row_count and coverage.first_ts is not None and coverage.last_ts is not None:
            rows = store.read(symbol, timeframe, coverage.first_ts, coverage.last_ts)
            return fingerprint_for_rows(symbol, timeframe, rows)
    path = choose_symbol_file(data_glob, symbol, timeframe=timeframe)
    if not path:
        return None
    return fingerprint_for_file(Path(path), with_enrichment=with_enrichment)


def params_hash(params: dict[str, Any] | None) -> str:
    """Stable short hash of a candidate parameter dict (canonical key)."""
    blob = json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]
