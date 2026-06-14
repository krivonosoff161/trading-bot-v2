# -*- coding: utf-8 -*-
"""
trigger_context.py — lightweight context builder for triggers.

Supports two context models:
  1. News corroboration model: search buffer/journal for matching mentions.
     Used for: tokenized_equity, pre_ipo_equity, equity, unknown.
  2. Flow confirmation model: context comes from the trigger itself.
     Used for: liquidation_flow. The flow event IS the context.

No web search, no paid LLM, no private data. Only local data sources:
  - news_buffer SQLite (recent headlines by asset/aliases)
  - scanner journal (recent events by asset)
  - source metadata (is there official confirmation?)
  - flow_context from the trigger item (for liquidations)

Output: context package with what's found + what's missing.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

try:
    from src.scout import news_buffer as NB
except ImportError:
    NB = None

try:
    from src.scout import scanner_journal as J
except ImportError:
    J = None


def build_context(
    symbol: str,
    text: str = "",
    asset_class: str = "unknown",
    source_id: str = "",
    lookback_hours: int = 48,
    flow_context: dict | None = None,
) -> dict:
    """Build context package for a trigger item.

    Returns:
        context_found: bool — whether any corroborating context was found
        context_missing: list[str] — what's still needed
        matching_headlines: list[dict] — recent headlines about this asset
        source_ids: list[str] — unique sources that mentioned this asset
        official_confirmation: bool — whether an official/exchange source confirmed
        context_summary: str — human-readable summary
        context_model: str — "news_corroboration" or "flow_confirmation"
        flow_context: dict | None — parsed flow fields (for liquidations)
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        return _empty_context(["symbol_required"])

    # Liquidation flow: context IS the flow event itself
    if asset_class == "liquidation_flow":
        return _build_flow_context(sym, flow_context, source_id)

    # News corroboration model: search for matching mentions
    matching: list[dict] = []
    source_ids: set[str] = set()

    # 1) Search news buffer for recent items mentioning this asset
    if NB is not None:
        try:
            ready = NB.ready_items(limit=200)
            for item in ready:
                item_asset = str(item.get("asset") or "").upper()
                item_text = str(item.get("text") or item.get("title") or "").lower()
                if item_asset == sym or sym.lower() in item_text:
                    ts = item.get("time") or item.get("published_at")
                    if _is_within_hours(ts, lookback_hours):
                        matching.append({
                            "title": item.get("title", "")[:200],
                            "source": item.get("source", ""),
                            "time": ts,
                            "asset": item_asset,
                        })
                        if item.get("source"):
                            source_ids.add(item["source"])
        except Exception:
            pass

    # 2) Search scanner journal for recent events
    if J is not None:
        try:
            recent = J.recent_events(lookback_hours)
            for asset_str, headline in recent:
                if str(asset_str or "").upper() == sym:
                    matching.append({
                        "title": str(headline or "")[:200],
                        "source": "journal",
                        "time": None,
                        "asset": sym,
                    })
        except Exception:
            pass

    # 3) Determine what's missing based on asset class
    missing = _missing_context(sym, asset_class, source_ids, matching)

    # 4) Official confirmation check
    official_sources = {"okx_listings", "sec_edgar", "okx_announcements", "globenewswire_public"}
    official = bool(source_ids & official_sources)

    context_found = len(matching) >= 1 and not missing
    summary = _build_summary(matching, source_ids, official, missing)

    return {
        "context_found": context_found,
        "context_missing": missing,
        "matching_headlines": matching[:10],
        "source_ids": sorted(source_ids),
        "official_confirmation": official,
        "context_summary": summary,
        "context_model": "news_corroboration",
        "flow_context": None,
    }


def _build_flow_context(
    symbol: str,
    flow_context: dict | None,
    source_id: str,
) -> dict:
    """Build context for liquidation flow events.

    Flow events are self-confirming: the liquidation itself IS the data.
    No need to search for "corroborating mentions" — that's the wrong model.
    Instead, check that the flow_context has parseable data.
    """
    fc = flow_context or {}
    missing: list[str] = []

    # Flow is self-confirming if we parsed at least direction OR notional
    has_direction = fc.get("direction_hint") is not None
    has_notional = fc.get("notional_usd") is not None

    if not has_direction and not has_notional:
        missing.append("unparseable_flow_data")

    direction_label = fc.get("direction_hint") or "unknown"
    notional_label = _fmt_usd(fc.get("notional_usd"))
    venue = fc.get("venue") or "unknown"

    summary_parts = [f"liquidation flow: {direction_label}"]
    if notional_label:
        summary_parts.append(f"notional ~{notional_label}")
    summary_parts.append(f"venue={venue}")
    if missing:
        summary_parts.append(f"missing: {', '.join(missing)}")

    return {
        "context_found": not missing,
        "context_missing": missing,
        "matching_headlines": [],
        "source_ids": [source_id] if source_id else [],
        "official_confirmation": False,
        "context_summary": "; ".join(summary_parts),
        "context_model": "flow_confirmation",
        "flow_context": fc,
    }


def _fmt_usd(val: float | None) -> str:
    if val is None:
        return ""
    if val >= 1_000_000:
        return f"${val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"${val / 1_000:.1f}K"
    return f"${val:.0f}"


def _is_within_hours(ts: str | None, hours: int) -> bool:
    if not ts:
        return False
    try:
        if "T" in str(ts):
            parsed = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        else:
            parsed = dt.datetime.strptime(str(ts)[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        now = dt.datetime.now(dt.timezone.utc)
        return (now - parsed.astimezone(dt.timezone.utc)).total_seconds() / 3600 <= hours
    except Exception:
        return False


def _missing_context(
    symbol: str,
    asset_class: str,
    source_ids: set[str],
    matching: list[dict],
) -> list[str]:
    """Determine what context is still missing based on asset class (news model only).

    Note: liquidation_flow is handled separately by _build_flow_context —
    it uses flow confirmation, not news corroboration.
    """
    missing: list[str] = []

    if asset_class in ("tokenized_equity", "pre_ipo_equity"):
        official_sources = {"okx_listings", "sec_edgar", "okx_announcements", "globenewswire_public"}
        if not (source_ids & official_sources):
            missing.append("exchange_official_confirmation")
        if not any("price" in str(m.get("title", "")).lower() for m in matching):
            missing.append("price_discovery")
        if len(matching) < 2:
            missing.append("multiple_source_corroboration")

    elif asset_class == "equity":
        if not any("earnings" in str(m.get("title", "")).lower() or "filing" in str(m.get("title", "")).lower()
                    for m in matching):
            missing.append("fundamental_data")

    elif asset_class == "unknown":
        if len(matching) < 2:
            missing.append("asset_identification")
        if not source_ids:
            missing.append("source_corroboration")

    return missing


def _build_summary(
    matching: list[dict],
    source_ids: set[str],
    official: bool,
    missing: list[str],
) -> str:
    parts = []
    if matching:
        parts.append(f"{len(matching)} recent mentions from {len(source_ids)} sources")
    else:
        parts.append("no recent mentions found")
    if official:
        parts.append("official source confirmed")
    if missing:
        parts.append(f"missing: {', '.join(missing[:3])}")
    return "; ".join(parts) or "no context available"


def _empty_context(missing: list[str]) -> dict:
    return {
        "context_found": False,
        "context_missing": missing,
        "matching_headlines": [],
        "source_ids": [],
        "official_confirmation": False,
        "context_summary": "no context (missing symbol)",
        "context_model": "news_corroboration",
        "flow_context": None,
    }
