# -*- coding: utf-8 -*-
"""Private canonical candle storage for the research station.

The store is deliberately small and dependency-free: standard-library SQLite,
one writer transaction at a time, many bounded readers, and no exchange/account
logic.  JSON slices remain a migration fallback; this database is the eventual
single source of candle identity and coverage.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from src.research_lab.paths import resolve_private_root

SCHEMA_VERSION = 1
DEFAULT_MAX_READ_ROWS = 50_000
WARNING_BUDGET_BYTES = 1_500_000_000
HARD_BUDGET_BYTES = 2_000_000_000
_OPTIONAL_FIELDS = (
    "funding", "oi", "index_px", "obi_top5", "spread_bps", "trade_delta_100",
)
_TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


class CandleStoreError(RuntimeError):
    """Base error for a local candle-store operation."""


class CandleValidationError(CandleStoreError):
    """A row cannot be represented as an honest confirmed OHLCV candle."""


@dataclass(frozen=True)
class Coverage:
    symbol: str
    timeframe: str
    first_ts: int | None
    last_ts: int | None
    row_count: int
    gap_count: int
    expected_rows: int
    missing_rows: int
    updated_at_ms: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UpsertResult:
    symbol: str
    timeframe: str
    accepted: int
    inserted_or_updated: int
    rejected: int
    coverage: Coverage

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["coverage"] = self.coverage.to_dict()
        return data


def normalize_symbol(symbol: str) -> str:
    token = str(symbol).strip().upper().replace("-", "_").replace("/", "_")
    if not token or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in token):
        raise ValueError(f"unsupported candle symbol: {symbol!r}")
    return token


def normalize_timeframe(timeframe: str) -> str:
    token = str(timeframe).strip().lower()
    if token not in _TIMEFRAME_MS:
        raise ValueError(f"unsupported candle timeframe: {timeframe!r}")
    return token


def candle_store_path(private_root: str | Path) -> Path:
    root = resolve_private_root(private_root)
    return root / "market_data" / "candles.sqlite3"


class CandleStore:
    """SQLite-backed canonical candle library under the private research root."""

    def __init__(
        self,
        private_root: str | Path,
        *,
        max_read_rows: int = DEFAULT_MAX_READ_ROWS,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.private_root = resolve_private_root(private_root)
        self.path = candle_store_path(self.private_root)
        self.max_read_rows = max(1, int(max_read_rows))
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def storage_usage_bytes(self) -> int:
        """Database + WAL/SHM footprint; never scans unrelated private artifacts."""
        return sum(
            path.stat().st_size for path in (
                self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm"),
            ) if path.is_file()
        )

    def budget_status(self) -> dict[str, Any]:
        used = self.storage_usage_bytes()
        return {
            "bytes": used,
            "warning_bytes": WARNING_BUDGET_BYTES,
            "hard_bytes": HARD_BUDGET_BYTES,
            "status": "hard_limit" if used >= HARD_BUDGET_BYTES else (
                "warning" if used >= WARNING_BUDGET_BYTES else "ok"
            ),
        }

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect(write=True) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS instruments (
                    inst_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL DEFAULT '',
                    inst_type TEXT NOT NULL DEFAULT '',
                    settle_ccy TEXT NOT NULL DEFAULT '',
                    list_time_ms INTEGER,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    vol REAL NOT NULL,
                    date TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    funding REAL,
                    oi REAL,
                    index_px REAL,
                    obi_top5 REAL,
                    spread_bps REAL,
                    trade_delta_100 REAL,
                    extra_json TEXT NOT NULL DEFAULT '{}',
                    ingested_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (symbol, timeframe, ts),
                    CHECK (high >= low),
                    CHECK (vol >= 0)
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS idx_candles_time
                    ON candles(timeframe, ts);
                CREATE TABLE IF NOT EXISTS series (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    first_ts INTEGER,
                    last_ts INTEGER,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    gap_count INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT '',
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (symbol, timeframe)
                ) WITHOUT ROWID;
                """
            )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def upsert_instrument(
        self,
        inst_id: str,
        *,
        state: str = "",
        inst_type: str = "",
        settle_ccy: str = "",
        list_time_ms: int | None = None,
        updated_at_ms: int | None = None,
    ) -> None:
        self.upsert_instruments([{
            "inst_id": inst_id,
            "state": state,
            "inst_type": inst_type,
            "settle_ccy": settle_ccy,
            "list_time_ms": list_time_ms,
        }], updated_at_ms=updated_at_ms)

    def upsert_instruments(
        self,
        instruments: Iterable[dict[str, Any]],
        *,
        updated_at_ms: int | None = None,
    ) -> int:
        self.initialize()
        now = int(updated_at_ms if updated_at_ms is not None else time.time() * 1000)
        rows: list[tuple[Any, ...]] = []
        for instrument in instruments:
            inst_id = instrument.get("inst_id") or instrument.get("instId")
            if not inst_id:
                continue
            canonical = normalize_symbol(str(inst_id)).replace("_", "-")
            list_time = instrument.get("list_time_ms", instrument.get("listTime"))
            try:
                parsed_list_time = int(list_time) if list_time not in (None, "") else None
            except (TypeError, ValueError):
                parsed_list_time = None
            rows.append((
                canonical,
                str(instrument.get("state") or ""),
                str(instrument.get("inst_type") or instrument.get("instType") or ""),
                str(instrument.get("settle_ccy") or instrument.get("settleCcy") or ""),
                parsed_list_time,
                now,
            ))
        if not rows:
            return 0
        with self._connect(write=True) as conn:
            conn.executemany(
                """INSERT INTO instruments
                   (inst_id, state, inst_type, settle_ccy, list_time_ms, updated_at_ms)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(inst_id) DO UPDATE SET
                     state=excluded.state, inst_type=excluded.inst_type,
                     settle_ccy=excluded.settle_ccy, list_time_ms=excluded.list_time_ms,
                     updated_at_ms=excluded.updated_at_ms""",
                rows,
            )
        return len(rows)

    def instrument(self, inst_id: str) -> dict[str, Any] | None:
        if not self.exists:
            return None
        canonical = normalize_symbol(inst_id).replace("_", "-")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT inst_id, state, inst_type, settle_ccy, list_time_ms, updated_at_ms "
                "FROM instruments WHERE inst_id=?",
                (canonical,),
            ).fetchone()
        return dict(row) if row is not None else None

    def upsert_candles(
        self,
        symbol: str,
        timeframe: str,
        rows: Iterable[dict[str, Any]],
        *,
        source: str = "",
        strict: bool = True,
        ingested_at_ms: int | None = None,
    ) -> UpsertResult:
        if self.storage_usage_bytes() >= HARD_BUDGET_BYTES:
            raise CandleStoreError(
                "candle store hard disk budget reached; review retention before adding data"
            )
        sym = normalize_symbol(symbol)
        tf = normalize_timeframe(timeframe)
        now = int(ingested_at_ms if ingested_at_ms is not None else time.time() * 1000)
        normalized: dict[int, tuple[Any, ...]] = {}
        rejected = 0
        for row in rows:
            try:
                clean = _normalize_row(row, source=source, ingested_at_ms=now)
            except CandleValidationError:
                if strict:
                    raise
                rejected += 1
                continue
            normalized[int(clean[0])] = clean

        self.initialize()
        statement = """INSERT INTO candles
            (symbol, timeframe, ts, open, high, low, close, vol, date, source,
             funding, oi, index_px, obi_top5, spread_bps, trade_delta_100,
             extra_json, ingested_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe, ts) DO UPDATE SET
              open=excluded.open, high=excluded.high, low=excluded.low,
              close=excluded.close, vol=excluded.vol, date=excluded.date,
              source=excluded.source,
              funding=COALESCE(excluded.funding, candles.funding),
              oi=COALESCE(excluded.oi, candles.oi),
              index_px=COALESCE(excluded.index_px, candles.index_px),
              obi_top5=COALESCE(excluded.obi_top5, candles.obi_top5),
              spread_bps=COALESCE(excluded.spread_bps, candles.spread_bps),
              trade_delta_100=COALESCE(excluded.trade_delta_100, candles.trade_delta_100),
              extra_json=excluded.extra_json,
              ingested_at_ms=excluded.ingested_at_ms"""
        with self._connect(write=True) as conn:
            if normalized:
                conn.executemany(
                    statement,
                    [(sym, tf, *values) for values in normalized.values()],
                )
            coverage = self._refresh_series(conn, sym, tf, source=str(source), now_ms=now)
        return UpsertResult(
            symbol=sym,
            timeframe=tf,
            accepted=len(normalized),
            inserted_or_updated=len(normalized),
            rejected=rejected,
            coverage=coverage,
        )

    def read(
        self,
        symbol: str,
        timeframe: str,
        start_ts: int,
        end_ts: int,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if not self.exists:
            return []
        sym = normalize_symbol(symbol)
        tf = normalize_timeframe(timeframe)
        start, end = int(start_ts), int(end_ts)
        if end < start:
            raise ValueError("end_ts must be >= start_ts")
        cap = self.max_read_rows if limit is None else int(limit)
        if cap < 1 or cap > self.max_read_rows:
            raise ValueError(f"read limit must be between 1 and {self.max_read_rows}")
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT ts, date, open, high, low, close, vol, source,
                          funding, oi, index_px, obi_top5, spread_bps,
                          trade_delta_100, extra_json
                   FROM candles
                   WHERE symbol=? AND timeframe=? AND ts BETWEEN ? AND ?
                   ORDER BY ts ASC LIMIT ?""",
                (sym, tf, start, end, cap + 1),
            ).fetchall()
        if len(rows) > cap:
            raise CandleStoreError(
                f"requested range exceeds bounded read cap ({cap} rows); split the range"
            )
        return [_row_to_candle(row) for row in rows]

    def coverage(self, symbol: str, timeframe: str) -> Coverage:
        sym = normalize_symbol(symbol)
        tf = normalize_timeframe(timeframe)
        if not self.exists:
            return Coverage(sym, tf, None, None, 0, 0, 0, 0, None)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT first_ts, last_ts, row_count, gap_count, updated_at_ms "
                "FROM series WHERE symbol=? AND timeframe=?",
                (sym, tf),
            ).fetchone()
        if row is None:
            return Coverage(sym, tf, None, None, 0, 0, 0, 0, None)
        return _coverage_from_values(sym, tf, *row)

    def series_summary(self) -> dict[str, Any]:
        if not self.exists:
            return {"available": False, "series": 0, "rows": 0, "gaps": 0, "by_timeframe": {}}
        with self._connect() as conn:
            totals = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(row_count),0), COALESCE(SUM(gap_count),0) FROM series"
            ).fetchone()
            by_timeframe = {
                str(row[0]): {"series": int(row[1]), "rows": int(row[2]), "gaps": int(row[3])}
                for row in conn.execute(
                    "SELECT timeframe, COUNT(*), COALESCE(SUM(row_count),0), "
                    "COALESCE(SUM(gap_count),0) FROM series GROUP BY timeframe ORDER BY timeframe"
                )
            }
        return {
            "available": True,
            "series": int(totals[0]),
            "rows": int(totals[1]),
            "gaps": int(totals[2]),
            "by_timeframe": by_timeframe,
            "budget": self.budget_status(),
        }

    def checkpoint(self, *, truncate: bool = False) -> tuple[int, int, int]:
        """Run an explicit bounded WAL checkpoint; never called on every read."""
        if not self.exists:
            return (0, 0, 0)
        mode = "TRUNCATE" if truncate else "PASSIVE"
        with self._connect(write=True) as conn:
            row = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return tuple(int(value) for value in row)

    def _refresh_series(
        self,
        conn: sqlite3.Connection,
        symbol: str,
        timeframe: str,
        *,
        source: str,
        now_ms: int,
    ) -> Coverage:
        interval = _TIMEFRAME_MS[timeframe]
        row = conn.execute(
            """WITH ordered AS (
                   SELECT ts, LAG(ts) OVER (ORDER BY ts) AS previous_ts
                   FROM candles WHERE symbol=? AND timeframe=?
                 )
                 SELECT MIN(ts), MAX(ts), COUNT(*),
                        COALESCE(SUM(CASE WHEN previous_ts IS NOT NULL
                                          AND ts - previous_ts > ? THEN 1 ELSE 0 END), 0)
                 FROM ordered""",
            (symbol, timeframe, interval),
        ).fetchone()
        first_ts, last_ts, count, gaps = row
        conn.execute(
            """INSERT INTO series
               (symbol, timeframe, first_ts, last_ts, row_count, gap_count, source, updated_at_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, timeframe) DO UPDATE SET
                 first_ts=excluded.first_ts, last_ts=excluded.last_ts,
                 row_count=excluded.row_count, gap_count=excluded.gap_count,
                 source=excluded.source, updated_at_ms=excluded.updated_at_ms""",
            (symbol, timeframe, first_ts, last_ts, int(count), int(gaps), source, now_ms),
        )
        return _coverage_from_values(
            symbol, timeframe, first_ts, last_ts, int(count), int(gaps), now_ms,
        )

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            conn.execute("PRAGMA foreign_keys=ON")
            if write:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA journal_size_limit=33554432")
            yield conn
            if write:
                conn.commit()
        except sqlite3.Error as exc:
            if write and conn is not None:
                conn.rollback()
            raise CandleStoreError("canonical candle database operation failed") from exc
        except Exception:
            if write and conn is not None:
                conn.rollback()
            raise
        finally:
            if conn is not None:
                conn.close()


def _normalize_row(
    row: dict[str, Any],
    *,
    source: str,
    ingested_at_ms: int,
) -> tuple[Any, ...]:
    if not isinstance(row, dict):
        raise CandleValidationError("candle row must be a mapping")
    try:
        ts = int(row["ts"])
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        vol = float(row.get("vol") or 0.0)
    except (KeyError, TypeError, ValueError) as exc:
        raise CandleValidationError("candle has invalid required fields") from exc
    values = (open_, high, low, close, vol)
    if ts < 0 or not all(math.isfinite(value) for value in values):
        raise CandleValidationError("candle contains a negative timestamp or non-finite number")
    if vol < 0:
        raise CandleValidationError("candle volume must be non-negative")
    if high < max(open_, low, close) or low > min(open_, high, close):
        raise CandleValidationError("candle OHLC geometry is impossible")
    confirm = str(row.get("confirm", "1")).strip().lower()
    if confirm in {"0", "false", "no"}:
        raise CandleValidationError("unconfirmed candle is not durable market data")
    date = str(row.get("date") or dt.datetime.fromtimestamp(
        ts / 1000, tz=dt.timezone.utc,
    ).isoformat())
    optional: list[float | None] = []
    for field in _OPTIONAL_FIELDS:
        value = row.get(field)
        if value is None:
            optional.append(None)
            continue
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise CandleValidationError(f"invalid optional candle field: {field}") from exc
        if not math.isfinite(number):
            raise CandleValidationError(f"non-finite optional candle field: {field}")
        optional.append(number)
    known = {"ts", "date", "open", "high", "low", "close", "vol", "confirm", *_OPTIONAL_FIELDS}
    extras = {str(key): value for key, value in row.items() if key not in known}
    try:
        extra_json = json.dumps(extras, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise CandleValidationError("candle extra fields are not JSON-serializable") from exc
    return (
        ts, open_, high, low, close, vol, date, str(source),
        *optional, extra_json, int(ingested_at_ms),
    )


def _row_to_candle(row: sqlite3.Row) -> dict[str, Any]:
    candle: dict[str, Any] = {
        "ts": int(row["ts"]),
        "date": str(row["date"]),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "vol": float(row["vol"]),
    }
    for field in _OPTIONAL_FIELDS:
        if row[field] is not None:
            candle[field] = float(row[field])
    try:
        extras = json.loads(row["extra_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        extras = {}
    if isinstance(extras, dict):
        candle.update({key: value for key, value in extras.items() if key not in candle})
    return candle


def _coverage_from_values(
    symbol: str,
    timeframe: str,
    first_ts: int | None,
    last_ts: int | None,
    row_count: int,
    gap_count: int,
    updated_at_ms: int | None,
) -> Coverage:
    expected = 0
    if first_ts is not None and last_ts is not None:
        expected = (int(last_ts) - int(first_ts)) // _TIMEFRAME_MS[timeframe] + 1
    missing = max(0, expected - int(row_count))
    return Coverage(
        symbol=symbol,
        timeframe=timeframe,
        first_ts=int(first_ts) if first_ts is not None else None,
        last_ts=int(last_ts) if last_ts is not None else None,
        row_count=int(row_count),
        gap_count=int(gap_count),
        expected_rows=int(expected),
        missing_rows=int(missing),
        updated_at_ms=int(updated_at_ms) if updated_at_ms is not None else None,
    )
