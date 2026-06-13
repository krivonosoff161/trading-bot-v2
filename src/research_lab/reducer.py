# -*- coding: utf-8 -*-
"""Reduce many run variants into per-group research verdicts (no profit claims).

Groups run results by (family, symbol) and decides a verdict from the *cluster*
of parameter variants, not from a single lucky best. A positive result that is
not supported by its parameter neighbors is flagged unstable, never promoted.

Verdicts: REJECT / OBSERVE / REGIME_SPECIFIC / FORWARD_PAPER / NEEDS_MORE_DATA.
All are research labels — none asserts profitability.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Verdicts
REJECT = "REJECT"
OBSERVE = "OBSERVE"
REGIME_SPECIFIC = "REGIME_SPECIFIC"
FORWARD_PAPER = "FORWARD_PAPER"
NEEDS_MORE_DATA = "NEEDS_MORE_DATA"

# Reason codes
TOO_FEW_TRADES = "too_few_trades"
UNSTABLE_PARAMETERS = "unstable_parameters"
REGIME_DEPENDENT = "regime_dependent"
ENTRY_LATE = "entry_late"
DRAWDOWN_TOO_HIGH = "drawdown_too_high"
WEAK_EDGE = "weak_edge"
CANDIDATE_FOR_FORWARD = "candidate_for_forward"

MIN_TRADES = 20
MIN_TRADES_FOR_DECISION = 10
MIN_VARIANTS_FOR_STABILITY = 3
SUPPORT_FOR_FORWARD = 0.6
PF_FORWARD = 1.2
CAPTURE_MIN = 0.3


@dataclass(frozen=True)
class GroupVerdict:
    family: str
    symbol: str
    verdict: str
    reason_codes: list[str]
    n_variants: int
    positive_variants: int
    support_ratio: float
    total_trades: int
    best_test_avg_net_pct: float
    best_profit_factor: float
    entry_timing: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReduceReport:
    groups: list[GroupVerdict] = field(default_factory=list)
    verdict_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "strategy_lab_reduce_report.v1",
            "disclaimer": "Research labels only; no profitability is claimed.",
            "verdict_counts": self.verdict_counts,
            "groups": [asdict(g) for g in self.groups],
        }


def reduce_results(results: list[Any]) -> ReduceReport:
    groups: dict[tuple[str, str], list[Any]] = {}
    for r in results:
        key = (_get(r, "family"), _get(r, "symbol"))
        groups.setdefault(key, []).append(r)
    verdicts = [_reduce_group(fam, sym, rows) for (fam, sym), rows in sorted(groups.items())]
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    return ReduceReport(groups=verdicts, verdict_counts=counts)


def _reduce_group(family: str, symbol: str, rows: list[Any]) -> GroupVerdict:
    metrics = [_metrics(r) for r in rows]
    n_variants = len(metrics)
    total_trades = sum(int(m.get("n_trades") or 0) for m in metrics)
    positive = [m for m in metrics if _f(m, "avg_net_pct") > 0 and _f(m, "test_avg_net_pct") > 0]
    support_ratio = round(len(positive) / n_variants, 4) if n_variants else 0.0
    best = max(metrics, key=lambda m: _f(m, "test_avg_net_pct"), default={})
    entry = _entry_timing_aggregate(metrics)
    event_entry = _event_timing_aggregate(metrics)
    if event_entry:
        entry = {**entry, **event_entry}

    reasons: list[str] = []
    if total_trades < MIN_TRADES_FOR_DECISION:
        return GroupVerdict(family, symbol, NEEDS_MORE_DATA, [TOO_FEW_TRADES], n_variants,
                            len(positive), support_ratio, total_trades,
                            _f(best, "test_avg_net_pct"), _f(best, "profit_factor"), entry)
    if int(best.get("n_trades") or 0) < MIN_TRADES:
        reasons.append(TOO_FEW_TRADES)

    _structural_reasons(best, entry, reasons)
    single_lucky = len(positive) <= 1 and n_variants >= MIN_VARIANTS_FOR_STABILITY
    if single_lucky or (positive and support_ratio < 0.5):
        reasons.append(UNSTABLE_PARAMETERS)

    verdict = _verdict_for(positive, support_ratio, best, reasons, single_lucky)
    if verdict == FORWARD_PAPER:
        reasons.append(CANDIDATE_FOR_FORWARD)
    return GroupVerdict(family, symbol, verdict, sorted(set(reasons)), n_variants,
                        len(positive), support_ratio, total_trades,
                        _f(best, "test_avg_net_pct"), _f(best, "profit_factor"), entry)


def _structural_reasons(best: dict[str, Any], entry: dict[str, Any], reasons: list[str]) -> None:
    total = _f(best, "total_net_pct")
    if total > 0 and _f(best, "max_drawdown_pct") > total * 0.8:
        reasons.append(DRAWDOWN_TOO_HIGH)
    if 0 < _f(best, "avg_net_pct") and _f(best, "profit_factor") < PF_FORWARD:
        reasons.append(WEAK_EDGE)
    if _regime_carry(best):
        reasons.append(REGIME_DEPENDENT)
    if _entry_is_late(entry):
        reasons.append(ENTRY_LATE)


def _verdict_for(positive, support_ratio, best, reasons, single_lucky) -> str:
    if not positive:
        if REGIME_DEPENDENT in reasons:
            return REGIME_SPECIFIC
        return REJECT
    if single_lucky or UNSTABLE_PARAMETERS in reasons:
        return OBSERVE
    if REGIME_DEPENDENT in reasons and _f(best, "avg_net_pct") <= 0:
        return REGIME_SPECIFIC
    strong = (
        support_ratio >= SUPPORT_FOR_FORWARD
        and _f(best, "test_avg_net_pct") > 0
        and _f(best, "profit_factor") >= PF_FORWARD
        and ENTRY_LATE not in reasons
        and DRAWDOWN_TOO_HIGH not in reasons
        and TOO_FEW_TRADES not in reasons
    )
    return FORWARD_PAPER if strong else OBSERVE


def _entry_timing_aggregate(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    blocks = [m.get("entry_timing") for m in metrics if isinstance(m.get("entry_timing"), dict)]
    if not blocks:
        return {}
    keys = ("avg_capture_ratio", "avg_mfe_pct", "avg_mae_pct", "late_entry_rate")
    out: dict[str, Any] = {}
    for k in keys:
        vals = [float(b[k]) for b in blocks if b.get(k) is not None]
        if vals:
            out[k] = round(sum(vals) / len(vals), 4)
    return out


def _event_timing_aggregate(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate event-anchored timing across variants (empty if no event context)."""
    blocks = [m.get("event_entry_timing") for m in metrics if isinstance(m.get("event_entry_timing"), dict)]
    if not blocks:
        return {}
    out: dict[str, Any] = {"event_variants": len(blocks)}
    caps = [float(b["capture_ratio"]) for b in blocks if b.get("capture_ratio") is not None]
    lags = [float(b["lag_bars"]) for b in blocks if b.get("lag_bars") is not None]
    if caps:
        out["event_capture_ratio"] = round(sum(caps) / len(caps), 4)
    if lags:
        out["event_avg_lag_bars"] = round(sum(lags) / len(lags), 4)
    out["event_late_entry_rate"] = round(sum(1 for b in blocks if b.get("late_entry")) / len(blocks), 4)
    return out


def _entry_is_late(entry: dict[str, Any]) -> bool:
    """Late by the proxy capture ratio, or by a majority of event-anchored entries."""
    if not entry:
        return False
    capture = entry.get("avg_capture_ratio")
    if capture is not None and float(capture) < CAPTURE_MIN:
        return True
    event_late = entry.get("event_late_entry_rate")
    return event_late is not None and float(event_late) >= 0.5


def _regime_carry(best: dict[str, Any]) -> bool:
    breakdown = best.get("regime_breakdown")
    if not isinstance(breakdown, dict):
        return False
    overall = _f(best, "avg_net_pct")
    for key, bucket in breakdown.items():
        if "unknown" in key:
            continue
        if int(bucket.get("n_trades") or 0) >= 8 and float(bucket.get("avg_net_pct") or 0.0) > max(0.0, overall):
            return True
    return False


def _metrics(r: Any) -> dict[str, Any]:
    m = _get(r, "metrics")
    return m if isinstance(m, dict) else {}


def _get(r: Any, name: str) -> Any:
    if isinstance(r, dict):
        return r.get(name)
    return getattr(r, name, None)


def _f(m: dict[str, Any], key: str) -> float:
    try:
        return float(m.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0
