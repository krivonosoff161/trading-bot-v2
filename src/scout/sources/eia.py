# -*- coding: utf-8 -*-
"""
eia.py - expected L4 source from EIA's Weekly Petroleum Status Report page.

This source uses EIA's official Weekly Petroleum Status Report page to read the
next release date for the inventory report. It is keyless and intended only to
open pending expected events for crude.

Вторая часть (P1 аудита 11.06): SURPRISE-интерфейс — «запасы упали» слабо, нужен
actual vs consensus. build_surprise_record() = офлайн-нормализация (фикстуры/ручной
ввод); fetch_actual_crude_stocks() = живой actual через EIA open data API v2
(нужен бесплатный EIA_API_KEY; нет ключа → честный None, graceful-disabled).
Консенсуса в бесплатном API нет → запись partial: surprise=None, помечено явно.
Торговых решений из этого модуля НЕ делается — только данные для pending/chief-контекста.
"""
from __future__ import annotations

import datetime as dt
import os
import re

import requests

from src.scout.router import tracked_assets

UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-eia; keyless-web)"}
WPSR_URL = "https://www.eia.gov/petroleum/supply/weekly/"
TIMEOUT = 20

_NEXT_RELEASE_RE = re.compile(r"Next Release Date:\s*([A-Za-z]+ \d{1,2}, \d{4})", re.I)

# EIA open data v2: еженедельные запасы сырой нефти США (серия WCESTUS1, тыс. барр.)
API_URL = "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
CRUDE_STOCKS_SERIES = "WCESTUS1"


# ── surprise-интерфейс (actual vs consensus) ─────────────────────────────────
def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_surprise_record(*, report_ts: str, actual_change_mbbl=None,
                          consensus_change_mbbl=None, previous_change_mbbl=None,
                          source: str = "eia_api",
                          series: str = CRUDE_STOCKS_SERIES) -> dict:
    """Нормализованная запись сюрприза недельных запасов (млн барр., change w/w).

    Консенсус недоступен → partial-запись: surprise_mbbl=None,
    surprise_available=False, direction_hint='unavailable' (не выдумываем)."""
    actual = _num(actual_change_mbbl)
    consensus = _num(consensus_change_mbbl)
    previous = _num(previous_change_mbbl)
    surprise = (actual - consensus) if (actual is not None and consensus is not None) else None
    if surprise is None:
        direction = "unavailable"
    elif surprise < 0:
        direction = "bullish_oil"      # запасы НИЖЕ ожиданий → дефицит → поддержка цены
    elif surprise > 0:
        direction = "bearish_oil"      # запасы ВЫШЕ ожиданий
    else:
        direction = "inline"
    return {
        "report_ts": report_ts,
        "series": series,
        "actual_change_mbbl": actual,
        "consensus_change_mbbl": consensus,
        "previous_change_mbbl": previous,
        "surprise_mbbl": round(surprise, 3) if surprise is not None else None,
        "surprise_available": surprise is not None,
        "direction_hint": direction,   # интерпретация данных, НЕ торговый сигнал
        "source": source,
    }


def surprise_status() -> dict:
    """Для отчётов: graceful-disabled без бесплатного EIA_API_KEY."""
    configured = bool(os.getenv("EIA_API_KEY", "").strip())
    return {"configured": configured, "provider": "eia_open_data_v2",
            "reason": None if configured else "not_configured: EIA_API_KEY отсутствует "
                                              "(бесплатный, https://www.eia.gov/opendata/)"}


def fetch_actual_crude_stocks(api_key: str | None = None, http=None) -> dict | None:
    """Живой actual (2 последние недели → change w/w) через EIA API. Без ключа → None.

    Консенсус в бесплатном API отсутствует → запись partial (surprise unavailable).
    http инжектируется в тестах. Любой сбой → None (петля не падает)."""
    key = (api_key or os.getenv("EIA_API_KEY", "")).strip()
    if not key:
        return None
    try:
        if http is None:
            import requests as http  # noqa: PLC0415
        resp = http.get(API_URL, params={
            "api_key": key, "frequency": "weekly", "data[0]": "value",
            "facets[series][]": CRUDE_STOCKS_SERIES,
            "sort[0][column]": "period", "sort[0][direction]": "desc", "length": "3",
        }, headers=UA, timeout=TIMEOUT)
        rows = ((resp.json() or {}).get("response") or {}).get("data") or []
        vals = [(str(r.get("period")), _num(r.get("value"))) for r in rows]
        vals = [(p, v) for p, v in vals if v is not None]
        if len(vals) < 2:
            return None
        latest, prev = vals[0], vals[1]
        actual_change = (latest[1] - prev[1]) / 1000.0          # тыс.барр. → млн барр.
        prev_change = ((prev[1] - vals[2][1]) / 1000.0) if len(vals) >= 3 else None
        return build_surprise_record(
            report_ts=latest[0], actual_change_mbbl=round(actual_change, 3),
            previous_change_mbbl=round(prev_change, 3) if prev_change is not None else None)
    except Exception:
        return None


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
