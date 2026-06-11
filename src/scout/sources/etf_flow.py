# -*- coding: utf-8 -*-
"""
etf_flow.py — L1 КОНТЕКСТ-обогащение: дневные потоки спот-ETF (BTC/ETH).

Это НЕ источник карточек и НЕ сигнал: модуль не создаёт событий, не зовёт chief и
не может породить GO. Он даёт одну строку рыночного фона для L1-кандидатов
(scanner_v0 подмешивает её в market_ctx, решение остаётся за существующими гейтами).

Провайдеры (env SCANNER_ETF_FLOW_PROVIDER):
  ""           — выключено (дефолт): fetch → [], context → "", status → not_configured.
  "manual_csv" — локальный файл data/scout/etf_flows.csv, который ведёт трейдер:
                 date,ticker,asset,flow_usd_m,source
                 2026-06-10,IBIT,BTC,-120.5,farside
                 Пустой/кривой flow → запись с flow=None, direction=unknown.

Данные НЕ выдумываются: нет провайдера/файла → честно пусто. Живой web-адаптер
(Farside/SoSoValue) — отдельный шаг плана источников, после GO трейдера.
"""
from __future__ import annotations

import csv
import io
import os
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
CSV_PATH = _ROOT / "data" / "scout" / "etf_flows.csv"

VALID_DIRECTIONS = ("inflow", "outflow", "unknown")


def provider() -> str:
    return os.getenv("SCANNER_ETF_FLOW_PROVIDER", "").strip().lower()


def status() -> dict:
    """Для отчётов: сконфигурирован ли источник и почему молчит."""
    p = provider()
    if not p:
        return {"configured": False, "provider": None,
                "reason": "not_configured: SCANNER_ETF_FLOW_PROVIDER не задан"}
    if p == "manual_csv":
        if CSV_PATH.exists():
            return {"configured": True, "provider": p, "path": str(CSV_PATH)}
        return {"configured": False, "provider": p,
                "reason": f"csv_missing: {CSV_PATH} не найден"}
    return {"configured": False, "provider": p, "reason": f"unknown_provider: {p}"}


def _safe_float(v) -> float | None:
    try:
        s = str(v).strip().replace(",", "")
        return float(s) if s not in ("", "n/a", "none", "-") else None
    except (TypeError, ValueError):
        return None


def normalize_record(raw: dict, *, source: str, source_quality: str) -> dict | None:
    """Сырая строка провайдера → нормализованная запись. Пропуски помечаются явно."""
    date = str(raw.get("date") or "").strip()
    ticker = str(raw.get("ticker") or "").strip().upper()
    asset = str(raw.get("asset") or "").strip().upper() or None
    if not date or not (ticker or asset):
        return None
    flow = _safe_float(raw.get("flow_usd_m"))
    if flow is None:
        direction = "unknown"
    else:
        direction = "inflow" if flow > 0 else "outflow" if flow < 0 else "unknown"
    return {
        "date": date,
        "ticker": ticker or None,
        "asset": asset,
        "flow_usd_m": flow,                       # None = провайдер не дал число
        "direction": direction,
        "source": str(raw.get("source") or source),
        "source_quality": source_quality,
    }


def parse_manual_csv(text: str) -> list[dict]:
    out: list[dict] = []
    for raw in csv.DictReader(io.StringIO(text or "")):
        rec = normalize_record(raw, source="manual_csv", source_quality="manual")
        if rec:
            out.append(rec)
    return out


def fetch_etf_flow_records(limit: int = 20) -> list[dict]:
    """Нормализованные записи потоков. Пусто, если провайдер не сконфигурирован."""
    st = status()
    if not st.get("configured"):
        return []
    if st["provider"] == "manual_csv":
        try:
            recs = parse_manual_csv(CSV_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
        recs.sort(key=lambda r: r["date"], reverse=True)
        return recs[:limit]
    return []


def context_line(records: list[dict] | None = None) -> str:
    """Одна строка фона для L1 (последняя дата по каждому активу). "" если данных нет."""
    recs = fetch_etf_flow_records() if records is None else records
    if not recs:
        return ""
    latest_date = max(r["date"] for r in recs)
    by_asset: dict[str, float] = {}
    unknown: set[str] = set()
    for r in recs:
        if r["date"] != latest_date:
            continue
        key = r.get("asset") or r.get("ticker") or "?"
        if r["flow_usd_m"] is None:
            unknown.add(key)
        else:
            by_asset[key] = by_asset.get(key, 0.0) + r["flow_usd_m"]
    bits = [f"{a} {v:+,.1f}M$" for a, v in sorted(by_asset.items())]
    bits += [f"{a} n/a" for a in sorted(unknown - set(by_asset))]
    if not bits:
        return ""
    return f"ETF-потоки {latest_date}: " + ", ".join(bits) + " (контекст, не сигнал)"


@lru_cache(maxsize=1)
def l1_context_line() -> str:
    """Кэш на процесс (сканер спавнится на каждый проход) — файл читается один раз."""
    try:
        return context_line()
    except Exception:
        return ""
