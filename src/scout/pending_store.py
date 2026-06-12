# -*- coding: utf-8 -*-
"""
pending_store.py - append-only store for expected/watch events.

This is the state layer for "будет / произошло":
  - calendar/expected sources open pending items
  - future L6/watch inference can also open pending items
  - realized news can match/close a pending item later

Storage is JSONL on purpose: easy to inspect, replay, and diff.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import json
import os
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = _ROOT / "logs" / "scout"
PENDING = OUT_DIR / "pending_events.jsonl"

STATUS_OPEN = "open"
STATUS_MATCHED = "matched"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"

KIND_CALENDAR = "calendar"
KIND_INFERRED = "inferred"
KIND_WATCH = "watch"

_EVENT_MATCH_TERMS = {
    "cpi": ["cpi", "consumer price index", "inflation"],
    "fomc": ["fomc", "federal open market committee", "fed decision", "rate decision"],
    "jobs": ["employment situation", "jobs report", "nonfarm payroll", "nonfarm payrolls", "nfp"],
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z") and "T" in s:
            return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        if len(s) == 10 and s.count("-") == 2:
            return dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        parsed = email.utils.parsedate_to_datetime(s)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def pending_id_for(asset: str | None, event_type: str | None, start_ts: str | None, kind: str, source_ref: str | None = None) -> str:
    base = "::".join(
        [
            str(kind or KIND_INFERRED),
            str(asset or "NA"),
            str(event_type or "unknown"),
            str(start_ts or "NA"),
            str(source_ref or ""),
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def ensure_store() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not PENDING.exists():
        PENDING.write_text("", encoding="utf-8")


def _read_rows() -> list[dict[str, Any]]:
    ensure_store()
    rows: list[dict[str, Any]] = []
    with PENDING.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _write_rows(rows: list[dict[str, Any]]) -> None:
    ensure_store()
    tmp = PENDING.with_name(f".{PENDING.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, PENDING)


def read_index() -> dict[str, dict[str, Any]]:
    return {str(r["pending_id"]): r for r in _read_rows() if r.get("pending_id")}


def upsert_pending(record: dict[str, Any]) -> tuple[str, bool]:
    """Upsert by pending_id. Returns (pending_id, inserted_or_updated)."""
    ensure_store()
    pending_id = str(record.get("pending_id") or "")
    if not pending_id:
        return "", False
    rows = _read_rows()
    changed = False
    found = False
    for i, row in enumerate(rows):
        if str(row.get("pending_id")) == pending_id:
            found = True
            # keep existing created_at; only bump updated_at if semantic fields changed
            candidate = {**row, **record, "created_at": row.get("created_at") or record.get("created_at")}
            if row.get("status") in (STATUS_MATCHED, STATUS_EXPIRED, STATUS_CANCELLED) and record.get("status") == STATUS_OPEN:
                candidate["status"] = row.get("status")
                candidate["matched_by_card_id"] = row.get("matched_by_card_id")
                candidate["matched_at"] = row.get("matched_at")
                candidate["match_note"] = row.get("match_note")
            old_cmp = {k: v for k, v in row.items() if k != "updated_at"}
            new_cmp = {k: v for k, v in candidate.items() if k != "updated_at"}
            if new_cmp != old_cmp:
                merged = {**candidate, "updated_at": now_iso()}
                rows[i] = merged
                changed = True
            break
    if not found:
        rows.append(record)
        changed = True
    if changed:
        _write_rows(rows)
    return pending_id, changed


def build_pending_record(
    *,
    asset: str | None,
    layer: int | None,
    event_type: str | None,
    expected_start_ts: str | None,
    expected_end_ts: str | None = None,
    kind: str = KIND_INFERRED,
    source_id: str | None = None,
    source_ref: str | None = None,
    expected_direction: str | None = None,
    expectation_text: str | None = None,
    confidence: float | None = None,
    linked_entities: list[str] | None = None,
    created_from_card_id: str | None = None,
    created_from_event_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pid = pending_id_for(asset, event_type, expected_start_ts, kind, source_ref or created_from_card_id)
    return {
        "schema": "ScoutPendingEvent.v1",
        "pending_id": pid,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "status": STATUS_OPEN,
        "kind": kind,
        "asset": asset,
        "layer": layer,
        "event_type": event_type or "unknown",
        "expected_start_ts": expected_start_ts,
        "expected_end_ts": expected_end_ts or expected_start_ts,
        "expected_direction": expected_direction or "unknown",
        "expectation_text": expectation_text or "",
        "confidence": confidence,
        "source_id": source_id,
        "source_ref": source_ref,
        "linked_entities": linked_entities or [],
        "created_from_card_id": created_from_card_id,
        "created_from_event_key": created_from_event_key,
        "matched_by_card_id": None,
        "matched_at": None,
        "match_note": None,
        "metadata": metadata or {},
    }


def build_pending_from_journal(row: dict[str, Any]) -> dict[str, Any] | None:
    phase = str(row.get("event_phase") or "").lower()
    if phase != "expected":
        return None
    source_ts = row.get("source_ts")
    event_type = str(row.get("event_type") or "unknown").lower()
    return build_pending_record(
        asset=row.get("asset"),
        layer=row.get("layer"),
        event_type=event_type,
        expected_start_ts=source_ts,
        expected_end_ts=source_ts,
        kind=KIND_CALENDAR,
        source_id=row.get("source"),
        source_ref=row.get("source_url"),
        expected_direction=row.get("side") or "unknown",
        expectation_text=row.get("summary") or row.get("headline") or "",
        confidence=row.get("agent_confidence"),
        created_from_card_id=row.get("card_id"),
        created_from_event_key=row.get("event_key"),
        metadata={
            "headline": row.get("headline"),
            "lead_class": row.get("lead_class"),
            "match_terms": _EVENT_MATCH_TERMS.get(event_type, []),
        },
    )


def open_items() -> list[dict[str, Any]]:
    return [r for r in _read_rows() if r.get("status") == STATUS_OPEN]


def match_realized_event(row: dict[str, Any], window_hours: int = 72) -> dict[str, Any] | None:
    """Find an open pending item that matches a realized row by asset + family + time window."""
    if str(row.get("event_phase") or "").lower() != "realized":
        return None
    asset = row.get("asset")
    event_type = row.get("event_type")
    headline_low = str(row.get("headline") or "").lower()
    source_ts = parse_ts(row.get("source_ts"))
    if not asset or not event_type or source_ts is None:
        return None

    best: dict[str, Any] | None = None
    best_delta: float | None = None
    for item in open_items():
        if item.get("asset") != asset:
            continue
        if item.get("event_type") != event_type:
            if str(event_type).lower() not in {"macro", "unclassified"}:
                continue
            meta = item.get("metadata") or {}
            match_terms = [str(x).lower() for x in (meta.get("match_terms") or []) if str(x).strip()]
            if not match_terms or not any(term in headline_low for term in match_terms):
                continue
        expected_start = parse_ts(item.get("expected_start_ts"))
        if expected_start is None:
            continue
        delta_h = abs((source_ts - expected_start).total_seconds()) / 3600
        if delta_h > window_hours:
            continue
        if best is None or delta_h < (best_delta or 10**9):
            best = item
            best_delta = delta_h
    return best


def mark_matched(pending_id: str, card_id: str, match_note: str | None = None) -> bool:
    rows = _read_rows()
    changed = False
    for row in rows:
        if str(row.get("pending_id")) == str(pending_id):
            if row.get("status") != STATUS_MATCHED or row.get("matched_by_card_id") != card_id:
                row["status"] = STATUS_MATCHED
                row["matched_by_card_id"] = card_id
                row["matched_at"] = now_iso()
                row["match_note"] = match_note or "matched_by_realized_event"
                row["updated_at"] = now_iso()
                changed = True
            break
    if changed:
        _write_rows(rows)
    return changed


def expire_old(now_utc: dt.datetime | None = None) -> int:
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    rows = _read_rows()
    changed = 0
    for row in rows:
        if row.get("status") != STATUS_OPEN:
            continue
        end_ts = parse_ts(row.get("expected_end_ts"))
        if end_ts is None:
            continue
        if now_utc > end_ts + dt.timedelta(hours=72):
            row["status"] = STATUS_EXPIRED
            row["updated_at"] = now_iso()
            changed += 1
    if changed:
        _write_rows(rows)
    return changed
