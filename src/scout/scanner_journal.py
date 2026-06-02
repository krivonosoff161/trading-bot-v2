# -*- coding: utf-8 -*-
"""
scanner_journal.py — событийный журнал инфо-эдж сканера.

ОТДЕЛЬНО от forward_series.csv (тот — дневной wide-снимок, дедуп по ДАТЕ;
этот — N строк/день, ключ = СОБЫТИЕ). Блокер №2 пре-старт аудита.

Формат: append-only JSONL (logs/scout/scanner_journal.jsonl) — поля плавают по слоям.
Идемпотентность: по card_id = sha1(source_url). Исход (outcome) дописывается позже
скриптом resolve_outcomes.py — здесь всегда null в момент решения.

Read-only к рынку, ничего не торгует. card_id связывает: дедуп + отложенный исход.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = _ROOT / "logs" / "scout"
JOURNAL = OUT_DIR / "scanner_journal.jsonl"
PENDING = OUT_DIR / "pending_events.jsonl"      # skeleton «будет» (анти-survivorship)

SCHEMA_VERSION = 1

# Поля, без которых карточку НЕ пишем (анти-survivorship + измеримость исхода).
# price_at_decision может быть None (актив вне OKX → outcome_source=manual), но
# присутствовать в записи обязан.
_REQUIRED = ("card_id", "ts_utc", "verdict", "horizon_hours", "source_ts", "source_url")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def card_id_for(url: str) -> str:
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]


def _existing_ids() -> set[str]:
    if not JOURNAL.exists():
        return set()
    ids: set[str] = set()
    with open(JOURNAL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line).get("card_id"))
            except Exception:
                continue
    return ids


def build_row(
    *,
    source_url: str,
    source_ts: str | None,
    layer: int,
    asset: str | None,
    trigger_type: str,
    headline: str,
    verdict: str,
    horizon_hours: int,
    price_at_decision: float | None,
    okx_inst: str | None = None,
    btc_at_decision: float | None = None,
    catalyst: str = "",
    in_price: str = "",
    red_flag: str = "",
    mechanics: str = "",
    surprise: str = "",
    side: str = "none",
    asymmetry: str = "",
    invalidation: str = "",
    forecast: str = "",
    summary: str = "",
    low_confidence: bool = False,
    outcome_source: str = "okx",
) -> dict:
    """Собрать запись журнала в момент РЕШЕНИЯ (outcome пустой — дописывается позже)."""
    url = source_url or ""
    cid = card_id_for(url)
    return {
        "schema_version": SCHEMA_VERSION,
        "card_id": cid,
        "ts_utc": now_iso(),                 # время РЕШЕНИЯ (обработки)
        "source_ts": source_ts,              # дата новости (для лаг-аудита)
        "layer": layer,
        "asset": asset,
        "trigger_type": trigger_type,
        "source_url": url,
        "headline": headline,
        "catalyst": catalyst,
        "in_price": in_price,
        "red_flag": red_flag,
        "mechanics": mechanics,
        "surprise": surprise,
        "verdict": verdict,
        "side": side,
        "asymmetry": asymmetry,
        "invalidation": invalidation,
        "forecast": forecast,
        "summary": summary,
        "horizon_hours": horizon_hours,
        "price_at_decision": price_at_decision,
        "okx_inst": okx_inst,                # инструмент для резолва цены актива
        "btc_at_decision": btc_at_decision,  # якорь baseline (anti-beta excess)
        "low_confidence": low_confidence,
        "dedup_key": f"{(asset or 'NA')}::{cid}",
        "outcome_source": outcome_source,    # okx | coingecko | manual
        "outcome": None,                     # {ret_pct, baseline_pct, excess_pct, scored, ...}
        "outcome_ts": None,
    }


def write_row(row: dict) -> str | None:
    """Идемпотентно дописать строку. Возвращает card_id, либо None (дубль/невалид)."""
    missing = [k for k in _REQUIRED if row.get(k) in (None, "") and k != "price_at_decision"]
    if missing:
        print(f"scanner_journal: НЕ пишу — нет обязательных полей {missing}")
        return None
    if "price_at_decision" not in row:
        print("scanner_journal: НЕ пишу — поле price_at_decision должно присутствовать (может быть None)")
        return None

    cid = row["card_id"]
    if cid in _existing_ids():
        return None  # уже логировали этот источник — идемпотентность

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return cid


def ensure_pending_store() -> None:
    """Создать skeleton pending_events.jsonl (хранилище «будет») если нет.

    Пустой файл-маркер: timestamp ожиданий начинаем фиксировать с самого начала
    (анти-survivorship). Наполнение/матчинг «произошло» — V1+.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not PENDING.exists():
        PENDING.write_text(
            "", encoding="utf-8"
        )


if __name__ == "__main__":
    # Самопроверка схемы (без сети): пишем и читаем тестовую строку во временный путь.
    ensure_pending_store()
    print(f"journal:  {JOURNAL}  (exists={JOURNAL.exists()})")
    print(f"pending:  {PENDING}  (exists={PENDING.exists()})")
    demo = build_row(
        source_url="https://example.com/test-card",
        source_ts="2026-06-02",
        layer=1, asset="BTC", trigger_type="rss_headline",
        headline="demo", verdict="NO_GO", horizon_hours=48,
        price_at_decision=71000.0, catalyst="demo", summary="schema self-check",
    )
    print("row keys:", list(demo.keys()))
    print("card_id:", demo["card_id"])
