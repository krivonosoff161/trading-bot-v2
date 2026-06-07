# -*- coding: utf-8 -*-
"""
earnings_calendar.py - expected L5 source from official SEC-linked earnings announcements.

Instead of scraping an algorithmic public calendar, this source scans fresh SEC
8-K current filings for tracked L5 issuers and extracts future earnings release
dates from the filing text. That keeps the source official-derived and narrow.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import xml.etree.ElementTree as ET
from html import unescape

import requests

from src.scout.router import route_asset

UA = {"User-Agent": os.getenv("SEC_EDGAR_UA", "trading-bot-v2 scanner (keyless research)")}
ATOM = {"a": "http://www.w3.org/2005/Atom"}
GETCURRENT = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
    "&type=8-K&company=&dateb=&owner=include&count=100&output=atom"
)
TIMEOUT = 20
WITHIN_DAYS = 45

_DATE_RE = re.compile(
    r"\b("
    r"January|February|March|April|May|June|July|August|September|October|November|December"
    r")\s+\d{1,2},\s+\d{4}\b",
    re.I,
)
_EARNINGS_HINT_RE = re.compile(
    r"(earnings|financial results|quarterly results|annual results|conference call|shareholder letter)",
    re.I,
)
_FUTURE_HINT_RE = re.compile(
    r"(will release|will report|scheduled to release|scheduled for|announce.*results on|conference call on|to discuss .* results on)",
    re.I,
)


def _within(updated: str, cutoff: dt.datetime) -> bool:
    try:
        return dt.datetime.fromisoformat(updated) >= cutoff
    except Exception:
        return True


def _company_from_title(title: str) -> str:
    rest = title.split(" - ", 1)[1] if " - " in title else title
    return rest.split(" (")[0].strip() or title


def _clean_text(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(unescape(text).split())


def _extract_earnings_date(text: str, today: dt.date) -> str | None:
    if not _EARNINGS_HINT_RE.search(text):
        return None
    for m in _DATE_RE.finditer(text):
        raw = m.group(0)
        left = text[max(0, m.start() - 180):m.start()]
        if not _FUTURE_HINT_RE.search(left) and "will" not in left.lower():
            continue
        try:
            when = dt.datetime.strptime(raw, "%B %d, %Y").date()
        except Exception:
            continue
        if when < today:
            continue
        return when.strftime("%Y-%m-%dT00:00:00Z")
    return None


def _fetch_feed() -> list[dict]:
    try:
        root = ET.fromstring(requests.get(GETCURRENT, headers=UA, timeout=TIMEOUT).content)
    except Exception:
        return []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=WITHIN_DAYS)
    out: list[dict] = []
    for entry in root.findall("a:entry", ATOM):
        title = (entry.findtext("a:title", default="", namespaces=ATOM) or "").strip()
        updated = (entry.findtext("a:updated", default="", namespaces=ATOM) or "").strip()
        link = entry.find("a:link", ATOM)
        href = link.get("href") if link is not None else ""
        if not title or not href or not _within(updated, cutoff):
            continue
        routed = route_asset(title, allowed_layers={5})
        if not routed:
            continue
        out.append(
            {
                "title": title,
                "updated": updated,
                "href": href,
                "asset": routed["asset"],
                "okx_inst": routed.get("okx_inst"),
                "baseline": routed.get("baseline"),
            }
        )
    return out


def fetch_earnings_calendar(limit: int = 8) -> list[dict]:
    today = dt.datetime.now(dt.timezone.utc).date()
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for filing in _fetch_feed():
        try:
            html = requests.get(filing["href"], headers=UA, timeout=TIMEOUT).text
        except Exception:
            continue
        text = _clean_text(html)
        release_ts = _extract_earnings_date(text, today)
        if not release_ts:
            continue
        key = (filing["asset"], release_ts[:10])
        if key in seen:
            continue
        seen.add(key)
        company = _company_from_title(filing["title"])
        out.append(
            {
                "title": f"{filing['asset']} earnings due {release_ts[:10]}",
                "text": (
                    f"Recent SEC-linked company announcement for {company} states the next earnings or results event is due "
                    f"on {release_ts[:10]}. Use this as expected earnings cadence, not as a trade verdict."
                ),
                "url": filing["href"],
                "time": release_ts,
                "source": "earnings_calendar",
                "source_class": "api",
                "lead_class": "LEADING",
                "asset": filing["asset"],
                "okx_inst": filing["okx_inst"],
                "layer": 5,
                "baseline": filing["baseline"],
                "phase": "EXPECTED",
                "event_type": "earnings",
                "trigger_type": "earnings_calendar",
                "event_key": f"earnings:{filing['asset']}:{release_ts[:10]}",
            }
        )
        if len(out) >= limit:
            break
    return out
