# -*- coding: utf-8 -*-
"""
eia.py - expected L4 source from EIA's Weekly Petroleum Status Report page.

This source uses EIA's official Weekly Petroleum Status Report page to read the
next release date for the inventory report. It is keyless and intended only to
open pending expected events for crude.
"""
from __future__ import annotations

import datetime as dt
import re

import requests

from src.scout.router import tracked_assets

UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-eia; keyless-web)"}
WPSR_URL = "https://www.eia.gov/petroleum/supply/weekly/"
TIMEOUT = 20

_NEXT_RELEASE_RE = re.compile(r"Next Release Date:\s*([A-Za-z]+ \d{1,2}, \d{4})", re.I)


def _tracked_crude() -> dict | None:
    for row in tracked_assets(layer=4):
        if str(row.get("sym") or "").upper() == "CL":
            return row
    return None


def _parse_release_date(raw: str) -> str | None:
    try:
        dt_obj = dt.datetime.strptime(raw.strip(), "%B %d, %Y").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None
    return dt_obj.strftime("%Y-%m-%dT15:30:00Z")  # 10:30 ET baseline


def fetch_eia_schedule(limit: int = 2) -> list[dict]:
    tracked = _tracked_crude()
    if not tracked or limit <= 0:
        return []

    try:
        text = requests.get(WPSR_URL, headers=UA, timeout=TIMEOUT).text
    except Exception:
        return []

    m = _NEXT_RELEASE_RE.search(text)
    if not m:
        return []
    release_ts = _parse_release_date(m.group(1))
    if not release_ts:
        return []

    title = f"EIA weekly petroleum inventory due {release_ts[:10]}"
    text = (
        "EIA Weekly Petroleum Status Report next release is scheduled. "
        "Crude inventory, products, and stockpile deltas can move oil and energy proxies."
    )
    return [
        {
            "title": title,
            "text": text,
            "url": WPSR_URL,
            "time": release_ts,
            "source": "eia",
            "source_class": "web",
            "lead_class": "LEADING",
            "asset": "CL",
            "okx_inst": tracked.get("okx_inst"),
            "layer": 4,
            "baseline": tracked.get("baseline"),
            "phase": "EXPECTED",
            "event_type": "inventory",
            "trigger_type": "energy_calendar",
            "event_key": f"eia:inventory:CL:{release_ts[:10]}",
        }
    ][:limit]
