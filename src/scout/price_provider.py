# -*- coding: utf-8 -*-
"""Instrument validation and price fetching with explicit reasons.

Purpose: instead of silently returning None when OKX returns 403, provide
structured reasons why price is unavailable.

Price reasons:
  - supported: price fetched successfully
  - instrument_not_listed: OKX does not list this instrument
  - provider_403: OKX API returned 403 (rate limit/auth)
  - provider_error: other API error
  - unsupported_asset_class: not a tradable crypto/perp
  - no_instrument: instrument ID is None
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parents[2]
UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 price-provider; keyless)"}
TIMEOUT = 15

_INSTRUMENT_CACHE: dict[str, dict] = {}
_CACHE_TTL_S = 3600


def _now() -> float:
    return time.monotonic()


def _is_cache_valid(entry: dict) -> bool:
    return (_now() - entry.get("ts", 0)) < _CACHE_TTL_S


def get_price(inst_id: str | None) -> tuple[float | None, str]:
    """Fetch price from OKX. Returns (price, reason)."""
    if not inst_id:
        return None, "no_instrument"

    cached = _INSTRUMENT_CACHE.get(inst_id)
    if cached and _is_cache_valid(cached):
        if cached.get("listed") is False:
            return None, cached.get("reason", "instrument_not_listed")
        if cached.get("price") is not None:
            return cached["price"], "supported"

    try:
        response = requests.get(
            "https://www.okx.com/api/v5/market/ticker",
            params={"instId": inst_id},
            headers=UA,
            timeout=TIMEOUT,
        )
        if response.status_code == 403:
            _INSTRUMENT_CACHE[inst_id] = {"listed": False, "reason": "provider_403", "ts": _now()}
            return None, "provider_403"
        if response.status_code == 404:
            _INSTRUMENT_CACHE[inst_id] = {
                "listed": False,
                "reason": "instrument_not_listed",
                "ts": _now(),
            }
            return None, "instrument_not_listed"

        payload = response.json()
        if str(payload.get("code")) == "0" and payload.get("data"):
            price = float(payload["data"][0]["last"])
            _INSTRUMENT_CACHE[inst_id] = {"listed": True, "price": price, "ts": _now()}
            return price, "supported"
        if str(payload.get("code")) == "51004":
            _INSTRUMENT_CACHE[inst_id] = {
                "listed": False,
                "reason": "instrument_not_listed",
                "ts": _now(),
            }
            return None, "instrument_not_listed"

        _INSTRUMENT_CACHE[inst_id] = {"listed": None, "reason": "provider_error", "ts": _now()}
        return None, "provider_error"
    except Exception:
        return None, "provider_error"


def classify_instrument(asset_class: str | None, okx_inst: str | None) -> tuple[str | None, str]:
    """Classify whether an instrument is tradable on OKX."""
    if not okx_inst:
        return None, "no_instrument"
    if asset_class in ("equity", "pre_ipo_equity", "tokenized_equity"):
        return okx_inst, "unsupported_asset_class"
    if asset_class == "unknown":
        return okx_inst, "unsupported_asset_class"
    return okx_inst, "supported"


def get_price_for_card(row: dict) -> tuple[float | None, str]:
    """Get price for a journal card row. Returns (price, reason)."""
    inst = row.get("okx_inst")
    asset_class = row.get("asset_class")

    inst, reason = classify_instrument(asset_class, inst)
    if reason != "supported":
        return None, reason

    return get_price(inst)


def price_stats_from_rows(rows: list[dict]) -> dict:
    """Count price availability across journal rows."""
    stats: dict[str, int] = {}
    for row in rows:
        _, reason = get_price_for_card(row)
        stats[reason] = stats.get(reason, 0) + 1
    return stats


def reset_cache() -> None:
    """Reset instrument cache for tests."""
    _INSTRUMENT_CACHE.clear()
