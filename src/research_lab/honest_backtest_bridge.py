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
import os
import sys
from pathlib import Path
from typing import Any

from src.research_lab.hard_validation_contract import (
    CandidateForValidation,
    HardValidationReport,
    HardValidationVerdict,
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
        apply_costs,
        bootstrap_ci,
        minimum_track_record_length,
        permutation_test,
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
        request_files = [requests_dir / f"{cid}.json" for cid in wanted]
        request_files = [p for p in request_files if p.exists()]
    else:
        request_files = sorted(requests_dir.glob("*.json"))[:limit]
    results = []
    for rf in request_files:
        try:
            data = _read_json(rf)
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
    for t in candidate.trades:
        pnl = float(t.get("net_pct") or t.get("pnl_pct") or 0.0)
        returns.append(pnl)
    if not returns and candidate.equity_curve:
        values = [float(p.get("value") or 0) for p in candidate.equity_curve]
        for i in range(1, len(values)):
            if values[i - 1] != 0:
                returns.append((values[i] / values[i - 1] - 1) * 100)
    return returns


def _n_trials(candidate: CandidateForValidation) -> int:
    """Number of parameter variants the candidate was selected from (>=1).

    This is the multiple-testing trial count used to deflate significance: picking the
    best of N variants inflates apparent significance, so a deeper sweep is penalized.
    """
    m = candidate.metrics or {}
    runtime = m.get("runtime") if isinstance(m.get("runtime"), dict) else {}
    for value in (runtime.get("n_variants_evaluated"), m.get("n_variants_evaluated"),
                  m.get("variant_count"), m.get("n_trials")):
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n >= 1:
            return n
    return 1


def _run_all_checks(
    candidate: CandidateForValidation,
    returns: list[float],
) -> list[dict[str, Any]]:
    checks = []
    checks.append(_check_costs(candidate, returns))
    checks.append(_check_splits(returns))
    checks.append(_check_significance(returns, n_trials=_n_trials(candidate)))
    checks.append(_check_robustness(returns))
    checks.append(_check_overfit(candidate, returns))
    checks.append(_check_forward_readiness(candidate))
    checks.append(_check_data_quality(candidate, returns))
    return checks


def _check_costs(
    candidate: CandidateForValidation, returns: list[float],
) -> dict[str, Any]:
    fee = candidate.fees_bps / 10000.0
    slippage = candidate.slippage_bps / 10000.0
    arr = _np.asarray(returns, dtype=float)
    result = apply_costs(arr, fee=fee, slippage=slippage, turnover=1.0)
    passed = bool(result["survives_costs"])
    return {
        "check_name": "costs",
        "passed": passed,
        "details": {
            "gross_mean": result["gross_mean"],
            "net_mean": result["net_mean"],
            "cost_drag": result["cost_drag"],
        },
        "message": (
            f"Gross {result['gross_mean']:.4f}%, "
            f"net {result['net_mean']:.4f}%, "
            f"drag {result['cost_drag']:.6f}"
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
    passed = bool(psr["psr"] > 0.5)
    return {
        "check_name": "overfit_psr",
        "passed": passed,
        "details": {
            "psr": psr["psr"],
            "sharpe": psr["sharpe"],
            "min_n": mtrl["min_n"],
            "sufficient": mtrl["sufficient"],
        },
        "message": (
            f"PSR={psr['psr']:.4f}, Sharpe={psr['sharpe']:.4f}, "
            f"MinTRL={mtrl['min_n']:.0f}"
        ),
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
    if "data_quality" in failed:
        return "FAILED_DATA_QUALITY"
    if "costs" in failed:
        return "FAILED_COSTS"
    if "oos_split" in failed:
        return "FAILED_OOS"
    if "robustness" in failed:
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

    report_path = reports_dir / f"{candidate.candidate_id}.json"
    write_json(report_path, report.to_dict())

    md_path = reports_dir / f"{candidate.candidate_id}.md"
    md_path.write_text(report.to_markdown(), encoding="utf-8")

    verdict_path = verdicts_dir / f"{candidate.candidate_id}.json"
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
    verdict_path = verdicts_dir / f"{candidate.candidate_id}.json"
    write_json(verdict_path, verdict_data)
    report_path = reports_dir / f"{candidate.candidate_id}.json"
    write_json(report_path, report.to_dict())
    md_path = reports_dir / f"{candidate.candidate_id}.md"
    md_path.write_text(report.to_markdown(), encoding="utf-8")


def _read_json(path: Path) -> Any:
    import json
    return json.loads(path.read_text(encoding="utf-8"))
