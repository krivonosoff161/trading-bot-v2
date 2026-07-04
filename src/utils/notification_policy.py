"""Telegram notification policy for product, scanner, and paper surfaces."""

from __future__ import annotations

from dataclasses import dataclass


PUBLIC_CHANNEL_EVENTS = {
    "NEWS",
    "MARKET_SUMMARY",
    "SCANNER_WATCH",
    "SCANNER_GO_PUBLIC_TEASER",
}

SUBSCRIBER_EVENTS = {
    "ACTIONABLE_ANALYSIS",
    "GO",
    "WATCH",
    "PAPER_SETUP",
    "VIP",
    "EDUCATION",
}

ADMIN_EVENTS = {
    "ADMIN_DIAGNOSTIC",
    "FARM_ERROR",
    "PAPER_DEBUG",
    "DELIVERY_ERROR",
}


@dataclass(frozen=True)
class NotificationDecision:
    allowed: bool
    destination: str
    reason: str
    requires_subscription: bool = False
    requires_admin: bool = False
    public_signal_levels_allowed: bool = False


def decide_notification(
    event_type: str,
    *,
    is_subscribed: bool = False,
    is_admin: bool = False,
) -> NotificationDecision:
    """Return the permitted destination for a normalized notification event."""
    event = event_type.strip().upper()
    if event in PUBLIC_CHANNEL_EVENTS:
        return NotificationDecision(
            allowed=True,
            destination="notification_channel",
            reason="public_channel_event",
            public_signal_levels_allowed=False,
        )
    if event in SUBSCRIBER_EVENTS:
        if not is_subscribed:
            return NotificationDecision(
                allowed=False,
                destination="personal_bot",
                reason="subscription_required",
                requires_subscription=True,
            )
        return NotificationDecision(
            allowed=True,
            destination="personal_bot",
            reason="subscriber_product_event",
            requires_subscription=True,
            public_signal_levels_allowed=event not in {"PAPER_SETUP"},
        )
    if event in ADMIN_EVENTS:
        if not is_admin:
            return NotificationDecision(
                allowed=False,
                destination="admin",
                reason="admin_required",
                requires_admin=True,
            )
        return NotificationDecision(
            allowed=True,
            destination="admin",
            reason="admin_event",
            requires_admin=True,
        )
    return NotificationDecision(
        allowed=False,
        destination="none",
        reason="unknown_event_type",
    )
