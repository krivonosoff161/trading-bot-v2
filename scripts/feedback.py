"""Feedback log helpers — save/load/update trade feedback entries.

Stores user trade outcomes in feedback_log.jsonl, linked to snapshots.
Used by telegram_bot.py (write) and feedback_stats.py (read).
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

FEEDBACK_LOG = Path(__file__).parent / "analysis_output" / "feedback_log.jsonl"
REMIND_AFTER_H = 24


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_entry(chat_id: str, symbol: str, snap: dict, snap_path: str = "") -> str:
    """Save a new feedback entry. Returns the short entry ID."""
    ctx = snap.get("llm_context", {})
    entry_id = str(uuid.uuid4())[:8]
    entry = {
        "id":         entry_id,
        "chat_id":    chat_id,
        "symbol":     symbol,
        "style":      ctx.get("trade_style_hint", ""),
        "side":       ctx.get("side", ""),
        "entry":      ctx.get("entry_price"),
        "sl":         ctx.get("sl_price"),
        "tp1":        ctx.get("tp1_price"),
        "signal":     ctx.get("entry_signal", ""),
        "created_at": _now_iso(),
        "snap_path":  snap_path,
        "entered":    None,   # True / False
        "result":     None,   # "tp1" / "tp2" / "sl" / "manual" / "skipped"
        "reminded":   False,
    }
    FEEDBACK_LOG.parent.mkdir(exist_ok=True)
    with open(FEEDBACK_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry_id


def update_entry(entry_id: str, **fields) -> None:
    """Update fields of an existing entry by ID (rewrites the file)."""
    if not FEEDBACK_LOG.exists():
        return
    lines = FEEDBACK_LOG.read_text(encoding="utf-8").splitlines()
    updated = []
    for line in lines:
        if not line.strip():
            continue
        e = json.loads(line)
        if e["id"] == entry_id:
            e.update(fields)
        updated.append(json.dumps(e, ensure_ascii=False))
    FEEDBACK_LOG.write_text("\n".join(updated) + "\n", encoding="utf-8")


def load_entries() -> list[dict]:
    """Load all entries from feedback_log.jsonl."""
    if not FEEDBACK_LOG.exists():
        return []
    entries = []
    for line in FEEDBACK_LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


def pending_reminders() -> list[dict]:
    """Return entries ready for 24h result reminder: entered, no result, not reminded, age >= 24h."""
    now = datetime.now(tz=timezone.utc)
    result = []
    for e in load_entries():
        if e.get("entered") is not True:
            continue
        if e.get("result") is not None:
            continue
        if e.get("reminded"):
            continue
        created = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00"))
        if (now - created).total_seconds() / 3600 >= REMIND_AFTER_H:
            result.append(e)
    return result


def pending_for_chat(chat_id: str) -> list[dict]:
    """Return open (entered, no result yet) entries for a given chat_id."""
    return [
        e for e in load_entries()
        if e["chat_id"] == chat_id
        and e.get("entered") is True
        and e.get("result") is None
    ]
