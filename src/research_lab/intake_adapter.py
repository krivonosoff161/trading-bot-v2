# -*- coding: utf-8 -*-
"""Main-scanner intake adapter — normalize events into the farm intake contract.

Reads the scanner's WATCH/GO rows (ScoutWatch.v1, written to logs/scout/watch_queue.jsonl)
and the OKX discovery snapshot / manual universe, and flattens each into ONE intake
contract the farm understands::

    {symbol, source, reason, observed_at, priority, asset_class,
     suggested_timeframes, evidence, raw_ref, event_id}

``event_id`` is a dedup key over (symbol, source, normalized reason, time-window) so
the same event surfacing twice does not enqueue twice. PURE + read-only: it never
imports the scanner module (which can send Telegram) — only the watch FILE / dicts.
No order path, no .env, no secrets.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

from src.research_lab.data_readiness_selector import timeframes_for_asset_class
from src.research_lab.farm_scheduler import classify_watch
from src.research_lab.farm_priority import PRIORITY_BACKGROUND

DEFAULT_WINDOW_SECONDS = 24 * 3600

# OKX discovery group -> readiness asset_class key (the two vocabularies differ).
GROUP_TO_ASSET_CLASS = {
    "crypto_major": "crypto_major",
    "meme_or_high_beta": "meme",
    "tokenized_equity": "tokenized_equity",
    "commodity": "commodity",
    "crypto_alt": "crypto_alt",
    "unknown": "unknown",
}


def _parse_ts(value: Any) -> float:
    """ISO/epoch -> epoch seconds; 0.0 if unparseable."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _norm_reason(text: str) -> str:
    return " ".join(str(text or "").lower().split())[:80]


def event_id(symbol: str, source: str, reason: str, observed_at: float,
             *, window_seconds: int = DEFAULT_WINDOW_SECONDS) -> str:
    """Dedup id over symbol+source+normalized-reason+time-window bucket."""
    bucket = int(observed_at // max(1, window_seconds))
    key = f"{str(symbol).upper()}::{source}::{_norm_reason(reason)}::{bucket}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def _watch_reason(watch: dict[str, Any]) -> str:
    scanner = watch.get("scanner") or {}
    trigger = watch.get("trigger") or {}
    parts = [str(scanner.get("event_type") or ""), str(scanner.get("escalation_gate") or ""),
             str(trigger.get("catalyst") or "")]
    return " ".join(p for p in parts if p) or "scanner_watch"


def watch_to_intake(watch: dict[str, Any], *, window_seconds: int = DEFAULT_WINDOW_SECONDS) -> dict[str, Any] | None:
    """Flatten one ScoutWatch.v1 row into the intake contract; None if no symbol."""
    asset = watch.get("asset") or {}
    farm = watch.get("farm") or {}
    scanner = watch.get("scanner") or {}
    trigger = watch.get("trigger") or {}
    symbol = asset.get("okx_inst") or asset.get("symbol")
    if not symbol:
        return None
    observed_at = _parse_ts(watch.get("created_at") or trigger.get("source_ts"))
    reason = _watch_reason(watch)
    source = str(trigger.get("source") or "scanner")
    asset_class = str(asset.get("okx_asset_class") or "unknown")
    selected = farm.get("selected_timeframe")
    suggested = [selected] if selected else timeframes_for_asset_class(asset_class, farm_only=True)
    return {
        "event_id": event_id(symbol, source, reason, observed_at, window_seconds=window_seconds),
        "symbol": str(symbol), "source": source, "reason": reason,
        "observed_at": observed_at, "priority": classify_watch(watch)[0],
        "asset_class": asset_class, "suggested_timeframes": suggested,
        "evidence": {
            "materiality_score": scanner.get("materiality_score"),
            "agent_confidence": scanner.get("agent_confidence"),
            "lead_class": scanner.get("lead_class"),
            "event_phase": scanner.get("event_phase"),
            "side": scanner.get("normalized_side"),
            "levels": watch.get("levels") or {},
        },
        "raw_ref": {
            "watch_id": watch.get("watch_id"), "card_id": watch.get("card_id"),
            "event_key": watch.get("event_key"), "source_url": trigger.get("source_url"),
        },
    }


def watches_to_intake(watches: list[dict[str, Any]], *,
                      window_seconds: int = DEFAULT_WINDOW_SECONDS) -> list[dict[str, Any]]:
    """Normalize + in-batch dedup a list of watches into intake events."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for watch in watches:
        event = watch_to_intake(watch, window_seconds=window_seconds)
        if event is None or event["event_id"] in seen:
            continue
        seen.add(event["event_id"])
        out.append(event)
    return out


def discovery_intake_events(snapshot: dict[str, Any], *, covered: set[str] | None = None,
                            now: float = 0.0, limit: int = 50) -> list[dict[str, Any]]:
    """Grind-source events for discovered OKX instruments not yet covered."""
    if limit <= 0:
        return []
    covered = {str(s).upper() for s in (covered or set())}
    instruments = snapshot.get("instruments") or {}
    out: list[dict[str, Any]] = []
    for symbol, meta in sorted(instruments.items()):
        if symbol.upper() in covered:
            continue
        asset_class = GROUP_TO_ASSET_CLASS.get(str(meta.get("group")), "unknown")
        out.append({
            "event_id": event_id(symbol, "okx_discovery", "universe_grind", now),
            "symbol": symbol, "source": "okx_discovery", "reason": "universe_grind",
            "observed_at": now, "priority": PRIORITY_BACKGROUND, "asset_class": asset_class,
            "suggested_timeframes": timeframes_for_asset_class(asset_class, farm_only=True),
            "evidence": {"group": meta.get("group")},
            "raw_ref": {"inst_id": meta.get("inst_id")},
        })
        if len(out) >= limit:
            break
    return out
