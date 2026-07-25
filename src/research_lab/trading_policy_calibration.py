"""Evidence gate for paper-trading geometry calibration.

This module does not choose prices or mutate signal state. It summarizes only
outcomes produced by the durable lifecycle and labels profile evidence for the
next bounded farm probe. Historical legacy rows remain visible as excluded
background, but cannot vote on policy.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

from src.research_lab.paper_projection_reader import read_projection_view

SCHEMA = "trading_policy_calibration.v1"
TRUSTED_LIFECYCLE_SCHEMA = "PaperSignalLifecycle.v2"
MIN_PROFILE_SAMPLE = 20
MIN_CELL_SAMPLE = 12
REQUIRED_ACCEPTANCE_SYMBOLS = ("KAITO_USDT_SWAP",)


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _wilson(wins: int, total: int, *, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 1.0
    p = wins / total
    denominator = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    spread = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return max(0.0, (centre - spread) / denominator), min(
        1.0, (centre + spread) / denominator
    )


def _group_summary(
    key: str, rows: list[dict[str, Any]], *, min_sample: int
) -> dict[str, Any]:
    raw_net = [_float(row.get("net_pct")) for row in rows]
    net = [value for value in raw_net if value is not None]
    wins = sum(value > 0.0 for value in net)
    losses = sum(value < 0.0 for value in net)
    raw_captures = [_float(row.get("capture")) for row in rows]
    captures = [value for value in raw_captures if value is not None]
    gave_back = sum(
        str(row.get("diagnosis") or "") == "bad_exit_gave_back"
        or str(row.get("outcome_learning_bucket") or "") == "gave_back"
        for row in rows
    )
    low, high = _wilson(wins, len(net))
    avg_net = sum(net) / len(net) if net else 0.0
    avg_capture = sum(captures) / len(captures) if captures else 0.0
    if len(net) < min_sample:
        verdict = "insufficient_evidence"
    elif avg_net < 0.0 and high < 0.50:
        verdict = "demote"
    elif gave_back / len(net) >= 0.30 or (captures and avg_capture < 0.30):
        verdict = "retest_exit_capture"
    elif avg_net > 0.0 and low >= 0.35:
        verdict = "retain_probe"
    else:
        verdict = "observe_mixed"
    return {
        "key": key,
        "terminal_rows": len(net),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(net), 4) if net else 0.0,
        "win_rate_wilson_95": [round(low, 4), round(high, 4)],
        "avg_net_pct": round(avg_net, 6),
        "median_net_pct": round(statistics.median(net), 6) if net else 0.0,
        "avg_capture": round(avg_capture, 6),
        "gave_back_rows": gave_back,
        "verdict": verdict,
        "minimum_sample": min_sample,
    }


def _summaries(
    rows: list[dict[str, Any]], fields: tuple[str, ...], *, min_sample: int
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = "|".join(str(row.get(field) or "unknown") for field in fields)
        grouped.setdefault(key, []).append(row)
    return {
        key: _group_summary(key, group, min_sample=min_sample)
        for key, group in sorted(grouped.items())
    }


def _horizon(timeframe: Any) -> str:
    value = str(timeframe or "").lower()
    if value in {"1m", "3m", "5m", "15m"}:
        return "tactical"
    if value in {"30m", "1h"}:
        return "intraday"
    if value in {"4h", "1d"}:
        return "slow"
    return "unknown"


def _opposing_side_conflicts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cohorts: dict[str, set[str]] = {}
    for row in rows:
        key = "|".join(
            str(row.get(field) or "unknown")
            for field in ("symbol", "timeframe", "boundary_ts")
        )
        cohorts.setdefault(key, set()).add(str(row.get("side") or "unknown").lower())
    conflicts = sorted(
        key for key, sides in cohorts.items() if {"long", "short"}.issubset(sides)
    )
    return {
        "cohorts": len(cohorts),
        "opposing_side_cohorts": len(conflicts),
        "sample_keys": conflicts[:20],
    }


def _account_primary(
    private_root: Path,
    trusted_rows: list[dict[str, Any]],
    *,
    current_generation: bool,
) -> dict[str, Any]:
    if current_generation:
        raw_pnl = [_float(row.get("paper_pnl_usdt")) for row in trusted_rows]
        pnl = [value for value in raw_pnl if value is not None]
        return {
            "terminal_trades": len(pnl),
            "wins": sum(value > 0.0 for value in pnl),
            "losses": sum(value < 0.0 for value in pnl),
            "total_pnl_usdt": round(sum(pnl), 6),
            "avg_pnl_usdt": round(sum(pnl) / len(pnl), 6) if pnl else 0.0,
            "evidence_role": "immutable_generation_primary_theses",
        }
    rows = _read_rows(private_root / "state" / "derived" / "paper_account_events.jsonl")
    closed = [
        row for row in rows if str(row.get("event_type") or "") == "position_closed"
    ]
    raw_pnl = [_float(row.get("pnl_usdt")) for row in closed]
    pnl = [value for value in raw_pnl if value is not None]
    return {
        "terminal_trades": len(pnl),
        "wins": sum(value > 0.0 for value in pnl),
        "losses": sum(value < 0.0 for value in pnl),
        "total_pnl_usdt": round(sum(pnl), 6),
        "avg_pnl_usdt": round(sum(pnl) / len(pnl), 6) if pnl else 0.0,
        "evidence_role": "legacy_display_only_shared_account",
    }


def _acceptance_cases(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for symbol in REQUIRED_ACCEPTANCE_SYMBOLS:
        selected = [
            row
            for row in rows
            if str(row.get("symbol") or "").upper().replace("-", "_") == symbol
        ]
        out[symbol] = {
            "trusted_terminal_rows": len(selected),
            "by_side": {
                side: sum(
                    str(row.get("side") or "").lower() == side for row in selected
                )
                for side in ("long", "short")
            },
            "ready": len(selected) >= MIN_CELL_SAMPLE,
        }
    return out


def build_trading_policy_calibration(
    private_root: Path,
    *,
    evidence_database_path: Path | str | None = None,
) -> dict[str, Any]:
    """Write an aggregate, private calibration view over trusted outcomes."""
    private_root = Path(private_root)
    derived = private_root / "state" / "derived"
    source = derived / "paper_signal_training.jsonl"
    rows = _read_rows(source)
    generation = read_projection_view(
        private_root,
        "trades",
        legacy_snapshot=derived / "main_paper_trades.json",
        evidence_database_path=evidence_database_path,
    )
    run_id = str(generation.get("paper_generation_run_id") or "")
    trusted = [
        row
        for row in rows
        if generation.get("current")
        and row.get("paper_only") is True
        and row.get("execution_allowed") is False
        and str(row.get("lifecycle_schema") or "") == TRUSTED_LIFECYCLE_SCHEMA
        and row.get("immutable_terminal_evidence") is True
        and row.get("paper_generation_run_id") == run_id
        and bool(row.get("terminal_lifecycle_event_id"))
        and bool(row.get("account_generation_id"))
        and _float(row.get("net_pct")) is not None
    ]
    for row in trusted:
        row["_calibration_horizon"] = _horizon(row.get("timeframe"))
    legacy = len(rows) - len(trusted)
    by_profile = _summaries(
        trusted, ("farm_geometry_profile_id",), min_sample=MIN_PROFILE_SAMPLE
    )
    by_profile_horizon = _summaries(
        trusted,
        ("farm_geometry_profile_id", "timeframe"),
        min_sample=MIN_PROFILE_SAMPLE,
    )
    by_horizon = _summaries(
        trusted, ("_calibration_horizon",), min_sample=MIN_PROFILE_SAMPLE
    )
    by_exit_mode = _summaries(trusted, ("exit_mode",), min_sample=MIN_PROFILE_SAMPLE)
    by_cell_profile = _summaries(
        trusted,
        ("symbol", "timeframe", "family", "farm_geometry_profile_id"),
        min_sample=MIN_CELL_SAMPLE,
    )
    verdicts: dict[str, int] = {}
    for item in by_profile.values():
        verdict = str(item["verdict"])
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
    summary = {
        "schema": SCHEMA,
        "trusted_lifecycle_schema": TRUSTED_LIFECYCLE_SCHEMA,
        "source_rows": len(rows),
        "trusted_terminal_rows": len(trusted),
        "legacy_rows_excluded": legacy,
        "calibration_ready": len(trusted) >= MIN_PROFILE_SAMPLE,
        "minimum_profile_sample": MIN_PROFILE_SAMPLE,
        "minimum_cell_sample": MIN_CELL_SAMPLE,
        "by_profile": by_profile,
        "by_profile_horizon": by_profile_horizon,
        "by_horizon": by_horizon,
        "by_exit_mode": by_exit_mode,
        "by_cell_profile": by_cell_profile,
        "opposing_side_conflicts": _opposing_side_conflicts(trusted),
        "shared_account_primary": _account_primary(
            private_root,
            trusted,
            current_generation=bool(generation.get("current")),
        ),
        "acceptance_cases": _acceptance_cases(trusted),
        "profile_verdicts": dict(sorted(verdicts.items())),
        "comparison_kind": "observational_paper_outcomes_not_causal_attribution",
        "paper_only": True,
        "execution_allowed": False,
        "source_path": str(source),
        "snapshot_path": str(derived / "trading_policy_calibration.json"),
        "paper_generation_run_id": run_id,
        "generation_status": str(generation.get("generation_status") or ""),
        "current_generation_compatible": bool(generation.get("current")),
        "display_only": not bool(generation.get("current")),
    }
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "trading_policy_calibration.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def profile_verdict(calibration: dict[str, Any] | None, profile_id: str) -> str:
    by_profile = (calibration or {}).get("by_profile")
    if not isinstance(by_profile, dict):
        return "insufficient_evidence"
    row = by_profile.get(str(profile_id or ""))
    return (
        str(row.get("verdict") or "insufficient_evidence")
        if isinstance(row, dict)
        else "insufficient_evidence"
    )
