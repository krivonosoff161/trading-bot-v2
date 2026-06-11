# -*- coding: utf-8 -*-
"""
token_unlocks.py - L2 expected source for upcoming unlock events.

Official source: Tokenomist Upcoming Unlock Events API v5.
This source is not keyless. It requires TOKENOMIST_API_KEY.
If the key is absent, the source stays silent.
"""
from __future__ import annotations

import datetime as dt
import os

import requests

from src.scout.router import tracked_assets

UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-token-unlocks; api)"}
API_URL = "https://api.tokenomist.ai/v5/unlock/events/upcoming"
TIMEOUT = 20

WINDOW_DAYS = 7
PAGE_SIZE = 200
MAX_PAGES = 3
MIN_UNLOCK_VALUE_USD = 1_000_000.0
MIN_VALUE_TO_MCAP_PCT = 0.5


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tracked_l2_index() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in tracked_assets(layer=2):
        sym = str(row.get("sym") or "").upper()
        if not sym:
            continue
        out[sym] = row
    return out


def _allocation_summary(cliff: dict) -> str:
    parts = []
    for row in (cliff.get("allocationBreakdown") or [])[:3]:
        name = row.get("standardAllocationName") or row.get("allocationName")
        value = _safe_float(row.get("cliffValue"))
        if name and value is not None:
            parts.append(f"{name}: ${value:,.0f}")
    return "; ".join(parts)


def _build_item(token: dict, tracked: dict) -> dict | None:
    symbol = str(token.get("tokenSymbol") or "").upper()
    upcoming = token.get("upcomingEvent") or {}
    unlock_date = upcoming.get("unlockDate")
    cliff = upcoming.get("cliffUnlocks") or {}
    total_value = _safe_float(cliff.get("cliffValue"))
    total_amount = _safe_float(cliff.get("cliffAmount"))
    value_to_mcap = _safe_float(cliff.get("valueToMarketCap"))
    market_cap = _safe_float(token.get("marketCap"))
    released_pct = _safe_float(token.get("releasedPercentage"))

    if not unlock_date or total_value is None or total_value < MIN_UNLOCK_VALUE_USD:
        return None
    if value_to_mcap is None or value_to_mcap < MIN_VALUE_TO_MCAP_PCT:
        return None

    token_id = str(token.get("tokenId") or symbol.lower())
    title = (
        f"{symbol} token unlock due {unlock_date[:10]}: "
        f"${total_value:,.0f} ({value_to_mcap:.2f}% mcap)"
    )
    allocation_summary = _allocation_summary(cliff)
    text = (
        f"Tokenomist upcoming unlock for {token.get('tokenName') or symbol}. "
        f"Unlock date {unlock_date}, total cliff value ${total_value:,.0f}, "
        f"value to market cap {value_to_mcap:.2f}%, amount {total_amount if total_amount is not None else 'n/a'} tokens. "
        f"Released supply {released_pct if released_pct is not None else 'n/a'}%, "
        f"market cap ${market_cap:,.0f}."
    )
    if allocation_summary:
        text += f" Allocation breakdown: {allocation_summary}."

    return {
        "title": title,
        "text": text,
        "url": f"https://tokenomist.ai/{token_id}",
        "time": unlock_date,
        "source": "token_unlocks",
        "source_class": "api",
        "lead_class": "LEADING",
        "asset": symbol,
        "okx_inst": tracked.get("okx_inst"),
        "layer": 2,
        "baseline": tracked.get("baseline"),
        "phase": "EXPECTED",
        "event_type": "unlock",
        "trigger_type": "token_unlock_calendar",
        "event_key": f"unlock:{symbol}:{unlock_date}",
        "token_id": token_id,
        "unlock_value_usd": total_value,
        "unlock_value_to_mcap_pct": value_to_mcap,
        "released_supply_pct": released_pct,        # None = API не дал
        "allocation_breakdown": cliff.get("allocationBreakdown") or [],
        "source_quality": "primary",                # Tokenomist API, точная дата/сумма
    }


def unlocks_status() -> dict:
    """Для отчётов: graceful-disabled без ключа (источник молчит by design)."""
    configured = bool(os.getenv("TOKENOMIST_API_KEY", "").strip())
    return {"configured": configured, "provider": "tokenomist",
            "reason": None if configured else "not_configured: TOKENOMIST_API_KEY отсутствует"}


def fetch_upcoming_unlocks(limit: int = 12) -> list[dict]:
    api_key = os.getenv("TOKENOMIST_API_KEY", "").strip()
    if not api_key:
        return []

    tracked = _tracked_l2_index()
    if not tracked:
        return []

    today = dt.datetime.now(dt.timezone.utc).date()
    end = today + dt.timedelta(days=WINDOW_DAYS)
    headers = {**UA, "x-api-key": api_key}
    params = {
        "start": today.isoformat(),
        "end": end.isoformat(),
        "page": 1,
        "pageSize": PAGE_SIZE,
        "minTotalUnlockAmount": MIN_UNLOCK_VALUE_USD,
        "minValueToMarketCap": MIN_VALUE_TO_MCAP_PCT,
    }

    out: list[dict] = []
    seen: set[str] = set()

    for page in range(1, MAX_PAGES + 1):
        params["page"] = page
        try:
            resp = requests.get(API_URL, params=params, headers=headers, timeout=TIMEOUT)
            payload = resp.json() or {}
        except Exception:
            break

        rows = payload.get("data") or []
        for token in rows:
            symbol = str(token.get("tokenSymbol") or "").upper()
            if symbol not in tracked or symbol in seen:
                continue
            item = _build_item(token, tracked[symbol])
            if not item:
                continue
            out.append(item)
            seen.add(symbol)
            if len(out) >= limit:
                return out

        meta = payload.get("metadata") or {}
        total_pages = int(meta.get("totalPages") or 1)
        if page >= total_pages:
            break

    return out
