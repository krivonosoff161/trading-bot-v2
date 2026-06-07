# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import scanner_journal as J  # noqa: E402
from src.scout import scanner_records as R  # noqa: E402
from src.scout import backfill_scanner_records as B  # noqa: E402


def _patch_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "OUT_DIR", tmp_path)
    monkeypatch.setattr(R, "EVENTS", tmp_path / "scanner_events.jsonl")
    monkeypatch.setattr(R, "REASONING", tmp_path / "scanner_reasoning.jsonl")
    monkeypatch.setattr(R, "TRAINING", tmp_path / "scanner_training.jsonl")
    monkeypatch.setattr(R, "MEMORY", tmp_path / "scanner_memory.jsonl")


def test_scanner_records_write_structured_blocks(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    row = J.build_row(
        source_url="https://example.com/zec",
        source_ts="2026-06-07",
        layer=2,
        asset="ZEC",
        trigger_type="rss_headline",
        headline="ZEC discloses critical vulnerability",
        verdict="WATCH",
        horizon_hours=48,
        price_at_decision=42.0,
        event_type="security_incident",
        event_phase="realized",
        lead_class="LAGGING",
        source="decrypt",
        source_class="rss",
        event_key="ZEC::security_incident",
        chief_called=True,
        agent_direction="short",
        agent_confidence=0.73,
        summary="Кейс требует внимания до пересмотра риска.",
    )
    news = {
        "headline": row["headline"],
        "text": "Zcash disclosed a critical counterfeiting vulnerability. " * 20,
        "date": "2026-06-07",
        "url": row["source_url"],
    }
    event_block = R.build_event_block(
        row=row,
        source_item={"title": row["headline"], "url": row["source_url"], "time": "2026-06-07"},
        news=news,
        market_ctx="fear 28/100",
        buffer_doc_id="doc-1",
        extraction_meta={"quality": 0.9, "status": "ok", "method": "rss_text"},
    )
    reasoning_block = R.build_reasoning_block(
        row=row,
        agent={
            "asset": "ZEC",
            "event_type": "security_incident",
            "phase": "realized",
            "direction": "short",
            "materiality": 0.88,
            "confidence": 0.73,
            "key_facts": ["counterfeiting vulnerability disclosed"],
            "numbers": ["38% intraday move"],
            "red_flags": ["credibility damage"],
            "mechanics": ["trust shock"],
            "reason_to_escalate": "critical security incident",
        },
        orchestrator={"decision": "chief", "chief_called": True, "send_channel": True},
        chief={
            "verdict": "WATCH",
            "side": "short",
            "in_price": "partial",
            "surprise": "magnitude",
            "asymmetry": "репутационный удар сильнее обычного",
            "invalidation": "быстрое закрытие риска аудитором",
            "forecast": "давление может продолжиться",
            "horizon_hours": 48,
            "confidence": 0.69,
            "levels": {"entry": None, "invalidation": None, "target": None},
            "summary": "Новость медвежья, но запоздалая.",
            "journal_reason": "известный риск, edge ограничен",
        },
        usage=[{"provider": "alibaba", "model": "qwen3.7-plus", "total_tokens": 321}],
        buffer_doc_id="doc-1",
    )

    assert R.write_event_block(event_block)
    assert not R.write_event_block(event_block)
    assert R.write_reasoning_block(reasoning_block)
    assert not R.write_reasoning_block(reasoning_block)

    loaded_event = R.read_index(R.EVENTS)[row["card_id"]]
    loaded_reasoning = R.read_index(R.REASONING)[row["card_id"]]
    assert loaded_event["normalized"]["asset"] == "ZEC"
    assert loaded_reasoning["agent"]["direction"] == "short"


def test_scanner_records_build_training_and_memory(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    row = J.build_row(
        source_url="https://example.com/nvda",
        source_ts="2026-06-07",
        layer=5,
        asset="NVDA",
        trigger_type="rss_headline",
        headline="Nvidia files major AI partnership update",
        verdict="GO",
        horizon_hours=24,
        price_at_decision=110.0,
        event_type="partnership",
        event_phase="realized",
        lead_class="LEADING",
        source="sec_edgar",
        source_class="api",
        event_key="NVDA::partnership",
        chief_called=True,
        agent_direction="long",
        agent_confidence=0.81,
        summary="Официальный апдейт даёт асимметрию в пользу long.",
        in_price="no",
        side="long",
    )
    event_block = {
        "card_id": row["card_id"],
        "input": {"text_excerpt": "8-K filing about partnership expansion", "market_context": "risk on"},
    }
    reasoning_block = {
        "card_id": row["card_id"],
        "agent": {
            "key_facts": ["official 8-K filing"],
            "numbers": ["multiyear agreement"],
            "red_flags": [],
            "mechanics": ["revenue visibility"],
        },
        "chief": {"journal_reason": "официальный leading-катализатор с ещё не полностью отыгранной механикой"},
    }
    outcome = {
        "card_id": row["card_id"],
        "resolved_ts": "2026-06-08T00:00:00Z",
        "scored": True,
        "price_at_decision": 110.0,
        "price_final": 118.0,
        "ret_pct": 7.273,
        "outcome_long_pct": 7.273,
        "outcome_short_pct": -7.273,
        "mfe_long_pct": 8.1,
        "mae_long_pct": -1.2,
        "price_after": {"1h": 1.1, "4h": 2.8, "24h": 7.273},
        "baseline_ret_pct": 1.5,
        "excess_pct": 5.773,
        "verdict_correct": True,
        "missed_move": False,
    }

    training = R.build_training_record(row, outcome, event_block=event_block, reasoning_block=reasoning_block)
    memory = R.build_memory_record(row, outcome, reasoning_block=reasoning_block)

    assert training is not None and training["valid"] is True
    assert "low_confidence_source" not in training["quality_flags"]
    assert memory is not None and memory["lesson"]["result_label"] == "correct_long"

    assert R.write_training_record(training)
    assert R.write_memory_record(memory)
    assert not R.write_training_record(training)
    assert not R.write_memory_record(memory)


def test_backfill_scanner_records_is_idempotent(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    journal_path = tmp_path / "scanner_journal.jsonl"
    outcomes_path = tmp_path / "scanner_outcomes.jsonl"
    row = J.build_row(
        source_url="https://example.com/btc",
        source_ts="2026-06-07",
        layer=1,
        asset="BTC",
        trigger_type="rss_headline",
        headline="SEC approves spot ETF and inflows surge",
        verdict="GO",
        horizon_hours=24,
        price_at_decision=70000.0,
        event_type="etf_approval",
        event_phase="realized",
        lead_class="LEADING",
        source="sec_edgar",
        source_class="api",
        event_key="BTC::etf_approval",
        chief_called=True,
        agent_direction="long",
        agent_confidence=0.92,
        summary="Официальный катализатор с сильной механикой.",
        side="long",
        in_price="no",
        catalyst="official approval; inflow path",
        mechanics="flows, access",
    )
    outcome = {
        "card_id": row["card_id"],
        "resolved_ts": "2026-06-08T00:00:00Z",
        "scored": True,
        "price_at_decision": 70000.0,
        "price_final": 74200.0,
        "ret_pct": 6.0,
        "outcome_long_pct": 6.0,
        "outcome_short_pct": -6.0,
        "mfe_long_pct": 7.2,
        "mae_long_pct": -0.8,
        "price_after": {"1h": 0.9, "4h": 2.0, "24h": 6.0},
        "baseline_ret_pct": 1.2,
        "excess_pct": 4.8,
        "verdict_correct": True,
        "missed_move": False,
    }
    journal_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    outcomes_path.write_text(json.dumps(outcome, ensure_ascii=False) + "\n", encoding="utf-8")

    first = B.backfill(journal_path=journal_path, outcomes_path=outcomes_path)
    second = B.backfill(journal_path=journal_path, outcomes_path=outcomes_path)

    assert first["events_written"] == 1
    assert first["reasoning_written"] == 1
    assert first["training_written"] == 1
    assert first["memory_written"] == 1
    assert second["events_written"] == 0
    assert second["reasoning_written"] == 0
    assert second["training_written"] == 0
    assert second["memory_written"] == 0
    assert second["already_present"] == 1
