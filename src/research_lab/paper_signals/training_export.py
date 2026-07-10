"""Training-friendly export from paper-watch outcomes.

This is a derived artifact: it reads the paper-signal audit log and writes a compact
JSONL/snapshot for analysis or model-training pipelines. It never calls exchanges,
Telegram, LLM providers, account endpoints, or order code.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import write_cycle_links
from src.research_lab.paper_signals import store
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.trade_math import first_tp, geometry, midpoint

SCHEMA = "TrainingRow.v2"
ROW_FIELDS_VERSION = "lifecycle_cursor.v2"
TERMINAL_STATUSES = {"closed_paper", "expired", "invalidated", "reviewed"}


def _source_hash(signals: list[PaperActionSignal]) -> str:
    payload = [sig.to_dict() for sig in sorted(signals, key=lambda item: item.signal_id)]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _refs_hash(
    signals: list[PaperActionSignal],
    *,
    cards: dict[str, dict[str, Any]],
    trades: dict[str, dict[str, Any]],
    advice_by_feature: dict[str, dict[str, Any]],
    policy_by_signal: dict[str, dict[str, Any]],
    outcome_reviews_by_training: dict[str, dict[str, Any]],
) -> str:
    """Hash only refs that can affect exported rows for the selected signals."""
    payload: list[dict[str, Any]] = []
    for sig in sorted(signals, key=lambda item: item.signal_id):
        payload.append(
            {
                "signal_id": sig.signal_id,
                "feature_packet_id": sig.feature_packet_id,
                "card": cards.get(sig.signal_id) or {},
                "trade": trades.get(sig.signal_id) or {},
                "advice": advice_by_feature.get(sig.feature_packet_id) or {},
                "policy": policy_by_signal.get(sig.signal_id) or {},
                "outcome_review": outcome_reviews_by_training.get(f"training_{sig.signal_id}") or {},
            }
        )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _card_refs_from_snapshot(path: Path) -> dict[str, dict[str, Any]]:
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


def _card_refs(private_root: Path) -> dict[str, dict[str, Any]]:
    """Resolve paper signal -> human card from durable ledger plus current preview.

    The current preview is a small moving window. The ledger preserves older private
    cards so reviewed outcomes can still link to what subscribers actually saw.
    """
    derived = private_root / "state" / "derived"
    refs = _card_refs_from_snapshot(derived / "paper_telegram_card_ledger.json")
    refs.update(_card_refs_from_snapshot(derived / "paper_telegram_preview.json"))
    return refs


def _trade_refs(private_root: Path) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for name in ("paper_product_trades.json", "main_paper_trades.json"):
        path = private_root / "state" / "derived" / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
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


def _outcome_review_refs(private_root: Path) -> dict[str, dict[str, Any]]:
    path = private_root / "state" / "llm_advice" / "outcome_reviews.jsonl"
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
        if str(row.get("role_id") or "") != "outcome_reviewer":
            continue
        source_ref = str(row.get("source_ref") or "")
        if not source_ref:
            continue
        current = refs.get(source_ref)
        if current is None or bool(row.get("accepted")) or not bool(current.get("accepted")):
            refs[source_ref] = row
    return refs


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16] if text else ""


def training_row(
    sig: PaperActionSignal,
    *,
    telegram_card: dict[str, Any] | None = None,
    paper_trade: dict[str, Any] | None = None,
    calculator_advice: dict[str, Any] | None = None,
    adaptive_policy: dict[str, Any] | None = None,
    outcome_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    outcome = sig.outcome or {}
    review = sig.review or {}
    geom = geometry(sig.entry_zone, sig.stop_loss, sig.take_profit_plan, sig.side)
    training_row_id = f"training_{sig.signal_id}"
    card = telegram_card or {}
    trade = paper_trade or {}
    advice = calculator_advice or {}
    policy = adaptive_policy or {}
    review_ref = outcome_review or {}
    review_payload = review_ref.get("payload") if isinstance(review_ref.get("payload"), dict) else {}
    paper_account = trade.get("paper_account") if isinstance(trade.get("paper_account"), dict) else {}
    card_text = str(card.get("text") or "")
    calculator_advice_id = str(advice.get("calculator_advice_id") or advice.get("advisor_ref") or "")
    llm_ref = calculator_advice_id or sig.llm_interpretation_ref
    validator_context = sig.validator_context or {}
    geometry_profile_id = str(
        validator_context.get("geometry_profile_id")
        or trade.get("farm_geometry_profile_id")
        or ""
    )
    geometry_profile_reason = str(
        validator_context.get("geometry_profile_reason")
        or trade.get("farm_geometry_profile_reason")
        or ""
    )
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
        "farm_geometry_profile_id": geometry_profile_id,
        "farm_geometry_profile_reason": geometry_profile_reason,
        "farm_geometry_entry_scale": validator_context.get("geometry_entry_scale")
        or trade.get("farm_geometry_entry_scale"),
        "farm_geometry_stop_scale": validator_context.get("geometry_stop_scale")
        or trade.get("farm_geometry_stop_scale"),
        "farm_geometry_tp_scale": validator_context.get("geometry_tp_scale")
        or trade.get("farm_geometry_tp_scale"),
        "farm_geometry_hold_scale": validator_context.get("geometry_hold_scale")
        or trade.get("farm_geometry_hold_scale"),
        "telegram_card_id": str(card.get("telegram_card_id") or ""),
        "paper_trade_id": str(trade.get("paper_trade_id") or ""),
        "paper_product_trade_id": str(trade.get("paper_product_trade_id") or ""),
        "main_paper_status": str(trade.get("status") or ""),
        "main_paper_runtime_id": str(trade.get("runtime_id") or ""),
        "outcome_id": f"outcome_{sig.signal_id}" if sig.status in TERMINAL_STATUSES else "",
        "source": sig.source,
        "symbol": sig.symbol,
        "okx_inst_id": sig.okx_inst_id,
        "timeframe": sig.timeframe,
        "family": sig.setup_family,
        "setup_family": sig.setup_family,
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
        "lifecycle_schema": str(outcome.get("lifecycle_schema") or "legacy"),
        "observed_entry": outcome.get("entry"),
        "observed_exit": outcome.get("exit"),
        "opened_at_bar_ts": outcome.get("opened_at_bar_ts"),
        "last_observed_bar_ts": outcome.get("last_observed_bar_ts"),
        "bars_waited": outcome.get("bars_waited"),
        "bars_held": outcome.get("bars_held"),
        "reached_tp1": bool(outcome.get("reached_tp1")),
        "partial_done": bool(outcome.get("partial_done")),
        "banked_pct": outcome.get("banked_pct"),
        "gross_pct": outcome.get("gross_pct"),
        "net_pct": outcome.get("net_pct"),
        "mfe_pct": outcome.get("mfe_pct"),
        "mae_pct": outcome.get("mae_pct"),
        "capture": outcome.get("capture") or review.get("capture_of_mfe"),
        "fees_bps_round_trip": outcome.get("fees_bps_round_trip") or geom["cost_assumptions"]["fees_bps_round_trip"],
        "slippage_bps_round_trip": (
            outcome.get("slippage_bps_round_trip") or geom["cost_assumptions"]["slippage_bps_round_trip"]
        ),
        "paper_deposit_usdt": paper_account.get("deposit_usdt"),
        "paper_position_margin_usdt": paper_account.get("position_margin_usdt"),
        "paper_leverage": paper_account.get("leverage"),
        "paper_notional_usdt": paper_account.get("notional_usdt"),
        "paper_pnl_usdt": paper_account.get("pnl_usdt"),
        "paper_equity_after_usdt": paper_account.get("equity_after_usdt"),
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
        "outcome_review_id": str(review_ref.get("review_id") or ""),
        "outcome_review_accepted": bool(review_ref.get("accepted")) if review_ref else False,
        "outcome_learning_review_kind": str(review_payload.get("review_kind") or ""),
        "outcome_learning_bucket": str(review_payload.get("outcome_bucket") or ""),
        "outcome_learning_actionability": str(review_payload.get("actionability") or ""),
        "llm_provider": str(advice.get("provider") or ""),
        "llm_model": str(advice.get("model") or ""),
        "prompt_version": str(advice.get("prompt_version") or ""),
        "prompt_hash": str(advice.get("prompt_hash") or ""),
        "final_card_text": card_text,
        "final_card_hash": _hash_text(card_text),
        "paper_only": True,
        "execution_allowed": False,
    }


def _load_existing_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k != "items"}
    return {}


def export_training_rows(private_root: Path, *, terminal_only: bool = True, force: bool = False) -> dict[str, Any]:
    private_root = Path(private_root)
    signals = store.load_signals(private_root)
    source_terminal = [sig for sig in signals if sig.status in TERMINAL_STATUSES]
    source_terminal_hash = _source_hash(source_terminal)
    cards = _card_refs(private_root)
    trades = _trade_refs(private_root)
    advice_by_feature = _calculator_refs(private_root)
    policy_by_signal = _adaptive_policy_refs(private_root)
    outcome_reviews_by_training = _outcome_review_refs(private_root)
    export_refs_hash = _refs_hash(
        source_terminal,
        cards=cards,
        trades=trades,
        advice_by_feature=advice_by_feature,
        policy_by_signal=policy_by_signal,
        outcome_reviews_by_training=outcome_reviews_by_training,
    )
    out_jsonl = private_root / "state" / "derived" / "paper_signal_training.jsonl"
    out_snapshot = private_root / "state" / "derived" / "paper_signal_training.json"
    existing = _load_existing_summary(out_snapshot)
    if (
        terminal_only
        and not force
        and out_jsonl.exists()
        and existing.get("source_terminal_hash") == source_terminal_hash
        and existing.get("export_refs_hash") == export_refs_hash
        and existing.get("row_fields_version") == ROW_FIELDS_VERSION
        and int(existing.get("source_terminal_rows") or -1) == len(source_terminal)
    ):
        return {
            **existing,
            "skipped": True,
            "skip_reason": "source_terminal_unchanged",
            "paper_only": True,
            "execution_allowed": False,
            "jsonl_path": str(out_jsonl),
            "snapshot_path": str(out_snapshot),
        }
    if terminal_only:
        signals = source_terminal
    rows = [
        training_row(
            sig,
            telegram_card=cards.get(sig.signal_id),
            paper_trade=trades.get(sig.signal_id),
            calculator_advice=advice_by_feature.get(sig.feature_packet_id),
            adaptive_policy=policy_by_signal.get(sig.signal_id),
            outcome_review=outcome_reviews_by_training.get(f"training_{sig.signal_id}"),
        )
        for sig in signals
    ]
    write_cycle_links(
        private_root,
        [
            {
                "scanner_event_id": row["scanner_event_id"],
                "data_packet_id": row["data_packet_id"],
                "feature_packet_id": row["feature_packet_id"],
                "setup_candidate_id": row["setup_candidate_id"],
                "sweep_run_id": row["sweep_run_id"],
                "validation_id": row["validation_id"],
                "paper_signal_id": row["paper_signal_id"],
                "telegram_card_id": row["telegram_card_id"],
                "paper_trade_id": row["paper_trade_id"],
                "outcome_id": row["outcome_id"],
                "outcome_review_id": row["outcome_review_id"],
                "training_row_id": row["training_row_id"],
                "llm_interpretation_ref": row["llm_interpretation_ref"],
                "adaptive_policy_id": row["adaptive_policy_id"],
                "source": row["source"],
                "symbol": row["symbol"],
                "instrument": row["okx_inst_id"],
                "timeframe": row["timeframe"],
                "setup_family": row["family"],
                "mode": row["mode"],
            }
            for row in rows
        ],
    )

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
        "row_fields_version": ROW_FIELDS_VERSION,
        "rows": len(rows),
        "terminal_only": terminal_only,
        "source_terminal_rows": len(source_terminal),
        "source_terminal_hash": source_terminal_hash,
        "export_refs_hash": export_refs_hash,
        "skipped": False,
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
