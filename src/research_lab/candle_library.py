# -*- coding: utf-8 -*-
"""One read interface for the research station's candle truth."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.research_lab.candle_store import CandleStore, CandleStoreError
from src.research_lab.experiment import choose_symbol_file, load_candles
from src.research_lab.paths import market_data_glob


@dataclass(frozen=True)
class CandleSlice:
    rows: list[dict[str, Any]]
    source: str
    label: str


def load_canonical_candles(
    private_root: str | Path,
    symbol: str,
    timeframe: str,
    *,
    fallback_glob: str | None = None,
) -> CandleSlice:
    """Read SQLite first; use a usable JSON slice only during migration."""
    store = CandleStore(private_root)
    try:
        coverage = store.coverage(symbol, timeframe)
        if coverage.row_count and coverage.first_ts is not None and coverage.last_ts is not None:
            rows = store.read(symbol, timeframe, coverage.first_ts, coverage.last_ts)
            if rows:
                label = f"sqlite:{str(symbol).replace('-', '_').upper()}:{str(timeframe).lower()}"
                return CandleSlice(rows, "sqlite", label)
    except (CandleStoreError, ValueError):
        # A damaged/locked migration target must not erase the still-retained JSON fallback.
        pass

    pattern = fallback_glob or market_data_glob(private_root, timeframe)
    path = choose_symbol_file(pattern, symbol, timeframe=timeframe)
    if path is None:
        return CandleSlice([], "missing", "")
    rows = load_candles(path)
    root = Path(private_root).resolve()
    try:
        label = path.resolve().relative_to(root).as_posix()
    except ValueError:
        label = path.name
    return CandleSlice(rows, "json", label)


def sync_json_to_store(
    private_root: str | Path, symbol: str, timeframe: str, path: str | Path,
    *, source: str,
) -> int:
    """Commit a validated legacy enrichment back into the canonical library."""
    rows = load_candles(Path(path))
    if not rows:
        return 0
    outcome = CandleStore(private_root).upsert_candles(
        symbol, timeframe, rows, source=source, strict=True,
    )
    return outcome.accepted
