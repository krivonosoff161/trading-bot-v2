"""Feedback log helpers — save/load/update trade feedback entries.

Each user's feedback is stored in logs/users/{chat_id}/feedback.jsonl.
Used by telegram_bot.py (write) and feedback_stats.py (read).
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USERS_ROOT = ROOT / "logs" / "users"
REMIND_AFTER_H      = 48   # fallback startup reminder: only if trade open > 48h
REMIND_MIN_INTERVAL = 4    # context reminder: no more than once per 4h per trade


def _user_log(chat_id: str) -> Path:
    p = USERS_ROOT / str(chat_id) / "feedback.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_entry(chat_id: str, symbol: str, snap: dict, snap_path: str = "") -> str:
    """Save a new feedback entry for this user. Returns the short entry ID."""
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
        "entered":          None,    # True / False
        "result":           None,    # "tp1" / "tp2" / "sl" / "manual" / "skipped"
        "reminded":         False,
        "last_reminded_at": None,    # ISO timestamp of last context reminder
    }
    log = _user_log(chat_id)
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry_id


def update_entry(entry_id: str, chat_id: str, **fields) -> None:
    """Update fields of an existing entry by ID in the user's log."""
    log = _user_log(chat_id)
    if not log.exists():
        return
    lines = log.read_text(encoding="utf-8").splitlines()
    updated = []
    for line in lines:
        if not line.strip():
            continue
        e = json.loads(line)
        if e["id"] == entry_id:
            e.update(fields)
        updated.append(json.dumps(e, ensure_ascii=False))
    log.write_text("\n".join(updated) + "\n", encoding="utf-8")


def load_entries(chat_id: str) -> list[dict]:
    """Load all feedback entries for a specific user."""
    log = _user_log(chat_id)
    if not log.exists():
        return []
    entries = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


def load_all_entries() -> list[dict]:
    """Load all feedback entries across all users (for admin stats)."""
    entries = []
    if not USERS_ROOT.exists():
        return entries
    for user_dir in USERS_ROOT.iterdir():
        log = user_dir / "feedback.jsonl"
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))
    return entries


def pending_reminders() -> list[dict]:
    """Return entries ready for 24h result reminder across all users."""
    now = datetime.now(tz=timezone.utc)
    result = []
    for e in load_all_entries():
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


def pending_for_chat(chat_id: str, symbol: str = None) -> list[dict]:
    """Return open (entered, no result yet) entries for a given chat_id.

    If symbol is given — only return entries for that pair.
    Skips entries reminded less than REMIND_MIN_INTERVAL hours ago.
    """
    now = datetime.now(tz=timezone.utc)
    result = []
    for e in load_entries(chat_id):
        if e.get("entered") is not True:
            continue
        if e.get("result") is not None:
            continue
        if symbol and e.get("symbol") != symbol:
            continue
        last = e.get("last_reminded_at")
        if last:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if (now - last_dt).total_seconds() / 3600 < REMIND_MIN_INTERVAL:
                continue
        result.append(e)
    return result
