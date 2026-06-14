# -*- coding: utf-8 -*-
"""Export eligible candidates from the registry into hard validation requests.

Reads the candidate registry, filters by lite_status (FORWARD_PAPER,
REGIME_SPECIFIC), deduplicates, rebuilds trades from experiment output
artifacts, and writes HardValidationRequest JSON files to the private root.

No network. No LLM. No live trading.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from src.research_lab.candidate_registry import load_entries
from src.research_lab.hard_validation_contract import (
    CandidateForValidation,
    CONTRACT_VERSION,
    write_json,
)

ELIGIBLE_STATUSES = {"FORWARD_PAPER", "REGIME_SPECIFIC"}
REQUESTS_DIR = "hard_validation/requests"


def export_requests(
    private_root: Path,
    *,
    dry_run: bool = True,
    limit: int = 50,
    status_filter: str | None = None,
    include_regime_specific: bool = False,
    since: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Find eligible candidates and write validation request files.

    Returns a summary dict with counts.
    """
    registry_file = private_root / "candidate-registry" / "candidates.jsonl"
    entries = load_entries(registry_file)
    eligible = _filter_entries(
        entries,
        status_filter=status_filter,
        include_regime_specific=include_regime_specific,
        since=since,
        candidate_id=candidate_id,
    )
    eligible = _deduplicate(eligible)
    eligible = eligible[:limit]

    summary: dict[str, Any] = {
        "total_registry": len(entries),
        "eligible_found": len(eligible),
        "exported": 0,
        "skipped_no_artifact": 0,
        "dry_run": dry_run,
    }

    if dry_run or not eligible:
        return summary

    out_dir = private_root / REQUESTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    for entry in eligible:
        candidate = _build_candidate(entry, private_root)
        if candidate is None:
            summary["skipped_no_artifact"] += 1
            continue
        req_path = out_dir / f"{candidate.candidate_id}.json"
        write_json(req_path, candidate.to_dict())
        summary["exported"] += 1

    return summary


def _filter_entries(
    entries: list[dict[str, Any]],
    *,
    status_filter: str | None = None,
    include_regime_specific: bool = False,
    since: str | None = None,
    candidate_id: str | None = None,
) -> list[dict[str, Any]]:
    result = []
    for e in entries:
        status = str(e.get("validation_status") or "")
        if status_filter:
            if status != status_filter:
                continue
        else:
            if status == "FORWARD_PAPER":
                pass
            elif status == "REGIME_SPECIFIC" and include_regime_specific:
                pass
            else:
                continue
        if candidate_id and e.get("candidate_id") != candidate_id:
            continue
        if since:
            created = str(e.get("created_at") or "")
            if created < since:
                continue
        result.append(e)
    return result


def _deduplicate(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in entries:
        key = (
            str(e.get("candidate_id") or ""),
            str(e.get("symbol") or ""),
            str(e.get("strategy_id") or ""),
        )
        if key not in seen:
            seen[key] = e
    return list(seen.values())


def _build_candidate(
    entry: dict[str, Any],
    private_root: Path,
) -> CandidateForValidation | None:
    artifact_label = str(entry.get("artifact_label") or "")
    metrics = _load_experiment_metrics(private_root, artifact_label, entry)
    if metrics is None:
        return None

    trades = metrics.pop("_trades", [])
    filters = dict(entry.get("filters") or metrics.pop("_filters", {}) or {})
    fee_value = entry["fees_bps"] if "fees_bps" in entry else metrics.pop("_fees_bps", 7.0)
    slippage_value = (
        entry["slippage_bps"] if "slippage_bps" in entry else metrics.pop("_slippage_bps", 3.0)
    )
    fees_bps = float(fee_value or 7.0)
    slippage_bps = float(slippage_value or 3.0)
    equity_curve = _build_equity_curve(trades)
    data_window = _build_data_window(trades)

    return CandidateForValidation(
        candidate_id=str(entry.get("candidate_id") or ""),
        source_run_id=str(entry.get("experiment_id") or ""),
        symbol=str(entry.get("symbol") or ""),
        normalized_symbol=str(entry.get("symbol", "").replace("-", "_")),
        timeframe=str(
            metrics.get("data_file_timeframe")
            or entry.get("timeframe")
            or metrics.get("_timeframe")
            or entry.get("params", {}).get("timeframe", "")
            or "unknown"
        ),
        strategy_id=str(entry.get("strategy_id") or ""),
        params=dict(entry.get("params") or {}),
        filters=filters,
        fees_bps=fees_bps,
        slippage_bps=slippage_bps,
        lite_status=str(entry.get("validation_status") or ""),
        lite_reasons=list(entry.get("validation_reasons") or []),
        risk_flags=list(entry.get("risk_flags") or []),
        metrics=metrics,
        trades=trades,
        equity_curve=equity_curve,
        data_window=data_window,
        created_at=str(
            entry.get("created_at")
            or dt.datetime.now(dt.timezone.utc).isoformat()
        ),
        contract_version=CONTRACT_VERSION,
    )


def _load_experiment_metrics(
    private_root: Path,
    artifact_label: str,
    entry: dict[str, Any],
) -> dict[str, Any] | None:
    if not artifact_label:
        return dict(entry.get("metrics_summary") or {})

    completed_dir = private_root / "experiments" / "completed"
    if not completed_dir.exists():
        return dict(entry.get("metrics_summary") or {})

    candidates = list(completed_dir.glob(f"*{artifact_label}*"))
    if not candidates:
        candidates = list(completed_dir.glob(f"*{entry.get('experiment_id', '')}*"))

    for run_dir in sorted(candidates, reverse=True):
        metrics_file = run_dir / "metrics.json"
        if not metrics_file.exists():
            continue
        try:
            data = json.loads(metrics_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        context = {
            "_filters": dict(data.get("filters") or {}),
            "_fees_bps": float(data.get("fees_bps", entry.get("fees_bps", 7.0)) or 7.0),
            "_slippage_bps": float(data.get("slippage_bps", entry.get("slippage_bps", 3.0)) or 3.0),
            "_timeframe": str(data.get("timeframe") or entry.get("timeframe") or ""),
        }
        results = data.get("results") or []
        candidate_id = str(entry.get("candidate_id") or "")
        for r in results:
            if str(r.get("run_id") or "") == candidate_id:
                out = dict(r.get("metrics") or {})
                out["_trades"] = list(r.get("_trades") or r.get("trades") or [])
                out.update(context)
                return out
        if results:
            first = dict(results[0].get("metrics") or {})
            first["_trades"] = list(
                results[0].get("_trades")
                or results[0].get("trades")
                or []
            )
            first.update(context)
            return first

    return dict(entry.get("metrics_summary") or {})


def _build_equity_curve(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not trades:
        return []
    equity = 10000.0
    curve = [{"ts": 0, "value": equity}]
    for t in trades:
        pnl_pct = float(t.get("net_pct") or t.get("pnl_pct") or 0.0)
        equity *= 1.0 + pnl_pct / 100.0
        ts = int(t.get("exit_ts") or t.get("entry_ts") or 0)
        curve.append({"ts": ts, "value": round(equity, 2)})
    return curve


def _build_data_window(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"start_ts": 0, "end_ts": 0, "n_bars": 0}
    entry_ts = [int(t.get("entry_ts") or 0) for t in trades if t.get("entry_ts")]
    exit_ts = [int(t.get("exit_ts") or 0) for t in trades if t.get("exit_ts")]
    return {
        "start_ts": min(entry_ts) if entry_ts else 0,
        "end_ts": max(exit_ts) if exit_ts else 0,
        "n_bars": len(trades),
    }
