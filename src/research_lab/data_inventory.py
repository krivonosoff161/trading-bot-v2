# -*- coding: utf-8 -*-
"""Data inventory builder for the strategy lab.

Scans local candle JSON files and reports what is actually usable before any
experiment is queued. Output rows never contain absolute private paths.
"""

from __future__ import annotations

import csv
import datetime as dt
import glob
import json
import re
from pathlib import Path
from typing import Any

MIN_USABLE_ROWS = 60
OHLCV_FIELDS = ("open", "high", "low", "close", "vol")
_TIMEFRAME_BUCKETS = [
    (60_000, "1m"),
    (300_000, "5m"),
    (900_000, "15m"),
    (3_600_000, "1H"),
    (14_400_000, "4H"),
    (86_400_000, "1D"),
]
_SYMBOL_RE = re.compile(r"^([A-Z0-9]+(?:_[A-Z0-9]+)*?_SWAP)", re.IGNORECASE)


def file_label(path: Path) -> str:
    """Public-safe label: file name only, never the absolute path."""
    return path.name


def infer_symbol(path: Path) -> str:
    match = _SYMBOL_RE.match(path.stem)
    return match.group(1) if match else path.stem


def infer_timeframe(ts_values: list[int]) -> str:
    if len(ts_values) < 3:
        return "unknown"
    deltas = sorted(b - a for a, b in zip(ts_values, ts_values[1:]) if b > a)
    if not deltas:
        return "unknown"
    median = deltas[len(deltas) // 2]
    for bucket_ms, label in _TIMEFRAME_BUCKETS:
        if abs(median - bucket_ms) <= bucket_ms * 0.05:
            return label
    return "unknown"


def inspect_file(path: Path) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": infer_symbol(path),
        "source_file_label": file_label(path),
        "rows": 0,
        "start_ts": None,
        "end_ts": None,
        "start_date": "",
        "end_date": "",
        "timeframe": "unknown",
        "has_ohlcv": False,
        "quality_status": "malformed",
        "notes": "",
    }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        row["notes"] = "unreadable_or_invalid_json"
        return row
    if not isinstance(data, list) or not data:
        row["notes"] = "not_a_candle_list"
        return row
    candles = [r for r in data if isinstance(r, dict) and _looks_like_candle(r)]
    row["rows"] = len(candles)
    if not candles:
        row["notes"] = "missing_ohlcv_fields"
        return row
    if len(candles) < len(data):
        row["notes"] = f"skipped_{len(data) - len(candles)}_bad_rows"
    ts_values = sorted(int(r["ts"]) for r in candles)
    row.update(
        start_ts=ts_values[0],
        end_ts=ts_values[-1],
        start_date=str(candles[0].get("date") or ""),
        end_date=str(candles[-1].get("date") or ""),
        timeframe=infer_timeframe(ts_values),
        has_ohlcv=True,
        quality_status="usable" if len(candles) >= MIN_USABLE_ROWS else "too_short",
    )
    return row


def _looks_like_candle(row: dict[str, Any]) -> bool:
    if "ts" not in row:
        return False
    try:
        int(row["ts"])
        for field in OHLCV_FIELDS:
            float(row[field])
    except (KeyError, TypeError, ValueError):
        return False
    return True


def build_inventory(data_glob: str, symbols: list[str] | None = None) -> dict[str, Any]:
    """Scan the glob and return a public-safe inventory payload.

    `data_glob` may contain a `{symbol}` placeholder; it is widened to `*` so
    the scan covers every file the experiment loader could possibly pick.
    """
    pattern = data_glob.replace("{symbol}", "*")
    paths = sorted(Path(p) for p in glob.glob(pattern))
    files = [inspect_file(p) for p in paths]
    rows = files + _missing_symbol_rows(files, symbols or [])
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["quality_status"]] = counts.get(row["quality_status"], 0) + 1
    return {
        "schema": "strategy_lab_data_inventory.v1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "glob_label": _glob_label(data_glob),
        "file_count": len(files),
        "quality_counts": counts,
        "rows": rows,
    }


def _glob_label(data_glob: str) -> str:
    normalized = data_glob.replace("\\", "/")
    marker = "scripts/analysis/research/"
    if marker in normalized:
        return marker + normalized.rsplit(marker, 1)[-1]
    return Path(normalized).name or "<glob>"


def _missing_symbol_rows(files: list[dict[str, Any]], symbols: list[str]) -> list[dict[str, Any]]:
    usable = {r["symbol"].upper() for r in files if r["quality_status"] == "usable"}
    missing = []
    for symbol in symbols:
        normalized = symbol.replace("-", "_").replace("/", "_").upper()
        if normalized in usable:
            continue
        missing.append(
            {
                "symbol": normalized,
                "source_file_label": "",
                "rows": 0,
                "start_ts": None,
                "end_ts": None,
                "start_date": "",
                "end_date": "",
                "timeframe": "unknown",
                "has_ohlcv": False,
                "quality_status": "missing",
                "notes": "requested_by_spec_no_usable_file",
            }
        )
    return missing


def write_inventory(inventory: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "data_inventory.json"
    json_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "symbol", "source_file_label", "rows", "start_ts", "end_ts",
        "start_date", "end_date", "timeframe", "has_ohlcv", "quality_status", "notes",
    ]
    with (out_dir / "data_inventory.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in inventory["rows"]:
            writer.writerow({k: row.get(k) for k in fields})
    return json_path
