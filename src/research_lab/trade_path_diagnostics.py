# -*- coding: utf-8 -*-
"""Derived trade-path characterization of rejected candidates (read-only, no compute).

A rejected/failed setup is not proven-bad. This module re-reads the EXISTING run
artifacts (experiments/<run_dir>/metrics.json, which already carry per-trade
mfe/mae/capture/outcome + an entry_timing aggregate) and the deduped
farm_tasks.unique_candidates rows, and derives, WITHOUT re-running any sweep/sim:

  * a deterministic reject sub-reason (the 8-way taxonomy: insufficient_data /
    tactical_candidate / wrong_exit / wrong_timeframe / wrong_costs /
    missing_oi_micro / validator_too_strict / confirmed_bad);
  * trade-path facts (avg/ best/ worst net, MFE/MAE, capture ratio, late-entry,
    tp-before-sl from the recorded outcome, timeout share).

It writes nothing back to the source DBs and never re-computes a trade; it is a pure
read of what already happened. No money path, no orders, no live.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from src.research_lab.data_fingerprint import params_hash
from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.strategy_registry import REGISTRY, get_strategy

# Round-trip taker cost floor (fees 7bps + slippage 3bps = 0.1%) — the same assumption
# the validator's cost check uses; a tactical window must clear it to be worth recycling.
COST_PCT = 0.1
CAPTURE_LATE = 0.3  # avg_capture_ratio below this => entry gave back the move
THIN_MAX = 2  # n_trades in 1..THIN_MAX = a tactical (too-thin-for-validator) window
POWER_FLOOR = (
    10  # validator _check_splits fails below n=10 => below this it had no power
)


def oi_micro_families() -> set[str]:
    """Families that declare oi/microstructure data needs (reuse the registry, do not duplicate)."""
    out: set[str] = set()
    for fam in REGISTRY:
        req = set(get_strategy(fam).required_data)
        if req & {"oi", "microstructure"}:
            out.add(fam)
    return out


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_rejected_uc(private_root: Path) -> list[dict[str, Any]]:
    import sqlite3

    path = tasks_db_path(private_root)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        # Rejected = lite REJECT OR any hard failure (FAILED_*/HARD_REJECT/REGIME_ONLY),
        # so hard-failed FORWARD_PAPER candidates (e.g. FAILED_COSTS) are characterized too.
        rows = conn.execute(
            "SELECT uc_key, symbol, timeframe, family, params_hash, n_trades, avg_net_pct, "
            "regime_bucket, hard_status, validation_status, decision, run_dir_label, updated_at "
            "FROM unique_candidates "
            "WHERE decision='REJECT' OR validation_status='REJECT' "
            "   OR hard_status LIKE 'FAILED%' OR hard_status IN ('HARD_REJECT','REGIME_ONLY')"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


REJECT_CACHE_SCHEMA = "RejectCharacterizationCache.v1"
REJECT_CLASSIFIER_VERSION = "trade_path_reject_taxonomy.v1"


class IncrementalRefreshDeferred(RuntimeError):
    """A bounded historical refresh yielded after safely checkpointing its cache."""

    def __init__(self, stats: dict[str, Any]) -> None:
        super().__init__("incremental reject refresh yielded before completion")
        self.stats = dict(stats)


def _source_digest(row: dict[str, Any]) -> str:
    payload = {
        key: row.get(key)
        for key in (
            "uc_key",
            "symbol",
            "timeframe",
            "family",
            "params_hash",
            "n_trades",
            "avg_net_pct",
            "regime_bucket",
            "hard_status",
            "validation_status",
            "decision",
            "run_dir_label",
            "updated_at",
        )
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _artifact_identity(private_root: Path, run_dir_label: str) -> dict[str, Any]:
    if not run_dir_label:
        return {"state": "unavailable", "size": 0, "mtime_ns": 0}
    path = Path(private_root) / run_dir_label / "metrics.json"
    try:
        stat = path.stat()
    except OSError:
        return {"state": "unavailable", "size": 0, "mtime_ns": 0}
    return {
        "state": "available",
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _classifier_context_digest() -> str:
    payload = {
        "classifier_version": REJECT_CLASSIFIER_VERSION,
        "cost_pct": COST_PCT,
        "capture_late": CAPTURE_LATE,
        "thin_max": THIN_MAX,
        "power_floor": POWER_FLOOR,
        "oi_micro_families": sorted(oi_micro_families()),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_reject_cache_with_status(
    path: Path,
    *,
    classifier_context_digest: str,
) -> tuple[dict[str, dict[str, Any]], str, bool]:
    """Load a derived accelerator without ever treating it as memory authority.

    ``complete`` describes only whether the last refresh saw every current
    source.  Individually identity-bound entries in an interrupted cache are
    still safe accelerators; a partial cache is never published as the setup
    outcome-memory snapshot consumed by later cycles.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "missing", False
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}, "unreadable", False
    if not isinstance(payload, dict):
        return {}, "invalid_payload", False
    if payload.get("schema") != REJECT_CACHE_SCHEMA:
        return {}, "schema_mismatch", False
    if payload.get("classifier_version") != REJECT_CLASSIFIER_VERSION:
        return {}, "classifier_version_mismatch", False
    if payload.get("classifier_context_digest") != classifier_context_digest:
        return {}, "classifier_context_mismatch", False
    items = payload.get("items")
    if not isinstance(items, dict):
        return {}, "items_invalid", False
    return ({
        str(key): value
        for key, value in items.items()
        if isinstance(key, str) and isinstance(value, dict)
    }, "ready_complete" if bool(payload.get("complete", True)) else "ready_partial", bool(payload.get("complete", True)))


def _load_reject_cache(
    path: Path,
    *,
    classifier_context_digest: str,
) -> dict[str, dict[str, Any]]:
    """Compatibility wrapper for callers interested only in safe cache items."""

    items, _state, _complete = _load_reject_cache_with_status(
        path,
        classifier_context_digest=classifier_context_digest,
    )
    return items


def _snapshot_bootstrap(
    path: Path,
) -> tuple[int, str, dict[str, dict[str, Any]]]:
    try:
        encoded = path.read_bytes()
        stat = path.stat()
        payload = json.loads(encoded.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return 0, "", {}
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return 0, "", {}
    snapshot_version = str(payload.get("reject_classifier_version") or "")
    # The pre-cache snapshot format had no version field.  It is accepted only
    # as the one-time bootstrap for this unchanged v1 taxonomy.  Once this
    # repair writes a versioned snapshot, any future taxonomy bump fails closed
    # and recomputes the affected history instead of reusing stale labels.
    if snapshot_version not in {"", REJECT_CLASSIFIER_VERSION}:
        return int(stat.st_mtime_ns), hashlib.sha256(encoded).hexdigest(), {}
    by_key = {
        str(row.get("uc_key") or ""): row
        for row in records
        if isinstance(row, dict) and str(row.get("uc_key") or "")
    }
    return int(stat.st_mtime_ns), hashlib.sha256(encoded).hexdigest(), by_key


def _epoch_to_ns(value: Any) -> int:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return 0
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return int(timestamp * 1_000_000_000)


def _bootstrap_characterization(record: dict[str, Any]) -> dict[str, Any] | None:
    reason = str(record.get("rejection_reason") or "")
    if not reason:
        return None
    return {
        "uc_key": str(record.get("uc_key") or ""),
        "symbol": str(record.get("symbol") or ""),
        "timeframe": str(record.get("timeframe") or ""),
        "family": str(record.get("family") or ""),
        "regime_bucket": str(record.get("regime_bucket") or ""),
        "hard_status": str(record.get("hard_status") or ""),
        "reject_subreason": reason,
        "recyclable": reason in _RECYCLABLE,
        "trades_available": bool(
            record.get("avg_mfe_pct")
            or record.get("avg_mae_pct")
            or record.get("avg_capture_ratio")
        ),
        "n_trades": int(record.get("n_trades") or 0),
        "avg_net_pct": float(record.get("baseline_net") or 0.0),
        "best_net_pct": 0.0,
        "worst_net_pct": 0.0,
        "avg_mfe_pct": float(record.get("avg_mfe_pct") or 0.0),
        "avg_mae_pct": float(record.get("avg_mae_pct") or 0.0),
        "avg_capture_ratio": float(record.get("avg_capture_ratio") or 0.0),
        "late_entry_rate": 0.0,
        "n_tp": 0,
        "n_sl": 0,
        "n_timeout": 0,
        "tp_before_sl_share": 0.0,
    }


def _atomic_write_reject_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _index_run_results(
    private_root: Path, run_dir_label: str
) -> dict[str, dict[str, Any]]:
    """params_hash -> result dict for one run's metrics.json (empty if missing)."""
    if not run_dir_label:
        return {}
    data = _read_json(Path(private_root) / run_dir_label / "metrics.json")
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for res in results:
        if isinstance(res, dict):
            out[params_hash(res.get("params") or {})] = res
    return out


def _trade_path_facts(result: dict[str, Any]) -> dict[str, Any]:
    """Aggregate trade-path facts from a result's stored per-trade records + entry_timing."""
    metrics = (
        raw_metrics if isinstance(raw_metrics := result.get("metrics"), dict) else {}
    )
    trades = result.get("trades") or result.get("_trades") or []
    nets = [float(t.get("net_pct") or 0.0) for t in trades if isinstance(t, dict)]
    outcomes = [str(t.get("outcome") or "") for t in trades if isinstance(t, dict)]
    et = raw_et if isinstance(raw_et := metrics.get("entry_timing"), dict) else {}
    n_tp = sum(1 for o in outcomes if o == "tp")
    n_sl = sum(1 for o in outcomes if o in ("stop", "sl"))
    n_timeout = sum(1 for o in outcomes if o in ("time_exit", "timeout"))
    return {
        "n_trades": int(metrics.get("n_trades") or len(trades)),
        "avg_net_pct": round(sum(nets) / len(nets), 4)
        if nets
        else float(metrics.get("avg_net_pct") or 0.0),
        "best_net_pct": round(max(nets), 4) if nets else 0.0,
        "worst_net_pct": round(min(nets), 4) if nets else 0.0,
        "avg_mfe_pct": float(et.get("avg_mfe_pct") or 0.0),
        "avg_mae_pct": float(et.get("avg_mae_pct") or 0.0),
        "avg_capture_ratio": float(et.get("avg_capture_ratio") or 0.0),
        "late_entry_rate": float(et.get("late_entry_rate") or 0.0),
        "n_tp": n_tp,
        "n_sl": n_sl,
        "n_timeout": n_timeout,
        "tp_before_sl_share": round(n_tp / len(outcomes), 4) if outcomes else 0.0,
        "trades_available": bool(trades),
    }


def classify_subreason(
    facts: dict[str, Any], hard_status: str, family: str, oi_micro: set[str]
) -> str:
    """Deterministic 8-way reject taxonomy from trade-path facts + hard_status + family."""
    n = int(facts.get("n_trades") or 0)
    avg = float(facts.get("avg_net_pct") or 0.0)
    best = float(facts.get("best_net_pct") or 0.0)
    mfe = float(facts.get("avg_mfe_pct") or 0.0)
    capture = float(facts.get("avg_capture_ratio") or 0.0)
    late = float(facts.get("late_entry_rate") or 0.0)
    if family in oi_micro:
        return "missing_oi_micro"
    if n == 0:
        return "insufficient_data"
    # Edge existed but the exit gave the move back (positive MFE, poor capture).
    if (
        facts.get("trades_available")
        and mfe > avg + COST_PCT
        and capture < CAPTURE_LATE
    ):
        return "wrong_exit"
    # Late entry on a real move (capture poor / majority late).
    if (
        facts.get("trades_available")
        and (late >= 0.5 or (0.0 < capture < CAPTURE_LATE))
        and mfe > COST_PCT
    ):
        return "wrong_timeframe"
    if hard_status == "FAILED_COSTS" and (mfe > COST_PCT or best > COST_PCT):
        return "wrong_costs"
    # 1-2 trades the validator could never score, but the trades it had were net-positive.
    if n <= THIN_MAX and (avg > 0 or best > COST_PCT):
        return "tactical_candidate"
    # 3..9 trades: the validator's split/PSR checks auto-fail below n=10 even if net-positive.
    if 3 <= n < POWER_FLOOR and avg > 0:
        return "validator_too_strict"
    # Validator had power (n>=10) and it still failed net-negative.
    if n >= POWER_FLOOR and avg <= 0:
        return "confirmed_bad"
    return "uncharacterized"


_RECYCLABLE = {
    "tactical_candidate",
    "wrong_exit",
    "wrong_timeframe",
    "wrong_costs",
    "missing_oi_micro",
    "validator_too_strict",
}


def _characterize_reject_rows(
    private_root: Path,
    uc: list[dict[str, Any]],
    *,
    progress: Callable[[str, int, int], None] | None = None,
    check_active: Callable[[], None] | None = None,
    on_group_complete: Callable[[list[tuple[dict[str, Any], dict[str, Any]]]], None]
    | None = None,
    checkpoint_rows: int | None = None,
    should_yield: Callable[[int, int], bool] | None = None,
) -> list[dict[str, Any]]:
    """Characterize rows while safely yielding between bounded checkpoints.

    A yield is deliberately different from a stop/fence failure: callers first
    persist the identity-bound accelerator rows that were already completed,
    then resume the remaining source rows in a later historical slice.  The
    full setup-memory snapshot is still published only after this function
    returns every requested row.
    """
    oi_micro = oi_micro_families()
    total = len(uc)
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for original_index, row in enumerate(uc):
        grouped.setdefault(str(row.get("run_dir_label") or ""), []).append(
            (original_index, row)
        )
    total_run_labels = len(grouped)
    if progress is not None:
        progress("rejected_candidates_loaded", total, total)
    rows: list[dict[str, Any] | None] = [None] * total
    indexed_runs = 0
    processed = 0
    for label, members in grouped.items():
        if check_active is not None:
            check_active()
        if should_yield is not None and should_yield(processed, total):
            raise IncrementalRefreshDeferred(
                {"completed": processed, "total": total, "reason": "slice_budget"}
            )
        run_results = _index_run_results(private_root, label)
        indexed_runs += 1
        if check_active is not None:
            check_active()
        if progress is not None:
            progress("run_artifacts_indexed", indexed_runs, total_run_labels)
        completed_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for original_index, r in members:
            if check_active is not None:
                check_active()
            result = run_results.get(str(r.get("params_hash") or ""))
            facts = (
                _trade_path_facts(result)
                if result
                else {
                    "n_trades": int(r.get("n_trades") or 0),
                    "avg_net_pct": float(r.get("avg_net_pct") or 0.0),
                    "best_net_pct": 0.0,
                    "worst_net_pct": 0.0,
                    "avg_mfe_pct": 0.0,
                    "avg_mae_pct": 0.0,
                    "avg_capture_ratio": 0.0,
                    "late_entry_rate": 0.0,
                    "n_tp": 0,
                    "n_sl": 0,
                    "n_timeout": 0,
                    "tp_before_sl_share": 0.0,
                    "trades_available": False,
                }
            )
            hard = str(r.get("hard_status") or "")
            sub = classify_subreason(
                facts,
                hard,
                str(r.get("family") or ""),
                oi_micro,
            )
            characterization = {
                "uc_key": str(r.get("uc_key") or ""),
                "symbol": str(r.get("symbol") or ""),
                "timeframe": str(r.get("timeframe") or ""),
                "family": str(r.get("family") or ""),
                "regime_bucket": str(r.get("regime_bucket") or ""),
                "hard_status": hard,
                "reject_subreason": sub,
                "recyclable": sub in _RECYCLABLE,
                "trades_available": facts["trades_available"],
                **{
                    key: facts[key]
                    for key in (
                        "n_trades",
                        "avg_net_pct",
                        "best_net_pct",
                        "worst_net_pct",
                        "avg_mfe_pct",
                        "avg_mae_pct",
                        "avg_capture_ratio",
                        "late_entry_rate",
                        "n_tp",
                        "n_sl",
                        "n_timeout",
                        "tp_before_sl_share",
                    )
                },
            }
            rows[original_index] = characterization
            completed_pairs.append((r, characterization))
            processed += 1
            if progress is not None and (
                processed == total or processed % 25 == 0
            ):
                progress("rejects_characterized", processed, total)
            if checkpoint_rows and len(completed_pairs) >= checkpoint_rows:
                if on_group_complete is not None:
                    on_group_complete(completed_pairs)
                completed_pairs = []
            if should_yield is not None and should_yield(processed, total):
                if completed_pairs and on_group_complete is not None:
                    on_group_complete(completed_pairs)
                raise IncrementalRefreshDeferred(
                    {"completed": processed, "total": total, "reason": "slice_budget"}
                )
        # Releasing each run before loading the next prevents an unbounded
        # multi-gigabyte JSON cache and its one large GIL-bound cleanup pause.
        del run_results
        if check_active is not None:
            check_active()
        if progress is not None:
            progress("run_artifacts_released", indexed_runs, total_run_labels)
        if completed_pairs and on_group_complete is not None:
            on_group_complete(completed_pairs)
    complete = [row for row in rows if row is not None]
    if len(complete) != total:
        raise RuntimeError("reject characterization lost source rows")
    return complete


def characterize_rejects(
    private_root: Path,
    *,
    limit: int | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    check_active: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """One row per rejected unique_candidate with sub-reason + trade-path facts."""
    private_root = Path(private_root)
    if check_active is not None:
        check_active()
    uc = _load_rejected_uc(private_root)
    if limit:
        uc = uc[:limit]
    return _characterize_reject_rows(
        private_root,
        uc,
        progress=progress,
        check_active=check_active,
    )


def characterize_rejects_incremental(
    private_root: Path,
    *,
    cache_path: Path,
    bootstrap_snapshot_path: Path | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    check_active: Callable[[], None] | None = None,
    max_recomputed_rows: int | None = None,
    deadline_monotonic: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Refresh reject facts without rereading immutable history on every farm cycle.

    The cache is a derived accelerator, never authority.  A row is reused only
    when both its complete DB source digest and its run-artifact stat identity
    still match.  A previous complete setup-memory snapshot can bootstrap the
    first cache only for source rows and metrics that predate that snapshot.
    Changed run groups are reread through the ordinary deterministic
    characterization path.  A caller may bound one historical slice by newly
    recomputed rows and/or a monotonic deadline.  A yielded slice writes only
    independently verified accelerator entries and never returns a partial
    memory record list as if it were complete.
    """

    private_root = Path(private_root)
    cache_path = Path(cache_path)
    if check_active is not None:
        check_active()
    source_rows = _load_rejected_uc(private_root)
    total = len(source_rows)
    if progress is not None:
        progress("incremental_sources_loaded", total, total)

    classifier_context_digest = _classifier_context_digest()
    existing, cache_input_state, cache_input_complete = _load_reject_cache_with_status(
        cache_path,
        classifier_context_digest=classifier_context_digest,
    )
    snapshot_mtime_ns = 0
    snapshot_sha256 = ""
    snapshot_rows: dict[str, dict[str, Any]] = {}
    if bootstrap_snapshot_path is not None and len(existing) < total:
        snapshot_mtime_ns, snapshot_sha256, snapshot_rows = _snapshot_bootstrap(
            Path(bootstrap_snapshot_path)
        )

    artifact_identities: dict[str, dict[str, Any]] = {}
    cache_items: dict[str, dict[str, Any]] = {}
    rows_by_key: dict[str, dict[str, Any]] = {}
    misses: list[dict[str, Any]] = []
    cache_hits = 0
    snapshot_hits = 0
    invalidated = 0

    for index, source in enumerate(source_rows, start=1):
        if check_active is not None:
            check_active()
        uc_key = str(source.get("uc_key") or "")
        label = str(source.get("run_dir_label") or "")
        if label not in artifact_identities:
            artifact_identities[label] = _artifact_identity(private_root, label)
        artifact_identity = artifact_identities[label]
        source_digest = _source_digest(source)
        prior = existing.get(uc_key) or {}
        characterization = prior.get("characterization")
        if (
            prior.get("source_digest") == source_digest
            and prior.get("artifact_identity") == artifact_identity
            and isinstance(characterization, dict)
        ):
            rows_by_key[uc_key] = characterization
            cache_items[uc_key] = prior
            cache_hits += 1
        else:
            if prior:
                invalidated += 1
            bootstrapped = None
            snapshot_row = snapshot_rows.get(uc_key)
            if (
                snapshot_row is not None
                and snapshot_mtime_ns > 0
                and _epoch_to_ns(source.get("updated_at")) <= snapshot_mtime_ns
                and int(artifact_identity.get("mtime_ns") or 0)
                <= snapshot_mtime_ns
                and (
                    not label or artifact_identity.get("state") == "available"
                )
            ):
                bootstrapped = _bootstrap_characterization(snapshot_row)
            if bootstrapped is not None:
                rows_by_key[uc_key] = bootstrapped
                cache_items[uc_key] = {
                    "artifact_identity": artifact_identity,
                    "characterization": bootstrapped,
                    "source_digest": source_digest,
                }
                snapshot_hits += 1
            else:
                misses.append(source)
        if progress is not None and (index == total or index % 500 == 0):
            progress("incremental_sources_classified", index, total)

    def cache_payload(*, complete: bool) -> dict[str, Any]:
        return {
            "schema": REJECT_CACHE_SCHEMA,
            "classifier_version": REJECT_CLASSIFIER_VERSION,
            "classifier_context_digest": classifier_context_digest,
            "bootstrap_snapshot_sha256": snapshot_sha256,
            "complete": complete,
            "items": dict(sorted(cache_items.items())),
        }

    def checkpoint_completed_group(
        completed_pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> None:
        """Persist safe accelerator entries after each completed bounded chunk.

        A crash or stop can leave this cache partial, but never leaves a partial
        setup-memory snapshot.  Every entry is independently revalidated by
        source and artifact identity before it can be reused on resume.
        """

        if not completed_pairs:
            return
        for source, row in completed_pairs:
            uc_key = str(row.get("uc_key") or "")
            label = str(source.get("run_dir_label") or "")
            rows_by_key[uc_key] = row
            cache_items[uc_key] = {
                "artifact_identity": artifact_identities[label],
                "characterization": row,
                "source_digest": _source_digest(source),
            }
        if check_active is not None:
            check_active()
        _atomic_write_reject_cache(cache_path, cache_payload(complete=False))
        if progress is not None:
            progress("incremental_cache_checkpointed", len(cache_items), total)
        if check_active is not None:
            check_active()

    def slice_exhausted(completed: int, total_misses: int) -> bool:
        if completed >= total_misses:
            return False
        if max_recomputed_rows is not None and completed >= max_recomputed_rows:
            return True
        return deadline_monotonic is not None and time.monotonic() >= deadline_monotonic

    try:
        recomputed = _characterize_reject_rows(
            private_root,
            misses,
            progress=(
                None
                if progress is None
                else lambda stage, completed, stage_total: progress(
                    f"incremental_miss:{stage}", completed, stage_total
                )
            ),
            check_active=check_active,
            on_group_complete=checkpoint_completed_group,
            checkpoint_rows=min(250, max_recomputed_rows or 250),
            should_yield=slice_exhausted,
        )
    except IncrementalRefreshDeferred as exc:
        # The checkpoint callback writes completed entries incrementally.  Mark
        # any resumed cache incomplete as well, even if the slice yielded before
        # the first chunk, so no consumer can mistake it for a complete refresh.
        _atomic_write_reject_cache(cache_path, cache_payload(complete=False))
        deferred_stats = {
            "schema": REJECT_CACHE_SCHEMA,
            "classifier_version": REJECT_CLASSIFIER_VERSION,
            "sources": total,
            "cache_input_state": cache_input_state,
            "cache_input_complete": cache_input_complete,
            "cache_complete": False,
            "cache_hits": cache_hits,
            "snapshot_bootstrap_hits": snapshot_hits,
            "recomputed": int(exc.stats.get("completed") or 0),
            "invalidated": invalidated,
            "run_artifacts_total": len(artifact_identities),
            "run_artifacts_reread": len(
                {
                    str(row.get("run_dir_label") or "")
                    for row in misses
                    if str(row.get("run_dir_label") or "")
                }
            ),
            "run_artifacts_unavailable": sum(
                identity.get("state") != "available" and bool(label)
                for label, identity in artifact_identities.items()
            ),
            "cache_written": True,
            "deferred": True,
            "deferred_reason": str(exc.stats.get("reason") or "slice_budget"),
        }
        raise IncrementalRefreshDeferred(deferred_stats) from exc

    ordered = [rows_by_key[str(row.get("uc_key") or "")] for row in source_rows]
    changed = cache_items != existing or not cache_input_complete
    if changed:
        if check_active is not None:
            check_active()
        _atomic_write_reject_cache(cache_path, cache_payload(complete=True))
        if check_active is not None:
            check_active()
    if progress is not None:
        progress("incremental_refresh_complete", total, total)
    return ordered, {
        "schema": REJECT_CACHE_SCHEMA,
        "classifier_version": REJECT_CLASSIFIER_VERSION,
        "sources": total,
        "cache_input_state": cache_input_state,
        "cache_input_complete": cache_input_complete,
        "cache_complete": True,
        "cache_hits": cache_hits,
        "snapshot_bootstrap_hits": snapshot_hits,
        "recomputed": len(recomputed),
        "invalidated": invalidated,
        "run_artifacts_total": len(artifact_identities),
        "run_artifacts_reread": len(
            {
                str(row.get("run_dir_label") or "")
                for row in misses
                if str(row.get("run_dir_label") or "")
            }
        ),
        "run_artifacts_unavailable": sum(
            identity.get("state") != "available" and bool(label)
            for label, identity in artifact_identities.items()
        ),
        "cache_written": changed,
    }


def summarize_characterization(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Deliverable tables: counts by sub-reason, family, timeframe + recyclable totals."""

    def _tally(key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    recyclable = [r for r in rows if r["recyclable"]]
    by_sub_tf: dict[str, dict[str, int]] = {}
    for r in rows:
        by_sub_tf.setdefault(r["reject_subreason"], {})
        tf = r["timeframe"]
        by_sub_tf[r["reject_subreason"]][tf] = (
            by_sub_tf[r["reject_subreason"]].get(tf, 0) + 1
        )
    return {
        "total_rejects": len(rows),
        "recyclable_total": len(recyclable),
        "confirmed_bad_total": sum(
            1 for r in rows if r["reject_subreason"] == "confirmed_bad"
        ),
        "by_subreason": _tally("reject_subreason"),
        "by_family": _tally("family"),
        "recyclable_by_family": _tally_subset(recyclable, "family"),
        "by_subreason_timeframe": by_sub_tf,
        "trades_available_count": sum(1 for r in rows if r["trades_available"]),
    }


def _tally_subset(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
