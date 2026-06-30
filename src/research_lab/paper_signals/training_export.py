"""Training-friendly export from paper-watch outcomes.

This is a derived artifact: it reads the paper-signal audit log and writes a compact
JSONL/snapshot for analysis or model-training pipelines. It never calls exchanges,
Telegram, LLM providers, account endpoints, or order code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import write_cycle_link
from src.research_lab.paper_signals import store
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.trade_math import first_tp, geometry, midpoint

SCHEMA = "TrainingRow.v2"
TERMINAL_STATUSES = {"closed_paper", "expired", "invalidated", "reviewed"}


def _card_refs(private_root: Path) -> dict[str, dict[str, Any]]:
    path = private_root / "state" / "derived" / "paper_telegram_preview.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    refs: dict[str, dict[str, Any]] = {}
    for item in data.get("items") or []:
        sid = str(item.get("source_signal_id") or "")
        if sid:
            refs[sid] = item
    return refs


def _calculator_refs(private_root: Path) -> dict[str, dict[str, Any]]:
    path = private_root / "state" / "llm_advice" / "calculator_advice.jsonl"
    if not path.exists():
        return {}
    refs: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return refs
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        fid = str(row.get("feature_packet_id") or "")
        if not fid:
            continue
        current = refs.get(fid)
        if current is None or bool(row.get("accepted")) or not bool(current.get("accepted")):
            refs[fid] = row
    return refs


def _adaptive_policy_refs(private_root: Path) -> dict[str, dict[str, Any]]:
    path = private_root / "state" / "derived" / "main_adaptive_policy.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    refs: dict[str, dict[str, Any]] = {}
    for item in data.get("items") or []:
        sid = str(item.get("source_signal_id") or "")
        if sid:
            refs[sid] = item
    return refs


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16] if text else ""


def training_row(
    sig: PaperActionSignal,
    *,
    telegram_card: dict[str, Any] | None = None,
    calculator_advice: dict[str, Any] | None = None,
    adaptive_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    outcome = sig.outcome or {}
    review = sig.review or {}
    geom = geometry(sig.entry_zone, sig.stop_loss, sig.take_profit_plan, sig.side)
    training_row_id = f"training_{sig.signal_id}"
    card = telegram_card or {}
    advice = calculator_advice or {}
    policy = adaptive_policy or {}
    card_text = str(card.get("text") or "")
    calculator_advice_id = str(advice.get("calculator_advice_id") or advice.get("advisor_ref") or "")
    llm_ref = calculator_advice_id or sig.llm_interpretation_ref
    validator_context = sig.validator_context or {}
    return {
        "schema": SCHEMA,
        "schema_compat": ["PaperSignalTrainingRow.v1", "PaperSignalTrainingRow.v2"],
        "training_row_id": training_row_id,
        "signal_id": sig.signal_id,
        "paper_signal_id": sig.signal_id,
        "dedup_key": sig.dedup_key,
        "data_fingerprint": sig.data_fingerprint,
        "scanner_event_id": sig.scanner_event_id,
        "data_packet_id": sig.data_packet_id,
        "feature_packet_id": sig.feature_packet_id,
        "setup_candidate_id": sig.setup_candidate_id,
        "sweep_run_id": sig.sweep_run_id,
        "validation_id": sig.validation_id,
        "ready_strategy_id": str(validator_context.get("ready_strategy_id") or ""),
        "setup_id": str(validator_context.get("setup_id") or ""),
        "candidate_id": str(validator_context.get("candidate_id") or ""),
        "source_validation_verdict": str(validator_context.get("source_validation_verdict") or ""),
        "telegram_card_id": str(card.get("telegram_card_id") or ""),
        "outcome_id": f"outcome_{sig.signal_id}" if sig.status in TERMINAL_STATUSES else "",
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
        "entry_mid": midpoint(sig.entry_zone),
        "entry_zone_low": float(sig.entry_zone[0]) if len(sig.entry_zone) == 2 else 0.0,
        "entry_zone_high": float(sig.entry_zone[1]) if len(sig.entry_zone) == 2 else 0.0,
        "stop_loss": float(sig.stop_loss),
        "tp1": first_tp(sig.take_profit_plan),
        "geometry": geom,
        "risk_pct": float(sig.risk_pct or 0.0),
        "max_hold_bars": int(sig.max_hold_bars),
        "max_hold_minutes": int(sig.max_hold_minutes),
        "result": str(outcome.get("result") or ""),
        "gross_pct": outcome.get("gross_pct"),
        "net_pct": outcome.get("net_pct"),
        "mfe_pct": outcome.get("mfe_pct"),
        "mae_pct": outcome.get("mae_pct"),
        "capture": outcome.get("capture") or review.get("capture_of_mfe"),
        "fees_bps_round_trip": outcome.get("fees_bps_round_trip") or geom["cost_assumptions"]["fees_bps_round_trip"],
        "slippage_bps_round_trip": (
            outcome.get("slippage_bps_round_trip") or geom["cost_assumptions"]["slippage_bps_round_trip"]
        ),
        "net_r": review.get("net_r"),
        "diagnosis": str(review.get("diagnosis") or ""),
        "reason_now": sig.reason_now,
        "invalidation_rule": sig.invalidation_rule,
        "chart_context_ref": sig.chart_context_ref,
        "llm_interpretation_ref": llm_ref,
        "calculator_advice_id": calculator_advice_id,
        "adaptive_policy_id": str(policy.get("policy_id") or ""),
        "adaptive_execution_profile": str(policy.get("execution_profile") or ""),
        "adaptive_entry_profile": str(policy.get("entry_profile") or ""),
        "adaptive_exit_profile": str(policy.get("exit_profile") or ""),
        "adaptive_stop_profile": str(policy.get("stop_profile") or ""),
        "adaptive_max_hold_profile": str(policy.get("max_hold_profile") or ""),
        "adaptive_regime_hint": str(policy.get("regime_hint") or ""),
        "adaptive_policy_confidence": policy.get("confidence"),
        "adaptive_policy_reasons": list(policy.get("reason_codes") or []),
        "llm_provider": str(advice.get("provider") or ""),
        "llm_model": str(advice.get("model") or ""),
        "prompt_version": str(advice.get("prompt_version") or ""),
        "prompt_hash": str(advice.get("prompt_hash") or ""),
        "final_card_text": card_text,
        "final_card_hash": _hash_text(card_text),
        "paper_only": True,
        "execution_allowed": False,
    }


def export_training_rows(private_root: Path, *, terminal_only: bool = True) -> dict[str, Any]:
    private_root = Path(private_root)
    signals = store.load_signals(private_root)
    if terminal_only:
        signals = [sig for sig in signals if sig.status in TERMINAL_STATUSES]
    cards = _card_refs(private_root)
    advice_by_feature = _calculator_refs(private_root)
    policy_by_signal = _adaptive_policy_refs(private_root)
    rows = [
        training_row(
            sig,
            telegram_card=cards.get(sig.signal_id),
            calculator_advice=advice_by_feature.get(sig.feature_packet_id),
            adaptive_policy=policy_by_signal.get(sig.signal_id),
        )
        for sig in signals
    ]
    for row in rows:
        write_cycle_link(
            private_root,
            {
                "scanner_event_id": row["scanner_event_id"],
                "data_packet_id": row["data_packet_id"],
                "feature_packet_id": row["feature_packet_id"],
                "setup_candidate_id": row["setup_candidate_id"],
                "sweep_run_id": row["sweep_run_id"],
                "validation_id": row["validation_id"],
                "paper_signal_id": row["paper_signal_id"],
                "telegram_card_id": row["telegram_card_id"],
                "outcome_id": row["outcome_id"],
                "training_row_id": row["training_row_id"],
                "llm_interpretation_ref": row["llm_interpretation_ref"],
                "adaptive_policy_id": row["adaptive_policy_id"],
                "source": row["source"],
                "symbol": row["symbol"],
                "instrument": row["okx_inst_id"],
                "timeframe": row["timeframe"],
                "setup_family": row["family"],
                "mode": row["mode"],
            },
        )

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
        "schema": "paper_signal_training_export.v2",
        "row_schema": SCHEMA,
        "rows": len(rows),
        "terminal_only": terminal_only,
        "paper_only": True,
        "execution_allowed": False,
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
