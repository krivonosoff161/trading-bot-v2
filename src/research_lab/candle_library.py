# -*- coding: utf-8 -*-
"""One read interface for the research station's candle truth."""

from __future__ import annotations

import glob as _glob
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.research_lab.candle_snapshot import (
    CandleSnapshotManifest,
    build_snapshot,
)
from src.research_lab.candle_store import CandleStore, CandleStoreError
from src.research_lab.paths import market_data_glob


@dataclass(frozen=True)
class CandleSlice:
    rows: list[dict[str, Any]]
    source: str
    label: str
    manifest: CandleSnapshotManifest


def load_canonical_candles(
    private_root: str | Path,
    symbol: str,
    timeframe: str,
    *,
    fallback_glob: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    as_of_ms: int | None = None,
    purpose: str = "canonical_read",
    coverage_policy: str = "available",
    progress: Callable[[str], None] | None = None,
) -> CandleSlice:
    """Select one explicit snapshot across SQLite and retained JSON candidates."""
    from src.research_lab.experiment import choose_symbol_file, load_candles

    if (start_ts is None) != (end_ts is None):
        raise ValueError("start_ts and end_ts must be provided together")
    store = CandleStore(private_root)
    candidates: list[CandleSlice] = []
    try:
        coverage = store.coverage(symbol, timeframe)
        if progress is not None:
            progress("coverage_checked")
        if (
            coverage.row_count
            and coverage.first_ts is not None
            and coverage.last_ts is not None
        ):
            selected_start = (
                int(start_ts) if start_ts is not None else coverage.first_ts
            )
            selected_end = int(end_ts) if end_ts is not None else coverage.last_ts
            snapshot = store.read_snapshot(
                symbol,
                timeframe,
                selected_start,
                selected_end,
                as_of_ms=as_of_ms,
                purpose=purpose,
                coverage_policy=coverage_policy,
            )
            if snapshot.rows:
                label = f"sqlite:{str(symbol).replace('-', '_').upper()}:{str(timeframe).lower()}"
                candidates.append(
                    CandleSlice(snapshot.rows, "sqlite", label, snapshot.manifest)
                )
            if progress is not None:
                progress("sqlite_snapshot_checked")
    except (CandleStoreError, ValueError):
        # A damaged/locked migration target must not erase the still-retained JSON fallback.
        pass

    pattern = fallback_glob or market_data_glob(private_root, timeframe)
    normalized = str(symbol).replace("-", "_").replace("/", "_")
    json_paths: set[Path] = set()
    chosen = choose_symbol_file(pattern, symbol, timeframe=timeframe)
    if chosen is not None:
        json_paths.add(Path(chosen))
    for token in (normalized, str(symbol)):
        try:
            json_paths.update(
                Path(item) for item in _glob.glob(pattern.format(symbol=token))
            )
        except (KeyError, IndexError, ValueError):
            continue
    for path in sorted(json_paths):
        if as_of_ms is not None:
            continue
        rows = load_candles(path)
        if progress is not None:
            progress("json_candidate_loaded")
        if start_ts is not None and end_ts is not None:
            rows = [
                row for row in rows if int(start_ts) <= int(row["ts"]) <= int(end_ts)
            ]
        root = Path(private_root).resolve()
        try:
            label = path.resolve().relative_to(root).as_posix()
        except ValueError:
            label = path.name
        legacy_rows = []
        for row in rows:
            item = dict(row)
            item["_source"] = "legacy-json"
            item["_provenance_status"] = "legacy_unknown"
            legacy_rows.append(item)
        json_start = (
            int(start_ts)
            if start_ts is not None
            else (int(legacy_rows[0]["ts"]) if legacy_rows else None)
        )
        json_end = (
            int(end_ts)
            if end_ts is not None
            else (int(legacy_rows[-1]["ts"]) if legacy_rows else None)
        )
        snapshot = build_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            rows=legacy_rows,
            start_ts=json_start,
            end_ts=json_end,
            as_of_ms=None,
            purpose=purpose,
            coverage_policy=coverage_policy,
            source_backend="json",
        )
        if snapshot.rows:
            candidates.append(
                CandleSlice(snapshot.rows, "json", label, snapshot.manifest)
            )

    acceptable = [
        candidate
        for candidate in candidates
        if coverage_policy == "available"
        or candidate.manifest.coverage_status == "complete"
    ]
    if acceptable:
        selected = max(
            acceptable,
            key=lambda candidate: (
                candidate.manifest.row_count,
                1 if candidate.source == "sqlite" else 0,
                candidate.manifest.snapshot_id,
            ),
        )
        if progress is not None:
            progress("canonical_snapshot_selected")
        return selected

    empty = build_snapshot(
        symbol=symbol,
        timeframe=timeframe,
        rows=[],
        start_ts=start_ts,
        end_ts=end_ts,
        as_of_ms=as_of_ms,
        purpose=purpose,
        coverage_policy=coverage_policy,
        source_backend="missing",
    )
    if progress is not None:
        progress("canonical_snapshot_missing")
    return CandleSlice([], "missing", "", empty.manifest)


def sync_json_to_store(
    private_root: str | Path,
    symbol: str,
    timeframe: str,
    path: str | Path,
    *,
    source: str,
    available_at_ms: int,
) -> int:
    """Commit a validated legacy enrichment back into the canonical library."""
    from src.research_lab.experiment import load_candles

    rows = load_candles(Path(path))
    if not rows:
        return 0
    outcome = CandleStore(private_root).upsert_candles(
        symbol,
        timeframe,
        rows,
        source=source,
        strict=True,
        observed_at_ms=int(available_at_ms),
        available_at_ms=int(available_at_ms),
        acquired_at_ms=int(available_at_ms),
    )
    return outcome.accepted
