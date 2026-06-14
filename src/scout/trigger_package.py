# -*- coding: utf-8 -*-
"""
trigger_package.py — small reusable trigger package for LLM framing.

Consolidates all metadata about a trigger item into a single dict:
  - asset identity (asset_class, layer, baseline, confidence)
  - trigger semantics (trigger_role, event_type, channel_kind)
  - temporal phase (phase, temporal_reason)
  - context status (context_found, context_missing, context_summary)
  - flow context (for liquidations)
  - source text/title/url/time

This package is passed to cheap/chief LLM agents and written to
routing_audit / event blocks for analysis.
"""
from __future__ import annotations

from typing import Any


def build_trigger_package(
    item: dict,
    headline: str = "",
    body_text: str = "",
    source_ts: str | None = None,
    price: float | None = None,
    layer: int | None = None,
    baseline_sym: str | None = None,
    phase: str | None = None,
    trigger_context_pkg: dict | None = None,
    context_status: str | None = None,
) -> dict[str, Any]:
    """Build a compact trigger package from item + resolved metadata.

    Returns dict with all fields needed by LLM agents and audit trail.
    """
    return {
        "asset": item.get("asset"),
        "asset_class": item.get("asset_class"),
        "layer": layer or item.get("layer"),
        "baseline": baseline_sym or item.get("baseline"),
        "okx_inst": item.get("okx_inst"),
        "source_id": item.get("source") or item.get("source_id"),
        "channel_kind": item.get("channel_kind"),
        "trigger_role": item.get("trigger_role"),
        "event_type": item.get("event_type"),
        "phase": phase or item.get("phase"),
        "temporal_reason": item.get("temporal_reason"),
        "identity_reason": item.get("identity_reason"),
        "identity_confidence": item.get("identity_confidence"),
        "requires_context": item.get("requires_context"),
        "context_status": context_status,
        "context_found": trigger_context_pkg.get("context_found") if trigger_context_pkg else None,
        "context_missing": trigger_context_pkg.get("context_missing") if trigger_context_pkg else None,
        "context_summary": trigger_context_pkg.get("context_summary") if trigger_context_pkg else None,
        "context_model": trigger_context_pkg.get("context_model") if trigger_context_pkg else None,
        "flow_context": item.get("flow_context"),
        "headline": headline or item.get("title"),
        "text_excerpt": (body_text or item.get("text") or "")[:500],
        "source_ts": source_ts or item.get("time"),
        "price_at_decision": price,
        "url": item.get("url"),
    }


def format_for_llm_prompt(pkg: dict) -> str:
    """Format trigger package as concise text for LLM prompt injection.

    Keeps it short — just the key framing fields, not raw JSON.
    """
    parts = []
    if pkg.get("asset"):
        parts.append(f"АКТИВ: {pkg['asset']}")
    if pkg.get("asset_class"):
        parts.append(f"ТИП: {pkg['asset_class']}")
    if pkg.get("trigger_role"):
        parts.append(f"РОЛЬ: {pkg['trigger_role']}")
    if pkg.get("channel_kind"):
        parts.append(f"КАНАЛ: {pkg['channel_kind']}")
    if pkg.get("phase"):
        parts.append(f"ФАЗА: {pkg['phase']}")
    if pkg.get("context_status"):
        parts.append(f"КОНТЕКСТ: {pkg['context_status']}")
    if pkg.get("context_summary"):
        parts.append(f"КОНТЕКСТ_ДАННЫЕ: {pkg['context_summary']}")
    fc = pkg.get("flow_context")
    if fc:
        parts.append(f"FLOW: {fc.get('direction_hint')} ${fc.get('notional_usd')} @{fc.get('entry_price')}")
    return " · ".join(parts)
