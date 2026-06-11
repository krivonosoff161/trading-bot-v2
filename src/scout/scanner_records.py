# -*- coding: utf-8 -*-
"""
scanner_records.py - structured append-only records for scanner training and memory.

Keeps backward compatibility with scanner_journal.jsonl while writing richer blocks:
  - event block: what entered the analyzer
  - reasoning block: what agents/orchestrator/chief produced
  - training record: merged decision + outcome
  - memory record: compact retrieval-friendly case summary
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = _ROOT / "logs" / "scout"
EVENTS = OUT_DIR / "scanner_events.jsonl"
REASONING = OUT_DIR / "scanner_reasoning.jsonl"
TRAINING = OUT_DIR / "scanner_training.jsonl"
MEMORY = OUT_DIR / "scanner_memory.jsonl"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _existing_ids(path: Path, key: str = "card_id") -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line).get(key)
            except Exception:
                continue
            if value:
                ids.add(str(value))
    return ids


def write_unique(path: Path, row: dict[str, Any], key: str = "card_id") -> bool:
    row_id = row.get(key)
    if not row_id:
        return False
    if str(row_id) in _existing_ids(path, key=key):
        return False
    _append_jsonl(path, row)
    return True


def read_index(path: Path, key: str = "card_id") -> dict[str, dict]:
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            row_id = row.get(key)
            if row_id:
                out[str(row_id)] = row
    return out


def compact_unique_file(path: Path, key: str = "card_id") -> dict[str, int]:
    """Rewrite a JSONL file keeping one row per key. Returns before/after counters."""
    if not path.exists():
        return {"before": 0, "after": 0, "removed": 0}
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get(key):
                rows.append(row)
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique[str(row[key])] = row
    before = len(rows)
    after = len(unique)
    if before != after:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for row in unique.values():
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {"before": before, "after": after, "removed": before - after}


def _cap_text(text: str | None, n: int = 4000) -> str:
    s = str(text or "").strip()
    return s[:n]


def _memory_result_label(journal: dict, outcome: dict) -> str:
    verdict = str(journal.get("verdict") or "")
    side = str(journal.get("side") or "none")
    correct = outcome.get("verdict_correct")
    missed = bool(outcome.get("missed_move"))
    if verdict == "NO_GO" and missed:
        return "missed_move"
    if verdict == "WATCH":
        return "watch_only"
    if correct is True and side == "short":
        return "correct_short"
    if correct is True and side == "long":
        return "correct_long"
    if correct is True and verdict == "NO_GO":
        return "correct_reject"
    if verdict == "GO" and correct is False:
        return "false_positive"
    if verdict == "NO_GO" and correct is False:
        return "false_reject"
    return "unclear"


def _memory_lesson(journal: dict, outcome: dict) -> str:
    label = _memory_result_label(journal, outcome)
    if label == "missed_move":
        return "Фильтр не взял значимый ход. Нужен повторный разбор этой семьи событий и ранних источников."
    if label == "correct_short":
        return "Медвежий сценарий сработал. Этот паттерн годится для retrieval как кейс успешного short."
    if label == "correct_long":
        return "Бычий сценарий сработал. Этот паттерн годится для retrieval как кейс успешного long."
    if label == "correct_reject":
        return "Рынок не дал достойного хода. Такой кейс полезен как отрицательный пример для главного процесса."
    if label == "false_positive":
        return "Решение было слишком агрессивным. Нужны tighter-гейты по сюрпризу, в цене и lead_class."
    if label == "false_reject":
        return "Рынок дал ход, но решение оказалось излишне консервативным. Нужен разбор missed setup."
    return "Информационный кейс без сильного урока. Полезен как фоновый пример, но не как ядро памяти."


def _training_quality(journal: dict, outcome: dict) -> tuple[bool, list[str], list[str]]:
    invalid: list[str] = []
    flags: list[str] = []

    if not journal.get("card_id"):
        invalid.append("missing_card_id")
    if not journal.get("asset"):
        invalid.append("missing_asset")
    if not journal.get("headline"):
        invalid.append("missing_headline")
    if not journal.get("event_type"):
        invalid.append("missing_event_type")
    if not outcome.get("scored"):
        invalid.append("outcome_not_scored")
    for key in ("ret_pct", "outcome_long_pct", "outcome_short_pct"):
        if outcome.get("scored") and not finite(outcome.get(key)):
            invalid.append(f"bad_outcome_{key}")

    if journal.get("low_confidence"):
        flags.append("low_confidence_source")
    if journal.get("chief_called") is False:
        flags.append("no_chief")
    if journal.get("lead_class") != "LEADING":
        flags.append("non_leading_source")
    if journal.get("event_phase") in ("context", "CONTEXT"):
        flags.append("context_phase")
    if journal.get("price_at_decision") in (None, 0):
        flags.append("no_entry_price")

    return not invalid, sorted(set(invalid)), sorted(set(flags))


def build_event_block_from_journal(row: dict) -> dict[str, Any]:
    """Fallback for historical rows that predate structured event capture."""
    return {
        "schema": "ScoutEvent.v1",
        "card_id": row.get("card_id"),
        "recorded_at": now_iso(),
        "buffer_doc_id": None,
        "event_key": row.get("event_key"),
        "decision_ts": row.get("ts_utc"),
        "source": {
            "source_id": row.get("source"),
            "source_class": row.get("source_class"),
            "lead_class": row.get("lead_class"),
            "source_trust": row.get("source_trust"),
            "source_phase_prior": row.get("source_phase_prior"),
            "source_url": row.get("source_url"),
            "source_ts": row.get("source_ts"),
            "trigger_type": row.get("trigger_type"),
        },
        "normalized": {
            "asset": row.get("asset"),
            "layer": row.get("layer"),
            "okx_inst": row.get("okx_inst"),
            "baseline_symbol": row.get("baseline_symbol"),
            "asset_confidence": row.get("asset_confidence"),
            "event_type": row.get("event_type"),
            "event_phase": row.get("event_phase"),
            "headline_phase": row.get("headline_phase"),
            "materiality_score": row.get("materiality_score"),
            "router_version": row.get("router_version"),
            "allowed_layers_from_source": row.get("allowed_layers_from_source") or [],
        },
        "input": {
            "headline": row.get("headline"),
            "text_excerpt": None,
            "text_len": None,
            "market_context": None,
        },
        "extraction": {
            "status": "journal_backfill",
            "method": None,
            "quality": None,
            "normalized_from_buffer": False,
            "source_item": {
                "title": row.get("headline"),
                "url": row.get("source_url"),
                "time": row.get("source_ts"),
            },
        },
    }


def build_reasoning_block_from_journal(row: dict) -> dict[str, Any]:
    """Fallback for historical rows that only have flattened journal fields."""
    key_facts = [x.strip() for x in str(row.get("catalyst") or "").split(";") if x.strip()]
    mechanics = [x.strip() for x in str(row.get("mechanics") or "").split(",") if x.strip() and x.strip().lower() != "none"]
    red_flags = [x.strip() for x in str(row.get("red_flag") or "").split(",") if x.strip() and x.strip().lower() != "none"]
    return {
        "schema": "ScoutReasoning.v1",
        "card_id": row.get("card_id"),
        "recorded_at": now_iso(),
        "buffer_doc_id": None,
        "event_key": row.get("event_key"),
        "decision_ts": row.get("ts_utc"),
        "asset": row.get("asset"),
        "layer": row.get("layer"),
        "agent": {
            "asset": row.get("asset"),
            "event_type": row.get("event_type"),
            "phase": row.get("event_phase"),
            "direction": row.get("agent_direction"),
            "materiality": row.get("materiality_score"),
            "confidence": row.get("agent_confidence"),
            "key_facts": key_facts,
            "numbers": [],
            "red_flags": red_flags,
            "mechanics": mechanics,
            "reason_to_escalate": "",
        },
        "orchestrator": {
            "decision": "backfilled",
            "chief_called": bool(row.get("chief_called")),
            "send_channel": False,
            "final_verdict": row.get("verdict"),
            "final_side": row.get("side"),
        },
        "chief": {
            "verdict": row.get("verdict"),
            "side": row.get("side"),
            "in_price": row.get("in_price"),
            "surprise": row.get("surprise"),
            "asymmetry": row.get("asymmetry"),
            "invalidation": row.get("invalidation"),
            "forecast": row.get("forecast"),
            "horizon_hours": row.get("horizon_hours"),
            "confidence": None,
            "levels": row.get("levels") or {},
            "summary": row.get("summary"),
            "journal_reason": row.get("summary"),
        },
        "usage": [],
    }


def build_event_block(
    *,
    row: dict,
    source_item: dict,
    news: dict,
    market_ctx: str | None,
    buffer_doc_id: str | None,
    extraction_meta: dict | None = None,
) -> dict[str, Any]:
    return {
        "schema": "ScoutEvent.v1",
        "card_id": row["card_id"],
        "recorded_at": now_iso(),
        "buffer_doc_id": buffer_doc_id,
        "event_key": row.get("event_key"),
        "decision_ts": row.get("ts_utc"),
        "source": {
            "source_id": row.get("source"),
            "source_class": row.get("source_class"),
            "lead_class": row.get("lead_class"),
            "source_trust": row.get("source_trust"),
            "source_phase_prior": row.get("source_phase_prior"),
            "source_url": row.get("source_url"),
            "source_ts": row.get("source_ts"),
            "trigger_type": row.get("trigger_type"),
        },
        "normalized": {
            "asset": row.get("asset"),
            "layer": row.get("layer"),
            "okx_inst": row.get("okx_inst"),
            "baseline_symbol": row.get("baseline_symbol"),
            "asset_confidence": row.get("asset_confidence"),
            "event_type": row.get("event_type"),
            "event_phase": row.get("event_phase"),
            "headline_phase": row.get("headline_phase"),
            "materiality_score": row.get("materiality_score"),
            "router_version": row.get("router_version"),
            "allowed_layers_from_source": row.get("allowed_layers_from_source") or [],
        },
        "input": {
            "headline": news.get("headline") or row.get("headline"),
            "text_excerpt": _cap_text(news.get("text")),
            "text_len": len(str(news.get("text") or "")),
            "market_context": market_ctx,
        },
        "extraction": {
            "status": (extraction_meta or {}).get("status"),
            "method": (extraction_meta or {}).get("method"),
            "quality": (extraction_meta or {}).get("quality"),
            "normalized_from_buffer": bool(buffer_doc_id),
            "source_item": {
                "title": source_item.get("title"),
                "url": source_item.get("url"),
                "time": source_item.get("time"),
            },
        },
    }


def build_reasoning_block(
    *,
    row: dict,
    agent: dict,
    orchestrator: dict,
    chief: dict | None,
    usage: list[dict] | None,
    buffer_doc_id: str | None,
) -> dict[str, Any]:
    return {
        "schema": "ScoutReasoning.v1",
        "card_id": row["card_id"],
        "recorded_at": now_iso(),
        "buffer_doc_id": buffer_doc_id,
        "event_key": row.get("event_key"),
        "decision_ts": row.get("ts_utc"),
        "asset": row.get("asset"),
        "layer": row.get("layer"),
        "agent": {
            "asset": agent.get("asset"),
            "event_type": agent.get("event_type"),
            "phase": agent.get("phase"),
            "direction": agent.get("direction"),
            "materiality": agent.get("materiality"),
            "confidence": agent.get("confidence"),
            "key_facts": agent.get("key_facts") or [],
            "numbers": agent.get("numbers") or [],
            "red_flags": agent.get("red_flags") or [],
            "veto_flags": agent.get("veto_flags") or [],
            "no_edge_flags": agent.get("no_edge_flags") or [],
            "mechanics": agent.get("mechanics") or [],
            "reason_to_escalate": agent.get("reason_to_escalate") or "",
            "pre_verdict": agent.get("pre_verdict") or "",
            "trigger_text": agent.get("trigger_text") or "",
            "no_go_reason": agent.get("no_go_reason") or "",
            "escalation_reason": agent.get("escalation_reason") or "",
            "suggested_horizon_hours": agent.get("suggested_horizon_hours"),
        },
        "orchestrator": {
            "decision": orchestrator.get("decision"),
            "chief_called": bool(orchestrator.get("chief_called")),
            "send_channel": bool(orchestrator.get("send_channel")),
            "pre_verdict": orchestrator.get("pre_verdict") or "",
            "should_escalate": bool(orchestrator.get("should_escalate")),
            "escalation_gate": orchestrator.get("escalation_gate") or "",
            "escalation_reason": orchestrator.get("escalation_reason") or "",
            "chief_error": bool(orchestrator.get("chief_error")),
            "retry_status": orchestrator.get("retry_status"),
            "final_verdict": row.get("verdict"),
            "final_side": row.get("side"),
        },
        "chief": {
            "verdict": (chief or {}).get("verdict"),
            "side": (chief or {}).get("side"),
            "in_price": (chief or {}).get("in_price"),
            "surprise": (chief or {}).get("surprise"),
            "asymmetry": (chief or {}).get("asymmetry"),
            "invalidation": (chief or {}).get("invalidation"),
            "forecast": (chief or {}).get("forecast"),
            "horizon_hours": (chief or {}).get("horizon_hours"),
            "confidence": (chief or {}).get("confidence"),
            "levels": (chief or {}).get("levels"),
            "summary": (chief or {}).get("summary"),
            "journal_reason": (chief or {}).get("journal_reason"),
        },
        "usage": usage or [],
    }


def build_training_record(
    journal: dict,
    outcome: dict,
    *,
    event_block: dict | None = None,
    reasoning_block: dict | None = None,
) -> dict[str, Any] | None:
    if not outcome.get("scored"):
        return None
    valid, invalid_reasons, quality_flags = _training_quality(journal, outcome)
    agent = (reasoning_block or {}).get("agent") or {}
    chief = (reasoning_block or {}).get("chief") or {}
    event_input = (event_block or {}).get("input") or {}
    return {
        "schema": "ScoutTrainingRecord.v1",
        "card_id": journal.get("card_id"),
        "recorded_at": now_iso(),
        "ts_decision": journal.get("ts_utc"),
        "resolved_ts": outcome.get("resolved_ts"),
        "asset": journal.get("asset"),
        "layer": journal.get("layer"),
        "source": {
            "source_id": journal.get("source"),
            "source_class": journal.get("source_class"),
            "lead_class": journal.get("lead_class"),
            "source_trust": journal.get("source_trust"),
            "source_phase_prior": journal.get("source_phase_prior"),
            "source_url": journal.get("source_url"),
            "source_ts": journal.get("source_ts"),
        },
        "event": {
            "event_key": journal.get("event_key"),
            "headline": journal.get("headline"),
            "event_type": journal.get("event_type"),
            "event_phase": journal.get("event_phase"),
            "headline_phase": journal.get("headline_phase"),
            "materiality_score": journal.get("materiality_score"),
            "baseline_symbol": journal.get("baseline_symbol"),
            "allowed_layers_from_source": journal.get("allowed_layers_from_source") or [],
            "text_excerpt": event_input.get("text_excerpt"),
            "market_context": event_input.get("market_context"),
        },
        "reasoning": {
            "verdict": journal.get("verdict"),
            "side": journal.get("side"),
            "horizon_hours": journal.get("horizon_hours"),
            "in_price": journal.get("in_price"),
            "chief_called": journal.get("chief_called"),
            "agent_direction": journal.get("agent_direction"),
            "agent_confidence": journal.get("agent_confidence"),
            "low_confidence": journal.get("low_confidence"),
            "summary": journal.get("summary"),
            "forecast": journal.get("forecast"),
            "asymmetry": journal.get("asymmetry"),
            "invalidation": journal.get("invalidation"),
            "surprise": journal.get("surprise"),
            "levels": journal.get("levels") or {},
            "key_facts": agent.get("key_facts") or [],
            "numbers": agent.get("numbers") or [],
            "red_flags": agent.get("red_flags") or [],
            "mechanics": agent.get("mechanics") or [],
            "journal_reason": chief.get("journal_reason"),
        },
        "outcome": {
            "price_at_decision": outcome.get("price_at_decision"),
            "price_final": outcome.get("price_final"),
            "ret_pct": outcome.get("ret_pct"),
            "outcome_long_pct": outcome.get("outcome_long_pct"),
            "outcome_short_pct": outcome.get("outcome_short_pct"),
            "mfe_long_pct": outcome.get("mfe_long_pct"),
            "mae_long_pct": outcome.get("mae_long_pct"),
            "price_after": outcome.get("price_after") or {},
            "baseline_ret_pct": outcome.get("baseline_ret_pct"),
            "excess_pct": outcome.get("excess_pct"),
            "verdict_correct": outcome.get("verdict_correct"),
            "missed_move": outcome.get("missed_move"),
        },
        "valid": valid,
        "invalid_reasons": invalid_reasons,
        "quality_flags": quality_flags,
    }


def build_memory_record(
    journal: dict,
    outcome: dict,
    *,
    reasoning_block: dict | None = None,
) -> dict[str, Any] | None:
    if not outcome.get("scored"):
        return None
    agent = (reasoning_block or {}).get("agent") or {}
    setup_family = f"L{journal.get('layer')}::{journal.get('event_type')}::{journal.get('lead_class')}"
    result_label = _memory_result_label(journal, outcome)
    return {
        "schema": "ScoutMemoryRecord.v1",
        "card_id": journal.get("card_id"),
        "recorded_at": now_iso(),
        "asset": journal.get("asset"),
        "layer": journal.get("layer"),
        "event_key": journal.get("event_key"),
        "setup_family": setup_family,
        "tags": sorted(
            {
                str(journal.get("source") or ""),
                str(journal.get("event_type") or ""),
                str(journal.get("event_phase") or ""),
                str(journal.get("lead_class") or ""),
                str(journal.get("verdict") or ""),
                str(journal.get("side") or ""),
            }
        ),
        "case": {
            "headline": journal.get("headline"),
            "summary": journal.get("summary"),
            "key_facts": agent.get("key_facts") or [],
            "red_flags": agent.get("red_flags") or [],
            "mechanics": agent.get("mechanics") or [],
        },
        "decision": {
            "verdict": journal.get("verdict"),
            "side": journal.get("side"),
            "horizon_hours": journal.get("horizon_hours"),
            "in_price": journal.get("in_price"),
            "chief_called": journal.get("chief_called"),
        },
        "outcome": {
            "ret_pct": outcome.get("ret_pct"),
            "outcome_long_pct": outcome.get("outcome_long_pct"),
            "outcome_short_pct": outcome.get("outcome_short_pct"),
            "mfe_long_pct": outcome.get("mfe_long_pct"),
            "mae_long_pct": outcome.get("mae_long_pct"),
            "excess_pct": outcome.get("excess_pct"),
            "verdict_correct": outcome.get("verdict_correct"),
            "missed_move": outcome.get("missed_move"),
        },
        "lesson": {
            "result_label": result_label,
            "retrieval_candidate": result_label not in {"watch_only", "unclear"},
            "note": _memory_lesson(journal, outcome),
        },
    }


def write_event_block(block: dict[str, Any]) -> bool:
    return write_unique(EVENTS, block)


def write_reasoning_block(block: dict[str, Any]) -> bool:
    return write_unique(REASONING, block)


def write_training_record(record: dict[str, Any]) -> bool:
    return write_unique(TRAINING, record)


def write_memory_record(record: dict[str, Any]) -> bool:
    return write_unique(MEMORY, record)
