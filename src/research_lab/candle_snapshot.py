# -*- coding: utf-8 -*-
"""Point-in-time candle selection evidence shared by every storage backend.

The manifest deliberately excludes the physical backend from its identity.  A
snapshot is identified by the selected content, revision/provenance references,
request boundary and policy, not by whether those bytes came from SQLite or a
retained JSON slice.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from src.research_lab.candle_identity import (
    candle_evidence_fingerprint,
    candle_revision_id,
    candle_row_content_hash,
    candle_slice_fingerprint,
    legacy_revision_ref,
)
SCHEMA = "CandleSnapshotManifest.v2"
SELECTION_POLICY = "latest_available_at_or_before.v1"
SUPPORTED_COVERAGE_POLICIES = frozenset({"available", "gap_free", "complete_range"})

_TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


def _normalize_symbol(symbol: str) -> str:
    token = str(symbol).strip().upper().replace("-", "_").replace("/", "_")
    if not token or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in token):
        raise ValueError(f"unsupported candle symbol: {symbol!r}")
    return token


def _normalize_timeframe(timeframe: str) -> str:
    token = str(timeframe).strip().lower()
    if token not in _TIMEFRAME_MS:
        raise ValueError(f"unsupported candle timeframe: {timeframe!r}")
    return token


@dataclass(frozen=True)
class CandleSnapshotManifest:
    snapshot_id: str
    symbol: str
    timeframe: str
    start_ts: int | None
    end_ts: int | None
    as_of_ms: int | None
    purpose: str
    coverage_policy: str
    selection_policy: str
    content_hash: str
    evidence_hash: str
    revision_ids: tuple[str, ...]
    row_count: int
    first_ts: int | None
    last_ts: int | None
    expected_rows: int
    missing_rows: int
    gap_count: int
    coverage_status: str
    provenance_status: str
    max_available_at_ms: int | None
    source_backend: str
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["revision_ids"] = list(self.revision_ids)
        return data


@dataclass(frozen=True)
class CandleSnapshot:
    rows: list[dict[str, Any]]
    manifest: CandleSnapshotManifest


def _gap_count(rows: list[dict[str, Any]], timeframe: str) -> int:
    interval = _TIMEFRAME_MS[timeframe]
    stamps = [int(row["ts"]) for row in rows]
    return sum(1 for left, right in zip(stamps, stamps[1:]) if right - left > interval)


def _coverage(
    rows: list[dict[str, Any]], timeframe: str, start_ts: int | None, end_ts: int | None,
    coverage_policy: str,
) -> tuple[int, int, int, str]:
    if not rows:
        return 0, 0, 0, "missing"
    interval = _TIMEFRAME_MS[timeframe]
    first_ts = int(rows[0]["ts"])
    last_ts = int(rows[-1]["ts"])
    gaps = _gap_count(rows, timeframe)
    requested_start = first_ts if start_ts is None else int(start_ts)
    requested_end = last_ts if end_ts is None else int(end_ts)
    expected = max(0, (requested_end - requested_start) // interval + 1)
    in_range = sum(1 for row in rows if requested_start <= int(row["ts"]) <= requested_end)
    missing = max(0, expected - in_range)
    if coverage_policy == "available":
        status = "available"
    elif coverage_policy == "gap_free":
        status = "complete" if gaps == 0 else "incomplete"
    else:
        complete = (
            first_ts <= requested_start
            and last_ts >= requested_end
            and gaps == 0
            and in_range == expected
        )
        status = "complete" if complete else "incomplete"
    return expected, missing, gaps, status


def build_snapshot_manifest(
    *,
    symbol: str,
    timeframe: str,
    rows: list[dict[str, Any]],
    start_ts: int | None,
    end_ts: int | None,
    as_of_ms: int | None,
    purpose: str,
    coverage_policy: str,
    source_backend: str,
) -> CandleSnapshotManifest:
    sym = _normalize_symbol(symbol)
    tf = _normalize_timeframe(timeframe)
    purpose_token = str(purpose or "unspecified").strip().lower()
    policy = str(coverage_policy or "available").strip().lower()
    if policy not in SUPPORTED_COVERAGE_POLICIES:
        raise ValueError(f"unsupported candle coverage policy: {coverage_policy!r}")
    selected = sorted((dict(row) for row in rows), key=lambda row: int(row["ts"]))
    revision_ids = tuple(
        str(row.get("_revision_id") or legacy_revision_ref(sym, tf, row))
        for row in selected
    )
    availability = [
        int(row["_available_at_ms"])
        for row in selected
        if row.get("_available_at_ms") is not None
    ]
    lineage_present = any(
        row.get("_revision_id") or row.get("_content_hash")
        or row.get("_available_at_ms") is not None
        for row in selected
    )
    provenance_complete = bool(selected) and all(
        _row_provenance_is_valid(
            row, symbol=sym, timeframe=tf, as_of_ms=as_of_ms,
        )
        for row in selected
    )
    provenance_status = (
        "complete" if provenance_complete else (
            "invalid" if lineage_present else "legacy_unknown"
        )
    )
    content_hash = candle_slice_fingerprint(sym, tf, selected) or ""
    evidence_hash = candle_evidence_fingerprint(sym, tf, selected) or ""
    expected, missing, gaps, coverage_status = _coverage(
        selected, tf, start_ts, end_ts, policy,
    )
    first_ts = int(selected[0]["ts"]) if selected else None
    last_ts = int(selected[-1]["ts"]) if selected else None
    identity = {
        "schema": SCHEMA,
        "symbol": sym,
        "timeframe": tf,
        "start_ts": int(start_ts) if start_ts is not None else None,
        "end_ts": int(end_ts) if end_ts is not None else None,
        "as_of_ms": int(as_of_ms) if as_of_ms is not None else None,
        "purpose": purpose_token,
        "coverage_policy": policy,
        "selection_policy": SELECTION_POLICY,
        "content_hash": content_hash,
        "evidence_hash": evidence_hash,
        "revision_ids": revision_ids,
        "coverage_status": coverage_status,
        "provenance_status": provenance_status,
    }
    blob = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    snapshot_id = f"csm_{hashlib.sha256(blob.encode('utf-8')).hexdigest()}"
    return CandleSnapshotManifest(
        snapshot_id=snapshot_id,
        symbol=sym,
        timeframe=tf,
        start_ts=int(start_ts) if start_ts is not None else None,
        end_ts=int(end_ts) if end_ts is not None else None,
        as_of_ms=int(as_of_ms) if as_of_ms is not None else None,
        purpose=purpose_token,
        coverage_policy=policy,
        selection_policy=SELECTION_POLICY,
        content_hash=content_hash,
        evidence_hash=evidence_hash,
        revision_ids=revision_ids,
        row_count=len(selected),
        first_ts=first_ts,
        last_ts=last_ts,
        expected_rows=expected,
        missing_rows=missing,
        gap_count=gaps,
        coverage_status=coverage_status,
        provenance_status=provenance_status,
        max_available_at_ms=max(availability) if availability else None,
        source_backend=str(source_backend),
    )


def _row_provenance_is_valid(
    row: dict[str, Any], *, symbol: str, timeframe: str, as_of_ms: int | None,
) -> bool:
    revision_id = str(row.get("_revision_id") or "")
    stored_hash = str(row.get("_content_hash") or "")
    observed = row.get("_observed_at_ms")
    available = row.get("_available_at_ms")
    acquired = row.get("_acquired_at_ms")
    field_provenance = row.get("_field_provenance")
    if (
        not revision_id or not stored_hash or observed is None
        or available is None or acquired is None
        or not isinstance(field_provenance, dict)
    ):
        return False
    try:
        observed_ms = int(observed)
        available_ms = int(available)
        acquired_ms = int(acquired)
    except (TypeError, ValueError):
        return False
    if min(observed_ms, available_ms, acquired_ms) < 0:
        return False
    if not observed_ms <= available_ms <= acquired_ms:
        return False
    if as_of_ms is not None and available_ms > int(as_of_ms):
        return False
    actual_hash = candle_row_content_hash(row)
    if stored_hash != actual_hash:
        return False
    source = str(row.get("_source") or "")
    expected_revision_id = candle_revision_id(
        symbol, timeframe, int(row["ts"]), actual_hash, source,
        observed_ms, available_ms,
    )
    if revision_id != expected_revision_id:
        return False
    public_fields = [str(key) for key in row if not str(key).startswith("_")]
    if set(field_provenance) != set(public_fields):
        return False
    for field in public_fields:
        ref = field_provenance.get(field)
        if not isinstance(ref, dict):
            return False
        if str(ref.get("revision_id") or "") != revision_id:
            return False
        if str(ref.get("source") or "") != source:
            return False
        try:
            if int(ref.get("available_at_ms")) != available_ms:
                return False
            if int(ref.get("observed_at_ms")) != observed_ms:
                return False
        except (TypeError, ValueError):
            return False
    return True


def build_snapshot(**kwargs: Any) -> CandleSnapshot:
    rows = sorted(
        (dict(row) for row in kwargs.pop("rows")), key=lambda row: int(row["ts"]),
    )
    manifest = build_snapshot_manifest(rows=rows, **kwargs)
    for row in rows:
        row["_snapshot_id"] = manifest.snapshot_id
        row["_snapshot_evidence_hash"] = manifest.evidence_hash
        row["_snapshot_provenance_status"] = manifest.provenance_status
    return CandleSnapshot(rows, manifest)
