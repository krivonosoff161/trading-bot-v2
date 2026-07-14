# -*- coding: utf-8 -*-
"""Canonical content identity for a bounded candle slice."""

from __future__ import annotations

import hashlib
import json
from typing import Any

IDENTITY_FIELDS = (
    "ts", "open", "high", "low", "close", "vol", "funding", "oi",
    "index_px", "obi_top5", "spread_bps", "trade_delta_100",
)


def candle_slice_fingerprint(
    symbol: str, timeframe: str, rows: list[dict[str, Any]],
) -> str | None:
    if not rows:
        return None
    digest = hashlib.sha256()
    header = {
        "symbol": str(symbol).replace("-", "_").upper(),
        "timeframe": str(timeframe).lower(),
        "rows": len(rows),
    }
    digest.update(json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest.update(b"\n")
    for row in rows:
        digest.update(json.dumps(
            [row.get(field) for field in IDENTITY_FIELDS],
            separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()[:20]


def candle_ohlcv_fingerprint(rows: list[dict[str, Any]]) -> str | None:
    """Content parity independent of optional later enrichment fields."""
    if not rows:
        return None
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(
            [row.get(field) for field in ("ts", "open", "high", "low", "close", "vol")],
            separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()[:20]
