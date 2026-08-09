"""Observe the main-readable paper runtime queue on public candles.

This module is the paper-only runtime layer after the farm/PFR bridge:

paper_signals/PFR -> main_paper_bridge -> main_paper_consumer ->
main_paper_runtime_adapter -> this observer.

It never imports the legacy live main engine, exchange clients, Telegram, env
loaders, or order execution. It only rebuilds PaperActionSignal objects from the
self-contained queue and advances their deterministic paper lifecycle.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from src.research_lab.main_paper_runtime_adapter import SCHEMA as QUEUE_ITEM_SCHEMA
from src.research_lab.paper_generation_contract import (
    PaperGenerationContext,
    PaperGenerationMismatch,
    canonical_digest,
    stage_envelope,
    verify_stage_envelope,
)
from src.research_lab.paper_signals import lane
from src.research_lab.paper_signals.contract import PaperActionSignal, validate_signal
from src.research_lab.paper_signals.outcome_evidence import (
    EVIDENCE_OPERATIONAL_INCIDENT,
    STATUS_DATA_GAP,
    STATUS_GENUINE_NO_MARKET_DATA,
    STATUS_PROVIDER_ERROR,
    classify_market_data_rows,
)
from src.research_lab.providers.okx_public import MarketDataError, OkxPublicMarketDataProvider

SUMMARY_SCHEMA = "main_paper_runtime_observation.v1"
TF_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


class CandleProvider(Protocol):
    def fetch_ohlcv(self, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        """Return canonical OHLCV rows with ts/open/high/low/close/vol."""


PersistObservation = Callable[
    [dict[str, Any], dict[str, Any]],
    dict[str, Any],
]


def _queue_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_runtime_queue.json"


def _queue_jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_runtime_queue.jsonl"


def _jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_runtime_observation.jsonl"


def _snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_runtime_observation.json"


def _load_queue_rows(
    private_root: Path,
) -> tuple[list[dict[str, Any]], Path | None, dict[str, Any]]:
    snapshot = _queue_snapshot_path(private_root)
    if snapshot.exists():
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        return list(items or []), snapshot, data if isinstance(data, dict) else {}

    jsonl = _queue_jsonl_path(private_root)
    if not jsonl.exists():
        return [], None, {}
    rows: list[dict[str, Any]] = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows, jsonl, {}


def _signal_from_queue(row: dict[str, Any]) -> PaperActionSignal:
    if row.get("schema") != QUEUE_ITEM_SCHEMA:
        raise ValueError("unexpected queue item schema")
    if row.get("paper_only") is not True or row.get("execution_allowed") is not False:
        raise ValueError("queue item is not paper-only")
    if row.get("runtime_action") != "watch_paper":
        raise ValueError("queue item action is not watch_paper")

    sig = PaperActionSignal(
        signal_id=str(row.get("source_signal_id") or row.get("runtime_id") or ""),
        source=str(row.get("source") or "farm"),
        symbol=str(row.get("okx_inst_id") or row.get("pair") or "").replace("-", "_"),
        okx_inst_id=str(row.get("okx_inst_id") or row.get("pair") or ""),
        timeframe=str(row.get("timeframe") or ""),
        side=str(row.get("side") or "").lower(),
        setup_family=str(row.get("setup_family") or ""),
        entry_zone=[float(v) for v in list(row.get("entry_zone") or [])[:2]],
        stop_loss=float(row.get("stop") or 0.0),
        invalidation_rule=f"paper queue invalidates beyond stop {row.get('stop')}",
        take_profit_plan=list(row.get("take_profit_plan") or []),
        max_hold_bars=int(row.get("max_hold_bars") or 0),
        max_hold_minutes=int(row.get("max_hold_min") or 0),
        reason_now=f"accepted farm/PFR paper queue item {row.get('runtime_id')}",
        risk_notes="main-paper observer only; no execution path",
        validator_context={
            "source_consumer_status": row.get("source_consumer_status"),
            "geometry_profile_id": row.get("farm_geometry_profile_id") or "",
            "geometry_profile_reason": row.get("farm_geometry_profile_reason") or "",
            "geometry_entry_scale": row.get("farm_geometry_entry_scale"),
            "geometry_stop_scale": row.get("farm_geometry_stop_scale"),
            "geometry_tp_scale": row.get("farm_geometry_tp_scale"),
            "geometry_hold_scale": row.get("farm_geometry_hold_scale"),
            "search_family_id": row.get("search_family_id") or "",
            "search_trial_id": row.get("search_trial_id") or "",
            "effective_n_trials": int(row.get("effective_n_trials") or 0),
        },
        outcome_memory_context={"priority_reasons": row.get("priority_reasons") or []},
        status="armed",
        created_at=float(row.get("created_at") or 0.0),
        expires_at=float(row.get("expires_at") or 0.0),
        ref_price=float(row.get("entry") or 0.0),
        risk_pct=float(row.get("risk_pct") or 0.0),
        boundary_ts=int(row.get("boundary_ts") or 0),
        data_fingerprint=str(row.get("data_fingerprint") or ""),
        dedup_key=str(row.get("dedup_key") or ""),
        mode=str(row.get("source_mode") or "live"),
        exit_mode=str(row.get("exit_mode") or "partial_be"),
    )
    ok, problems = validate_signal(sig)
    if not ok:
        raise ValueError(";".join(problems))
    return sig


def _fetch_window(sig: PaperActionSignal, provider: CandleProvider, now_ms: int) -> list[dict[str, Any]]:
    tf_ms = TF_MS.get(sig.timeframe)
    if tf_ms is None:
        raise ValueError(f"unsupported timeframe {sig.timeframe!r}")
    if now_ms <= sig.boundary_ts:
        return []
    # Include a small pre-boundary prefix so review charts have context while
    # lifecycle remains strict: lane.observe only consumes ts > boundary_ts.
    start_ts = max(0, sig.boundary_ts - 2 * tf_ms)
    max_end = sig.boundary_ts + (sig.max_hold_bars + lane.ARM_WINDOW_BARS + 2) * tf_ms
    end_ts = min(now_ms, max_end)
    return provider.fetch_ohlcv(sig.okx_inst_id, sig.timeframe, start_ts, end_ts)


def observe_main_paper_runtime(
    private_root: Path,
    *,
    limit: int = 50,
    apply: bool = False,
    provider: CandleProvider | None = None,
    now_ms: int | None = None,
    expected_run_id: str = "",
    expected_input_digest: str = "",
    persist_observation: PersistObservation | None = None,
) -> dict[str, Any]:
    """Observe queued paper items and optionally persist a status artifact."""
    rows, source_path, source_payload = _load_queue_rows(private_root)
    generation_context: PaperGenerationContext | None = None
    if source_payload.get("paper_stage_schema") or expected_run_id or expected_input_digest:
        generation_context = verify_stage_envelope(
            source_payload,
            stage="queue",
            expected_run_id=expected_run_id,
            expected_input_digest=expected_input_digest,
        )
    if limit >= 0 and generation_context is None:
        rows = rows[:limit]
    provider = provider or OkxPublicMarketDataProvider()
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)

    items: list[dict[str, Any]] = []
    counts = {
        "rows_read": len(rows),
        "observed": 0,
        "reviewed": 0,
        "pending": 0,
        "terminal_unreviewed": 0,
        "invalid": 0,
        "provider_error": 0,
        "no_elapsed_bars": 0,
        "data_gap": 0,
        "genuine_no_market_data": 0,
    }

    for row in rows:
        try:
            sig = _signal_from_queue(row)
            if now_ms <= sig.boundary_ts:
                counts["no_elapsed_bars"] += 1
                items.append(_item_result(row, sig, "pending_clock", [], now_ms, provider))
                continue
            candles = _fetch_window(sig, provider, now_ms)
            observation = classify_market_data_rows(
                candles,
                timeframe_ms=TF_MS[sig.timeframe],
            )
            if not observation.usable:
                counts[observation.status] += 1
                items.append(
                    _operational_result(row, observation.status, observation.reason)
                )
                continue
            candles = list(observation.rows)
            manifest = _observation_manifest(sig, candles, now_ms, provider)
            if generation_context is not None:
                if persist_observation is None:
                    raise PaperGenerationMismatch(
                        "v2 calculation requires a persisted immutable observation"
                    )
                persisted = persist_observation(row, manifest)
                if (
                    persisted.get("schema") != "CandleSnapshotManifest.v2"
                    or persisted.get("manifest_digest") != manifest["manifest_digest"]
                    or persisted.get("rows_digest") != manifest["rows_digest"]
                    or persisted.get("request_digest") != manifest["request_digest"]
                    or persisted.get("provider_identity") != manifest["provider_identity"]
                    or persisted.get("acquisition_id") != manifest["acquisition_id"]
                    or not persisted.get("observation_id")
                ):
                    raise PaperGenerationMismatch(
                        "persisted observation does not match provider acquisition"
                    )
                candles = list(persisted.get("rows") or [])
                manifest = persisted
            observed = lane.observe(sig, candles)
            counts["observed"] += 1
            if observed.status in {"closed_paper", "expired"}:
                observed = lane.review(observed)
                counts["reviewed"] += 1
            elif observed.status in {"armed", "opened_paper"}:
                counts["pending"] += 1
            else:
                counts["terminal_unreviewed"] += 1
            item = _item_result(row, observed, "observed", candles, now_ms, provider)
            if generation_context is not None:
                item["observation_manifest"] = manifest
            items.append(item)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            counts["invalid"] += 1
            items.append(_error_result(row, "invalid", str(exc)))
        except (MarketDataError, OSError, TimeoutError) as exc:
            counts["provider_error"] += 1
            items.append(
                _operational_result(
                    row,
                    STATUS_PROVIDER_ERROR,
                    f"public_provider_{type(exc).__name__}",
                )
            )

    if generation_context is not None:
        for item in items:
            if (
                item.get("paper_generation_run_id") != generation_context.run_id
                or item.get("source_producer_generation_id")
                != generation_context.producer_generation_id
            ):
                raise PaperGenerationMismatch("queue item generation does not match envelope")
            item["queue_output_digest"] = generation_context.input_digest
    generation = stage_envelope("observer", generation_context, items)
    summary = {
        "schema": SUMMARY_SCHEMA,
        "source_schema": QUEUE_ITEM_SCHEMA,
        "source_path": str(source_path) if source_path else "",
        "source_exists": source_path is not None,
        "limit": limit,
        "scheduling_limit_deferred_to_durable_cursor": generation_context is not None,
        "now_ms": now_ms,
        "paper_only": True,
        "execution_allowed": False,
        **counts,
        "items": items,
        "snapshot_path": str(_snapshot_path(private_root)),
        "jsonl_path": str(_jsonl_path(private_root)),
        **generation,
    }
    if apply:
        _write_summary(private_root, summary)
    return summary


def _error_result(row: dict[str, Any], status: str, error: str) -> dict[str, Any]:
    return {
        "runtime_id": row.get("runtime_id", ""),
        "source_signal_id": row.get("source_signal_id", ""),
        "source": row.get("source", ""),
        "okx_inst_id": row.get("okx_inst_id") or row.get("pair") or "",
        "timeframe": row.get("timeframe", ""),
        "setup_family": row.get("setup_family", ""),
        "side": row.get("side", ""),
        "adaptive_policy_id": row.get("adaptive_policy_id", ""),
        "adaptive_execution_profile": row.get("adaptive_execution_profile", ""),
        "adaptive_entry_profile": row.get("adaptive_entry_profile", ""),
        "adaptive_exit_profile": row.get("adaptive_exit_profile", ""),
        "adaptive_stop_profile": row.get("adaptive_stop_profile", ""),
        "adaptive_max_hold_profile": row.get("adaptive_max_hold_profile", ""),
        "adaptive_regime_hint": row.get("adaptive_regime_hint", ""),
        "adaptive_policy_confidence": row.get("adaptive_policy_confidence", 0.0),
        "adaptive_policy_reasons": row.get("adaptive_policy_reasons", []),
        "farm_geometry_profile_id": row.get("farm_geometry_profile_id", ""),
        "farm_geometry_profile_reason": row.get("farm_geometry_profile_reason", ""),
        "farm_geometry_entry_scale": row.get("farm_geometry_entry_scale"),
        "farm_geometry_stop_scale": row.get("farm_geometry_stop_scale"),
        "farm_geometry_tp_scale": row.get("farm_geometry_tp_scale"),
        "farm_geometry_hold_scale": row.get("farm_geometry_hold_scale"),
        "status": status,
        "signal_status": "",
        "outcome": {},
        "review": {},
        "error": str(error)[:240],
        "new_bars": 0,
        "paper_only": True,
        "execution_allowed": False,
        **_generation_refs(row),
    }


def _operational_result(
    row: dict[str, Any],
    status: str,
    reason: str,
) -> dict[str, Any]:
    if status not in {
        STATUS_PROVIDER_ERROR,
        STATUS_DATA_GAP,
        STATUS_GENUINE_NO_MARKET_DATA,
    }:
        raise ValueError("unsupported operational market-data status")
    return {
        **_error_result(row, status, ""),
        "signal_status": str(row.get("status") or ""),
        "market_data_status": status,
        "outcome_evidence_kind": EVIDENCE_OPERATIONAL_INCIDENT,
        "reason": str(reason)[:120],
        "error": "",
    }


def _generation_refs(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_generation_run_id": str(row.get("paper_generation_run_id") or ""),
        "source_producer_generation_id": str(row.get("source_producer_generation_id") or ""),
        "source_member_payload_digest": str(row.get("source_member_payload_digest") or ""),
        "source_validation_generation_id": str(
            row.get("source_validation_generation_id") or ""
        ),
        "queue_output_digest": str(row.get("consumer_output_digest") or ""),
    }


def _observation_manifest(
    sig: PaperActionSignal,
    candles: list[dict[str, Any]],
    now_ms: int,
    provider: CandleProvider,
) -> dict[str, Any]:
    tf_ms = TF_MS.get(sig.timeframe, 0)
    start_ts = max(0, sig.boundary_ts - 2 * tf_ms) if tf_ms else 0
    max_end = sig.boundary_ts + (sig.max_hold_bars + lane.ARM_WINDOW_BARS + 2) * tf_ms
    request = {
        "symbol": sig.okx_inst_id,
        "timeframe": sig.timeframe,
        "start_ts": start_ts,
        "end_ts": min(now_ms, max_end),
    }
    provider_identity = str(getattr(provider, "name", "") or type(provider).__name__)
    rows_digest = canonical_digest(candles)
    acquisition_id = canonical_digest(
        {
            "provider_identity": provider_identity,
            "request": request,
            "rows_digest": rows_digest,
            "observed_at_ms": now_ms,
        }
    )
    return {
        "schema": "CandleSnapshotManifest.v2",
        "request": request,
        "request_digest": canonical_digest(request),
        "rows": candles,
        "rows_digest": rows_digest,
        "observed_at_ms": now_ms,
        "available_at_ms": now_ms,
        "provider_identity": provider_identity,
        "acquisition_id": acquisition_id,
        "manifest_digest": canonical_digest(
            {
                "schema": "CandleSnapshotManifest.v2",
                "request_digest": canonical_digest(request),
                "rows_digest": rows_digest,
                "observed_at_ms": now_ms,
                "available_at_ms": now_ms,
                "provider_identity": provider_identity,
                "acquisition_id": acquisition_id,
            }
        ),
    }


def _item_result(
    row: dict[str, Any],
    sig: PaperActionSignal,
    status: str,
    candles: list[dict[str, Any]],
    now_ms: int,
    provider: CandleProvider,
) -> dict[str, Any]:
    result = {
        "runtime_id": row.get("runtime_id", ""),
        "source_signal_id": sig.signal_id,
        "source": sig.source,
        "okx_inst_id": sig.okx_inst_id,
        "timeframe": sig.timeframe,
        "setup_family": sig.setup_family,
        "side": sig.side,
        "adaptive_policy_id": row.get("adaptive_policy_id", ""),
        "adaptive_execution_profile": row.get("adaptive_execution_profile", ""),
        "adaptive_entry_profile": row.get("adaptive_entry_profile", ""),
        "adaptive_exit_profile": row.get("adaptive_exit_profile", ""),
        "adaptive_stop_profile": row.get("adaptive_stop_profile", ""),
        "adaptive_max_hold_profile": row.get("adaptive_max_hold_profile", ""),
        "adaptive_regime_hint": row.get("adaptive_regime_hint", ""),
        "adaptive_policy_confidence": row.get("adaptive_policy_confidence", 0.0),
        "adaptive_policy_reasons": row.get("adaptive_policy_reasons", []),
        "farm_geometry_profile_id": row.get("farm_geometry_profile_id", ""),
        "farm_geometry_profile_reason": row.get("farm_geometry_profile_reason", ""),
        "farm_geometry_entry_scale": row.get("farm_geometry_entry_scale"),
        "farm_geometry_stop_scale": row.get("farm_geometry_stop_scale"),
        "farm_geometry_tp_scale": row.get("farm_geometry_tp_scale"),
        "farm_geometry_hold_scale": row.get("farm_geometry_hold_scale"),
        "status": status,
        "signal_status": sig.status,
        "outcome": sig.outcome,
        "review": sig.review,
        "new_bars": len([c for c in candles if int(c.get("ts") or 0) > sig.boundary_ts]),
        "paper_only": True,
        "execution_allowed": False,
        **_generation_refs(row),
    }
    if row.get("paper_generation_run_id"):
        result["observation_manifest"] = _observation_manifest(sig, candles, now_ms, provider)
    return result


def _write_summary(private_root: Path, summary: dict[str, Any]) -> None:
    out_jsonl = _jsonl_path(private_root)
    out_snapshot = _snapshot_path(private_root)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for item in summary["items"]:
            fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    out_snapshot.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
