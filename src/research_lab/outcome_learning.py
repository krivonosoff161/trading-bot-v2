"""Deterministic paper-outcome learning cases.

This module turns already-exported ``TrainingRow.v2`` records into compact,
sanitized review packs for advisory LLM roles. It does not call providers,
exchanges, Telegram, dotenv, or live/order code. The output is a private
research artifact input; it never grants paper-ready or execution authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable

from src.research_lab import feedback_reader as fr
from src.research_lab.lineage_contract import stable_id

SCHEMA = "OutcomeLearningCase.v1"

LOSS_RESULTS = {"stop", "loss", "stopped"}
WIN_RESULTS = {"take", "tp", "tp1", "closed_take"}
BREAKEVEN_RESULTS = {"simple_be", "breakeven", "be"}
EXPIRED_RESULTS = {"expired", "no_entry", "expired_no_entry"}


@dataclass(frozen=True)
class OutcomeLearningCase:
    case_id: str
    review_kind: str
    outcome_bucket: str
    actionability: str
    source_ref: str
    paper_signal_id: str
    symbol: str
    timeframe: str
    family: str
    diagnosis: str
    result: str
    deterministic_hints: list[str] = field(default_factory=list)
    next_test_dimensions: list[str] = field(default_factory=list)
    peer_stats: dict[str, Any] = field(default_factory=dict)
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _float(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_result(row: dict[str, Any]) -> str:
    return str(row.get("result") or row.get("status") or "").strip().lower()


def _bucket(row: dict[str, Any]) -> str:
    result = _normalized_result(row)
    diagnosis = str(row.get("diagnosis") or "").strip().lower()
    net = _float(row, "net_pct")
    mfe = _float(row, "mfe_pct")
    capture = _float(row, "capture")
    if result in WIN_RESULTS or diagnosis == "good_signal" or (net is not None and net > 0):
        if capture is not None and capture < 0.35 and mfe is not None and mfe > 0.5:
            return "win_low_capture"
        return "win"
    if result in BREAKEVEN_RESULTS or diagnosis == "breakeven_save":
        return "breakeven"
    if result in EXPIRED_RESULTS or diagnosis == "expired_no_entry":
        return "expired_no_entry"
    if diagnosis == "bad_exit_gave_back":
        return "gave_back"
    if result in LOSS_RESULTS or (net is not None and net < 0):
        if mfe is not None and mfe > 0.5:
            return "loss_after_positive_mfe"
        return "loss"
    return "unclear"


def _review_kind(outcome_bucket: str) -> str:
    if outcome_bucket in {"loss", "loss_after_positive_mfe", "gave_back"}:
        return "loss"
    if outcome_bucket == "expired_no_entry":
        return "missed"
    if outcome_bucket in {"win_low_capture", "breakeven"}:
        return "counterfactual"
    if outcome_bucket == "win":
        return "win"
    return "unclear"


def _actionability(outcome_bucket: str) -> str:
    if outcome_bucket in {"loss_after_positive_mfe", "gave_back", "win_low_capture"}:
        return "retest_exit_or_capture"
    if outcome_bucket == "expired_no_entry":
        return "retest_entry_timing"
    if outcome_bucket == "loss":
        return "cluster_before_retest"
    if outcome_bucket == "win":
        return "preserve_pattern"
    if outcome_bucket == "breakeven":
        return "compare_breakeven_policy"
    return "observe_more"


def _deterministic_hints(row: dict[str, Any], outcome_bucket: str) -> list[str]:
    diagnosis = str(row.get("diagnosis") or "").strip()
    mfe = _float(row, "mfe_pct")
    mae = _float(row, "mae_pct")
    capture = _float(row, "capture")
    hints: list[str] = []
    if diagnosis:
        hints.append(f"diagnosis:{diagnosis}")
    if outcome_bucket == "loss_after_positive_mfe":
        hints.append("price moved in favour before terminal loss")
    if outcome_bucket == "gave_back":
        hints.append("favourable move was not retained by exit policy")
    if outcome_bucket == "expired_no_entry":
        hints.append("setup never filled within paper entry window")
    if outcome_bucket == "win_low_capture":
        hints.append("profitable outcome captured a small share of favourable excursion")
    if mfe is not None and mae is not None and mfe > abs(mae):
        hints.append("mfe exceeded adverse excursion")
    if capture is not None and capture < 0.35:
        hints.append("low capture ratio")
    return hints


def _next_test_dimensions(outcome_bucket: str) -> list[str]:
    mapping = {
        "loss": ["regime_filter", "confirmation_gate"],
        "loss_after_positive_mfe": ["exit_mode_partial_be_vs_fixed", "earlier_profit_lock"],
        "gave_back": ["exit_mode_partial_be_vs_fixed", "time_stop_after_mfe"],
        "expired_no_entry": ["entry_zone_width", "entry_timeout", "pretrigger_watch"],
        "breakeven": ["breakeven_policy", "tp1_size_fraction"],
        "win_low_capture": ["tp_ladder", "max_hold_after_tp1"],
        "win": ["preserve_family", "shadow_same_context"],
        "unclear": ["collect_more_outcomes"],
    }
    return list(mapping.get(outcome_bucket, ["collect_more_outcomes"]))


def peer_stats(row: dict[str, Any], rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    family = str(row.get("family") or "")
    timeframe = str(row.get("timeframe") or "")
    peers = [r for r in rows if str(r.get("family") or "") == family and str(r.get("timeframe") or "") == timeframe]
    counts: dict[str, int] = {}
    net_values: list[float] = []
    for peer in peers:
        bucket = _bucket(peer)
        counts[bucket] = counts.get(bucket, 0) + 1
        net = _float(peer, "net_pct")
        if net is not None:
            net_values.append(net)
    avg_net = round(sum(net_values) / len(net_values), 4) if net_values else None
    return {
        "scope": "same_family_timeframe",
        "rows": len(peers),
        "by_outcome_bucket": counts,
        "avg_net_pct": avg_net,
    }


def build_outcome_learning_case(row: dict[str, Any], *, peers: Iterable[dict[str, Any]] = ()) -> OutcomeLearningCase:
    outcome_bucket = _bucket(row)
    source_ref = str(row.get("training_row_id") or row.get("paper_signal_id") or row.get("signal_id") or "")
    payload = {
        "source_ref": source_ref,
        "paper_signal_id": row.get("paper_signal_id"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "family": row.get("family"),
        "result": row.get("result"),
        "diagnosis": row.get("diagnosis"),
        "outcome_bucket": outcome_bucket,
    }
    return OutcomeLearningCase(
        case_id=stable_id("olc", payload),
        review_kind=_review_kind(outcome_bucket),
        outcome_bucket=outcome_bucket,
        actionability=_actionability(outcome_bucket),
        source_ref=source_ref,
        paper_signal_id=str(row.get("paper_signal_id") or row.get("signal_id") or ""),
        symbol=str(row.get("symbol") or ""),
        timeframe=str(row.get("timeframe") or ""),
        family=str(row.get("family") or ""),
        diagnosis=str(row.get("diagnosis") or ""),
        result=str(row.get("result") or ""),
        deterministic_hints=_deterministic_hints(row, outcome_bucket),
        next_test_dimensions=_next_test_dimensions(outcome_bucket),
        peer_stats=peer_stats(row, peers),
    )


def build_outcome_review_pack(row: dict[str, Any], *, peers: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Build the sanitized source payload for the advisory outcome reviewer.

    Exact trade levels and final human card text intentionally stay out of the
    LLM packet. Deterministic code may still retain them in private training
    rows; the reviewer receives outcome facts and lineage refs only.
    """
    case = build_outcome_learning_case(row, peers=peers)
    return {
        "schema": f"{SCHEMA}.review_input",
        "case": case.to_dict(),
        "facts": {
            "symbol": row.get("symbol"),
            "okx_inst_id": row.get("okx_inst_id"),
            "timeframe": row.get("timeframe"),
            "family": row.get("family"),
            "status": row.get("status"),
            "mode": row.get("mode"),
            "exit_mode": row.get("exit_mode"),
            "result": row.get("result"),
            "diagnosis": row.get("diagnosis"),
            "net_pct": row.get("net_pct"),
            "net_r": row.get("net_r"),
            "gross_pct": row.get("gross_pct"),
            "mfe_pct": row.get("mfe_pct"),
            "mae_pct": row.get("mae_pct"),
            "capture": row.get("capture"),
            "risk_pct": row.get("risk_pct"),
            "fees_bps_round_trip": row.get("fees_bps_round_trip"),
            "slippage_bps_round_trip": row.get("slippage_bps_round_trip"),
        },
        "lineage_refs": {
            "training_row_id": row.get("training_row_id"),
            "paper_signal_id": row.get("paper_signal_id"),
            "scanner_event_id": row.get("scanner_event_id"),
            "data_packet_id": row.get("data_packet_id"),
            "feature_packet_id": row.get("feature_packet_id"),
            "setup_candidate_id": row.get("setup_candidate_id"),
            "sweep_run_id": row.get("sweep_run_id"),
            "validation_id": row.get("validation_id"),
            "telegram_card_id": row.get("telegram_card_id"),
            "paper_trade_id": row.get("paper_trade_id"),
            "calculator_advice_id": row.get("calculator_advice_id"),
            "adaptive_policy_id": row.get("adaptive_policy_id"),
        },
        "hard_rules": {
            "paper_only": True,
            "execution_allowed": False,
            "llm_may_change_trade_numbers": False,
            "llm_may_set_validator_status": False,
            "llm_may_set_paper_ready": False,
        },
        "paper_only": True,
        "execution_allowed": False,
    }


def learning_summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    by_kind: dict[str, int] = {}
    by_bucket: dict[str, int] = {}
    by_actionability: dict[str, int] = {}
    for row in items:
        case = build_outcome_learning_case(row, peers=items)
        by_kind[case.review_kind] = by_kind.get(case.review_kind, 0) + 1
        by_bucket[case.outcome_bucket] = by_bucket.get(case.outcome_bucket, 0) + 1
        by_actionability[case.actionability] = by_actionability.get(case.actionability, 0) + 1
    return {
        "schema": "OutcomeLearningSummary.v1",
        "rows": len(items),
        "by_review_kind": by_kind,
        "by_outcome_bucket": by_bucket,
        "by_actionability": by_actionability,
        "paper_only": True,
        "execution_allowed": False,
    }


def training_rows_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_signal_training.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def load_training_rows(private_root: Path) -> list[dict[str, Any]]:
    return _read_jsonl(training_rows_path(private_root))


def load_outcome_reviews(private_root: Path) -> list[dict[str, Any]]:
    return _read_jsonl(Path(private_root) / "state" / "llm_advice" / "outcome_reviews.jsonl")


def _action_from_review(payload: dict[str, Any]) -> str:
    actionability = str(payload.get("actionability") or "").strip()
    if actionability in {"retest_exit_or_capture", "retest_entry_timing", "compare_breakeven_policy"}:
        return fr.NARROW_PARAMS
    if actionability == "preserve_pattern":
        return fr.PROMOTE
    if actionability in {"cluster_before_retest", "observe_more"}:
        return fr.REQUIRE_MORE_DATA
    if bool(payload.get("requires_retest")):
        return fr.NARROW_PARAMS
    return fr.REQUIRE_MORE_DATA


def _priority_from_review(payload: dict[str, Any]) -> str:
    actionability = str(payload.get("actionability") or "")
    if actionability in {"retest_exit_or_capture", "retest_entry_timing"}:
        return "normal"
    if actionability == "preserve_pattern":
        return "low"
    return "low"


def recommendations_from_outcome_reviews(
    training_rows: Iterable[dict[str, Any]],
    review_rows: Iterable[dict[str, Any]],
    *,
    max_recommendations: int = 20,
) -> list[fr.Recommendation]:
    rows_by_ref = {str(row.get("training_row_id") or ""): row for row in training_rows}
    out: list[fr.Recommendation] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for review in review_rows:
        if len(out) >= max(0, int(max_recommendations)):
            break
        if str(review.get("role_id") or "") != "outcome_reviewer":
            continue
        if not bool(review.get("accepted")):
            continue
        payload = review.get("payload") if isinstance(review.get("payload"), dict) else {}
        source_ref = str(review.get("source_ref") or "")
        row = rows_by_ref.get(source_ref)
        if not row:
            continue
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id:
            continue
        strategy_id = str(row.get("family") or "")
        symbol = str(row.get("symbol") or "")
        timeframe = str(row.get("timeframe") or "")
        action = _action_from_review(payload)
        bucket = str(payload.get("outcome_bucket") or row.get("diagnosis") or "unknown")
        actionability = str(payload.get("actionability") or "observe_more")
        key = (candidate_id, strategy_id, symbol, timeframe, action)
        if key in seen:
            continue
        seen.add(key)
        reason_codes = [
            f"outcome_review:{review.get('review_id') or ''}",
            f"review_kind:{payload.get('review_kind') or ''}",
            f"outcome_bucket:{bucket}",
            f"actionability:{actionability}",
        ]
        for dimension in payload.get("next_test_dimensions") or []:
            if dimension:
                reason_codes.append(f"next_test:{dimension}")
        out.append(
            fr.Recommendation(
                action=action,
                strategy_id=strategy_id,
                symbol=symbol,
                timeframe=timeframe,
                reason=str(payload.get("summary") or f"outcome review suggests {actionability}"),
                hard_status=f"OUTCOME_{bucket.upper()}",
                priority=_priority_from_review(payload),
                candidate_ids=[candidate_id],
                reason_codes=reason_codes,
            )
        )
    return out


def build_outcome_review_recommendations(private_root: Path, *, max_recommendations: int = 20) -> list[fr.Recommendation]:
    return recommendations_from_outcome_reviews(
        load_training_rows(private_root),
        load_outcome_reviews(private_root),
        max_recommendations=max_recommendations,
    )
