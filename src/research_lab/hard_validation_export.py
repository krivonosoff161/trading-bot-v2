# -*- coding: utf-8 -*-
"""Export eligible candidates from the registry into hard validation requests.

Reads the candidate registry, filters by lite_status (FORWARD_PAPER,
REGIME_SPECIFIC), deduplicates, rebuilds trades from experiment output
artifacts, and writes HardValidationRequest JSON files to the private root.

No network. No LLM. No live trading.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.research_lab.candidate_registry import load_entries
from src.research_lab.candle_library import load_canonical_candles
from src.research_lab.data_fingerprint import params_hash
from src.research_lab.data_inventory import normalize_timeframe, timeframe_from_filename
from src.research_lab.experiment import (
    annotate_signals_with_regime,
    filter_signals,
    generate_signals,
    simulate_trades,
)
from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.hard_validation_contract import (
    CandidateForValidation,
    CONTRACT_VERSION,
    trade_evidence_hash,
    write_json,
)
from src.research_lab.honest_backtest_bridge import _artifact_stem
from src.research_lab.search_trial_evidence import validate_search_trial_evidence
from src.research_lab.time_aware_validation import (
    PANEL_STATUS_SCHEMA,
    build_dependence_evidence,
    classify_legacy_search_bias_evidence,
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
    source: str = "auto",
    uc_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Find eligible candidates and write validation request files.

    Returns a summary dict with counts.
    """
    if source not in {"auto", "farm_tasks", "legacy_registry"}:
        raise ValueError("source must be one of: auto, farm_tasks, legacy_registry")
    source_used = "legacy_registry"
    entries: list[dict[str, Any]] = []
    if source in {"auto", "farm_tasks"}:
        entries = _load_farm_unique_entries(private_root, uc_keys=uc_keys)
        if entries:
            source_used = "farm_tasks"
        elif source == "farm_tasks":
            source_used = "farm_tasks"
    if not entries and source in {"auto", "legacy_registry"}:
        registry_file = private_root / "candidate-registry" / "candidates.jsonl"
        entries = load_entries(registry_file)
        source_used = "legacy_registry"
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
        "source": source_used,
        "total_registry": len(entries) if source_used == "legacy_registry" else 0,
        "total_unique_candidates": len(entries) if source_used == "farm_tasks" else 0,
        "eligible_found": len(eligible),
        "exported": 0,
        "exported_ids": [],
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
        req_path = out_dir / f"{_artifact_stem(candidate.candidate_id)}.json"
        write_json(req_path, candidate.to_dict())
        summary["exported"] += 1
        summary["exported_ids"].append(candidate.candidate_id)

    return summary


def validation_id_for_unique_candidate(row: dict[str, Any]) -> str:
    """Stable hard-validation id for one unique candidate row.

    Raw candidate IDs are not globally unique in the farm: the same result ID can
    appear under different timeframes or data fingerprints. The validation layer
    writes files keyed by candidate_id, so it needs a fingerprint-level ID.
    """
    uc_key = str(row.get("uc_key") or "")
    if not uc_key:
        uc_key = "::".join(
            str(row.get(k) or "")
            for k in ("symbol", "timeframe", "family", "params_hash", "data_fingerprint")
        )
    digest = hashlib.sha256(uc_key.encode("utf-8")).hexdigest()
    return f"fv_{digest}"


def _load_farm_unique_entries(private_root: Path, *, uc_keys: list[str] | None = None) -> list[dict[str, Any]]:
    db = tasks_db_path(Path(private_root))
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        where = "WHERE validation_status IN ('FORWARD_PAPER', 'REGIME_SPECIFIC')"
        params: list[Any] = []
        if uc_keys:
            marks = ",".join("?" for _ in uc_keys)
            where += f" AND uc_key IN ({marks})"
            params.extend(uc_keys)
        rows = conn.execute(
            f"""SELECT * FROM unique_candidates {where}
                ORDER BY updated_at DESC LIMIT 5000""",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [_entry_from_unique_candidate(dict(r)) for r in rows]


def _entry_from_unique_candidate(row: dict[str, Any]) -> dict[str, Any]:
    run_dir_label = str(row.get("run_dir_label") or "")
    run_id = Path(run_dir_label.replace("\\", "/")).name if run_dir_label else ""
    validation_id = validation_id_for_unique_candidate(row)
    try:
        params = json.loads(row.get("params_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        params = {}
    metrics_summary = {
        "n_trades": int(row.get("n_trades") or 0),
        "avg_net_pct": float(row.get("avg_net_pct") or 0.0),
        "source_candidate_id": str(row.get("candidate_id") or ""),
        "uc_key": str(row.get("uc_key") or ""),
        "params_hash": str(row.get("params_hash") or ""),
        "data_fingerprint": str(row.get("data_fingerprint") or ""),
    }
    return {
        "candidate_id": validation_id,
        "source_candidate_id": str(row.get("candidate_id") or ""),
        "uc_key": str(row.get("uc_key") or ""),
        "experiment_id": run_id,
        "artifact_label": run_dir_label,
        "symbol": str(row.get("symbol") or ""),
        "timeframe": str(row.get("timeframe") or ""),
        "strategy_id": str(row.get("family") or ""),
        "params_hash": str(row.get("params_hash") or ""),
        "data_fingerprint": str(row.get("data_fingerprint") or ""),
        "params": params if isinstance(params, dict) else {},
        "metrics_summary": metrics_summary,
        "decision": str(row.get("decision") or ""),
        "validation_status": str(row.get("validation_status") or ""),
        "validation_reasons": [],
        "risk_flags": [],
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


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
            str(e.get("timeframe") or ""),
            str(e.get("params_hash") or ""),
            str(e.get("data_fingerprint") or ""),
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
    evidence = metrics.get("search_trial_evidence")
    if not isinstance(evidence, dict):
        return None
    try:
        validate_search_trial_evidence(evidence, require_complete=True)
    except (TypeError, ValueError):
        return None

    trades = metrics.pop("_trades", [])
    filters = dict(entry.get("filters") or metrics.pop("_filters", {}) or {})
    fee_value = entry["fees_bps"] if "fees_bps" in entry else metrics.pop("_fees_bps", 7.0)
    slippage_value = (
        entry["slippage_bps"] if "slippage_bps" in entry else metrics.pop("_slippage_bps", 3.0)
    )
    fees_bps = float(7.0 if fee_value is None else fee_value)
    slippage_bps = float(3.0 if slippage_value is None else slippage_value)
    metrics["returns_basis"] = "net_pct"
    metrics["costs_applied"] = True
    metrics["data_fingerprint"] = str(
        metrics.get("data_fingerprint") or entry.get("data_fingerprint") or ""
    )
    metrics["params_hash"] = str(
        metrics.get("params_hash") or entry.get("params_hash") or ""
    )
    metrics["validation_evidence_profile"] = "time_aware_v2"
    metrics.setdefault(
        "validation_observation_status",
        {
            "schema": "ValidationObservationStatus.v2",
            "status": "unavailable",
            "reason_codes": [
                "explicit_period_and_feature_horizon_evidence_not_exported"
            ],
        },
    )
    if trades:
        metrics.setdefault(
            "dependence_evidence",
            build_dependence_evidence(
                method="iid_bootstrap_kill_test",
                seed=42,
                block_length=None,
                effective_n=len(trades),
            ),
        )
    else:
        metrics.setdefault(
            "dependence_evidence_status",
            {
                "schema": "DependenceEvidenceStatus.v2",
                "status": "unavailable",
                "reason_codes": ["no_return_observations"],
            },
        )
    metrics.setdefault("validation_epoch", {
        "schema": "ValidationEpoch.v1",
        "evidence_stage": "selection_only",
        "selection_data_fingerprint": metrics["data_fingerprint"],
        "selection_evidence_hash": trade_evidence_hash(trades),
        "selection_evidence": trades,
        "evaluation_data_fingerprint": "",
        "evaluation_evidence_hash": "",
        "hypothesis_frozen_at": str(entry.get("updated_at") or ""),
        "evaluation_started_at": "",
    })
    equity_curve = _build_equity_curve(trades)
    data_window = _build_data_window(trades)

    return CandidateForValidation(
        candidate_id=str(entry.get("candidate_id") or ""),
        source_run_id=str(entry.get("experiment_id") or ""),
        symbol=str(entry.get("symbol") or ""),
        normalized_symbol=str(entry.get("symbol", "").replace("-", "_")),
        timeframe=_recover_timeframe(metrics, entry),
        strategy_id=str(entry.get("strategy_id") or ""),
        params=dict(entry.get("params") or metrics.pop("_params", {}) or {}),
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


def _recover_timeframe(metrics: dict[str, Any], entry: dict[str, Any]) -> str:
    """Resolve the strategy timeframe, recovering it instead of defaulting to 'unknown'.

    Ordered chain: explicit run/registry timeframe -> the run's data-file label
    (e.g. 'DOGE_USDT_SWAP_430d_1Dutc.json' -> '1d') -> params timeframe. Only when
    nothing is recoverable does it fall back to 'unknown'.
    """
    for source in (
        metrics.get("data_file_timeframe"),
        entry.get("timeframe"),
        metrics.get("_timeframe"),
        entry.get("params", {}).get("timeframe"),
    ):
        tf = normalize_timeframe(source)
        if tf:
            return tf
    tf = timeframe_from_filename(str(metrics.get("data_file_label") or ""))
    return tf or "unknown"


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
            "_fees_bps": float(
                7.0 if data.get("fees_bps", entry.get("fees_bps")) is None
                else data.get("fees_bps", entry.get("fees_bps"))
            ),
            "_slippage_bps": float(
                3.0 if data.get("slippage_bps", entry.get("slippage_bps")) is None
                else data.get("slippage_bps", entry.get("slippage_bps"))
            ),
            "_timeframe": str(data.get("timeframe") or entry.get("timeframe") or ""),
            "search_trial_evidence_id": str(data.get("search_trial_evidence_id") or ""),
            "multiple_testing_family_hash": str(
                data.get("multiple_testing_family_hash") or ""
            ),
            "runtime": dict(data.get("runtime") or {}),
        }
        evidence_file = run_dir / "search_trial_evidence.json"
        if not evidence_file.exists():
            return None
        try:
            trial_evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
            counts = validate_search_trial_evidence(
                trial_evidence,
                require_complete=True,
            )
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return None
        if (
            context["search_trial_evidence_id"]
            != str(trial_evidence.get("search_trial_evidence_id") or "")
            or context["multiple_testing_family_hash"]
            != str(trial_evidence.get("multiple_testing_family_hash") or "")
        ):
            return None
        context["search_trial_evidence"] = trial_evidence
        context["n_variants_evaluated"] = counts["effective_n_trials"]
        results = data.get("results") or []
        candidate_id = str(entry.get("candidate_id") or "")
        source_candidate_id = str(entry.get("source_candidate_id") or "")
        wanted_params_hash = str(entry.get("params_hash") or "")
        wanted_symbol = str(entry.get("symbol") or "").replace("-", "_").replace("/", "_").upper()
        wanted_family = str(entry.get("strategy_id") or "")
        for r in results:
            row_id = str(r.get("run_id") or r.get("candidate_id") or "")
            row_symbol = str(r.get("symbol") or "").replace("-", "_").replace("/", "_").upper()
            row_family = str(r.get("family") or "")
            row_params_hash = params_hash(r.get("params") or {})
            exact_id = row_id == candidate_id or (source_candidate_id and row_id == source_candidate_id)
            exact_hash = wanted_params_hash and row_params_hash == wanted_params_hash
            exact_scope = (
                (not wanted_symbol or row_symbol == wanted_symbol)
                and (not wanted_family or row_family == wanted_family)
            )
            if exact_scope and (exact_id or exact_hash):
                out = dict(r.get("metrics") or {})
                out.update(context)
                trial_panel = _comparable_trial_panel(
                    results,
                    trial_evidence,
                    symbol=row_symbol,
                    family=row_family,
                )
                out["pbo_dsr_family_coverage"] = trial_panel["coverage"]
                legacy = classify_legacy_search_bias_evidence(
                    trial_panel["trial_returns"]
                )
                out["search_trial_panel"] = {
                    **legacy,
                    "schema": PANEL_STATUS_SCHEMA,
                    "search_family_id": str(
                        trial_evidence.get("search_family_id") or ""
                    ),
                    "coverage": trial_panel["coverage"],
                }
                out["_params"] = _params_from_result(r)
                trades = list(r.get("_trades") or r.get("trades") or [])
                if not trades and int(out.get("n_trades") or 0) > 0:
                    trades = _rebuild_trades_from_result(private_root, r, out, context)
                out["_trades"] = trades
                out["source_candidate_id"] = row_id
                out["uc_key"] = str(entry.get("uc_key") or "")
                if entry.get("data_fingerprint"):
                    out["data_fingerprint"] = str(entry.get("data_fingerprint") or "")
                return out
        if results and not entry.get("uc_key"):
            first = dict(results[0].get("metrics") or {})
            first.update(context)
            first["_params"] = _params_from_result(results[0])
            trades = list(results[0].get("_trades") or results[0].get("trades") or [])
            if not trades and int(first.get("n_trades") or 0) > 0:
                trades = _rebuild_trades_from_result(private_root, results[0], first, context)
            first["_trades"] = trades
            return first

    return dict(entry.get("metrics_summary") or {})


def _comparable_trial_panel(
    results: list[dict[str, Any]],
    evidence: dict[str, Any],
    *,
    symbol: str,
    family: str,
) -> dict[str, Any]:
    by_run_id = {
        str(row.get("run_id") or row.get("candidate_id") or ""): row
        for row in results
    }
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    trial_returns: list[list[float]] = []
    for trial in evidence.get("trials") or []:
        trial_symbol = (
            str(trial.get("symbol") or "").replace("-", "_").replace("/", "_").upper()
        )
        execution_id = str(trial.get("execution_id") or "")
        run_id = str(trial.get("run_id") or "")
        if trial_symbol != symbol:
            excluded.append(
                {
                    "execution_id": execution_id,
                    "run_id": run_id,
                    "reason": "different_symbol_scope",
                }
            )
            continue
        if str(trial.get("family") or "") != family:
            excluded.append(
                {
                    "execution_id": execution_id,
                    "run_id": run_id,
                    "reason": "different_family_scope",
                }
            )
            continue
        disposition = str(trial.get("terminal_disposition") or "")
        if disposition != "evaluated":
            excluded.append(
                {
                    "execution_id": execution_id,
                    "run_id": run_id,
                    "reason": f"terminal_{disposition or 'unknown'}",
                }
            )
            continue
        row = by_run_id.get(run_id)
        if row is None:
            excluded.append(
                {"execution_id": execution_id, "run_id": run_id, "reason": "result_missing"}
            )
            continue
        values = [
            float(trade["net_pct"])
            for trade in (row.get("trades") or [])
            if trade.get("net_pct") is not None
        ]
        if len(values) < 3:
            excluded.append(
                {
                    "execution_id": execution_id,
                    "run_id": run_id,
                    "reason": "fewer_than_3_trades",
                }
            )
            continue
        included.append(
            {
                "execution_id": execution_id,
                "run_id": run_id,
                "trade_count": len(values),
            }
        )
        trial_returns.append(values)
    selected = len(included) + len(excluded)
    expected_selected = int(
        (evidence.get("search_space") or {}).get("selected_executions") or 0
    )
    return {
        "trial_returns": trial_returns,
        "coverage": {
            "schema": "PboDsrFamilyCoverage.v1",
            "search_family_id": str(evidence.get("search_family_id") or ""),
            "selected_executions": selected,
            "included_count": len(included),
            "excluded_count": len(excluded),
            "included": included,
            "excluded": excluded,
            "complete": selected > 0 and selected == expected_selected,
        },
    }


def _params_from_result(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row.get("params") or {})


def _rebuild_trades_from_result(
    private_root: Path,
    row: dict[str, Any],
    metrics: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Recover trades only when the exact experiment snapshot can be re-selected.

    Aggregate-only legacy artifacts did not bind a point-in-time candle snapshot.
    Replaying them against the current local series would create different evidence
    under the old result identity, so those artifacts now fail closed.
    """
    tf = normalize_timeframe(metrics.get("data_file_timeframe") or context.get("_timeframe"))
    symbol = str(row.get("symbol") or context.get("_symbol") or "")
    expected_snapshot_id = str(metrics.get("data_snapshot_id") or "")
    if not tf or not symbol or not expected_snapshot_id:
        return []
    selected = load_canonical_candles(
        private_root, symbol, tf, purpose="experiment", coverage_policy="gap_free",
    )
    if (
        not selected.rows
        or selected.manifest.snapshot_id != expected_snapshot_id
        or selected.manifest.provenance_status != "complete"
    ):
        return []
    try:
        candles = selected.rows
        family = str(row.get("family") or "")
        params = dict(row.get("params") or {})
        signals = generate_signals(candles, family, params)
        signals = annotate_signals_with_regime(candles, signals, {})
        signals = filter_signals(signals, dict(context.get("_filters") or {}))
        return simulate_trades(
            candles,
            signals,
            params,
            fees_bps=float(7.0 if context.get("_fees_bps") is None else context["_fees_bps"]),
            slippage_bps=float(
                3.0 if context.get("_slippage_bps") is None else context["_slippage_bps"]
            ),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _build_equity_curve(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not trades:
        return []
    equity = 10000.0
    curve = [{"ts": 0, "value": equity}]
    for t in trades:
        raw = t.get("net_pct") if t.get("net_pct") is not None else t.get("pnl_pct")
        pnl_pct = float(raw if raw is not None else 0.0)
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
