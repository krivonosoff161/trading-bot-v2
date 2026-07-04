"""Policy-gated Telegram delivery helpers.

The router keeps product/subscriber/admin notification routing out of scanner,
farm, and paper code. Callers pass a normalized event type; the policy decides
whether levels may leave the machine and where they go.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict
from typing import Any

from src.utils.notification_policy import decide_notification
from src.utils.telegram import chat_ids, send_message_to
from src.utils.telegram_audit import record_message_audit

Sender = Callable[[str, str], Awaitable[int | None]]


def _active_subscriber_ids(users: Iterable[dict[str, Any]]) -> list[str]:
    return [
        str(u.get("chat_id"))
        for u in users
        if str(u.get("status") or "").lower() in {"active", "superadmin"}
    ]


async def deliver_notification(
    *,
    event_type: str,
    text: str,
    users: Iterable[dict[str, Any]] = (),
    chat_env: str = "TELEGRAM_NOTIFICATION_CHAT_ID",
    admin_chat_env: str = "TELEGRAM_ADMIN_CHAT_ID",
    sender: Sender = send_message_to,
    symbol: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Deliver one notification according to policy.

    Defaults to dry-run so scanner/farm callers cannot accidentally start a
    broadcast during integration.
    """
    event = event_type.strip().upper()
    rows: list[dict[str, Any]] = []
    candidate_targets: list[tuple[str, bool, bool]] = []

    if event in {"NEWS", "MARKET_SUMMARY", "SCANNER_WATCH", "SCANNER_GO_PUBLIC_TEASER"}:
        candidate_targets = [(cid, False, False) for cid in chat_ids(chat_env)]
    elif event in {"ACTIONABLE_ANALYSIS", "GO", "WATCH", "PAPER_SETUP", "VIP", "EDUCATION"}:
        candidate_targets = [(cid, True, False) for cid in _active_subscriber_ids(users)]
    elif event in {"ADMIN_DIAGNOSTIC", "FARM_ERROR", "PAPER_DEBUG", "DELIVERY_ERROR"}:
        candidate_targets = [(cid, False, True) for cid in chat_ids(admin_chat_env)]

    for chat_id, subscribed, admin in candidate_targets:
        decision = decide_notification(event, is_subscribed=subscribed, is_admin=admin)
        message_id = None
        status = "blocked"
        if decision.allowed:
            status = "dry_run"
            if not dry_run:
                message_id = await sender(chat_id, text)
                status = "sent" if message_id is not None else "skipped_no_token"
        record_message_audit(
            chat_id=chat_id,
            direction="outgoing",
            mode="notification_router",
            event=event,
            text=text,
            symbol=symbol,
            delivery_status=status,
            message_id=message_id,
            extra={"policy": asdict(decision), "dry_run": dry_run},
        )
        rows.append({"chat_id": chat_id, "status": status, "policy": asdict(decision)})

    return {
        "schema": "telegram_delivery_router.v1",
        "event_type": event,
        "dry_run": dry_run,
        "targets": len(candidate_targets),
        "delivered": sum(1 for r in rows if r["status"] == "sent"),
        "rows": rows,
    }
