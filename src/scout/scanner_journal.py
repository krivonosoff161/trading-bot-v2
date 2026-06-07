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

from src.scout import pending_store as PS

_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = _ROOT / "logs" / "scout"
JOURNAL = OUT_DIR / "scanner_journal.jsonl"
PENDING = OUT_DIR / "pending_events.jsonl"      # skeleton «будет» (анти-survivorship)
DROPS = OUT_DIR / "drops.jsonl"                 # отброшенное фильтром (анти-survivorship)
BUDGET = OUT_DIR / "llm_budget.jsonl"           # наблюдаемость стоимости LLM
INGEST = OUT_DIR / "ingest_log.jsonl"           # КАЖДОЕ входящее событие до фильтров (полный аудит)

ROUTING_AUDIT = OUT_DIR / "routing_audit.jsonl"

SCHEMA_VERSION = 3   # v3: +source routing/phase provenance fields

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
    levels: dict | None = None,
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
    event_type: str = "unclassified",
    event_phase: str = "unknown",        # FUTURE/REALIZED/CONTEXT/AMBIGUOUS (темпорал-роутер)
    materiality_score: float | None = None,
    lead_class: str = "LAGGING",         # LEADING/COINCIDENT/LAGGING (карта источника)
    source: str = "cointelegraph",       # имя фида (per-source метрики/latency)
    source_class: str = "rss",
    baseline_symbol: str | None = None,  # per-layer якорь excess (не хардкод BTC)
    router_version: str = "v0",
    asset_confidence: float | None = None,
    source_count: int = 1,
    event_key: str | None = None,
    chief_called: bool = False,
    agent_direction: str = "none",       # намёк дешёвого слой-агента (long/short/none/mixed)
    agent_confidence: float | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    source_trust: str | None = None,
    source_phase_prior: str | None = None,
    headline_phase: str | None = None,
    allowed_layers_from_source: list[int] | None = None,
    cross_layer: bool = False,           # актив восстановлен сильным алиасом вне слоя источника
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
        "btc_at_decision": btc_at_decision,  # цена baseline-якоря на момент (anti-beta)
        "baseline_symbol": baseline_symbol,  # КАКОЙ якорь (per-layer, не хардкод BTC)
        "levels": levels or {},              # entry/invalidation/target (только GO)
        "event_type": event_type,
        "event_phase": event_phase,
        "materiality_score": materiality_score,
        "lead_class": lead_class,
        "source": source,
        "source_class": source_class,
        "source_trust": source_trust,
        "source_phase_prior": source_phase_prior,
        "headline_phase": headline_phase,
        "allowed_layers_from_source": allowed_layers_from_source or [],
        "cross_layer": cross_layer,
        "router_version": router_version,
        "asset_confidence": asset_confidence,
        "source_count": source_count,
        "event_key": event_key,                     # актив+хэш-темы (event-дедуп, Этап 2)
        "chief_called": chief_called,               # звали ли мощную модель (экономия токенов)
        "agent_direction": agent_direction,         # намёк слой-агента
        "agent_confidence": agent_confidence,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "low_confidence": low_confidence,
        "dedup_key": event_key or f"{(asset or 'NA')}::{cid}",
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
    PS.ensure_store()


def write_drop(source_url: str, headline: str, drop_reason: str,
               asset: str | None = None, drop_stage: str = "router",
               source: str | None = None) -> None:
    """Залогировать отброшенное (анти-survivorship: фильтр видит только прошедшее → порог не настроить)."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": now_iso(), "source": source, "source_url": source_url, "headline": headline,
           "asset": asset, "drop_stage": drop_stage, "drop_reason": drop_reason}
    with open(DROPS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_budget(stats: dict) -> None:
    """Залогировать стоимость прохода (наблюдаемость бюджета Яндекса + ловля взрыва на флуде)."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": now_iso(), **stats}
    with open(BUDGET, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_routing_audit(record: dict) -> None:
    """Persist one routing attempt for source/layer/phase quality analysis."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": now_iso(), **record}
    with open(ROUTING_AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _ingest_keys() -> set:
    if not INGEST.exists():
        return set()
    keys = set()
    with open(INGEST, "r", encoding="utf-8") as f:
        for line in f:
            try:
                keys.add(json.loads(line).get("canon"))
            except Exception:
                continue
    return keys


def write_ingest(records: list) -> int:
    """Лог КАЖДОГО входящего события (до фильтров), дедуп по canon. Полный аудит:
    что сканер ВИДЕЛ, даже если не дошло до карточки/дропа (превышен лимит, упало и т.п.).
    records=[{canon, source, headline, url}]. Возвращает сколько новых записано."""
    existing = _ingest_keys()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(INGEST, "a", encoding="utf-8") as f:
        for r in records:
            c = r.get("canon")
            if c and c not in existing:
                f.write(json.dumps({"ts": now_iso(), **r}, ensure_ascii=False) + "\n")
                existing.add(c)
                n += 1
    return n


def recent_events(hours: int = 48) -> list:
    """(asset, headline) из журнала за последние N часов — окно event-дедупа."""
    if not JOURNAL.exists():
        return []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    out = []
    with open(JOURNAL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = dt.datetime.strptime(r["ts_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
                if ts >= cutoff:
                    out.append((r.get("asset"), r.get("headline")))
            except Exception:
                continue
    return out


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
