# -*- coding: utf-8 -*-
"""
okx_listings.py — опережающий источник: новые листинги OKX (LEADING).

Сейчас отдаёт только «ПРОИЗОШЛО» (REALIZED, инструмент недавно стал live) — это уже
раньше новостного цикла. «БУДЕТ» (preopen/upcoming) сейчас в public-эндпойнте нет
(все live) → придёт из OKX announcements позже (Этап 4+).

Открытая вселенная: актив берём из ИНСТРУМЕНТА (не из заголовка), слой — classify_layer
(ASML→5 акции, EWT→2 крипта). baseline — per-layer. События pre-routed (актив/слой готовы).
Keyless, тот же okx public, что в репо.
"""
from __future__ import annotations

import datetime as dt
import time

import requests

from src.scout.router import classify_layer, baseline_for_layer

UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-listings; keyless)"}


def fetch_new_listings(within_hours: int = 24, limit: int = 10) -> list[dict]:
    """OKX SWAP → события свежих листингов (REALIZED), pre-routed. Пусто при ошибке."""
    try:
        r = requests.get("https://www.okx.com/api/v5/public/instruments",
                         params={"instType": "SWAP"}, headers=UA, timeout=20)
        data = r.json().get("data", [])
    except Exception:
        return []
    cutoff_ms = (time.time() - within_hours * 3600) * 1000
    fresh = []
    for i in data:
        lt = int(i.get("listTime") or 0)
        if i.get("state") != "live" or lt < cutoff_ms:
            continue
        inst = i.get("instId", "")
        base = inst.split("-")[0]
        layer = classify_layer(base)
        listed = dt.datetime.utcfromtimestamp(lt / 1000).strftime("%Y-%m-%dT%H:%M:%SZ")
        fresh.append({
            "title": f"Новый листинг на OKX: {inst}",
            "url": f"https://www.okx.com/trade-swap/{inst.lower()}",
            "time": listed,
            "source": "okx_listings",
            "source_class": "ws",
            "lead_class": "LEADING",            # биржа-primary, раньше новостного цикла
            "asset": base,
            "okx_inst": inst,
            "layer": layer,
            "baseline": baseline_for_layer(layer),
            "phase": "REALIZED",                # листинг состоялся (preopen=будет придёт позже)
            "event_type": "listing",
            "list_time_ms": lt,
        })
    fresh.sort(key=lambda e: e["list_time_ms"], reverse=True)
    return fresh[:limit]
