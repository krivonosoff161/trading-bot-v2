"""Paper/research lineage contracts and private JSONL indexes.

The records here link existing farm and paper ids without replacing them. They
are public-safe schemas; raw market data and derived artifacts are written under
the private Strategy Lab root by callers.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SCANNER_EVENT_SCHEMA = "ScannerEvent.v1"
CYCLE_LINK_SCHEMA = "LineageLink.v1"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def stable_id(prefix: str, payload: dict[str, Any], *, length: int = 16) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def append_jsonl(path: Path, row: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def lineage_dir(private_root: Path) -> Path:
    return Path(private_root) / "state" / "lineage"


def scanner_events_path(private_root: Path) -> Path:
    return lineage_dir(private_root) / "scanner_events.jsonl"


def cycle_links_path(private_root: Path) -> Path:
    return lineage_dir(private_root) / "cycle_links.jsonl"


def _lineage_link_id(row: dict[str, Any]) -> str:
    keys = (
        "scanner_event_id",
        "data_packet_id",
        "feature_packet_id",
        "setup_candidate_id",
        "sweep_run_id",
        "validation_id",
        "paper_signal_id",
        "telegram_card_id",
        "outcome_id",
        "training_row_id",
        "source",
        "symbol",
        "instrument",
        "timeframe",
        "setup_family",
        "mode",
    )
    payload = {key: row.get(key) for key in keys if row.get(key)}
    return stable_id("link", payload, length=20)


def _existing_link_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        ids.add(str(row.get("lineage_link_id") or _lineage_link_id(row)))
    return ids


@dataclass(frozen=True)
class ScannerEvent:
    scanner_event_id: str
    symbol: str
    instrument: str
    timeframe: str
    source: str
    reason: str
    timestamp: str
    mode: str
    movement_stats: dict[str, Any] = field(default_factory=dict)
    liquidity: dict[str, Any] = field(default_factory=dict)
    context_refs: dict[str, Any] = field(default_factory=dict)
    data_freshness: dict[str, Any] = field(default_factory=dict)
    raw_ref: dict[str, Any] = field(default_factory=dict)
    schema: str = SCANNER_EVENT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scanner_event_from_mover(
    mover: dict[str, Any],
    *,
    symbol: str,
    instrument: str,
    timeframe: str,
    mode: str,
    timestamp: str | None = None,
) -> ScannerEvent:
    source = str(mover.get("source") or "live_universe")
    reason = str(mover.get("_reason") or mover.get("reason") or "ranked_live_mover")
    payload = {
        "symbol": symbol,
        "instrument": instrument,
        "timeframe": timeframe,
        "source": source,
        "reason": reason,
        "bucket": mover.get("_bucket"),
        "score": mover.get("score"),
        "mode": mode,
    }
    return ScannerEvent(
        scanner_event_id=stable_id("se", payload),
        symbol=symbol,
        instrument=instrument,
        timeframe=timeframe,
        source=source,
        reason=reason,
        timestamp=timestamp or utc_now(),
        mode=mode,
        movement_stats={
            "score": mover.get("score"),
            "priority": mover.get("_priority"),
            "move_pct": mover.get("move_pct"),
        },
        liquidity={
            "vol_usd": mover.get("vol_usd"),
            "spread_bps": mover.get("spread_bps"),
        },
        context_refs={"bucket": mover.get("_bucket")},
        data_freshness={"source_ts": mover.get("ts") or mover.get("updated_at")},
        raw_ref={k: mover.get(k) for k in ("symbol", "inst_id", "group") if k in mover},
    )


def scanner_event_from_intake(event: dict[str, Any], *, mode: str = "live", timeframe: str | None = None) -> ScannerEvent:
    symbol = str(event.get("symbol") or "").upper()
    instrument = symbol.replace("_", "-")
    suggested = event.get("suggested_timeframes") or []
    selected_tf = timeframe or (str(suggested[0]) if suggested else "15m")
    source = str(event.get("source") or "scanner")
    reason = str(event.get("reason") or "scanner_event")
    observed = event.get("observed_at")
    timestamp = (
        dt.datetime.fromtimestamp(float(observed), tz=dt.timezone.utc).isoformat()
        if isinstance(observed, (int, float)) and float(observed) > 0
        else utc_now()
    )
    payload = {
        "event_id": event.get("event_id"),
        "symbol": symbol,
        "timeframe": selected_tf,
        "source": source,
        "reason": reason,
        "mode": mode,
    }
    evidence = event.get("evidence") or {}
    raw_ref = event.get("raw_ref") or {}
    return ScannerEvent(
        scanner_event_id=stable_id("se", payload),
        symbol=symbol,
        instrument=instrument,
        timeframe=selected_tf,
        source=source,
        reason=reason,
        timestamp=timestamp,
        mode=mode,
        movement_stats={
            "priority": event.get("priority"),
            "materiality_score": evidence.get("materiality_score"),
            "agent_confidence": evidence.get("agent_confidence"),
            "lead_class": evidence.get("lead_class"),
            "event_phase": evidence.get("event_phase"),
            "side": evidence.get("side"),
        },
        liquidity={
            "spread_bps": evidence.get("spread_bps"),
            "volume_usd": evidence.get("volume_usd"),
        },
        context_refs={
            "asset_class": event.get("asset_class"),
            "suggested_timeframes": suggested,
            "levels": evidence.get("levels") or {},
        },
        data_freshness={"observed_at": observed},
        raw_ref={k: v for k, v in raw_ref.items() if v is not None},
    )


def _existing_scanner_event_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("scanner_event_id"):
            ids.add(str(row["scanner_event_id"]))
    return ids


def write_scanner_event(private_root: Path, event: ScannerEvent) -> Path:
    path = scanner_events_path(private_root)
    if event.scanner_event_id in _existing_scanner_event_ids(path):
        return path
    return append_jsonl(path, event.to_dict())


def write_cycle_link(private_root: Path, row: dict[str, Any]) -> Path:
    return write_cycle_links(private_root, [row])


def _cycle_link_payload(row: dict[str, Any], *, link_id: str) -> dict[str, Any]:
    return {
        "schema": CYCLE_LINK_SCHEMA,
        "lineage_link_id": link_id,
        "linked_at": utc_now(),
        "paper_only": True,
        "execution_allowed": False,
        **row,
    }


def write_cycle_links(private_root: Path, rows: list[dict[str, Any]]) -> Path:
    """Append unique lineage links, reading the existing JSONL index once.

    Paper training exports can contain thousands of rows. Calling the single-row
    writer in a loop rescans ``cycle_links.jsonl`` for every row, which turns an
    idempotent export into quadratic JSON parsing. This batch path preserves the
    same link ids and append-only contract while keeping one existing-id pass per
    export.
    """
    path = cycle_links_path(private_root)
    existing = _existing_link_ids(path)
    pending: list[dict[str, Any]] = []
    for row in rows:
        link_id = _lineage_link_id(row)
        if link_id in existing:
            continue
        existing.add(link_id)
        pending.append(_cycle_link_payload(row, link_id=link_id))
    if not pending:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for payload in pending:
            fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def load_jsonl_counts(path: Path, *, key: str | None = None) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "rows": 0, "by_key": {}}
    rows = 0
    by_key: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows += 1
        if key:
            try:
                value = str(json.loads(line).get(key) or "")
            except json.JSONDecodeError:
                value = "invalid_json"
            if value:
                by_key[value] = by_key.get(value, 0) + 1
    return {"exists": True, "rows": rows, "by_key": by_key}
