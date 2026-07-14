# -*- coding: utf-8 -*-
"""Non-destructive JSON -> canonical candle-store migration.

The operation is a report unless ``apply=True``. Source JSON files are never
renamed or deleted; retirement is a separate operator decision after parity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.research_lab.candle_store import CandleStore
from src.research_lab.candle_identity import candle_ohlcv_fingerprint
from src.research_lab.experiment import load_candles
from src.research_lab.paths import resolve_private_root

SUPPORTED_TIMEFRAMES = ("1m", "15m", "1h", "4h", "1d")


@dataclass(frozen=True)
class MigrationFile:
    path: str
    symbol: str
    timeframe: str
    rows: int
    first_ts: int | None
    last_ts: int | None
    status: str
    rejected: int = 0
    source_fingerprint: str | None = None
    stored_range_fingerprint: str | None = None
    parity: bool | None = None


def discover_json_candles(private_root: str | Path) -> list[tuple[Path, str]]:
    root = resolve_private_root(private_root)
    found: list[tuple[Path, str]] = []
    for timeframe in SUPPORTED_TIMEFRAMES:
        folder = root / "market_data" / timeframe
        if folder.is_dir():
            found.extend((path, timeframe) for path in sorted(folder.glob("*.json")))
    return found


def _symbol_from_rows_or_name(path: Path, rows: list[dict[str, Any]]) -> str:
    if rows:
        symbol = str(rows[0].get("symbol") or "").strip()
        if symbol:
            return symbol
    stem = path.stem.upper()
    for marker in ("_USDT_SWAP", "-USDT-SWAP"):
        pos = stem.find(marker)
        if pos >= 0:
            return f"{stem[:pos]}_USDT_SWAP"
    return ""


def migrate_json_candles(
    private_root: str | Path, *, apply: bool = False,
) -> dict[str, Any]:
    root = resolve_private_root(private_root)
    store = CandleStore(root)
    files: list[MigrationFile] = []
    accepted = 0
    rejected = 0

    for path, timeframe in discover_json_candles(root):
        rows = load_candles(path)
        symbol = _symbol_from_rows_or_name(path, rows)
        first_ts = int(rows[0]["ts"]) if rows else None
        last_ts = int(rows[-1]["ts"]) if rows else None
        if not rows or not symbol:
            files.append(MigrationFile(
                str(path.relative_to(root)).replace("\\", "/"), symbol,
                timeframe, len(rows), first_ts, last_ts, "skipped_invalid",
            ))
            continue
        status = "would_import"
        file_rejected = 0
        source_fingerprint = candle_ohlcv_fingerprint(rows)
        if apply:
            outcome = store.upsert_candles(
                symbol, timeframe, rows, source="json_migration", strict=False,
            )
            file_rejected = outcome.rejected
            status = "imported" if not file_rejected else "imported_with_rejections"
            accepted += outcome.accepted
            rejected += outcome.rejected
        stored_range = (
            store.read(symbol, timeframe, first_ts, last_ts)
            if store.exists and first_ts is not None and last_ts is not None else []
        )
        stored_by_ts = {int(row["ts"]): row for row in stored_range}
        stored_rows = [
            stored_by_ts[int(row["ts"])] for row in rows
            if int(row["ts"]) in stored_by_ts
        ]
        stored_fingerprint = candle_ohlcv_fingerprint(stored_rows)
        parity = (
            len(stored_rows) == len(rows) and stored_fingerprint == source_fingerprint
            if stored_rows else None
        )
        files.append(MigrationFile(
            str(path.relative_to(root)).replace("\\", "/"), symbol,
            timeframe, len(rows), first_ts, last_ts, status, file_rejected,
            source_fingerprint, stored_fingerprint, parity,
        ))

    series: list[dict[str, Any]] = []
    if apply:
        for symbol, timeframe in sorted({(f.symbol, f.timeframe) for f in files if f.symbol}):
            series.append(store.coverage(symbol, timeframe).to_dict())

    return {
        "schema": "strategy_lab_candle_migration.v1",
        "mode": "apply" if apply else "dry_report",
        "source_files_deleted": 0,
        "files_seen": len(files),
        "rows_accepted": accepted,
        "rows_rejected": rejected,
        "parity_failures": sum(1 for item in files if item.parity is False),
        "store_budget": store.budget_status(),
        "files": [asdict(item) for item in files],
        "series": series,
    }
