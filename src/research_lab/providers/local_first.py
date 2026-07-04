# -*- coding: utf-8 -*-
"""Local-first market data provider for paper/research loops.

Reads prepared canonical candles from the private Strategy Lab cache first and
delegates to a fallback provider only when the cache has no usable rows. It never
writes data, never uses account/private/order endpoints, and never changes live
execution state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.research_lab.experiment import load_candles
from src.research_lab.paths import market_data_dir, resolve_private_root


class LocalFirstMarketDataProvider:
    """Read-only cache wrapper around another market-data provider."""

    configured = True

    def __init__(self, private_root: Path | str, fallback: Any, *, min_rows: int = 20) -> None:
        self.private_root = resolve_private_root(private_root)
        self.fallback = fallback
        self.min_rows = max(1, int(min_rows))
        self.name = f"local-cache+{getattr(fallback, 'name', 'fallback')}"

    def fetch_ohlcv(self, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        cached = self._fetch_cached(symbol, timeframe, int(start_ts), int(end_ts))
        if len(cached) >= self.min_rows:
            return cached
        return self.fallback.fetch_ohlcv(symbol, timeframe, start_ts, end_ts)

    def _fetch_cached(self, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        tf = str(timeframe).strip().lower()
        norm = str(symbol).replace("-", "_").replace("/", "_")
        data_dir = market_data_dir(self.private_root, tf)
        if not data_dir.exists():
            return []
        rows_by_ts: dict[int, dict[str, Any]] = {}
        for path in sorted(data_dir.glob(f"{norm}_*_{tf}.json")):
            try:
                rows = load_candles(path)
            except Exception:  # noqa: BLE001 - bad cache slice should not break paper loop
                continue
            for row in rows:
                try:
                    ts = int(row["ts"])
                    float(row["open"])
                    float(row["high"])
                    float(row["low"])
                    float(row["close"])
                    float(row.get("vol") or 0.0)
                except (KeyError, TypeError, ValueError):
                    continue
                if start_ts <= ts <= end_ts:
                    rows_by_ts[ts] = dict(row)
        return [rows_by_ts[ts] for ts in sorted(rows_by_ts)]
