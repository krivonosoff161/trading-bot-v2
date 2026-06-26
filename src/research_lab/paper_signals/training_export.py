"""Training-friendly export from paper-watch outcomes.

This is a derived artifact: it reads the paper-signal audit log and writes a compact
JSONL/snapshot for analysis or model-training pipelines. It never calls exchanges,
Telegram, LLM providers, account endpoints, or order code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.paper_signals import store
from src.research_lab.paper_signals.contract import PaperActionSignal

SCHEMA = "PaperSignalTrainingRow.v1"
TERMINAL_STATUSES = {"closed_paper", "expired", "invalidated", "reviewed"}


def _midpoint(values: list[float]) -> float:
    if len(values) != 2:
        return 0.0
    return round((float(values[0]) + float(values[1])) / 2.0, 10)


def _first_tp(sig: PaperActionSignal) -> float:
    if not sig.take_profit_plan:
        return 0.0
    try:
        return float(sig.take_profit_plan[0].get("price") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def training_row(sig: PaperActionSignal) -> dict[str, Any]:
    outcome = sig.outcome or {}
    review = sig.review or {}
    return {
        "schema": SCHEMA,
        "signal_id": sig.signal_id,
        "dedup_key": sig.dedup_key,
        "data_fingerprint": sig.data_fingerprint,
        "source": sig.source,
        "symbol": sig.symbol,
        "okx_inst_id": sig.okx_inst_id,
        "timeframe": sig.timeframe,
        "family": sig.setup_family,
        "side": sig.side,
        "status": sig.status,
        "mode": sig.mode,
        "exit_mode": sig.exit_mode,
        "created_at": sig.created_at,
        "boundary_ts": sig.boundary_ts,
        "entry_mid": _midpoint(sig.entry_zone),
        "entry_zone_low": float(sig.entry_zone[0]) if len(sig.entry_zone) == 2 else 0.0,
        "entry_zone_high": float(sig.entry_zone[1]) if len(sig.entry_zone) == 2 else 0.0,
        "stop_loss": float(sig.stop_loss),
        "tp1": _first_tp(sig),
        "risk_pct": float(sig.risk_pct or 0.0),
        "max_hold_bars": int(sig.max_hold_bars),
        "max_hold_minutes": int(sig.max_hold_minutes),
        "result": str(outcome.get("result") or ""),
        "net_pct": outcome.get("net_pct"),
        "mfe_pct": outcome.get("mfe_pct"),
        "mae_pct": outcome.get("mae_pct"),
        "capture": outcome.get("capture"),
        "net_r": review.get("net_r"),
        "diagnosis": str(review.get("diagnosis") or ""),
        "reason_now": sig.reason_now,
        "invalidation_rule": sig.invalidation_rule,
        "chart_context_ref": sig.chart_context_ref,
        "paper_only": True,
    }


def export_training_rows(private_root: Path, *, terminal_only: bool = True) -> dict[str, Any]:
    private_root = Path(private_root)
    signals = store.load_signals(private_root)
    if terminal_only:
        signals = [sig for sig in signals if sig.status in TERMINAL_STATUSES]
    rows = [training_row(sig) for sig in signals]

    out_jsonl = private_root / "state" / "derived" / "paper_signal_training.jsonl"
    out_snapshot = private_root / "state" / "derived" / "paper_signal_training.json"
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    by_family: dict[str, int] = {}
    by_diagnosis: dict[str, int] = {}
    by_result: dict[str, int] = {}
    for row in rows:
        by_family[row["family"]] = by_family.get(row["family"], 0) + 1
        if row["diagnosis"]:
            by_diagnosis[row["diagnosis"]] = by_diagnosis.get(row["diagnosis"], 0) + 1
        if row["result"]:
            by_result[row["result"]] = by_result.get(row["result"], 0) + 1

    summary = {
        "schema": "paper_signal_training_export.v1",
        "rows": len(rows),
        "terminal_only": terminal_only,
        "paper_only": True,
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
        "by_family": by_family,
        "by_diagnosis": by_diagnosis,
        "by_result": by_result,
    }
    out_snapshot.write_text(
        json.dumps({**summary, "items": rows[:200]}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
