"""Pure setup confirmation between scanner watches and technical analysis.

This module does not fetch data, send Telegram messages, or place orders. It
only classifies whether a scanner watch has technical support from a
SignalResult-like object.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from src.scout.watch_queue import normalize_trade_side, parse_ts

ConfirmationStatus = Literal[
    "WATCH_CONTINUE",
    "SETUP_FORMING",
    "TRADE_PLAN_READY",
    "INVALIDATED",
    "EXPIRED",
    "NEEDS_DATA",
]


@dataclass(frozen=True)
class SetupConfirmation:
    confirmation_id: str
    watch_id: str
    status: ConfirmationStatus
    symbol: str | None
    desired_side: str
    technical_side: str
    entry_signal: str | None
    reason: str
    execution_allowed: bool = False
    trade_plan: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _confirmation_id(watch_id: str, captured_at: str | None, status: str) -> str:
    raw = f"{watch_id}:{captured_at or ''}:{status}"
    return "conf_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _expired(watch: dict[str, Any], now_utc: dt.datetime | None) -> bool:
    expires = parse_ts(watch.get("expires_at"))
    if expires is None:
        return False
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    return expires < now_utc


def confirm_setup(
    watch: dict[str, Any],
    signal_result: Any | None,
    *,
    now_utc: dt.datetime | None = None,
) -> SetupConfirmation:
    """Classify one watch against a SignalResult-like object.

    TRADE_PLAN_READY is still paper-only. The returned record always has
    execution_allowed=False; a separate future risk/execution gate would be
    required before any order path can use it.
    """
    watch_id = str(watch.get("watch_id") or "")
    scanner = watch.get("scanner") or {}
    asset = watch.get("asset") or {}
    desired_side = normalize_trade_side(scanner.get("normalized_side") or scanner.get("side"))
    symbol = asset.get("okx_inst") or asset.get("symbol")

    if _expired(watch, now_utc):
        return SetupConfirmation(
            confirmation_id=_confirmation_id(watch_id, None, "EXPIRED"),
            watch_id=watch_id,
            status="EXPIRED",
            symbol=symbol,
            desired_side=desired_side,
            technical_side="none",
            entry_signal=None,
            reason="watch_ttl_expired",
        )

    if signal_result is None:
        return SetupConfirmation(
            confirmation_id=_confirmation_id(watch_id, None, "NEEDS_DATA"),
            watch_id=watch_id,
            status="NEEDS_DATA",
            symbol=symbol,
            desired_side=desired_side,
            technical_side="none",
            entry_signal=None,
            reason="missing_technical_snapshot",
        )

    entry_signal = str(_get(signal_result, "entry_signal", "") or "").upper()
    technical_side = normalize_trade_side(_get(signal_result, "side"))
    captured_at = _get(signal_result, "captured_at")
    evidence = {
        "regime": _get(signal_result, "regime"),
        "drop_reason": _get(signal_result, "drop_reason"),
        "trade_style": _get(signal_result, "trade_style"),
        "engine_summary": _get(signal_result, "engine_summary"),
    }

    if desired_side in {"buy", "sell"} and technical_side in {"buy", "sell"} and technical_side != desired_side:
        return SetupConfirmation(
            confirmation_id=_confirmation_id(watch_id, captured_at, "INVALIDATED"),
            watch_id=watch_id,
            status="INVALIDATED",
            symbol=symbol,
            desired_side=desired_side,
            technical_side=technical_side,
            entry_signal=entry_signal,
            reason="technical_side_opposes_event_side",
            evidence=evidence,
        )

    if entry_signal == "ENTRY" and desired_side in {"buy", "sell"} and technical_side == desired_side:
        trade_plan = {
            "entry_price": _get(signal_result, "entry_price"),
            "sl_price": _get(signal_result, "sl_price"),
            "tp1_price": _get(signal_result, "tp1_price"),
            "tp2_price": _get(signal_result, "tp2_price"),
            "max_hold_min": _get(signal_result, "max_hold_min"),
            "trade_style": _get(signal_result, "trade_style"),
            "regime": _get(signal_result, "regime"),
            "paper_only": True,
        }
        return SetupConfirmation(
            confirmation_id=_confirmation_id(watch_id, captured_at, "TRADE_PLAN_READY"),
            watch_id=watch_id,
            status="TRADE_PLAN_READY",
            symbol=symbol,
            desired_side=desired_side,
            technical_side=technical_side,
            entry_signal=entry_signal,
            reason="event_side_confirmed_by_entry_signal",
            trade_plan=trade_plan,
            evidence=evidence,
        )

    if entry_signal in {"ENTRY", "WAIT"}:
        return SetupConfirmation(
            confirmation_id=_confirmation_id(watch_id, captured_at, "SETUP_FORMING"),
            watch_id=watch_id,
            status="SETUP_FORMING",
            symbol=symbol,
            desired_side=desired_side,
            technical_side=technical_side,
            entry_signal=entry_signal,
            reason="technical_structure_present_but_not_actionable",
            evidence=evidence,
        )

    return SetupConfirmation(
        confirmation_id=_confirmation_id(watch_id, captured_at, "WATCH_CONTINUE"),
        watch_id=watch_id,
        status="WATCH_CONTINUE",
        symbol=symbol,
        desired_side=desired_side,
        technical_side=technical_side,
        entry_signal=entry_signal or None,
        reason="no_confirming_entry_signal_yet",
        evidence=evidence,
    )
