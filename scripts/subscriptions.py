"""Subscription access control for Telegram bot.

subscriptions.json format:
{
  "CHAT_ID": {"expires": null, "plan": "superadmin"},   // permanent
  "CHAT_ID": {"expires": "2026-04-23", "plan": "monthly"}  // paid
}

null expires = superadmin (permanent access).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

SUBS_FILE = Path(__file__).parent / "subscriptions.json"


def _load() -> dict:
    if not SUBS_FILE.exists():
        return {}
    with open(SUBS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(SUBS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def is_subscribed(chat_id: str) -> bool:
    """Return True if user has active subscription or is superadmin."""
    data = _load()
    entry = data.get(str(chat_id))
    if not entry:
        return False
    if entry["expires"] is None:
        return True  # superadmin
    return date.fromisoformat(entry["expires"]) >= date.today()


def get_status(chat_id: str) -> dict | None:
    """Return subscription entry or None."""
    return _load().get(str(chat_id))


def add_user(chat_id: str, days: int, plan: str = "monthly") -> str:
    """Add or extend subscription. Returns new expiry date string."""
    data = _load()
    existing = data.get(str(chat_id))
    if existing and existing.get("plan") == "superadmin":
        return "superadmin — не изменяется"

    if existing and existing.get("expires"):
        current_expiry = max(date.fromisoformat(existing["expires"]), date.today())
    else:
        current_expiry = date.today()

    new_expiry = current_expiry + timedelta(days=days)
    data[str(chat_id)] = {"expires": new_expiry.isoformat(), "plan": plan}
    _save(data)
    return new_expiry.isoformat()


def remove_user(chat_id: str) -> bool:
    """Remove user. Returns True if existed, False if not found."""
    data = _load()
    if str(chat_id) not in data:
        return False
    del data[str(chat_id)]
    _save(data)
    return True


def list_users() -> list[dict]:
    """Return all users with their status."""
    data = _load()
    today = date.today()
    result = []
    for cid, entry in data.items():
        if entry["expires"] is None:
            status = "superadmin"
        elif date.fromisoformat(entry["expires"]) >= today:
            days_left = (date.fromisoformat(entry["expires"]) - today).days
            status = f"активен ({days_left}д)"
        else:
            status = "истёк"
        result.append({
            "chat_id":       cid,
            "plan":          entry["plan"],
            "expires":       entry["expires"],
            "status":        status,
            "last_reminded": entry.get("last_reminded"),
        })
    return result


def list_delivery_users() -> list[dict]:
    """Return users normalized for notification delivery.

    This keeps delivery code away from localized display strings returned by
    list_users().
    """
    data = _load()
    today = date.today()
    result = []
    for cid, entry in data.items():
        expires = entry.get("expires")
        if expires is None:
            status = "superadmin"
        elif date.fromisoformat(expires) >= today:
            status = "active"
        else:
            status = "expired"
        result.append({
            "chat_id": cid,
            "plan": entry.get("plan", ""),
            "expires": expires,
            "status": status,
        })
    return result


def mark_reminded(chat_id: str, when: str) -> None:
    """Записать дату когда юзеру отправлено напоминание о скором истечении."""
    data = _load()
    if str(chat_id) in data:
        data[str(chat_id)]["last_reminded"] = when
        _save(data)
