# -*- coding: utf-8 -*-
"""
fred_calendar.py - expected macro source for L1/L3.

Uses the official FRED release calendar to open pending macro events for the
next few weeks. The source is intentionally narrow:
  - CPI
  - FOMC
  - Employment Situation / jobs

Requires FRED_API_KEY. If the key is absent, the source stays silent.
"""
from __future__ import annotations

import datetime as dt
import os

import requests

from src.scout.router import tracked_assets

UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-fred-calendar; api)"}
API_URL = "https://api.stlouisfed.org/fred/releases/dates"
TIMEOUT = 20
WINDOW_DAYS = 21
LIMIT = 200

TARGETS = {
    "cpi": {
        "label": "CPI",
        "patterns": ("consumer price index",),
        "assets": ("BTC", "XAU"),
        "expectation": "Consumer inflation release due; macro sensitivity for crypto and gold.",
    },
    "fomc": {
        "label": "FOMC",
        "patterns": ("federal open market committee", "fomc"),
        "assets": ("BTC", "XAU"),
        "expectation": "FOMC policy decision due; rate path and risk appetite may reprice.",
    },
    "jobs": {
        "label": "Employment Situation",
        "patterns": ("employment situation",),
        "assets": ("BTC", "XAU"),
        "expectation": "US labor-market release due; rate expectations may move.",
    },
}


def _tracked_index() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in tracked_assets():
        sym = str(row.get("sym") or "").upper()
        if sym:
            out[sym] = row
    return out


def _parse_date(raw: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(raw or "")[:10])
    except Exception:
        return None


def _target_key(name: str) -> str | None:
    low = str(name or "").lower()
    for key, meta in TARGETS.items():
        if any(pat in low for pat in meta["patterns"]):
            return key
    return None


def _build_item(*, event_key: str, target_key: str, release_name: str, release_id: int | str, release_date: str, tracked: dict) -> dict:
    meta = TARGETS[target_key]
    symbol = str(tracked.get("sym") or "").upper()
    title = f"{meta['label']} due {release_date}: macro event on FRED calendar"
    text = (
        f"FRED release calendar shows {release_name} scheduled for {release_date}. "
        f"{meta['expectation']}"
    )
    return {
        "title": title,
        "text": text,
        "url": f"https://fred.stlouisfed.org/release?rid={release_id}",
        "time": f"{release_date}T00:00:00Z",
        "source": "fred_calendar",
        "source_class": "api",
        "lead_class": "LEADING",
        "asset": symbol,
        "okx_inst": tracked.get("okx_inst"),
        "layer": tracked.get("layer"),
        "baseline": tracked.get("baseline"),
        "phase": "EXPECTED",
        "event_type": target_key,
        "trigger_type": "macro_calendar",
        "event_key": event_key,
        "release_name": release_name,
        "release_id": release_id,
        "calendar_family": "macro_release",
    }


def fetch_fred_calendar(limit: int = 12) -> list[dict]:
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return []

    tracked = _tracked_index()
    if "BTC" not in tracked or "XAU" not in tracked:
        return []

    today = dt.datetime.now(dt.timezone.utc).date()
    end = today + dt.timedelta(days=WINDOW_DAYS)
    params = {
        "api_key": api_key,
        "file_type": "json",
        "realtime_start": today.isoformat(),
        "limit": LIMIT,
        "offset": 0,
        "order_by": "release_date",
        "sort_order": "asc",
        "include_release_dates_with_no_data": "true",
    }

    try:
        payload = requests.get(API_URL, params=params, headers=UA, timeout=TIMEOUT).json() or {}
    except Exception:
        return []

    rows = payload.get("release_dates") or []
    chosen: dict[str, tuple[str, int | str, str]] = {}
    for row in rows:
        release_name = str(row.get("release_name") or "")
        release_id = row.get("release_id")
        release_date = str(row.get("date") or "")
        day = _parse_date(release_date)
        if not release_name or release_id is None or day is None:
            continue
        if day < today or day > end:
            continue
        key = _target_key(release_name)
        if not key or key in chosen:
            continue
        chosen[key] = (release_name, release_id, release_date)
        if len(chosen) == len(TARGETS):
            break

    out: list[dict] = []
    for target_key, (release_name, release_id, release_date) in chosen.items():
        for symbol in TARGETS[target_key]["assets"]:
            tracked_row = tracked.get(symbol)
            if not tracked_row:
                continue
            event_key = f"fred:{target_key}:{symbol}:{release_date}"
            out.append(
                _build_item(
                    event_key=event_key,
                    target_key=target_key,
                    release_name=release_name,
                    release_id=release_id,
                    release_date=release_date,
                    tracked=tracked_row,
                )
            )
            if len(out) >= limit:
                return out

    return out
