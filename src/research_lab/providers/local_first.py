# -*- coding: utf-8 -*-
"""Local-first market data provider for paper/research loops.

Reads prepared canonical candles from the private Strategy Lab cache first and
delegates to a fallback provider when the cache is missing, too short, or stale for
the requested timeframe. It never writes data, never uses account/private/order
endpoints, and never changes live execution state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.research_lab.candle_store import CandleStore, CandleStoreError
from src.research_lab.experiment import load_candles
from src.research_lab.paths import market_data_dir, resolve_private_root

_TF_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


class LocalFirstMarketDataProvider:
    """Read-only cache wrapper around another market-data provider."""

    configured = True

    def __init__(
        self,
        private_root: Path | str,
        fallback: Any,
        *,
        min_rows: int = 20,
        max_stale_bars: int = 3,
    ) -> None:
        self.private_root = resolve_private_root(private_root)
        self.fallback = fallback
        self.store = CandleStore(self.private_root)
        self.min_rows = max(1, int(min_rows))
        self.max_stale_bars = max(1, int(max_stale_bars))
        self.name = f"local-cache+{getattr(fallback, 'name', 'fallback')}"

    def fetch_ohlcv(self, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        cached = self._fetch_cached(symbol, timeframe, int(start_ts), int(end_ts))
        if (len(cached) >= self.min_rows
                and self._is_fresh(cached, timeframe, int(end_ts))
                and self._covers_without_gaps(cached, timeframe, int(start_ts))):
            return cached
        return self.fallback.fetch_ohlcv(symbol, timeframe, start_ts, end_ts)

    def _covers_without_gaps(
        self, rows: list[dict[str, Any]], timeframe: str, start_ts: int,
    ) -> bool:
        tf_ms = _TF_MS.get(str(timeframe).strip().lower())
        if tf_ms is None or not rows:
            return False
        stamps = [int(row["ts"]) for row in rows]
        if stamps[0] > start_ts + tf_ms:
            return False
        return all(b - a == tf_ms for a, b in zip(stamps, stamps[1:]))

    def _is_fresh(self, rows: list[dict[str, Any]], timeframe: str, end_ts: int) -> bool:
        try:
            last_ts = int(rows[-1]["ts"])
        except (IndexError, KeyError, TypeError, ValueError):
            return False
        tf_ms = _TF_MS.get(str(timeframe).strip().lower(), _TF_MS["15m"])
        return end_ts - last_ts <= self.max_stale_bars * tf_ms

    def _fetch_cached(self, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        tf = str(timeframe).strip().lower()
        norm = str(symbol).replace("-", "_").replace("/", "_")
        if self.store.exists:
            try:
                stored = self.store.read(norm, tf, start_ts, end_ts)
            except (CandleStoreError, ValueError):
                stored = []
            if stored:
                return stored
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
