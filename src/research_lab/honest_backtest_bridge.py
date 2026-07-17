# -*- coding: utf-8 -*-
"""Bridge between Strategy Lab hard validation requests and honest-backtest.

Loads a CandidateForValidation, attempts to import honest-backtest as a
library, runs the applicable validation layers, and produces a
HardValidationReport + HardValidationVerdict.

If honest-backtest is not importable, returns a bridge_unavailable report.

No network. No LLM. No live trading.
"""
from __future__ import annotations

import datetime as dt
import math
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

from src.research_lab.hard_validation_contract import (
    CONTRACT_VERSION,
    CandidateForValidation,
    HardValidationReport,
    HardValidationVerdict,
    trade_evidence_hash,
    validation_evidence_hash,
    write_json,
)

try:
    import numpy as _np

    _HAS_NUMPY = True
except ImportError:
    _np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

_HB_ENV = os.environ.get("STRATEGY_LAB_HONEST_BACKTEST_SRC", "").strip()
_HB_VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "honest-backtest" / "src"
for _path_text in (_HB_ENV, str(_HB_VENDOR)):
    if not _path_text:
        continue
    _path = Path(_path_text)
    if _path.exists() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

try:
    import backtest_sanity as _bs
    from backtest_sanity import (
        bootstrap_ci,
        deflated_sharpe_ratio,
        minimum_track_record_length,
        permutation_test,
        probability_of_backtest_overfitting,
        probabilistic_sharpe_ratio,
        subperiod_stability,
        walk_forward,
    )

    _HAS_BACKTEST_SANITY = True
    _BACKTEST_SANITY_PATH = getattr(_bs, "__file__", "")
except ImportError:
    _HAS_BACKTEST_SANITY = False
    _BACKTEST_SANITY_PATH = ""


REPORTS_DIR = "hard_validation/reports"
VERDICTS_DIR = "hard_validation/verdicts"


class BridgeUnavailableError(RuntimeError):
    """honest-backtest is not importable and degraded validation is not allowed.

    Raised so that a missing/broken statistical engine fails LOUD instead of
    masquerading as an ordinary ``NEEDS_MORE_DATA`` verdict. The vendored copy
    lives at ``vendor/honest-backtest/src`` (see ``vendor/honest-backtest/VENDOR.md``).
    """


def _allow_degraded() -> bool:
    """True only when the operator explicitly opts into degraded validation."""
    value = os.environ.get("STRATEGY_LAB_ALLOW_DEGRADED_VALIDATION", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _missing_components() -> list[str]:
    missing = []
    if not _HAS_NUMPY:
        missing.append("numpy")
    if not _HAS_BACKTEST_SANITY:
        missing.append("backtest-sanity")
    return missing


def ensure_bridge_available(*, strict: bool | None = None) -> None:
    """Fail loud when the honest-backtest engine is unavailable.

    By default (``strict=None``) this raises ``BridgeUnavailableError`` unless
    ``STRATEGY_LAB_ALLOW_DEGRADED_VALIDATION`` is set, so a clean checkout without
    numpy/backtest_sanity cannot silently turn every candidate into NEEDS_MORE_DATA.
    """
    if _HAS_NUMPY and _HAS_BACKTEST_SANITY:
        return
    if strict is None:
        strict = not _allow_degraded()
    if strict:
        missing = ", ".join(_missing_components())
        raise BridgeUnavailableError(
            f"honest-backtest bridge unavailable: missing {missing}. "
            "Expected vendored copy at vendor/honest-backtest/src "
            "(see vendor/honest-backtest/VENDOR.md); or pip install numpy. "
            "Set STRATEGY_LAB_ALLOW_DEGRADED_VALIDATION=1 to allow a degraded "
            "NEEDS_MORE_DATA fallback instead of failing."
        )


def bridge_available() -> dict[str, Any]:
    return {
        "numpy": _HAS_NUMPY,
        "backtest_sanity": _HAS_BACKTEST_SANITY,
        "available": _HAS_NUMPY and _HAS_BACKTEST_SANITY,
        "degraded_allowed": _allow_degraded(),
        "source": _BACKTEST_SANITY_PATH,
        "vendored": "vendor" in _BACKTEST_SANITY_PATH.replace("\\", "/").lower(),
    }


def run_validation(
    candidate: CandidateForValidation,
    private_root: Path,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Run hard validation on a single candidate.

    Returns a summary dict with the verdict and report paths.
    """
    ensure_bridge_available()
    if not _HAS_NUMPY or not _HAS_BACKTEST_SANITY:
        result = _bridge_unavailable(candidate)
        if not dry_run:
            _write_minimal_artifacts(private_root, candidate, result)
        return result

    contract_errors = _candidate_contract_errors(candidate)
    if contract_errors:
        checks = [{"check_name": "contract_provenance", "passed": False,
                   "details": {"errors": contract_errors},
                   "message": "; ".join(contract_errors)}]
        verdict = _build_verdict(candidate, checks)
        report = _build_report(candidate, verdict, checks)
        if not dry_run:
            _write_artifacts(private_root, candidate, verdict, report)
        return {"candidate_id": candidate.candidate_id,
                "hard_status": verdict.hard_status, "checks_run": 1,
                "checks_passed": 0, "checks_failed": 1, "dry_run": dry_run}

    returns = _extract_returns(candidate)
    if len(returns) < 3:
        result = _insufficient_data(candidate, len(returns))
        if not dry_run:
            _write_minimal_artifacts(private_root, candidate, result)
        return result

    checks = _run_all_checks(candidate, returns)
    verdict = _build_verdict(candidate, checks)
    report = _build_report(candidate, verdict, checks)

    if not dry_run:
        _write_artifacts(private_root, candidate, verdict, report)

    return {
        "candidate_id": candidate.candidate_id,
        "hard_status": verdict.hard_status,
        "checks_run": len(checks),
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_failed": sum(1 for c in checks if not c["passed"]),
        "dry_run": dry_run,
    }


def run_validation_batch(
    requests_dir: Path,
    private_root: Path,
    *,
    dry_run: bool = True,
    limit: int = 50,
    candidate_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run validation on all request files in a directory."""
    ensure_bridge_available()
    if not requests_dir.exists():
        return {"total": 0, "validated": 0, "errors": 0}

    if candidate_ids:
        wanted = [str(cid) for cid in candidate_ids if str(cid)]
        request_files = [requests_dir / f"{_artifact_stem(cid)}.json" for cid in wanted]
        request_files = [p for p in request_files if p.exists()]
    else:
        request_files = sorted(requests_dir.glob("*.json"))[:limit]
    results = []
    for rf in request_files:
        try:
            data = _read_json(rf)
            version = data.get("contract_version") if isinstance(data, dict) else None
            if version != CONTRACT_VERSION:
                raise ValueError(
                    f"unsupported contract_version {version!r}; expected {CONTRACT_VERSION!r}"
                )
            candidate = CandidateForValidation.from_dict(data)
            result = run_validation(candidate, private_root, dry_run=dry_run)
            results.append(result)
        except Exception as exc:
            results.append({
                "candidate_id": rf.stem,
                "error": str(exc),
            })

    return {
        "total": len(request_files),
        "validated": sum(1 for r in results if "hard_status" in r),
        "errors": sum(1 for r in results if "error" in r),
        "results": results,
    }


def _bridge_unavailable(candidate: CandidateForValidation) -> dict[str, Any]:
    missing = []
    if not _HAS_NUMPY:
        missing.append("numpy")
    if not _HAS_BACKTEST_SANITY:
        missing.append("backtest-sanity")
    return {
        "candidate_id": candidate.candidate_id,
        "hard_status": "NEEDS_MORE_DATA",
        "bridge_unavailable": True,
        "missing": missing,
        "message": (
            f"honest-backtest bridge unavailable: missing {', '.join(missing)}. "
            "Install: pip install numpy backtest-sanity"
        ),
    }


def _insufficient_data(
    candidate: CandidateForValidation, n_returns: int,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "hard_status": "NEEDS_MORE_DATA",
        "n_returns": n_returns,
        "message": f"Only {n_returns} returns available, need at least 3.",
    }


def _extract_returns(candidate: CandidateForValidation) -> list[float]:
    returns = []
    basis = str(candidate.metrics.get("returns_basis") or "net_pct")
    preferred = "pnl_pct" if basis == "gross_pct" else "net_pct"
    for t in candidate.trades:
        raw = t.get(preferred)
        pnl = float(raw if raw is not None else 0.0)
        returns.append(pnl)
    if not returns and candidate.equity_curve:
        values = [float(p.get("value") or 0) for p in candidate.equity_curve]
        for i in range(1, len(values)):
            if values[i - 1] != 0:
                returns.append((values[i] / values[i - 1] - 1) * 100)
    return returns


def _candidate_contract_errors(candidate: CandidateForValidation) -> list[str]:
    errors = []
    if candidate.contract_version != CONTRACT_VERSION:
        errors.append("contract_version_mismatch")
    if not candidate.candidate_id or not candidate.source_run_id:
        errors.append("missing_candidate_or_source_run_id")
    if not candidate.symbol or not candidate.strategy_id:
        errors.append("missing_symbol_or_strategy_id")
    if not candidate.timeframe or candidate.timeframe == "unknown":
        errors.append("missing_timeframe_provenance")
    fingerprint = str((candidate.metrics or {}).get("data_fingerprint") or "")
    if not fingerprint or fingerprint == "nofp":
        errors.append("missing_data_fingerprint")
    basis = str((candidate.metrics or {}).get("returns_basis") or "")
    costs_applied = (candidate.metrics or {}).get("costs_applied")
    if (
        not math.isfinite(candidate.fees_bps)
        or not math.isfinite(candidate.slippage_bps)
        or candidate.fees_bps < 0
        or candidate.slippage_bps < 0
    ):
        errors.append("invalid_cost_assumptions")
    if basis not in {"gross_pct", "net_pct"}:
        errors.append("missing_returns_basis")
    elif costs_applied is not (basis == "net_pct"):
        errors.append("inconsistent_cost_application_provenance")
    elif candidate.trades and any(
        trade.get("pnl_pct" if basis == "gross_pct" else "net_pct") is None
        for trade in candidate.trades
    ):
        errors.append("returns_basis_field_missing")
    try:
        _n_trials(candidate)
    except (TypeError, ValueError) as exc:
        errors.append(f"invalid_search_family_evidence:{exc}")
    return errors


def _n_trials(candidate: CandidateForValidation) -> int:
    """Recompute the effective family count from verified immutable evidence."""
    from src.research_lab.search_trial_evidence import validate_search_trial_evidence

    m = candidate.metrics or {}
    evidence = m.get("search_trial_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("verified search family evidence is required")
    counts = validate_search_trial_evidence(evidence, require_complete=True)
    n = counts["effective_n_trials"]
    runtime = m.get("runtime") if isinstance(m.get("runtime"), dict) else {}
    claims = (
        ("runtime.n_variants_evaluated", runtime.get("n_variants_evaluated"), counts["attempted_executions"]),
        ("n_variants_evaluated", m.get("n_variants_evaluated"), counts["attempted_executions"]),
        ("variant_count", m.get("variant_count"), counts["selected_points"]),
        ("n_trials", m.get("n_trials"), n),
    )
    for label, value, expected in claims:
        if value is None:
            continue
        try:
            claimed = int(value)
        except (TypeError, ValueError):
            raise ValueError("producer n_trials is not an integer") from None
        if claimed != expected:
            raise ValueError(
                f"producer {label} mismatch: claimed={claimed}, recomputed={expected}"
            )
    return n


def _run_all_checks(
    candidate: CandidateForValidation,
    returns: list[float],
) -> list[dict[str, Any]]:
    checks = []
    checks.append(_check_independent_evaluation(candidate))
    checks.append(_check_costs(candidate, returns))
    checks.append(_check_splits(returns))
    checks.append(_check_significance(returns, n_trials=_n_trials(candidate)))
    checks.append(_check_robustness(returns))
    checks.append(_check_overfit(candidate, returns))
    checks.append(_check_return_concentration(returns))
    checks.append(_check_forward_readiness(candidate))
    checks.append(_check_data_quality(candidate, returns))
    return checks


def _check_independent_evaluation(candidate: CandidateForValidation) -> dict[str, Any]:
    epoch = candidate.metrics.get("validation_epoch")
    problems: list[str] = []
    if not isinstance(epoch, dict) or epoch.get("schema") != "ValidationEpoch.v1":
        problems.append("validation_epoch_missing")
        epoch = {}
    selection_fp = str(epoch.get("selection_data_fingerprint") or "")
    evaluation_fp = str(epoch.get("evaluation_data_fingerprint") or "")
    if not selection_fp or not evaluation_fp:
        problems.append("validation_epoch_fingerprint_missing")
    elif selection_fp == evaluation_fp:
        problems.append("evaluation_reuses_selection_data")
    if evaluation_fp and evaluation_fp != str(candidate.metrics.get("data_fingerprint") or ""):
        problems.append("evaluation_fingerprint_not_bound_to_candidate")
    selection_hash = str(epoch.get("selection_evidence_hash") or "")
    evaluation_hash = str(epoch.get("evaluation_evidence_hash") or "")
    selection_evidence = epoch.get("selection_evidence")
    actual_hash = validation_evidence_hash(candidate.trades, candidate.equity_curve)
    if not selection_hash or not evaluation_hash:
        problems.append("validation_epoch_evidence_hash_missing")
    elif evaluation_hash != actual_hash:
        problems.append("evaluation_evidence_hash_mismatch")
    elif selection_hash == evaluation_hash:
        problems.append("evaluation_reuses_selection_evidence")
    if not isinstance(selection_evidence, list) or not selection_evidence:
        problems.append("selection_evidence_missing")
    elif trade_evidence_hash(selection_evidence) != selection_hash:
        problems.append("selection_evidence_hash_mismatch")
    if epoch.get("evidence_stage") != "untouched_evaluation":
        problems.append("evidence_stage_not_untouched")
    try:
        frozen = dt.datetime.fromisoformat(str(epoch["hypothesis_frozen_at"]).replace("Z", "+00:00"))
        started = dt.datetime.fromisoformat(str(epoch["evaluation_started_at"]).replace("Z", "+00:00"))
        if started <= frozen:
            problems.append("evaluation_not_after_freeze")
        selection_bounds = _evidence_time_bounds(selection_evidence, [])
        evaluation_bounds = _evidence_time_bounds(candidate.trades, candidate.equity_curve)
        if selection_bounds is None or evaluation_bounds is None:
            problems.append("validation_epoch_evidence_time_missing")
        else:
            if selection_bounds[1] > frozen:
                problems.append("selection_evidence_after_freeze")
            if evaluation_bounds[0] != started:
                problems.append("evaluation_start_not_bound_to_evidence")
            if evaluation_bounds[0] <= frozen:
                problems.append("evaluation_evidence_not_after_freeze")
    except (KeyError, TypeError, ValueError):
        problems.append("validation_epoch_time_invalid")
    return {
        "check_name": "independent_evaluation",
        "passed": not problems,
        "details": {"errors": sorted(set(problems)), "epoch": epoch},
        "message": "untouched evaluation epoch verified" if not problems else "; ".join(sorted(set(problems))),
    }


def _evidence_time_bounds(
    trades: list[dict[str, Any]], equity_curve: list[dict[str, Any]]
) -> tuple[dt.datetime, dt.datetime] | None:
    values: list[dt.datetime] = []
    source = trades if trades else equity_curve
    keys = ("entry_ts", "exit_ts") if trades else ("ts",)
    for row in source:
        for key in keys:
            raw = row.get(key)
            if raw is None:
                continue
            if isinstance(raw, (int, float)):
                number = float(raw)
                if number > 10_000_000_000:
                    number /= 1000.0
                values.append(dt.datetime.fromtimestamp(number, tz=dt.timezone.utc))
            else:
                parsed = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                values.append(parsed.astimezone(dt.timezone.utc))
    return (min(values), max(values)) if values else None


def _check_costs(
    candidate: CandidateForValidation, returns: list[float],
) -> dict[str, Any]:
    arr = _np.asarray(returns, dtype=float)
    cost_pct = (candidate.fees_bps + candidate.slippage_bps) / 100.0
    basis = str(candidate.metrics.get("returns_basis"))
    if basis == "net_pct":
        net, gross = arr, arr + cost_pct
    else:
        gross, net = arr, arr - cost_pct
    gross_mean, net_mean = float(_np.mean(gross)), float(_np.mean(net))
    passed = net_mean > 0
    return {
        "check_name": "costs",
        "passed": passed,
        "details": {
            "gross_mean": gross_mean,
            "net_mean": net_mean,
            "cost_drag": cost_pct,
            "returns_basis": basis,
        },
        "message": (
            f"Gross {gross_mean:.4f}%, net {net_mean:.4f}%, "
            f"drag {cost_pct:.6f}% (input={basis})"
        ),
    }


def _check_splits(returns: list[float]) -> dict[str, Any]:
    arr = _np.asarray(returns, dtype=float)
    n = len(arr)
    if n < 10:
        return {
            "check_name": "oos_split",
            "passed": False,
            "details": {"n": n},
            "message": f"Too few returns ({n}) for split analysis.",
        }
    splits = walk_forward(
        n, train_size=max(5, n // 2), test_size=max(3, n // 4),
    )
    if not splits:
        return {
            "check_name": "oos_split",
            "passed": False,
            "details": {"n": n},
            "message": "Cannot form test window.",
        }
    _, test_idx = splits[-1]
    test_returns = arr[test_idx]
    test_mean = float(_np.mean(test_returns))
    return {
        "check_name": "oos_split",
        "passed": test_mean > 0,
        "details": {
            "n_windows": len(splits),
            "test_size": len(test_idx),
            "test_mean": test_mean,
        },
        "message": f"OOS test mean: {test_mean:.4f}%",
    }


def _check_significance(returns: list[float], n_trials: int = 1) -> dict[str, Any]:
    arr = _np.asarray(returns, dtype=float)
    boot = bootstrap_ci(arr, n_boot=1000, seed=42)
    perm = permutation_test(arr, n_perm=1000, seed=42)
    p_raw = float(perm["p_value"])
    n = max(1, int(n_trials))
    # Sidak family-wise adjustment for selecting the best of n variants: a deeper sweep
    # needs a stronger raw p to stay significant.
    p_adj = 1.0 - (1.0 - p_raw) ** n if n > 1 else p_raw
    p_adj = min(1.0, max(0.0, p_adj))
    ci_above_zero = boot[1] > 0
    # Stricter than before: require BOTH a positive bootstrap lower bound AND an
    # adjusted-significant permutation p (was an OR with a loose p<0.10).
    passed = bool(ci_above_zero and p_adj < 0.05)
    return {
        "check_name": "significance",
        "passed": passed,
        "details": {
            "bootstrap_ci": [boot[1], boot[2]],
            "permutation_p": p_raw,
            "permutation_p_adjusted": p_adj,
            "n_trials": n,
            "point_estimate": boot[0],
        },
        "message": (
            f"Bootstrap CI [{boot[1]:.4f}, {boot[2]:.4f}], "
            f"permutation p={p_raw:.4f} (adj {p_adj:.4f} over {n} trials); "
            "pass needs CI>0 AND adj-p<0.05"
        ),
    }


def _check_robustness(returns: list[float]) -> dict[str, Any]:
    arr = _np.asarray(returns, dtype=float)
    result = subperiod_stability(arr, n_periods=min(4, max(2, len(arr) // 5)))
    passed = result["positive_periods"] >= result["n_periods"] * 0.5
    return {
        "check_name": "robustness",
        "passed": passed,
        "details": {
            "period_means": result["period_means"],
            "positive_periods": result["positive_periods"],
            "n_periods": result["n_periods"],
        },
        "message": (
            f"{result['positive_periods']}/{result['n_periods']} "
            f"periods positive"
        ),
    }


def _check_overfit(
    candidate: CandidateForValidation, returns: list[float],
) -> dict[str, Any]:
    arr = _np.asarray(returns, dtype=float)
    n = len(arr)
    if n < 6:
        return {
            "check_name": "overfit_psr",
            "passed": False,
            "details": {"n": n},
            "message": f"Too few returns ({n}) for PSR.",
        }
    variance = float(_np.var(arr))
    if variance == 0.0:
        return {
            "check_name": "overfit_psr",
            "passed": False,
            "details": {"n": n, "variance": 0.0},
            "message": "Zero variance: Sharpe undefined for flat series.",
        }
    try:
        psr = probabilistic_sharpe_ratio(arr, benchmark_sr=0.0)
        mtrl = minimum_track_record_length(
            arr, benchmark_sr=0.0, confidence=0.95,
        )
    except ValueError:
        return {
            "check_name": "overfit_psr",
            "passed": False,
            "details": {"n": n},
            "message": "PSR computation failed (pathological moments).",
        }
    passed = bool(psr["psr"] >= 0.95 and mtrl["sufficient"])
    details = {
        "psr": psr["psr"],
        "sharpe": psr["sharpe"],
        "min_n": mtrl["min_n"],
        "sufficient": mtrl["sufficient"],
        "search_bias_metrics_mode": "shadow_only",
    }
    search_evidence_valid = True
    if isinstance(candidate.metrics.get("search_trial_evidence"), dict):
        coverage = candidate.metrics.get("pbo_dsr_family_coverage")
        if not isinstance(coverage, dict) or not coverage.get("complete"):
            details["family_coverage_error"] = "missing_or_incomplete_family_coverage"
            search_evidence_valid = False
        elif int(coverage.get("included_count") or 0) < 2:
            details["family_coverage_error"] = "fewer_than_two_comparable_family_trials"
            details["family_coverage"] = coverage
            search_evidence_valid = False
        else:
            details["family_coverage"] = coverage
    trial_sharpes = candidate.metrics.get("trial_sharpes")
    if isinstance(trial_sharpes, list) and len(trial_sharpes) >= 2:
        try:
            dsr = deflated_sharpe_ratio(arr, trial_sharpes)
            details["dsr"] = dsr
            details["dsr_shadow_pass"] = bool(dsr["dsr"] >= 0.95)
        except ValueError as exc:
            details["dsr_error"] = str(exc)
            details["dsr_shadow_pass"] = False
            search_evidence_valid = False
    trial_returns = candidate.metrics.get("trial_returns")
    if isinstance(trial_returns, list) and trial_returns:
        try:
            pbo = probability_of_backtest_overfitting(trial_returns)
            details["pbo"] = pbo
            details["pbo_shadow_pass"] = bool(pbo["pbo"] < 0.5)
        except ValueError as exc:
            details["pbo_error"] = str(exc)
            details["pbo_shadow_pass"] = False
            search_evidence_valid = False
    passed = passed and search_evidence_valid
    return {
        "check_name": "overfit_psr",
        "passed": passed,
        "details": details,
        "message": (
            f"PSR={psr['psr']:.4f}, Sharpe={psr['sharpe']:.4f}, "
            f"MinTRL={mtrl['min_n']:.0f}; pass needs PSR>=0.95 and sufficient MinTRL"
        ),
    }


def _check_return_concentration(returns: list[float]) -> dict[str, Any]:
    """Reject edges whose positive mean exists only because of one best trade."""
    arr = _np.asarray(returns, dtype=float)
    n = len(arr)
    if n < 6:
        return {"check_name": "return_concentration", "passed": False,
                "details": {"n": n},
                "message": f"Too few returns ({n}) for concentration analysis."}
    best_idx = int(_np.argmax(arr))
    without_best = _np.delete(arr, best_idx)
    full_sum = float(_np.sum(arr))
    best = float(arr[best_idx])
    loo_mean = float(_np.mean(without_best))
    dominance = best / full_sum if full_sum > 0 and best > 0 else 0.0
    passed = bool(loo_mean > 0 and dominance <= 0.5)
    return {
        "check_name": "return_concentration",
        "passed": passed,
        "details": {"n": n, "best_return": best,
                    "leave_best_out_mean": loo_mean,
                    "best_trade_profit_share": dominance},
        "message": (f"leave-best-out mean={loo_mean:.4f}%, "
                    f"best-trade profit share={dominance:.4f}"),
    }


def _check_forward_readiness(candidate: CandidateForValidation) -> dict[str, Any]:
    ready = candidate.lite_status == "FORWARD_PAPER"
    return {
        "check_name": "forward_readiness",
        "passed": ready,
        "details": {"lite_status": candidate.lite_status},
        "message": (
            "Lite status is FORWARD_PAPER"
            if ready
            else f"Lite status is {candidate.lite_status}"
        ),
    }


def _check_data_quality(
    candidate: CandidateForValidation, returns: list[float],
) -> dict[str, Any]:
    n = len(returns)
    metrics = candidate.metrics
    reported_n = int(metrics.get("n_trades") or 0)
    consistency = n == reported_n or reported_n == 0
    has_nan = any(math.isnan(r) for r in returns)
    has_inf = any(math.isinf(r) for r in returns)
    passed = consistency and not has_nan and not has_inf
    return {
        "check_name": "data_quality",
        "passed": passed,
        "details": {
            "returns_count": n,
            "reported_n_trades": reported_n,
            "has_nan": has_nan,
            "has_inf": has_inf,
        },
        "message": (
            f"{n} returns, consistent={consistency}, "
            f"nan={has_nan}, inf={has_inf}"
        ),
    }


def _build_verdict(
    candidate: CandidateForValidation,
    checks: list[dict[str, Any]],
) -> HardValidationVerdict:
    failed = [c["check_name"] for c in checks if not c["passed"]]
    hard_status = _map_failed_to_status(failed, candidate)
    reason_codes = []
    if "costs" in failed:
        reason_codes.append("edge_thinner_than_costs")
    if "oos_split" in failed:
        reason_codes.append("oos_negative")
    if "significance" in failed:
        reason_codes.append("not_significant")
    if "robustness" in failed:
        reason_codes.append("fragile")
    if "overfit_psr" in failed:
        reason_codes.append("low_psr")
    if "data_quality" in failed:
        reason_codes.append("data_quality_issue")
    if "contract_provenance" in failed:
        reason_codes.append("invalid_contract_or_provenance")
    if "independent_evaluation" in failed:
        reason_codes.append("untouched_evaluation_required")
    if "return_concentration" in failed:
        reason_codes.append("single_trade_dominance")

    return HardValidationVerdict(
        candidate_id=candidate.candidate_id,
        hard_status=hard_status,
        checks=checks,
        failed_checks=failed,
        reason_codes=reason_codes,
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )


def _map_failed_to_status(
    failed: list[str], candidate: CandidateForValidation,
) -> str:
    if "independent_evaluation" in failed:
        return "NEEDS_MORE_DATA"
    if "data_quality" in failed or "contract_provenance" in failed:
        return "FAILED_DATA_QUALITY"
    if "costs" in failed:
        return "FAILED_COSTS"
    if "oos_split" in failed:
        return "FAILED_OOS"
    if "robustness" in failed or "return_concentration" in failed:
        return "FAILED_FRAGILITY"
    if "overfit_psr" in failed:
        return "FAILED_OVERFIT"
    if "significance" in failed and "forward_readiness" in failed:
        return "HARD_REJECT"
    if "significance" in failed:
        return "REGIME_ONLY"
    if "forward_readiness" in failed:
        if candidate.lite_status == "REGIME_SPECIFIC":
            return "REGIME_ONLY"
        return "NEEDS_MORE_DATA"
    return "PAPER_FORWARD_READY"


def _build_report(
    candidate: CandidateForValidation,
    verdict: HardValidationVerdict,
    checks: list[dict[str, Any]],
) -> HardValidationReport:
    passed = sum(1 for c in checks if c["passed"])
    return HardValidationReport(
        candidate_id=candidate.candidate_id,
        source_run_id=candidate.source_run_id,
        symbol=candidate.symbol,
        timeframe=candidate.timeframe,
        strategy_id=candidate.strategy_id,
        verdict=verdict.to_dict(),
        checks_summary={
            "total": len(checks),
            "passed": passed,
            "failed": len(checks) - passed,
        },
        created_at=verdict.created_at,
    )


def _write_artifacts(
    private_root: Path,
    candidate: CandidateForValidation,
    verdict: HardValidationVerdict,
    report: HardValidationReport,
) -> None:
    reports_dir = private_root / REPORTS_DIR
    verdicts_dir = private_root / VERDICTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    verdicts_dir.mkdir(parents=True, exist_ok=True)

    stem = _artifact_stem(candidate.candidate_id)
    report_path = reports_dir / f"{stem}.json"
    write_json(report_path, report.to_dict())

    md_path = reports_dir / f"{stem}.md"
    md_path.write_text(report.to_markdown(), encoding="utf-8")

    verdict_path = verdicts_dir / f"{stem}.json"
    write_json(verdict_path, verdict.to_dict())


def _write_minimal_artifacts(
    private_root: Path,
    candidate: CandidateForValidation,
    result: dict[str, Any],
) -> None:
    """Write verdict + report for cases where full validation couldn't run.

    The downstream pipeline builds feedback from reports. If insufficient-data
    cases only write a verdict, the farm never receives the "collect more data"
    feedback loop and the product path silently stops after validation.
    """
    reports_dir = private_root / REPORTS_DIR
    verdicts_dir = private_root / VERDICTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    verdicts_dir.mkdir(parents=True, exist_ok=True)
    created_at = dt.datetime.now(dt.timezone.utc).isoformat()
    verdict_data = {
        "candidate_id": candidate.candidate_id,
        "hard_status": result.get("hard_status", "NEEDS_MORE_DATA"),
        "checks": [],
        "failed_checks": [],
        "reason_codes": ["insufficient_validation_data"],
        "message": result.get("message", ""),
        "created_at": created_at,
    }
    report = HardValidationReport(
        candidate_id=candidate.candidate_id,
        source_run_id=candidate.source_run_id,
        symbol=candidate.symbol,
        timeframe=candidate.timeframe,
        strategy_id=candidate.strategy_id,
        verdict=verdict_data,
        checks_summary={"total": 0, "passed": 0, "failed": 0},
        created_at=created_at,
    )
    stem = _artifact_stem(candidate.candidate_id)
    verdict_path = verdicts_dir / f"{stem}.json"
    write_json(verdict_path, verdict_data)
    report_path = reports_dir / f"{stem}.json"
    write_json(report_path, report.to_dict())
    md_path = reports_dir / f"{stem}.md"
    md_path.write_text(report.to_markdown(), encoding="utf-8")


def _artifact_stem(candidate_id: str) -> str:
    """Keep untrusted candidate identifiers out of filesystem path semantics."""
    digest = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:24]
    return f"candidate_{digest}"


def _read_json(path: Path) -> Any:
    import json
    return json.loads(path.read_text(encoding="utf-8"))
