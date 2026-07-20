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

PROVENANCE_FIELDS = (
    "_revision_id", "_content_hash", "_source", "_observed_at_ms",
    "_available_at_ms", "_field_provenance",
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
        digest.update(candle_row_content_hash(row).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()[:20]


def candle_row_content_hash(row: dict[str, Any]) -> str:
    """Full canonical row content, including public-safe extension fields."""
    derived_or_provenance = {
        "confirm", "date", "source", "observed_at_ms", "available_at_ms",
        "acquired_at_ms", "ingested_at_ms",
    }
    content = {
        str(key): value
        for key, value in row.items()
        if not str(key).startswith("_") and key not in derived_or_provenance
    }
    blob = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def candle_revision_id(
    symbol: str, timeframe: str, ts: int, content_hash: str, source: str,
    observed_at_ms: int, available_at_ms: int,
) -> str:
    payload = {
        "schema": "CandleRevision.v2",
        "symbol": str(symbol).replace("-", "_").upper(),
        "timeframe": str(timeframe).lower(),
        "ts": int(ts),
        "content_hash": str(content_hash),
        "source": str(source),
        "observed_at_ms": int(observed_at_ms),
        "available_at_ms": int(available_at_ms),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"cr_{hashlib.sha256(blob.encode('utf-8')).hexdigest()}"


def legacy_revision_ref(symbol: str, timeframe: str, row: dict[str, Any]) -> str:
    """Explicit non-historical identity for a row with unknown availability."""
    payload = {
        "schema": "LegacyCandleRevisionRef.v1",
        "symbol": str(symbol).replace("-", "_").upper(),
        "timeframe": str(timeframe).lower(),
        "content_hash": candle_row_content_hash(row),
        "provenance_status": str(row.get("_provenance_status") or "legacy_unknown"),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"legacy_{hashlib.sha256(blob.encode('utf-8')).hexdigest()}"


def candle_evidence_fingerprint(
    symbol: str, timeframe: str, rows: list[dict[str, Any]],
) -> str | None:
    """Content plus acquisition/revision evidence, independent of physical backend."""
    if not rows:
        return None
    digest = hashlib.sha256()
    header = {
        "schema": "CandleEvidenceFingerprint.v2",
        "symbol": str(symbol).replace("-", "_").upper(),
        "timeframe": str(timeframe).lower(),
        "rows": len(rows),
    }
    digest.update(json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest.update(b"\n")
    for row in rows:
        evidence = {
            "content_hash": candle_row_content_hash(row),
            "revision_id": str(row.get("_revision_id") or legacy_revision_ref(symbol, timeframe, row)),
            "source": str(row.get("_source") or ""),
            "observed_at_ms": row.get("_observed_at_ms"),
            "available_at_ms": row.get("_available_at_ms"),
            "field_provenance": row.get("_field_provenance") or {},
        }
        digest.update(json.dumps(
            evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
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
