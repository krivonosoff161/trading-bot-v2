# -*- coding: utf-8 -*-
"""
trigger_policy.py — channel-specific trigger policy loader.

Reads trigger_channel_policy.yaml and provides get_policy(source_id, source_meta)
to telegram_web.py and scanner_v0.py.

Deterministic, 0 LLM, 0 network. Used before LLM to decide:
  - trigger_role / event_type / requires_context defaults
  - unknown_ticker_policy (can it become L2?)
  - context_profile for context builder
  - flow_fields for liquidation parsing
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_CFG = Path(__file__).resolve().parent / "config" / "trigger_channel_policy.yaml"


@lru_cache(maxsize=1)
def _cfg() -> dict:
    return yaml.safe_load(_CFG.read_text(encoding="utf-8"))


def get_policy(source_id: str, source_meta: dict | None = None) -> dict:
    """Get trigger policy for a source.

    Args:
        source_id: e.g. "tg_new_listings_feed"
        source_meta: source_registry entry (for channel_kind fallback)

    Returns:
        dict with: channel_kind, default_trigger_role, default_event_type,
        requires_context, context_profile, unknown_ticker_policy,
        max_items_per_pass, phase_default, flow_fields, notes
    """
    cfg = _cfg()
    channels = cfg.get("channels", {})

    # Try exact source_id match first
    if source_id in channels:
        return dict(channels[source_id])

    # Try matching by telegram_kind from source_meta
    meta = source_meta or {}
    kind = str(meta.get("telegram_kind") or "").strip().lower()
    if kind:
        for ch_id, ch_policy in channels.items():
            if ch_id.startswith("_"):
                continue
            if ch_policy.get("channel_kind") == kind:
                return dict(ch_policy)

    # Fallback to _default_telegram for telegram_web sources
    if meta.get("source_class") == "telegram_web":
        return dict(channels.get("_default_telegram", _fallback()))

    # Fallback for non-telegram sources (RSS, API, etc.)
    return dict(_non_telegram_fallback())


def _fallback() -> dict:
    return {
        "channel_kind": "news",
        "default_trigger_role": "needs_context",
        "default_event_type": "news_trigger",
        "requires_context": True,
        "context_profile": "generic_news",
        "unknown_ticker_policy": "needs_context",
        "max_items_per_pass": 10,
        "phase_default": "AMBIGUOUS",
        "flow_fields": None,
        "notes": "fallback",
    }


def _non_telegram_fallback() -> dict:
    return {
        "channel_kind": "rss_or_api",
        "default_trigger_role": "signal",
        "default_event_type": "unclassified",
        "requires_context": False,
        "context_profile": None,
        "unknown_ticker_policy": "router_default",
        "max_items_per_pass": 50,
        "phase_default": "MIXED",
        "flow_fields": None,
        "notes": "non-telegram source, uses standard router path",
    }


def get_context_profile(profile_name: str | None) -> dict:
    """Get context profile rules by name."""
    if not profile_name:
        return {}
    cfg = _cfg()
    return dict((cfg.get("context_profiles") or {}).get(profile_name) or {})


def should_require_context(source_id: str, source_meta: dict | None,
                           asset_class: str | None = None) -> bool:
    """Determine if context is required, considering both channel policy and asset class.

    Channel policy provides the default; asset_class can override upward
    (e.g., tokenized_equity always needs context regardless of channel).
    """
    policy = get_policy(source_id, source_meta)
    channel_requires = policy.get("requires_context", True)

    # Asset class overrides: equity/tokenized/pre_ipo always need context
    if asset_class in ("tokenized_equity", "pre_ipo_equity"):
        return True
    if asset_class == "liquidation_flow":
        # Liquidation flow uses flow model — context from the flow itself
        return False

    return channel_requires
