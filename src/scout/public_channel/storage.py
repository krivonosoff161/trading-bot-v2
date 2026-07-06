from __future__ import annotations

import json
import datetime as dt
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = ROOT / "logs" / "scout" / "public_channel"
SENT_KEYS = STATE_DIR / "sent_keys.json"
AUDIT_LOG = STATE_DIR / "publisher_audit.jsonl"
QUEUE_PATH = STATE_DIR / "news_queue.json"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def sent_keys(path: Path = SENT_KEYS) -> set[str]:
    data = _read_json(path)
    return {str(x) for x in data.get("sent_keys", []) if str(x)}


def was_sent(key: str, path: Path = SENT_KEYS) -> bool:
    return key in sent_keys(path)


def mark_sent(key: str, path: Path = SENT_KEYS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = sent_keys(path)
    keys.add(str(key))
    path.write_text(json.dumps({"sent_keys": sorted(keys)}, ensure_ascii=False, indent=2), encoding="utf-8")


def append_audit(row: dict[str, Any], path: Path = AUDIT_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_queue(path: Path = QUEUE_PATH) -> list[dict[str, Any]]:
    data = _read_json(path)
    rows = data.get("items", [])
    return [row for row in rows if isinstance(row, dict)]


def write_queue(rows: list[dict[str, Any]], path: Path = QUEUE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "PublicChannelNewsQueue.v1", "items": rows}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def enqueue_items(
    items: list[dict[str, Any]],
    *,
    queue_path: Path = QUEUE_PATH,
    sent_path: Path = SENT_KEYS,
    max_items: int = 200,
) -> dict[str, Any]:
    """Add unseen public news items to the local queue.

    The queue is the handoff between a frequent source scan and a slower public
    posting cadence.
    """
    now = _now()
    rows = read_queue(queue_path)
    by_key = {str(row.get("key")): dict(row) for row in rows if row.get("key")}
    sent = sent_keys(sent_path)
    added = 0
    updated = 0
    skipped_sent = 0
    skipped_invalid = 0
    for item in items:
        key = str(item.get("key") or "")
        if not key:
            skipped_invalid += 1
            continue
        if key in sent:
            skipped_sent += 1
            continue
        if key in by_key:
            row = by_key[key]
            row["last_seen"] = now
            row["seen_count"] = int(row.get("seen_count") or 1) + 1
            row["item"] = item
            by_key[key] = row
            updated += 1
            continue
        by_key[key] = {
            "schema": "PublicChannelQueueItem.v1",
            "key": key,
            "first_seen": now,
            "last_seen": now,
            "seen_count": 1,
            "item": item,
        }
        added += 1
    next_rows = sorted(by_key.values(), key=lambda row: str(row.get("first_seen") or ""))
    if max_items > 0 and len(next_rows) > max_items:
        next_rows = next_rows[-max_items:]
    write_queue(next_rows, queue_path)
    return {
        "schema": "PublicChannelQueueUpdate.v1",
        "input": len(items),
        "added": added,
        "updated": updated,
        "skipped_sent": skipped_sent,
        "skipped_invalid": skipped_invalid,
        "queue_size": len(next_rows),
    }


def remove_queue_keys(keys: set[str], path: Path = QUEUE_PATH) -> int:
    if not keys:
        return 0
    rows = read_queue(path)
    next_rows = [row for row in rows if str(row.get("key") or "") not in keys]
    removed = len(rows) - len(next_rows)
    if removed:
        write_queue(next_rows, path)
    return removed

