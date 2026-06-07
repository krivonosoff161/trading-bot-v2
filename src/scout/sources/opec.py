# -*- coding: utf-8 -*-
"""
opec.py - expected L4 source from OPEC official press releases.

The source looks for the latest OPEC/OPEC+ meeting release that explicitly
states the date of the next meeting. It is keyless and opens a pending event
for crude.
"""
from __future__ import annotations

import datetime as dt
import re
from html import unescape
from urllib.parse import urljoin

import requests

from src.scout.router import tracked_assets

UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-opec; keyless-web)"}
INDEX_URL = "https://www.opec.org/press-releases.html"
BASE_URL = "https://www.opec.org/"
TIMEOUT = 20

_LINK_RE = re.compile(r'href="([^"]+)"[^>]*>\s*Read more\s*<', re.I)
_NEXT_RE = re.compile(
    r"(?:The next meeting of the JMMC.*?scheduled for|will meet on)\s+([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})",
    re.I | re.S,
)
_TITLE_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.I | re.S)


def _tracked_crude() -> dict | None:
    for row in tracked_assets(layer=4):
        if str(row.get("sym") or "").upper() == "CL":
            return row
    return None


def _parse_date(raw: str) -> str | None:
    try:
        dt_obj = dt.datetime.strptime(" ".join(raw.split()), "%d %B %Y").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None
    return dt_obj.strftime("%Y-%m-%dT10:00:00Z")


def _clean_title(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return " ".join(unescape(text).split())


def fetch_opec_schedule(limit: int = 2) -> list[dict]:
    tracked = _tracked_crude()
    if not tracked or limit <= 0:
        return []

    try:
        index_html = requests.get(INDEX_URL, headers=UA, timeout=TIMEOUT).text
    except Exception:
        return []

    links = _LINK_RE.findall(index_html)
    for href in links[:8]:
        article_url = urljoin(BASE_URL, href)
        try:
            article_html = requests.get(article_url, headers=UA, timeout=TIMEOUT).text
        except Exception:
            continue
        next_match = _NEXT_RE.search(article_html)
        if not next_match:
            continue
        release_ts = _parse_date(next_match.group(1))
        if not release_ts:
            continue
        title_match = _TITLE_RE.search(article_html)
        release_title = _clean_title(title_match.group(1) if title_match else "OPEC meeting update")
        title = f"OPEC/OPEC+ meeting due {release_ts[:10]}"
        text = (
            f"OPEC official release '{release_title}' states the next meeting is scheduled for {release_ts[:10]}. "
            "Production decisions and guidance can move crude and energy proxies."
        )
        return [
            {
                "title": title,
                "text": text,
                "url": article_url,
                "time": release_ts,
                "source": "opec",
                "source_class": "web",
                "lead_class": "LEADING",
                "asset": "CL",
                "okx_inst": tracked.get("okx_inst"),
                "layer": 4,
                "baseline": tracked.get("baseline"),
                "phase": "EXPECTED",
                "event_type": "opec",
                "trigger_type": "energy_calendar",
                "event_key": f"opec:meeting:CL:{release_ts[:10]}",
                "release_title": release_title,
            }
        ][:limit]
    return []
