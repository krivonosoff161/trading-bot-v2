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

from src.research_lab.candle_identity import candle_revision_id, candle_row_content_hash
from src.research_lab.paths import resolve_private_root

SCHEMA_VERSION = 2
DEFAULT_MAX_READ_ROWS = 50_000
WARNING_BUDGET_BYTES = 1_500_000_000
HARD_BUDGET_BYTES = 2_000_000_000
_OPTIONAL_FIELDS = (
    "funding",
    "oi",
    "index_px",
    "obi_top5",
    "spread_bps",
    "trade_delta_100",
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
    if not token or any(
        ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in token
    ):
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
            path.stat().st_size
            for path in (
                self.path,
                Path(f"{self.path}-wal"),
                Path(f"{self.path}-shm"),
            )
            if path.is_file()
        )

    def budget_status(self) -> dict[str, Any]:
        used = self.storage_usage_bytes()
        return {
            "bytes": used,
            "warning_bytes": WARNING_BUDGET_BYTES,
            "hard_bytes": HARD_BUDGET_BYTES,
            "status": "hard_limit"
            if used >= HARD_BUDGET_BYTES
            else ("warning" if used >= WARNING_BUDGET_BYTES else "ok"),
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
                CREATE TABLE IF NOT EXISTS candle_revisions (
                    revision_id TEXT PRIMARY KEY,
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
                    content_hash TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL,
                    available_at_ms INTEGER NOT NULL,
                    acquired_at_ms INTEGER NOT NULL,
                    acquisition_order INTEGER NOT NULL,
                    parent_revision_id TEXT,
                    field_provenance_json TEXT NOT NULL,
                    CHECK (high >= low),
                    CHECK (vol >= 0),
                    CHECK (observed_at_ms <= available_at_ms),
                    CHECK (available_at_ms <= acquired_at_ms)
                );
                CREATE INDEX IF NOT EXISTS idx_candle_revisions_asof
                    ON candle_revisions(
                        symbol, timeframe, ts, available_at_ms,
                        acquired_at_ms, acquisition_order
                    );
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
        self.upsert_instruments(
            [
                {
                    "inst_id": inst_id,
                    "state": state,
                    "inst_type": inst_type,
                    "settle_ccy": settle_ccy,
                    "list_time_ms": list_time_ms,
                }
            ],
            updated_at_ms=updated_at_ms,
        )

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
                parsed_list_time = (
                    int(list_time)
                    if isinstance(list_time, (str, bytes, bytearray, int, float))
                    and list_time != ""
                    else None
                )
            except (TypeError, ValueError):
                parsed_list_time = None
            rows.append(
                (
                    canonical,
                    str(instrument.get("state") or ""),
                    str(
                        instrument.get("inst_type") or instrument.get("instType") or ""
                    ),
                    str(
                        instrument.get("settle_ccy")
                        or instrument.get("settleCcy")
                        or ""
                    ),
                    parsed_list_time,
                    now,
                )
            )
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
        observed_at_ms: int | None = None,
        available_at_ms: int | None = None,
        acquired_at_ms: int | None = None,
    ) -> UpsertResult:
        if self.storage_usage_bytes() >= HARD_BUDGET_BYTES:
            raise CandleStoreError(
                "candle store hard disk budget reached; review retention before adding data"
            )
        sym = normalize_symbol(symbol)
        tf = normalize_timeframe(timeframe)
        if ingested_at_ms is not None and acquired_at_ms is not None:
            if int(ingested_at_ms) != int(acquired_at_ms):
                raise ValueError("ingested_at_ms and acquired_at_ms disagree")
        now = int(
            acquired_at_ms
            if acquired_at_ms is not None
            else (ingested_at_ms if ingested_at_ms is not None else time.time() * 1000)
        )
        default_available = int(available_at_ms if available_at_ms is not None else now)
        default_observed = int(
            observed_at_ms if observed_at_ms is not None else default_available
        )
        _validate_provenance_times(default_observed, default_available, now)
        normalized: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        accepted_ts: set[int] = set()
        rejected = 0
        for order, row in enumerate(rows):
            try:
                row_acquired = _row_time(row, "acquired_at_ms", now)
                row_available = _row_time(row, "available_at_ms", default_available)
                row_observed = _row_time(row, "observed_at_ms", default_observed)
                _validate_provenance_times(row_observed, row_available, row_acquired)
                row_source = str(row.get("_source") or row.get("source") or source)
                clean = _normalize_row(
                    row, source=row_source, ingested_at_ms=row_acquired
                )
            except CandleValidationError:
                if strict:
                    raise
                rejected += 1
                continue
            content = _clean_to_candle(clean)
            content_hash = candle_row_content_hash(content)
            revision_id = candle_revision_id(
                sym,
                tf,
                int(clean[0]),
                content_hash,
                row_source,
                row_observed,
                row_available,
            )
            field_provenance = _field_provenance(
                content,
                revision_id=revision_id,
                source=row_source,
                observed_at_ms=row_observed,
                available_at_ms=row_available,
            )
            normalized.append(
                (
                    clean,
                    {
                        "revision_id": revision_id,
                        "content_hash": content_hash,
                        "observed_at_ms": row_observed,
                        "available_at_ms": row_available,
                        "acquired_at_ms": row_acquired,
                        "acquisition_order": order,
                        "field_provenance_json": json.dumps(
                            field_provenance,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                )
            )
            accepted_ts.add(int(clean[0]))

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
              funding=excluded.funding,
              oi=excluded.oi,
              index_px=excluded.index_px,
              obi_top5=excluded.obi_top5,
              spread_bps=excluded.spread_bps,
              trade_delta_100=excluded.trade_delta_100,
              extra_json=excluded.extra_json,
              ingested_at_ms=excluded.ingested_at_ms"""
        with self._connect(write=True) as conn:
            if normalized:
                for clean, meta in normalized:
                    parent = conn.execute(
                        """SELECT revision_id FROM candle_revisions
                           WHERE symbol=? AND timeframe=? AND ts=?
                           ORDER BY available_at_ms DESC, acquired_at_ms DESC,
                                    acquisition_order DESC, revision_id DESC
                           LIMIT 1""",
                        (sym, tf, int(clean[0])),
                    ).fetchone()
                    conn.execute(
                        """INSERT OR IGNORE INTO candle_revisions
                           (revision_id, symbol, timeframe, ts, open, high, low, close,
                            vol, date, source, funding, oi, index_px, obi_top5,
                            spread_bps, trade_delta_100, extra_json, content_hash,
                            observed_at_ms, available_at_ms, acquired_at_ms,
                            acquisition_order, parent_revision_id, field_provenance_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                   ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            meta["revision_id"],
                            sym,
                            tf,
                            *clean[:-1],
                            meta["content_hash"],
                            meta["observed_at_ms"],
                            meta["available_at_ms"],
                            meta["acquired_at_ms"],
                            meta["acquisition_order"],
                            str(parent[0]) if parent is not None else None,
                            meta["field_provenance_json"],
                        ),
                    )
                for ts in sorted(accepted_ts):
                    latest = conn.execute(
                        """SELECT ts, open, high, low, close, vol, date, source,
                                  funding, oi, index_px, obi_top5, spread_bps,
                                  trade_delta_100, extra_json, acquired_at_ms
                           FROM candle_revisions
                           WHERE symbol=? AND timeframe=? AND ts=?
                           ORDER BY available_at_ms DESC, acquired_at_ms DESC,
                                    acquisition_order DESC, revision_id DESC
                           LIMIT 1""",
                        (sym, tf, ts),
                    ).fetchone()
                    if latest is not None:
                        conn.execute(statement, (sym, tf, *tuple(latest)))
            coverage = self._refresh_series(
                conn, sym, tf, source=str(source), now_ms=now
            )
        return UpsertResult(
            symbol=sym,
            timeframe=tf,
            accepted=len(accepted_ts),
            inserted_or_updated=len(accepted_ts),
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
        reader_version: str = "v2",
    ) -> list[dict[str, Any]]:
        version = str(reader_version).strip().lower()
        if version not in {"v1", "v2"}:
            raise ValueError(f"unsupported candle reader version: {reader_version!r}")
        if version == "v2":
            return self.read_snapshot(
                symbol,
                timeframe,
                start_ts,
                end_ts,
                as_of_ms=None,
                purpose="compat_read",
                coverage_policy="available",
                limit=limit,
            ).rows
        return self._read_v1(symbol, timeframe, start_ts, end_ts, limit=limit)

    def _read_v1(
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

    def read_snapshot(
        self,
        symbol: str,
        timeframe: str,
        start_ts: int,
        end_ts: int,
        *,
        as_of_ms: int | None,
        purpose: str,
        coverage_policy: str,
        limit: int | None = None,
    ):
        from src.research_lab.candle_snapshot import build_snapshot

        sym = normalize_symbol(symbol)
        tf = normalize_timeframe(timeframe)
        start, end = int(start_ts), int(end_ts)
        if end < start:
            raise ValueError("end_ts must be >= start_ts")
        cap = self.max_read_rows if limit is None else int(limit)
        if cap < 1 or cap > self.max_read_rows:
            raise ValueError(f"read limit must be between 1 and {self.max_read_rows}")
        rows: list[dict[str, Any]] = []
        if self.exists:
            with self._connect() as conn:
                has_v2 = (
                    conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='candle_revisions'"
                    ).fetchone()
                    is not None
                )
                if has_v2:
                    boundary = (
                        int(as_of_ms)
                        if as_of_ms is not None
                        else 9_223_372_036_854_775_807
                    )
                    selected = conn.execute(
                        """WITH ranked AS (
                               SELECT *, ROW_NUMBER() OVER (
                                   PARTITION BY ts
                                   ORDER BY available_at_ms DESC, acquired_at_ms DESC,
                                            acquisition_order DESC, revision_id DESC
                               ) AS revision_rank
                               FROM candle_revisions
                               WHERE symbol=? AND timeframe=? AND ts BETWEEN ? AND ?
                                 AND available_at_ms <= ?
                           )
                           SELECT ts, date, open, high, low, close, vol, source,
                                  funding, oi, index_px, obi_top5, spread_bps,
                                  trade_delta_100, extra_json, revision_id, content_hash,
                                  observed_at_ms, available_at_ms, acquired_at_ms,
                                  parent_revision_id, field_provenance_json
                           FROM ranked WHERE revision_rank=1
                           ORDER BY ts ASC LIMIT ?""",
                        (sym, tf, start, end, boundary, cap + 1),
                    ).fetchall()
                    if len(selected) > cap:
                        raise CandleStoreError(
                            f"requested range exceeds bounded read cap ({cap} rows); split the range"
                        )
                    rows = [_row_to_candle(row) for row in selected]
        if as_of_ms is None:
            legacy_rows = self._read_v1(sym, tf, start, end, limit=limit)
            by_ts: dict[int, dict[str, Any]] = {}
            for row in legacy_rows:
                row["_provenance_status"] = "legacy_unknown"
                by_ts[int(row["ts"])] = row
            for row in rows:
                by_ts[int(row["ts"])] = row
            rows = [by_ts[ts] for ts in sorted(by_ts)]
        return build_snapshot(
            symbol=sym,
            timeframe=tf,
            rows=rows,
            start_ts=start,
            end_ts=end,
            as_of_ms=as_of_ms,
            purpose=purpose,
            coverage_policy=coverage_policy,
            source_backend="sqlite",
        )

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
            return {
                "available": False,
                "series": 0,
                "rows": 0,
                "gaps": 0,
                "by_timeframe": {},
            }
        with self._connect() as conn:
            totals = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(row_count),0), COALESCE(SUM(gap_count),0) FROM series"
            ).fetchone()
            by_timeframe = {
                str(row[0]): {
                    "series": int(row[1]),
                    "rows": int(row[2]),
                    "gaps": int(row[3]),
                }
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
        if row is None or len(row) != 3:
            raise CandleStoreError("unexpected WAL checkpoint response")
        return int(row[0]), int(row[1]), int(row[2])

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
            (
                symbol,
                timeframe,
                first_ts,
                last_ts,
                int(count),
                int(gaps),
                source,
                now_ms,
            ),
        )
        return _coverage_from_values(
            symbol,
            timeframe,
            first_ts,
            last_ts,
            int(count),
            int(gaps),
            now_ms,
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
            raise CandleStoreError(
                "canonical candle database operation failed"
            ) from exc
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
        raise CandleValidationError(
            "candle contains a negative timestamp or non-finite number"
        )
    if vol < 0:
        raise CandleValidationError("candle volume must be non-negative")
    if high < max(open_, low, close) or low > min(open_, high, close):
        raise CandleValidationError("candle OHLC geometry is impossible")
    confirm = str(row.get("confirm", "1")).strip().lower()
    if confirm in {"0", "false", "no"}:
        raise CandleValidationError("unconfirmed candle is not durable market data")
    date = str(
        row.get("date")
        or dt.datetime.fromtimestamp(
            ts / 1000,
            tz=dt.timezone.utc,
        ).isoformat()
    )
    optional: list[float | None] = []
    for field in _OPTIONAL_FIELDS:
        value = row.get(field)
        if value is None:
            optional.append(None)
            continue
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise CandleValidationError(
                f"invalid optional candle field: {field}"
            ) from exc
        if not math.isfinite(number):
            raise CandleValidationError(f"non-finite optional candle field: {field}")
        optional.append(number)
    known = {
        "ts",
        "date",
        "open",
        "high",
        "low",
        "close",
        "vol",
        "confirm",
        "source",
        "observed_at_ms",
        "available_at_ms",
        "acquired_at_ms",
        "ingested_at_ms",
        *_OPTIONAL_FIELDS,
    }
    extras = {
        str(key): value
        for key, value in row.items()
        if key not in known and not str(key).startswith("_")
    }
    try:
        extra_json = json.dumps(
            extras, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise CandleValidationError(
            "candle extra fields are not JSON-serializable"
        ) from exc
    return (
        ts,
        open_,
        high,
        low,
        close,
        vol,
        date,
        str(source),
        *optional,
        extra_json,
        int(ingested_at_ms),
    )


def _row_time(row: dict[str, Any], field: str, default: int) -> int:
    value = row.get(f"_{field}", row.get(field, default))
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CandleValidationError(
            f"invalid candle provenance field: {field}"
        ) from exc


def _validate_provenance_times(observed: int, available: int, acquired: int) -> None:
    if min(observed, available, acquired) < 0:
        raise CandleValidationError("candle provenance timestamps must be non-negative")
    if observed > available:
        raise CandleValidationError("observed_at_ms must be <= available_at_ms")
    if available > acquired:
        raise CandleValidationError("available_at_ms must be <= acquired_at_ms")


def _clean_to_candle(clean: tuple[Any, ...]) -> dict[str, Any]:
    row = {
        "ts": int(clean[0]),
        "date": str(clean[6]),
        "open": float(clean[1]),
        "high": float(clean[2]),
        "low": float(clean[3]),
        "close": float(clean[4]),
        "vol": float(clean[5]),
    }
    for field, value in zip(_OPTIONAL_FIELDS, clean[8:14]):
        if value is not None:
            row[field] = float(value)
    try:
        extras = json.loads(clean[14] or "{}")
    except (TypeError, json.JSONDecodeError):
        extras = {}
    if isinstance(extras, dict):
        row.update({key: value for key, value in extras.items() if key not in row})
    return row


def _field_provenance(
    row: dict[str, Any],
    *,
    revision_id: str,
    source: str,
    observed_at_ms: int,
    available_at_ms: int,
) -> dict[str, dict[str, Any]]:
    common = {
        "revision_id": revision_id,
        "source": str(source),
        "observed_at_ms": int(observed_at_ms),
        "available_at_ms": int(available_at_ms),
    }
    return {str(field): dict(common) for field in sorted(row)}


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
        candle.update(
            {key: value for key, value in extras.items() if key not in candle}
        )
    keys = set(row.keys())
    if "source" in keys and row["source"]:
        candle["_source"] = str(row["source"])
    if "revision_id" in keys:
        candle["_revision_id"] = str(row["revision_id"])
        candle["_content_hash"] = str(row["content_hash"])
        candle["_observed_at_ms"] = int(row["observed_at_ms"])
        candle["_available_at_ms"] = int(row["available_at_ms"])
        candle["_acquired_at_ms"] = int(row["acquired_at_ms"])
        candle["_parent_revision_id"] = str(row["parent_revision_id"] or "")
        try:
            provenance = json.loads(row["field_provenance_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            provenance = {}
        candle["_field_provenance"] = provenance if isinstance(provenance, dict) else {}
        candle["_provenance_status"] = "complete"
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
