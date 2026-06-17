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
# Perp/dash pairs ("RE-USDT-SWAP", "GRAM-USDT") common in trading-update titles.
_PERP_RE = re.compile(r"\b([A-Z0-9]{2,15})-(?:USDT|USD|USDC)(?:-SWAP)?\b")
_EXCLUDE_TICKERS = {"OKX", "USD", "USDT", "USDC", "EUR", "BTC", "ETH", "SWAP", "API"}


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
    for regex in (_PAIR_RE, _PERP_RE, _TOKEN_RE, _TICKER_QUOTE_RE):
        for match in regex.finditer(title or ""):
            sym = match.group(1).upper()
            if sym in _EXCLUDE_TICKERS or sym.isdigit():
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
                # Provenance: official id/type/time/url preserved; body filled on demand.
                "announcement_id": ann_id,
                "detail_url": url or None,
                "extraction_status": "title_only",
            }
        )
    return out


def _detail_text(url: str, fetch) -> tuple[str, str]:
    """Best-effort detail-page body via an injected/lazy extractor → (text, status)."""
    if not url:
        return "", "no_detail_url"
    if fetch is None:
        from src.scout.page_extract import extract as fetch  # lazy: keep import tree light
    try:
        ext = fetch(url) or {}
    except Exception:
        return "", "detail_fetch_error"
    body = str(ext.get("text") or "").strip()
    if len(body) >= 200:
        return body, "api_detail_body"
    return "", "detail_too_short"


def fetch_okx_announcements(limit: int = 12, *, with_body: bool = False, fetch=None) -> list[dict]:
    """Return official OKX announcement items (LEADING, primary).

    ``with_body=True`` enriches each item with the detail-page body via ``fetch``
    (defaults to page_extract.extract). Off by default so the hot scanner loop adds
    no per-announcement network; the body is then resolved later in the buffer path.
    """
    items: list[dict] = []
    for row in _announcement_rows():
        items.extend(_row_to_items(row))
        if len(items) >= limit:
            break
    items = items[:limit]
    if with_body:
        body_cache: dict[str, tuple[str, str]] = {}
        for item in items:
            url = item.get("detail_url") or ""
            if url not in body_cache:
                body_cache[url] = _detail_text(url, fetch)
            body, status = body_cache[url]
            item["extraction_status"] = status
            if body:
                item["text"] = f"{item['text']} Detail: {body[:4000]}"
    return items
