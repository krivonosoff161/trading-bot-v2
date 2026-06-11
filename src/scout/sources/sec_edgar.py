# -*- coding: utf-8 -*-
"""
sec_edgar.py — опережающий источник L5: свежие филинги SEC (LEADING, official).

EDGAR getcurrent Atom-фид (keyless, нужен только описательный User-Agent — правило SEC).
Берём последние 8-K/S-1/424B по ВСЕМ компаниям, имя эмитента прогоняем через тот же
route_asset(allowed_layers={5}) — матч в нашей L5-вселенной (NVDA/TSLA/MSTR/COIN) → событие.
Новый L5-тикер = строка в entities.yaml, не правка кода. События pre-routed (актив/слой готовы).

Филинг = REALIZED, но раньше новостного цикла → lead_class LEADING.

Вторая часть модуля (аудит 11.06: ВСЕ sec-доки были title_only по 21-43 символа,
AVAX-карточка решалась по заголовку): экстракция machine_doc из самого филинга —
index-страница → primary document → чистый текст + структурные метаданные
(CIK/форма/accession/exhibits/items). Сетевая, но fetch инжектируется (тесты на фикстурах).
"""
from __future__ import annotations

import datetime as dt
import html as html_mod
import os
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

import requests

from src.scout.router import route_asset

# SEC требует контактный User-Agent (без ключа). Можно переопределить через .env.
UA = {"User-Agent": os.getenv("SEC_EDGAR_UA", "trading-bot-v2 scanner (keyless research)")}
_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_GETCURRENT = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
               "&type={form}&company=&dateb=&owner=include&count=100&output=atom")
_POLITE_DELAY_S = 0.2          # SEC fair-access: <10 req/s; мы сильно ниже


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


# ── machine_doc из филинга (index → primary doc → текст + метаданные) ────────
def _polite_get(url: str) -> str | None:
    """GET с SEC-friendly UA и паузой. None при любой ошибке (фолбэк = title_only)."""
    try:
        time.sleep(_POLITE_DELAY_S)
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None


def strip_filing_html(raw: str) -> str:
    """HTML филинга → читаемый текст (без bs4: script/style вон, теги вон, пробелы сжать)."""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw or "")
    s = re.sub(r"(?i)<(br|/p|/div|/tr|/li|/h\d)[^>]*>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def _index_meta(index_url: str, raw: str) -> dict:
    """CIK/accession из URL + компания/форма/дата из шапки index-страницы."""
    m = re.search(r"/Archives/edgar/data/(\d+)/(\d+)/([\d-]+)-index", index_url)
    cik = m.group(1) if m else None
    accession = m.group(3) if m else None
    company = None
    cm = re.search(r'(?is)class="companyName"[^>]*>(.*?)(?:<|\()', raw)
    if cm:
        company = html_mod.unescape(re.sub(r"\s+", " ", cm.group(1))).strip() or None
    form = None
    fm = re.search(r"(?is)<strong>\s*Form\s+([^<\s][^<]*?)\s*</strong>", raw) \
        or re.search(r"(?is)Type:\s*</strong>\s*([A-Z0-9/-]+)", raw) \
        or re.search(r"(?is)Form\s+([A-Z0-9/-]{1,12})\s*-", raw)
    if fm:
        form = fm.group(1).strip()
    filed = None
    dm = re.search(r'(?is)Filing Date\s*</div>\s*<div[^>]*>\s*(\d{4}-\d{2}-\d{2})', raw) \
        or re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if dm:
        filed = dm.group(1)
    return {"cik": cik, "accession": accession, "company_name": company,
            "form_type": form, "filed_at": filed, "filing_index_url": index_url}


def parse_filing_index(raw: str, index_url: str) -> dict | None:
    """index.htm → метаданные + primary_document_url + exhibits. None если таблицы нет."""
    if not raw:
        return None
    meta = _index_meta(index_url, raw)
    rows = re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", raw)
    primary = None
    exhibits: list[dict] = []
    for tr in rows:
        cells = re.findall(r"(?is)<td[^>]*>(.*?)</td>", tr)
        if len(cells) < 4:
            continue
        href_m = re.search(r'(?is)href="([^"]+)"', cells[2])
        if not href_m:
            continue
        href = href_m.group(1).strip()
        href = re.sub(r"^/?ix\?doc=", "", href)          # inline-XBRL viewer → прямой документ
        doc_url = urljoin(index_url, href)
        doc_type = strip_filing_html(cells[3]).strip()
        desc = strip_filing_html(cells[1]).strip()
        if not doc_url.lower().endswith((".htm", ".html", ".txt")):
            continue
        if "-index" in doc_url:
            continue
        if doc_type.upper().startswith("EX-"):
            exhibits.append({"type": doc_type, "description": desc, "url": doc_url})
            continue
        if doc_type.upper() in ("GRAPHIC", "XML", "ZIP", "JSON", "COVER"):
            continue
        if primary is None:
            primary = {"type": doc_type, "description": desc, "url": doc_url}
            if not meta.get("form_type") and doc_type:
                meta["form_type"] = doc_type
    if primary is None:
        return None
    meta["primary_document_url"] = primary["url"]
    meta["primary_document_type"] = primary.get("type")
    meta["exhibits"] = exhibits
    return meta


def extract_filing(index_url: str, *, fetch=None) -> dict | None:
    """index-URL филинга → {'title','text','method','metadata'} либо None (фолбэк title_only).

    fetch(url)->str|None инжектируется в тестах (фикстуры, без сети)."""
    fetch = fetch or _polite_get
    raw_index = fetch(index_url)
    if not raw_index:
        return None
    meta = parse_filing_index(raw_index, index_url)
    if not meta:
        return None
    raw_doc = fetch(meta["primary_document_url"])
    text = strip_filing_html(raw_doc) if raw_doc else ""
    if not text:
        return None
    if len(text) > 250_000:        # S-1 бывают мегабайтными — кап против раздувания буфера
        text = text[:250_000]
    items = []
    for it in re.findall(r"(?i)\bItem\s+(\d+\.\d+)", text):
        if it not in items:
            items.append(it)
    meta["items"] = items[:12]
    title = f"SEC {meta.get('form_type') or 'filing'}: {meta.get('company_name') or ''}".strip(": ")
    return {"title": title, "text": text, "method": "sec_primary_doc", "metadata": meta}


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
