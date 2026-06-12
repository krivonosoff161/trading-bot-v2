# -*- coding: utf-8 -*-
"""Validator-lite for lab candidates.

The grading step (PROMOTE_FOR_PRESSURE_TEST) only means "worth validating".
This validator decides what the candidate actually is right now:

- REJECT          dead on basic statistics
- OBSERVE         not dead, not trustworthy; keep collecting evidence
- REGIME_SPECIFIC overall weak but one regime bucket carries the result
- FORWARD_PAPER   passed every lite gate; next step is paper-forward tracking

None of the statuses is a profitability claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PF_THRESHOLD = 1.2
BEST_TRADE_SHARE_MAX = 0.45
MIN_TEST_TRADES = 5
TEST_VS_TRAIN_MIN_RATIO = 0.3
REGIME_BUCKET_MIN_TRADES = 8

NEXT_ACTIONS = {
    "REJECT": "archive; do not retest identical params without a new hypothesis",
    "OBSERVE": "keep in registry; revisit after more data or a wider neighborhood sweep",
    "REGIME_SPECIFIC": "re-run with the strong regime as a spec filter and re-validate OOS",
    "FORWARD_PAPER": "track paper-forward only; no live interpretation",
}


@dataclass(frozen=True)
class ValidationResult:
    status: str
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    next_action: str = ""


def validate_candidate(metrics: dict[str, Any], decision: str) -> ValidationResult:
    reasons: list[str] = []
    risk_flags: list[str] = []

    n_trades = int(metrics.get("n_trades") or 0)
    min_trades = int(metrics.get("min_trades") or 0)
    if n_trades < min_trades:
        reasons.append("too_few_trades")
        return _result("REJECT", reasons, risk_flags)

    failures = _gate_failures(metrics, risk_flags)
    reasons.extend(failures)

    if not failures:
        return _result("FORWARD_PAPER", ["passed_lite_validation"], risk_flags)

    hard_failures = {"oos_not_positive", "negative_or_flat_average"} & set(failures)
    if hard_failures:
        strong_bucket = _strong_regime_bucket(metrics)
        if strong_bucket:
            reasons.append(f"strong_regime_bucket:{strong_bucket}")
            return _result("REGIME_SPECIFIC", reasons, risk_flags)
        return _result("REJECT", reasons, risk_flags)
    if decision == "REJECT":
        return _result("REJECT", reasons, risk_flags)
    return _result("OBSERVE", reasons, risk_flags)


def _gate_failures(metrics: dict[str, Any], risk_flags: list[str]) -> list[str]:
    failures: list[str] = []
    if _f(metrics, "avg_net_pct") <= 0:
        failures.append("negative_or_flat_average")
    if int(metrics.get("test_trades") or 0) < MIN_TEST_TRADES:
        failures.append("weak_oos_sample")
    if _f(metrics, "test_avg_net_pct") <= 0:
        failures.append("oos_not_positive")
    if _f(metrics, "profit_factor") < PF_THRESHOLD:
        failures.append("weak_profit_factor")
    if _f(metrics, "best_trade_share") > BEST_TRADE_SHARE_MAX:
        failures.append("single_trade_dominance")
    total = _f(metrics, "total_net_pct")
    if total > 0 and _f(metrics, "max_drawdown_pct") > total * 0.8:
        failures.append("drawdown_too_large_vs_total")
    train = _f(metrics, "train_avg_net_pct")
    test = _f(metrics, "test_avg_net_pct")
    if train > 0 and test > 0 and test < train * TEST_VS_TRAIN_MIN_RATIO:
        failures.append("train_test_inconsistent")
    if "stress_avg_net_pct" in metrics and _f(metrics, "stress_avg_net_pct") <= 0:
        failures.append("dies_under_cost_stress")
    if "regime_breakdown" not in metrics:
        risk_flags.append("regime_data_unavailable")
    risk_flags.append("parameter_fragility_unknown")
    return failures


def _strong_regime_bucket(metrics: dict[str, Any]) -> str | None:
    """Best regime bucket that could carry the candidate on its own."""
    breakdown = metrics.get("regime_breakdown")
    if not isinstance(breakdown, dict):
        return None
    best_key = None
    best_avg = 0.0
    for key, bucket in breakdown.items():
        if "unknown" in key:
            continue
        n = int(bucket.get("n_trades") or 0)
        avg = float(bucket.get("avg_net_pct") or 0.0)
        if n >= REGIME_BUCKET_MIN_TRADES and avg > best_avg:
            best_key, best_avg = key, avg
    return best_key


def _result(status: str, reasons: list[str], risk_flags: list[str]) -> ValidationResult:
    return ValidationResult(
        status=status,
        reasons=reasons,
        risk_flags=sorted(set(risk_flags)),
        next_action=NEXT_ACTIONS[status],
    )


def _f(metrics: dict[str, Any], key: str) -> float:
    try:
        return float(metrics.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0
