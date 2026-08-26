"""Read-only watch queue between the news scanner and technical confirmation.

The queue is append-only/idempotent JSONL. It is intentionally not an execution
queue: rows carry scanner context for later confirmation, but never permission
to place an order.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root

LOG_DIR: Path
WATCH_QUEUE: Path


def configure_private_root(root: Path) -> None:
    """Bind mutable scanner-to-farm hand-off state to a private root."""
    global LOG_DIR, WATCH_QUEUE
    LOG_DIR = resolve_private_root(root) / "logs" / "scout"
    WATCH_QUEUE = LOG_DIR / "watch_queue.jsonl"


configure_private_root(DEFAULT_PRIVATE_ROOT)

SCHEMA = "ScoutWatch.v1"
STATUS_OPEN = "open"
STATUS_EXPIRED = "expired"

WATCH_VERDICTS = {"WATCH", "GO"}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def iso_after(start_iso: str | None, hours: int | float | None) -> str:
    start = parse_ts(start_iso) or dt.datetime.now(dt.timezone.utc)
    try:
        h = float(hours if hours is not None else 48)
    except (TypeError, ValueError):
        h = 48.0
    h = max(1.0, min(h, 168.0))
    return (start + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_trade_side(side: str | None, fallback: str | None = None) -> str:
    raw = str(side or "").strip().lower()
    if raw in {"long", "buy", "bull", "bullish"}:
        return "buy"
    if raw in {"short", "sell", "bear", "bearish"}:
        return "sell"
    raw = str(fallback or "").strip().lower()
    if raw in {"long", "buy", "bull", "bullish"}:
        return "buy"
    if raw in {"short", "sell", "bear", "bearish"}:
        return "sell"
    return "none"


def should_queue(row: dict[str, Any]) -> bool:
    verdict = str(row.get("verdict") or "").upper()
    return verdict in WATCH_VERDICTS and bool(row.get("card_id")) and bool(row.get("asset"))


def build_watch_record(row: dict[str, Any]) -> dict[str, Any] | None:
    if not should_queue(row):
        return None
    watch_id = f"watch_{row['card_id']}"
    side = normalize_trade_side(row.get("side"), row.get("agent_direction"))
    created_at = str(row.get("ts_utc") or now_iso())
    horizon_hours = row.get("horizon_hours") or 48
    return {
        "schema": SCHEMA,
        "watch_id": watch_id,
        "status": STATUS_OPEN,
        "created_at": created_at,
        "updated_at": now_iso(),
        "expires_at": iso_after(created_at, horizon_hours),
        "confirm_required": True,
        "execution_allowed": False,
        "card_id": row.get("card_id"),
        "event_key": row.get("event_key"),
        "scanner": {
            "verdict": str(row.get("verdict") or "").upper(),
            "side": row.get("side") or "none",
            "normalized_side": side,
            "layer": row.get("layer"),
            "event_type": row.get("event_type"),
            "event_phase": row.get("event_phase"),
            "lead_class": row.get("lead_class"),
            "escalation_gate": row.get("escalation_gate"),
            "chief_called": bool(row.get("chief_called")),
            "materiality_score": row.get("materiality_score"),
            "agent_confidence": row.get("agent_confidence"),
        },
        "asset": {
            "symbol": row.get("asset"),
            "okx_inst": row.get("okx_inst"),
            "baseline_symbol": row.get("baseline_symbol"),
            "price_at_decision": row.get("price_at_decision"),
            "okx_resolved": row.get("okx_resolved"),
            "okx_inst_type": row.get("okx_inst_type"),
            "okx_asset_class": row.get("okx_asset_class"),
        },
        # Farm hand-off context (paper research scheduling only — never execution).
        "farm": {
            "eligible": row.get("farm_eligible"),
            "pending_reason": row.get("farm_pending_reason"),
            "data_readiness_status": row.get("data_readiness_status"),
            "selected_timeframe": row.get("selected_timeframe"),
            "okx_resolution_reason": row.get("okx_resolution_reason"),
        },
        "trigger": {
            "headline": row.get("headline"),
            "summary": row.get("summary"),
            "source": row.get("source"),
            "source_url": row.get("source_url"),
            "source_ts": row.get("source_ts"),
            "catalyst": row.get("catalyst"),
            "mechanics": row.get("mechanics"),
            "asymmetry": row.get("asymmetry"),
            "forecast": row.get("forecast"),
            "invalidation": row.get("invalidation"),
        },
        "levels": row.get("levels") or {},
        "confirmation": {
            "latest_status": None,
            "latest_confirmation_id": None,
            "matched_signal_id": None,
            "matched_at": None,
        },
    }


def _read_rows(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or WATCH_QUEUE
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _write_rows(rows: list[dict[str, Any]], path: Path | None = None) -> None:
    path = path or WATCH_QUEUE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# Volatile audit fields must not, on their own, count as a change. `updated_at` is
# stamped with now() on every build_watch_record() call, so comparing the whole record
# would make idempotency depend on wall-clock: two upserts of the same row straddling a
# 1-second boundary would falsely report "changed" (the watch_queue test flake). Compare
# substantive content only; updated_at is carried along when a real change is written.
_VOLATILE_FIELDS = ("updated_at",)


def _substantive(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in _VOLATILE_FIELDS}


def upsert_watch(row: dict[str, Any], path: Path | None = None) -> tuple[str, bool]:
    record = build_watch_record(row)
    if record is None:
        return "", False
    active_path = path or WATCH_QUEUE
    rows = _read_rows(active_path)
    for idx, existing in enumerate(rows):
        if existing.get("watch_id") == record["watch_id"]:
            candidate = {**existing, **record, "created_at": existing.get("created_at") or record["created_at"]}
            if _substantive(candidate) != _substantive(existing):
                rows[idx] = candidate
                _write_rows(rows, active_path)
                return str(record["watch_id"]), True
            return str(record["watch_id"]), False
    rows.append(record)
    _write_rows(rows, active_path)
    return str(record["watch_id"]), True


def open_watches(
    *,
    symbol: str | None = None,
    okx_inst: str | None = None,
    now_utc: dt.datetime | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    out: list[dict[str, Any]] = []
    wanted_symbol = str(symbol or "").upper()
    wanted_inst = str(okx_inst or "").upper()
    for row in _read_rows(path or WATCH_QUEUE):
        if row.get("status") != STATUS_OPEN:
            continue
        expires = parse_ts(row.get("expires_at"))
        if expires is not None and expires < now_utc:
            continue
        asset = row.get("asset") or {}
        row_symbol = str(asset.get("symbol") or "").upper()
        row_inst = str(asset.get("okx_inst") or "").upper()
        if wanted_symbol and row_symbol != wanted_symbol:
            continue
        if wanted_inst and row_inst != wanted_inst:
            continue
        out.append(row)
    return out


def expire_old(now_utc: dt.datetime | None = None, path: Path | None = None) -> int:
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    active_path = path or WATCH_QUEUE
    rows = _read_rows(active_path)
    changed = 0
    for row in rows:
        if row.get("status") != STATUS_OPEN:
            continue
        expires = parse_ts(row.get("expires_at"))
        if expires is not None and expires < now_utc:
            row["status"] = STATUS_EXPIRED
            row["updated_at"] = now_iso()
            changed += 1
    if changed:
        _write_rows(rows, active_path)
    return changed
