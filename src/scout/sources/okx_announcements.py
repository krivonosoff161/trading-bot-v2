# -*- coding: utf-8 -*-
"""OKX public announcements source.

Reads official OKX support announcements without API keys and emits pre-routed
scanner items for listing/trading-update announcements when a ticker can be
extracted from the title.
"""
from __future__ import annotations

import datetime as dt
import re

import requests

from src.scout.router import baseline_for_layer, classify_layer

BASE_URL = "https://www.okx.com"
ANNOUNCEMENTS_PATH = "/api/v5/support/announcements"
UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-okx-announcements; keyless)"}
TIMEOUT = 15

INTERESTING_TYPES = {
    "announcements-new-listings",
    "announcements-trading-updates",
    "announcements-delisting",
    "announcements-deposit-withdrawal-suspension",
    "announcements-event-contracts",
}

_PAIR_RE = re.compile(r"\b([A-Z0-9]{2,15})/(?:USDT|USD|EUR|BTC|ETH)\b")
_TOKEN_RE = re.compile(r"\b(?:list|launch|support|delist|suspend)[^\n]{0,90}\b([A-Z0-9]{2,15})\s+token\b", re.I)
_TICKER_QUOTE_RE = re.compile(r"\b([A-Z0-9]{2,15})\s*\((?:[A-Z][A-Za-z0-9 .'-]{2,80})\)")


def _ms_to_iso(value) -> str:
    try:
        ms = int(value)
        if ms > 0:
            return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        pass
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _announcement_rows() -> list[dict]:
    try:
        resp = requests.get(f"{BASE_URL}{ANNOUNCEMENTS_PATH}", headers=UA, timeout=TIMEOUT)
        payload = resp.json() or {}
    except Exception:
        return []
    if payload.get("code") != "0":
        return []
    data = payload.get("data") or {}
    rows: list[dict] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            nested = item.get("details")
            if isinstance(nested, list):
                rows.extend(row for row in nested if isinstance(row, dict))
            else:
                rows.append(item)
    else:
        rows = data.get("details") or data.get("announcements") or []
    return [row for row in rows if isinstance(row, dict)]


def _extract_assets(title: str) -> list[str]:
    out: list[str] = []
    for regex in (_PAIR_RE, _TOKEN_RE, _TICKER_QUOTE_RE):
        for match in regex.finditer(title or ""):
            sym = match.group(1).upper()
            if sym in {"OKX", "USD", "USDT", "BTC", "ETH", "EUR"}:
                continue
            if sym not in out:
                out.append(sym)
    return out[:5]


def _event_type(ann_type: str, title: str) -> str:
    low = f"{ann_type} {title}".lower()
    if "delist" in low:
        return "delisting"
    if "suspend" in low or "deposit" in low or "withdraw" in low:
        return "deposit_withdrawal_update"
    if "event contract" in low:
        return "event_contract"
    if "list" in low or "launch" in low:
        return "listing"
    return "trading_update"


def _phase(event_type: str, title: str) -> str:
    low = title.lower()
    if "will" in low or "to launch" in low or "to list" in low or "set to" in low:
        return "FUTURE"
    if event_type == "listing":
        return "LEADING"
    return "REALIZED"


def _row_to_items(row: dict) -> list[dict]:
    title = " ".join(str(row.get("title") or "").split()).strip()
    if not title:
        return []
    ann_type = str(row.get("annType") or row.get("type") or "").strip()
    if ann_type and ann_type not in INTERESTING_TYPES and not any(k in title.lower() for k in ("list", "launch", "delist", "deposit", "withdraw")):
        return []

    assets = _extract_assets(title)
    if not assets:
        return []

    url = str(row.get("url") or "").strip()
    if url and url.startswith("/"):
        url = f"{BASE_URL}{url}"
    published = _ms_to_iso(row.get("pTime") or row.get("businessPTime"))
    event_type = _event_type(ann_type, title)
    phase = _phase(event_type, title)
    ann_id = str(row.get("annId") or row.get("id") or row.get("url") or title)
    text = (
        f"Official OKX announcement: {title}. "
        f"Announcement type: {ann_type or 'unknown'}. "
        f"Published at {published}."
    )

    out: list[dict] = []
    for asset in assets:
        layer = classify_layer(asset)
        out.append(
            {
                "title": title,
                "text": text,
                "url": url or "https://www.okx.com/help/section/announcements-latest-announcements",
                "time": published,
                "source": "okx_announcements",
                "source_class": "api",
                "lead_class": "LEADING",
                "asset": asset,
                "okx_inst": f"{asset}-USDT-SWAP",
                "layer": layer,
                "baseline": baseline_for_layer(layer),
                "phase": phase,
                "event_type": event_type,
                "trigger_type": "okx_official_announcement",
                "event_key": f"okx_ann:{ann_id}:{asset}",
                "channel_kind": "listing" if event_type == "listing" else "news",
                "asset_class": "crypto_major" if layer == 1 else "crypto_alt",
                "trigger_role": "signal",
                "requires_context": False,
                "identity_reason": "okx_official_announcement",
                "identity_confidence": 0.9,
                "ann_type": ann_type,
            }
        )
    return out


def fetch_okx_announcements(limit: int = 12) -> list[dict]:
    """Return official OKX announcement items."""
    items: list[dict] = []
    for row in _announcement_rows():
        items.extend(_row_to_items(row))
        if len(items) >= limit:
            break
    return items[:limit]
