"""Private training export for manual/VIP product signal events.

This module reads the sanitized ``signal_event.v1`` log written by Telegram
manual/VIP product surfaces and mirrors it into the private Strategy Lab root as
training-ready rows. It never calls Telegram, exchanges, LLM providers, or order
code.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import stable_id, write_cycle_link
from src.research_lab.paper_signals import outcome_evidence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_LOG = ROOT / "logs" / "signals" / "signal_events.jsonl"
SCHEMA = "ProductSignalTrainingRow.v1"


def _hash_text(value: Any) -> str:
    raw = str(value or "")
    return (
        hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]
        if raw
        else ""
    )


def _clean_artifacts(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw in value.items():
        text = str(raw or "")
        if not text:
            continue
        out[str(key)] = text
    return out


def _read_events(path: Path) -> tuple[list[dict[str, Any]], int, str]:
    if not path.exists():
        return [], 0, ""
    rows: list[dict[str, Any]] = []
    invalid = 0
    read_error = ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], 0, type(exc).__name__
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows, invalid, read_error


def product_training_row(event: dict[str, Any]) -> dict[str, Any]:
    """Build one private training row from a sanitized product signal event."""

    payload = {
        "signal_id": event.get("signal_id"),
        "source": event.get("source"),
        "mode": event.get("mode"),
        "decision": event.get("decision"),
        "created_at": event.get("created_at"),
        "symbol": event.get("symbol"),
        "timeframe": event.get("timeframe"),
    }
    row_id = stable_id("product_training", payload, length=20)
    artifacts = _clean_artifacts(event.get("artifacts"))
    extra = raw_extra if isinstance(raw_extra := event.get("extra"), dict) else {}
    return {
        "schema": SCHEMA,
        "training_row_id": row_id,
        "product_event_id": str(event.get("signal_id") or row_id),
        "created_at": str(event.get("created_at") or ""),
        "source": str(event.get("source") or ""),
        "mode": str(event.get("mode") or ""),
        "decision": str(event.get("decision") or ""),
        "status": str(event.get("status") or ""),
        "symbol": str(event.get("symbol") or ""),
        "timeframe": str(event.get("timeframe") or ""),
        "side": str(event.get("side") or ""),
        "entry_zone": event.get("entry_zone")
        if isinstance(event.get("entry_zone"), list)
        else [],
        "stop_loss": event.get("stop_loss"),
        "take_profit_plan": event.get("take_profit_plan")
        if isinstance(event.get("take_profit_plan"), list)
        else [],
        "invalidation_rule": str(event.get("invalidation_rule") or ""),
        "max_hold_minutes": event.get("max_hold_minutes"),
        "risk_pct": event.get("risk_pct"),
        "reason_codes": event.get("reason_codes")
        if isinstance(event.get("reason_codes"), list)
        else [],
        "provider": str(event.get("provider") or ""),
        "model": str(event.get("model") or ""),
        "prompt_version": str(event.get("prompt_version") or ""),
        "chat_id_hash": _hash_text(event.get("chat_id")),
        "message_id_hash": _hash_text(event.get("message_id")),
        "artifact_refs": artifacts,
        "artifact_ref_count": len(artifacts),
        "category": str(extra.get("category") or ""),
        "provider_scope": str(extra.get("provider_scope") or ""),
        "source_schema": str(event.get("schema") or ""),
        "outcome_evidence_kind": outcome_evidence.EVIDENCE_MARKET_OUTCOME,
        "paper_only": True,
        "execution_allowed": False,
    }


def export_product_signal_training(
    private_root: Path,
    *,
    source_log: Path = DEFAULT_SOURCE_LOG,
) -> dict[str, Any]:
    """Export sanitized product signal rows into the private Strategy Lab root."""

    private_root = Path(private_root)
    source_log = Path(source_log)
    events, invalid, read_error = _read_events(source_log)
    eligible_events = [
        event for event in events if outcome_evidence.is_market_outcome(event)
    ]
    rows = [
        product_training_row(event)
        for event in eligible_events
        if event.get("schema") == "signal_event.v1"
    ]
    out_jsonl = private_root / "state" / "derived" / "product_signal_training.jsonl"
    out_snapshot = private_root / "state" / "derived" / "product_signal_training.json"
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    by_source: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    by_status: dict[str, int] = {}
    execution_allowed_true = 0
    paper_only_false = 0
    for row in rows:
        by_source[row["source"]] = by_source.get(row["source"], 0) + 1
        by_decision[row["decision"]] = by_decision.get(row["decision"], 0) + 1
        by_provider[row["provider"] or "none"] = (
            by_provider.get(row["provider"] or "none", 0) + 1
        )
        by_status[row["status"] or "missing"] = (
            by_status.get(row["status"] or "missing", 0) + 1
        )
        execution_allowed_true += int(row["execution_allowed"] is True)
        paper_only_false += int(row["paper_only"] is not True)
        write_cycle_link(
            private_root,
            {
                "training_row_id": row["training_row_id"],
                "source": row["source"],
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "mode": row["mode"],
                "product_event_id": row["product_event_id"],
            },
        )

    summary = {
        "schema": "product_signal_training_export.v1",
        "row_schema": SCHEMA,
        "rows": len(rows),
        "source_rows": len(events),
        "operational_incidents_censored": len(events) - len(eligible_events),
        "source_invalid_json": invalid,
        "source_read_error": read_error,
        "source_exists": source_log.exists(),
        "source_log_label": "logs/signals/signal_events.jsonl",
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
        "by_source": by_source,
        "by_decision": by_decision,
        "by_provider": by_provider,
        "by_status": by_status,
        "paper_only_false": paper_only_false,
        "execution_allowed_true": execution_allowed_true,
        "paper_only": True,
        "execution_allowed": False,
    }
    out_snapshot.write_text(
        json.dumps(
            {**summary, "items": rows[:200]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary
