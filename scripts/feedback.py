"""Feedback log helpers for telegram bot and analysis tools.

Canonical import path: ``scripts.feedback``.
Each user's feedback is stored in ``logs/users/{chat_id}/feedback.jsonl``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
USERS_ROOT = ROOT / "logs" / "users"
REMIND_AFTER_H = 48
REMIND_MIN_INTERVAL = 4


def _user_log(chat_id: str) -> Path:
    path = USERS_ROOT / str(chat_id) / "feedback.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_entry(chat_id: str, symbol: str, snap: dict, snap_path: str = "") -> str:
    """Save a new feedback entry for this user and return short entry id."""
    ctx = snap.get("llm_context", {})
    entry_id = str(uuid.uuid4())[:8]
    entry = {
        "id": entry_id,
        "chat_id": chat_id,
        "symbol": symbol,
        "style": ctx.get("trade_style_hint", ""),
        "side": ctx.get("side", ""),
        "entry": ctx.get("entry_price"),
        "sl": ctx.get("sl_price"),
        "tp1": ctx.get("tp1_price"),
        "signal": ctx.get("entry_signal", ""),
        "created_at": _now_iso(),
        "snap_path": snap_path,
        "entered": None,
        "result": None,
        "reminded": False,
        "last_reminded_at": None,
    }
    log = _user_log(chat_id)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry_id


def update_entry(entry_id: str, chat_id: str, **fields) -> None:
    """Update fields of an existing entry by id in the user's log."""
    log = _user_log(chat_id)
    if not log.exists():
        return
    lines = log.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("id") == entry_id:
            entry.update(fields)
        updated.append(json.dumps(entry, ensure_ascii=False))
    log.write_text("\n".join(updated) + "\n", encoding="utf-8")


def load_entries(chat_id: str) -> list[dict]:
    """Load all feedback entries for a specific user."""
    log = _user_log(chat_id)
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_all_entries() -> list[dict]:
    """Load all feedback entries across all users."""
    entries: list[dict] = []
    if not USERS_ROOT.exists():
        return entries
    for user_dir in USERS_ROOT.iterdir():
        log = user_dir / "feedback.jsonl"
        if not log.exists():
            continue
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
    return entries


def pending_reminders() -> list[dict]:
    """Return entries ready for delayed reminder across all users."""
    now = datetime.now(tz=timezone.utc)
    result: list[dict] = []
    for entry in load_all_entries():
        if entry.get("entered") is not True:
            continue
        if entry.get("result") is not None:
            continue
        if entry.get("reminded"):
            continue
        created = datetime.fromisoformat(str(entry["created_at"]).replace("Z", "+00:00"))
        if (now - created).total_seconds() / 3600 >= REMIND_AFTER_H:
            result.append(entry)
    return result


def pending_for_chat(chat_id: str, symbol: str | None = None) -> list[dict]:
    """Return open entries for a given chat id.

    If ``symbol`` is passed, only entries for that symbol are returned.
    Entries reminded less than ``REMIND_MIN_INTERVAL`` hours ago are skipped.
    """
    now = datetime.now(tz=timezone.utc)
    result: list[dict] = []
    for entry in load_entries(chat_id):
        if entry.get("entered") is not True:
            continue
        if entry.get("result") is not None:
            continue
        if symbol and entry.get("symbol") != symbol:
            continue
        last = entry.get("last_reminded_at")
        if last:
            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if (now - last_dt).total_seconds() / 3600 < REMIND_MIN_INTERVAL:
                continue
        result.append(entry)
    return result
