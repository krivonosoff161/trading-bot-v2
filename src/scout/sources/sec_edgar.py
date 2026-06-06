# -*- coding: utf-8 -*-
"""
sec_edgar.py — опережающий источник L5: свежие филинги SEC (LEADING, official).

EDGAR getcurrent Atom-фид (keyless, нужен только описательный User-Agent — правило SEC).
Берём последние 8-K/S-1/424B по ВСЕМ компаниям, имя эмитента прогоняем через тот же
route_asset(allowed_layers={5}) — матч в нашей L5-вселенной (NVDA/TSLA/MSTR/COIN) → событие.
Новый L5-тикер = строка в entities.yaml, не правка кода. События pre-routed (актив/слой готовы).

Филинг = REALIZED, но раньше новостного цикла → lead_class LEADING.
"""
from __future__ import annotations

import datetime as dt
import os
import xml.etree.ElementTree as ET

import requests

from src.scout.router import route_asset

# SEC требует контактный User-Agent (без ключа). Можно переопределить через .env.
UA = {"User-Agent": os.getenv("SEC_EDGAR_UA", "trading-bot-v2 scanner (keyless research)")}
_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_GETCURRENT = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
               "&type={form}&company=&dateb=&owner=include&count=100&output=atom")


def _company_from_title(title: str) -> str:
    """'8-K - NVIDIA CORP (0001045810) (Filer)' → 'NVIDIA CORP' (для читаемого заголовка)."""
    rest = title.split(" - ", 1)[1] if " - " in title else title
    return rest.split(" (")[0].strip() or title


def _within(updated: str, cutoff: dt.datetime) -> bool:
    """True если updated свежее cutoff (или метку не распарсить — не режем)."""
    try:
        return dt.datetime.fromisoformat(updated) >= cutoff
    except (ValueError, TypeError):
        return True


def _fetch_form(form: str, within_hours: int) -> list[dict]:
    """Один тип формы → события по матчам L5-вселенной."""
    try:
        r = requests.get(_GETCURRENT.format(form=form), headers=UA, timeout=20)
        root = ET.fromstring(r.content)
    except Exception:
        return []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=within_hours)
    out = []
    for entry in root.findall("a:entry", _ATOM):
        title = (entry.findtext("a:title", default="", namespaces=_ATOM) or "").strip()
        updated = (entry.findtext("a:updated", default="", namespaces=_ATOM) or "").strip()
        link = entry.find("a:link", _ATOM)
        href = link.get("href") if link is not None else ""
        if not title or not _within(updated, cutoff):
            continue
        routed = route_asset(title, allowed_layers={5})   # имя эмитента → наш L5-тикер
        if not routed:
            continue
        company = _company_from_title(title)
        out.append({
            "title": f"SEC {form}: {company}",
            "url": href or f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}",
            "time": updated or None,
            "source": "sec_edgar",
            "source_class": "api",
            "lead_class": "LEADING",
            "asset": routed["asset"],
            "okx_inst": routed.get("okx_inst"),
            "layer": 5,
            "baseline": routed.get("baseline"),
            "phase": "REALIZED",
            "event_type": f"filing_{form.lower()}",
        })
    return out


def fetch_recent_filings(forms=("8-K", "S-1", "424B5"), within_hours: int = 24,
                         limit: int = 10) -> list[dict]:
    """Свежие филинги SEC по матчам L5, pre-routed. Дедуп (актив, форма). Пусто при ошибке."""
    out = []
    for form in forms:
        out.extend(_fetch_form(form, within_hours))
    out.sort(key=lambda e: e.get("time") or "", reverse=True)
    seen, uniq = set(), []
    for e in out:
        key = (e["asset"], e["event_type"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    return uniq[:limit]
