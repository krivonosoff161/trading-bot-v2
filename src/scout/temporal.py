# -*- coding: utf-8 -*-
"""
temporal.py — deterministic temporal classifier for source items.

Extends router.route_temporal() with source-aware analysis:
  - Pre-routed items get temporal phase from text + source metadata
  - Liquidation flow is COINCIDENT (happening now)
  - Listings are LEADING or REALIZED based on wording
  - News headlines are AMBIGUOUS unless timing is clear

Returns: phase, temporal_reason, is_stale
"""
from __future__ import annotations

import datetime as dt
import re


# FUTURE markers: "will list", "upcoming", "expected to", "set to"
_FUTURE_RE = re.compile(
    r"\b(will|expected|scheduled|upcoming|plans?\s+to|set\s+to|"
    r"to\s+(launch|list|release|unveil|vote|decide|raise)|ahead\s+of|"
    r"next\s+(week|month)|due|tomorrow|next\s+week)\b", re.I)

# REALIZED markers: "listed", "launched", "approved", "happened"
_REALIZED_RE = re.compile(
    r"\b(announced|announces|approved|approves|rejected|rejects|reported|reports|"
    r"released|releases|launched|launches|listed|lists|filed|files|halted|halts|"
    r"hacked|hacks|surged|surges|crashed|crashes|plunged|plunges|jumped|jumps|"
    r"fell|rose|rises|beat|beats|missed|misses|dropped|drops|unveiled|unveils|"
    r"raised|raises|cut|cuts|soared|soars|just\s+now|happened)\b", re.I)

# CONTEXT markers: analysis, opinion, forecast
_CONTEXT_RE = re.compile(
    r"\b(analysis|prediction|forecast|recap|opinion|explain(ed|er)?|guide|outlook|"
    r"how\s+to|reasons?\s+to|what\s+to\s+know|looks?\s+cheap|"
    r"should\s+you\s+(buy|sell|hold)|buy,\s*sell,\s*or\s*hold|"
    r"is\s+it\s+a\s+good\s+investment|better\s+buy|fairly\s+valued|"
    r"ahead\s+of\s+earnings|the\s+real\s+reason)\b", re.I)

# STALE markers: "last week", "yesterday", "last month"
_STALE_RE = re.compile(
    r"\b(last\s+(week|month|quarter|year)|yesterday|ago|previously|earlier\s+this)\b", re.I)


def classify_temporal(
    text: str,
    source_kind: str = "",
    source_ts: str | None = None,
    event_type: str = "",
    phase_prior: str = "",
) -> dict:
    """Classify temporal phase for a source item.

    Args:
        text: headline/title text
        source_kind: listing / liquidations / news / rss_or_api
        source_ts: source timestamp string
        event_type: exchange_listing / liquidation_flow / news_trigger / ...
        phase_prior: from source_registry.yaml (realized/expected/mixed)

    Returns:
        phase: LEADING / REALIZED / COINCIDENT / FUTURE / AMBIGUOUS / STALE
        temporal_reason: human-readable reason
        is_stale: bool — whether item is too old to process
    """
    low = (text or "").lower()

    # 1) Liquidation flow is always COINCIDENT (happening now)
    if source_kind == "liquidations" or event_type == "liquidation_flow":
        return {
            "phase": "COINCIDENT",
            "temporal_reason": "liquidation_flow_happening_now",
            "is_stale": False,
        }

    # 2) Check for stale markers
    if _STALE_RE.search(low):
        # But "last week" in a listing announcement is still relevant
        if source_kind == "listing":
            return {
                "phase": "REALIZED",
                "temporal_reason": "listing_with_stale_marker_but_relevant",
                "is_stale": False,
            }
        return {
            "phase": "STALE",
            "temporal_reason": "stale_marker_found",
            "is_stale": True,
        }

    # 3) Check temporal markers in text
    has_future = bool(_FUTURE_RE.search(low))
    has_realized = bool(_REALIZED_RE.search(low))
    has_context = bool(_CONTEXT_RE.search(low))

    # 4) Source-specific phase assignment
    if source_kind == "listing":
        if has_future:
            return {
                "phase": "FUTURE",
                "temporal_reason": "listing_with_future_markers",
                "is_stale": False,
            }
        # Listings from official channels are LEADING (they happen before market moves)
        if phase_prior == "realized":
            return {
                "phase": "LEADING",
                "temporal_reason": "official_listing_source",
                "is_stale": False,
            }
        if has_realized:
            return {
                "phase": "REALIZED",
                "temporal_reason": "listing_with_realized_markers",
                "is_stale": False,
            }
        return {
            "phase": "LEADING",
            "temporal_reason": "listing_default_leading",
            "is_stale": False,
        }

    # 5) News/markettwits
    if source_kind == "news":
        if has_realized and not has_future:
            return {
                "phase": "REALIZED",
                "temporal_reason": "news_with_realized_markers",
                "is_stale": False,
            }
        if has_future and not has_realized:
            return {
                "phase": "FUTURE",
                "temporal_reason": "news_with_future_markers",
                "is_stale": False,
            }
        if has_context:
            return {
                "phase": "CONTEXT",
                "temporal_reason": "news_opinion_or_analysis",
                "is_stale": False,
            }
        return {
            "phase": "AMBIGUOUS",
            "temporal_reason": "news_no_clear_temporal_markers",
            "is_stale": False,
        }

    # 6) RSS/API — use source timestamp if available
    if source_ts:
        age_hours = _age_hours(source_ts)
        if age_hours is not None and age_hours > 168:  # > 7 days
            return {
                "phase": "STALE",
                "temporal_reason": f"source_too_old_{age_hours:.0f}h",
                "is_stale": True,
            }

    # 7) Fallback: use text markers
    if has_realized and not has_future:
        return {
            "phase": "REALIZED",
            "temporal_reason": "text_realized_markers",
            "is_stale": False,
        }
    if has_future and not has_realized:
        return {
            "phase": "FUTURE",
            "temporal_reason": "text_future_markers",
            "is_stale": False,
        }
    if has_context:
        return {
            "phase": "CONTEXT",
            "temporal_reason": "text_context_markers",
            "is_stale": False,
        }

    return {
        "phase": "AMBIGUOUS",
        "temporal_reason": "no_temporal_markers",
        "is_stale": False,
    }


def _age_hours(ts: str | None) -> float | None:
    """Calculate age in hours from timestamp string."""
    if not ts:
        return None
    try:
        if "T" in str(ts):
            parsed = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        else:
            parsed = dt.datetime.strptime(str(ts)[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        now = dt.datetime.now(dt.timezone.utc)
        return (now - parsed.astimezone(dt.timezone.utc)).total_seconds() / 3600
    except Exception:
        return None
