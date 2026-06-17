# -*- coding: utf-8 -*-
"""Trigger enrichment — assemble a structured package the moment a trigger fires.

When the scanner flags an asset, this gathers the deterministic context that
decides whether a farm job is even possible, in ONE structured object instead of
plain text:

    * OKX instrument resolution (does it trade? SWAP/SPOT? real instId?),
    * data readiness + selected timeframe (enough candles? which timeframe?),
    * recent buffer mentions of the same asset (corroboration, deduped),
    * an EXPLICIT body/extraction status (why the body is missing, not a template).

Everything is injectable (resolver / market-data provider / mentions function) so
it runs offline in tests and degrades gracefully: a provider failure yields a
``provider_error`` status, never an exception, and never blocks the news card.
Public/paper-only — it never resolves into an order path.
"""
from __future__ import annotations

from typing import Any, Callable

from src.research_lab import data_readiness_selector as RS
from src.scout.okx_instrument_resolver import OkxInstrumentResolver, resolve_instrument

# A SWAP that resolves AND has usable history is farm-eligible (paper research only).
FARM_USABLE = RS.STATUS_USABLE
MIN_BODY_CHARS = 200


def body_status(body_text: str | None, extraction_status: str | None = None) -> dict:
    """Explicit body availability — say WHY it is missing, not a placeholder."""
    n = len(str(body_text or "").strip())
    if n >= MIN_BODY_CHARS:
        return {"extracted": True, "status": "extracted", "chars": n, "reason": "ok"}
    reason = str(extraction_status or "").strip() or ("empty" if n == 0 else "too_short")
    return {"extracted": False, "status": "not_extracted", "chars": n, "reason": reason}


def _farm_gate(resolution: dict, readiness: dict) -> tuple[bool | None, str | None]:
    """(farm_eligible, farm_pending_reason) — resolved AND usable history; else why not."""
    if not resolution.get("resolved"):
        return False, resolution.get("reason") or "no_okx_instrument"
    status = readiness.get("status")
    if status is None:
        return None, "readiness_not_assessed"
    if status == FARM_USABLE:
        return True, None
    return False, status


def build_trigger_package(
    *,
    asset: str | None,
    okx_inst: str | None,
    asset_class: str | None = None,
    headline: str | None = None,
    body_text: str | None = None,
    source: str | None = None,
    extraction_status: str | None = None,
    list_time_ms: int | None = None,
    listing_state: str | None = None,
    resolver: OkxInstrumentResolver | None = None,
    provider: Any | None = None,
    mentions_fn: Callable[[str], list[dict]] | None = None,
    now_ms: int | None = None,
) -> dict:
    """Build the structured trigger package. ``provider=None`` skips readiness.

    Resolution always runs (cheap, cached). Readiness runs only when a market-data
    ``provider`` is supplied. Mentions run only when ``mentions_fn`` is supplied.
    """
    query = okx_inst or asset or ""
    resolution = resolver.resolve(query) if resolver is not None else resolve_instrument(query)

    # Prefer the OKX-verified class; fall back to the identity-supplied class.
    okx_class = resolution.get("asset_class") if resolution.get("resolved") else None
    eff_class = okx_class or asset_class or "unknown"

    readiness: dict = {"status": None, "selected_timeframe": None, "rows": 0,
                       "reason": "not_assessed", "checked": {}}
    if provider is not None and resolution.get("resolved"):
        readiness = RS.select_timeframe(
            provider,
            resolution["primary_inst_id"],
            candidate_timeframes=RS.timeframes_for_asset_class(eff_class),
            list_time_ms=list_time_ms or resolution.get("list_time_ms"),
            listing_state=listing_state or resolution.get("state"),
            now_ms=now_ms,
        )

    mentions: list[dict] = []
    if mentions_fn is not None and asset:
        try:
            mentions = list(mentions_fn(asset))
        except Exception:
            mentions = []

    farm_eligible, farm_pending_reason = _farm_gate(resolution, readiness)
    body = body_status(body_text, extraction_status)

    return {
        "asset": (asset or "").upper() or None,
        "trigger": {"headline": headline, "source": source},
        "resolution": resolution,
        "readiness": readiness,
        "body": body,
        "mentions": mentions,
        "mention_count": len(mentions),
        "okx_asset_class": eff_class if resolution.get("resolved") else None,
        "farm_eligible": farm_eligible,
        "farm_pending_reason": farm_pending_reason,
    }


def package_to_journal_fields(pkg: dict) -> dict:
    """Flatten a trigger package into the new scanner-journal/build_row kwargs."""
    res = pkg.get("resolution") or {}
    rd = pkg.get("readiness") or {}
    return {
        "okx_resolved": res.get("resolved"),
        "okx_inst_type": res.get("inst_type"),
        "okx_asset_class": pkg.get("okx_asset_class"),
        "okx_resolution_reason": res.get("reason"),
        "data_readiness_status": rd.get("status"),
        "selected_timeframe": rd.get("selected_timeframe"),
        "farm_eligible": pkg.get("farm_eligible"),
        "farm_pending_reason": pkg.get("farm_pending_reason"),
    }
