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

from src.research_lab.paper_projection_reader import (
    read_projection_view,
    select_current_terminal_training_rows,
)
from src.research_lab.paper_signals import outcome_evidence

from src.research_lab.candle_library import load_canonical_candles
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
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    try:
        return int(float(value))
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
    if (
        result in WIN_RESULTS
        or diagnosis == "good_signal"
        or (net is not None and net > 0)
    ):
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
        hints.append(
            "profitable outcome captured a small share of favourable excursion"
        )
    if mfe is not None and mae is not None and mfe > abs(mae):
        hints.append("mfe exceeded adverse excursion")
    if capture is not None and capture < 0.35:
        hints.append("low capture ratio")
    return hints


def _next_test_dimensions(outcome_bucket: str) -> list[str]:
    mapping = {
        "loss": ["regime_filter", "confirmation_gate"],
        "loss_after_positive_mfe": [
            "exit_mode_partial_be_vs_fixed",
            "earlier_profit_lock",
        ],
        "gave_back": ["exit_mode_partial_be_vs_fixed", "time_stop_after_mfe"],
        "expired_no_entry": ["entry_zone_width", "entry_timeout", "pretrigger_watch"],
        "breakeven": ["breakeven_policy", "tp1_size_fraction"],
        "win_low_capture": ["tp_ladder", "max_hold_after_tp1"],
        "win": ["preserve_family", "shadow_same_context"],
        "unclear": ["collect_more_outcomes"],
    }
    return list(mapping.get(outcome_bucket, ["collect_more_outcomes"]))


def _round_float(value: Any, digits: int = 8) -> float | None:
    if value in ("", None):
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _compact_float(value: Any) -> float | None:
    parsed = _round_float(value, 8)
    if parsed is None:
        return None
    return parsed


def _pct(entry: float | None, price: float | None, side: str) -> float | None:
    if entry is None or price is None or entry <= 0 or price <= 0:
        return None
    direction = 1.0 if str(side).lower() == "long" else -1.0
    return round((price - entry) / entry * 100.0 * direction, 4)


def _planned_trade(row: dict[str, Any]) -> dict[str, Any]:
    entry_mid = _compact_float(row.get("entry_mid"))
    stop = _compact_float(row.get("stop_loss"))
    tp1 = _compact_float(row.get("tp1"))
    return {
        "side": str(row.get("side") or ""),
        "entry_mid": entry_mid,
        "entry_zone": [
            value
            for value in (
                _compact_float(row.get("entry_zone_low")),
                _compact_float(row.get("entry_zone_high")),
            )
            if value is not None
        ],
        "stop_loss": stop,
        "tp1": tp1,
        "tp1_distance_pct": _pct(entry_mid, tp1, str(row.get("side") or "")),
        "stop_distance_pct": _pct(entry_mid, stop, str(row.get("side") or "")),
        "risk_pct": _compact_float(row.get("risk_pct")),
        "max_hold_bars": _int(row, "max_hold_bars"),
        "max_hold_minutes": _int(row, "max_hold_minutes"),
        "exit_mode": str(row.get("exit_mode") or ""),
        "invalidation_rule": str(row.get("invalidation_rule") or ""),
        "reason_now": str(row.get("reason_now") or ""),
        "source_validation_verdict": str(row.get("source_validation_verdict") or ""),
        "ready_strategy_id": str(row.get("ready_strategy_id") or ""),
    }


def _observed_trade(row: dict[str, Any]) -> dict[str, Any]:
    entry = _compact_float(row.get("observed_entry")) or _compact_float(
        row.get("entry_mid")
    )
    exit_price = _compact_float(row.get("observed_exit"))
    return {
        "observed_entry": entry,
        "observed_exit": exit_price,
        "observed_return_pct": _pct(entry, exit_price, str(row.get("side") or "")),
        "result": str(row.get("result") or ""),
        "diagnosis": str(row.get("diagnosis") or ""),
        "gross_pct": row.get("gross_pct"),
        "net_pct": row.get("net_pct"),
        "net_r": row.get("net_r"),
        "mfe_pct": row.get("mfe_pct"),
        "mae_pct": row.get("mae_pct"),
        "capture": row.get("capture"),
        "bars_held": _int(row, "bars_held"),
        "reached_tp1": bool(row.get("reached_tp1")),
        "partial_done": bool(row.get("partial_done")),
        "banked_pct": row.get("banked_pct"),
        "fees_bps_round_trip": row.get("fees_bps_round_trip"),
        "slippage_bps_round_trip": row.get("slippage_bps_round_trip"),
    }


def _timeframe_ms(timeframe: str) -> int:
    tf = str(timeframe or "").strip().lower()
    return {
        "1m": 60_000,
        "5m": 5 * 60_000,
        "15m": 15 * 60_000,
        "1h": 60 * 60_000,
        "4h": 4 * 60 * 60_000,
        "1d": 24 * 60 * 60_000,
    }.get(tf, 15 * 60_000)


def _candle_price(row: dict[str, Any], key: str) -> float | None:
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return None


def _compact_candles(
    candles: list[dict[str, Any]], *, entry: float | None, side: str
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for idx, row in enumerate(candles):
        high = _candle_price(row, "high")
        low = _candle_price(row, "low")
        close = _candle_price(row, "close")
        compact.append(
            {
                "i": idx,
                "ts": int(row.get("ts") or 0),
                "open": _compact_float(row.get("open")),
                "high": _compact_float(high),
                "low": _compact_float(low),
                "close": _compact_float(close),
                "vol": _compact_float(row.get("vol")),
                "close_vs_entry_pct": _pct(entry, close, side),
                "high_vs_entry_pct": _pct(entry, high, side),
                "low_vs_entry_pct": _pct(entry, low, side),
            }
        )
    return compact


def _path_summary(
    candles: list[dict[str, Any]], *, entry: float | None, side: str
) -> dict[str, Any]:
    if not candles or entry is None or entry <= 0:
        return {"bars": len(candles), "status": "no_entry_or_empty_path"}
    favourable: list[tuple[int, float]] = []
    adverse: list[tuple[int, float]] = []
    for idx, row in enumerate(candles):
        high = _candle_price(row, "high")
        low = _candle_price(row, "low")
        if str(side).lower() == "long":
            fav = _pct(entry, high, side)
            adv = _pct(entry, low, side)
        else:
            fav = _pct(entry, low, side)
            adv = _pct(entry, high, side)
        if fav is not None:
            favourable.append((idx, fav))
        if adv is not None:
            adverse.append((idx, adv))
    best = max(favourable, key=lambda item: item[1], default=(None, None))
    worst = min(adverse, key=lambda item: item[1], default=(None, None))
    closes = [_pct(entry, _candle_price(row, "close"), side) for row in candles]
    closes = [value for value in closes if value is not None]
    return {
        "bars": len(candles),
        "best_favourable_pct": best[1],
        "best_favourable_bar": best[0],
        "worst_adverse_pct": worst[1],
        "worst_adverse_bar": worst[0],
        "last_close_vs_entry_pct": closes[-1] if closes else None,
        "status": "available",
    }


def _market_context(row: dict[str, Any], private_root: Path | None) -> dict[str, Any]:
    if private_root is None:
        return {
            "schema": "OutcomeMarketContext.v2",
            "status": "not_available",
            "reason": "private_root_not_supplied",
            "candles": [],
        }
    symbol = str(row.get("okx_inst_id") or row.get("symbol") or "").strip()
    timeframe = str(row.get("timeframe") or "").strip().lower()
    if not symbol or not timeframe:
        return {
            "schema": "OutcomeMarketContext.v2",
            "status": "not_available",
            "reason": "missing_symbol_or_timeframe",
            "candles": [],
        }
    try:
        candle_slice = load_canonical_candles(
            private_root,
            symbol,
            timeframe,
            purpose="outcome_learning",
            coverage_policy="available",
        )
        candles = candle_slice.rows
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        candle_slice = None
        candles = []
    if not candles:
        return {
            "schema": "OutcomeMarketContext.v2",
            "status": "not_available",
            "reason": "prepared_candles_not_found",
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": [],
        }
    assert candle_slice is not None
    boundary = _int(row, "boundary_ts")
    tf_ms = _timeframe_ms(timeframe)
    hold = max(1, _int(row, "max_hold_bars") or 1)
    entry = _compact_float(row.get("observed_entry")) or _compact_float(
        row.get("entry_mid")
    )
    side = str(row.get("side") or "")
    if boundary:
        before = 24
        after = min(64, hold + 16)
        start_ts = boundary - before * tf_ms
        end_ts = boundary + after * tf_ms
        window = [c for c in candles if start_ts <= int(c.get("ts") or 0) <= end_ts]
        pre_bars = sum(1 for c in window if int(c.get("ts") or 0) <= boundary)
    else:
        window = candles[-80:]
        pre_bars = 0
    if len(window) > 88:
        window = window[-88:]
    return {
        "schema": "OutcomeMarketContext.v2",
        "status": "available" if window else "not_available",
        "reason": "" if window else "no_candles_in_signal_window",
        "symbol": symbol,
        "timeframe": timeframe,
        "source_label": candle_slice.label,
        "candle_source": candle_slice.source,
        "data_snapshot_id": candle_slice.manifest.snapshot_id,
        "data_evidence_hash": candle_slice.manifest.evidence_hash,
        "data_provenance_status": candle_slice.manifest.provenance_status,
        "boundary_ts": boundary,
        "pre_bars": pre_bars,
        "post_bars": max(0, len(window) - pre_bars),
        "summary": _path_summary(window, entry=entry, side=side),
        "candles": _compact_candles(window, entry=entry, side=side),
    }


def peer_stats(row: dict[str, Any], rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    family = str(row.get("family") or "")
    timeframe = str(row.get("timeframe") or "")
    peers = [
        r
        for r in rows
        if str(r.get("family") or "") == family
        and str(r.get("timeframe") or "") == timeframe
    ]
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


def build_outcome_learning_case(
    row: dict[str, Any], *, peers: Iterable[dict[str, Any]] = ()
) -> OutcomeLearningCase:
    if not outcome_evidence.is_market_outcome(row):
        raise ValueError("operational incident is not outcome-learning evidence")
    peers = [peer for peer in peers if outcome_evidence.is_market_outcome(peer)]
    outcome_bucket = _bucket(row)
    source_ref = str(
        row.get("training_row_id")
        or row.get("paper_signal_id")
        or row.get("signal_id")
        or ""
    )
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


def build_outcome_review_pack(
    row: dict[str, Any],
    *,
    peers: Iterable[dict[str, Any]] = (),
    private_root: Path | None = None,
) -> dict[str, Any]:
    """Build the sanitized source payload for the advisory outcome reviewer.

    Trade levels are included only as read-only observed/planned facts so the
    analyst can explain the outcome and propose retest dimensions. Final human
    card text stays out of the packet. The reviewer still cannot output trade
    levels, validator verdicts, paper_ready, orders, or execution commands.
    """
    case = build_outcome_learning_case(row, peers=peers)
    return {
        "schema": f"{SCHEMA}.review_input",
        "case": case.to_dict(),
        "original_plan": _planned_trade(row),
        "observed_trade": _observed_trade(row),
        "market_context": _market_context(row, private_root),
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
            "llm_may_read_trade_numbers": True,
            "llm_may_change_trade_numbers": False,
            "llm_may_set_validator_status": False,
            "llm_may_set_paper_ready": False,
            "llm_output_must_be_hypotheses_not_orders": True,
        },
        "paper_only": True,
        "execution_allowed": False,
    }


def learning_summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    source_items = list(rows)
    items = [row for row in source_items if outcome_evidence.is_market_outcome(row)]
    by_kind: dict[str, int] = {}
    by_bucket: dict[str, int] = {}
    by_actionability: dict[str, int] = {}
    for row in items:
        case = build_outcome_learning_case(row, peers=items)
        by_kind[case.review_kind] = by_kind.get(case.review_kind, 0) + 1
        by_bucket[case.outcome_bucket] = by_bucket.get(case.outcome_bucket, 0) + 1
        by_actionability[case.actionability] = (
            by_actionability.get(case.actionability, 0) + 1
        )
    return {
        "schema": "OutcomeLearningSummary.v1",
        "rows": len(items),
        "source_rows": len(source_items),
        "operational_incidents_censored": len(source_items) - len(items),
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
    """Load the rebuildable export without granting it evidence authority."""
    return _read_jsonl(training_rows_path(private_root))


def load_current_training_evidence(
    private_root: Path,
    *,
    evidence_database_path: Path | str | None = None,
) -> dict[str, Any]:
    """Select exact current-generation terminal rows for adaptive consumers.

    ``paper_signal_training.jsonl`` is a rebuildable projection.  Its rows may
    remain useful for forensic display, but they can steer an LLM review,
    retest, recommendation, or System Analyst task only when they bind to the
    exact current completed paper-evidence generation and terminal account
    result.
    """
    private_root = Path(private_root)
    rows = load_training_rows(private_root)
    generation = read_projection_view(
        private_root,
        "trades",
        legacy_snapshot=private_root
        / "state"
        / "derived"
        / "main_paper_trades.json",
        evidence_database_path=evidence_database_path,
    )
    return select_current_terminal_training_rows(rows, generation)


def load_current_training_rows(
    private_root: Path,
    *,
    evidence_database_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return copies of only current evidence-authoritative training rows."""
    selection = load_current_training_evidence(
        private_root,
        evidence_database_path=evidence_database_path,
    )
    return list(selection["items"])


def load_outcome_reviews(private_root: Path) -> list[dict[str, Any]]:
    return _read_jsonl(
        Path(private_root) / "state" / "llm_advice" / "outcome_reviews.jsonl"
    )


def _action_from_review(payload: dict[str, Any]) -> str:
    actionability = str(payload.get("actionability") or "").strip()
    if actionability in {
        "retest_exit_or_capture",
        "retest_entry_timing",
        "compare_breakeven_policy",
    }:
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
        payload = (
            raw_payload
            if isinstance(raw_payload := review.get("payload"), dict)
            else {}
        )
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
                reason=str(
                    payload.get("summary") or f"outcome review suggests {actionability}"
                ),
                hard_status=f"OUTCOME_{bucket.upper()}",
                priority=_priority_from_review(payload),
                candidate_ids=[candidate_id],
                reason_codes=reason_codes,
            )
        )
    return out


def build_outcome_review_recommendations(
    private_root: Path, *, max_recommendations: int = 20
) -> list[fr.Recommendation]:
    return recommendations_from_outcome_reviews(
        load_current_training_rows(private_root),
        load_outcome_reviews(private_root),
        max_recommendations=max_recommendations,
    )
