"""Pure v2 evidence contracts for time-aware hard validation.

The module deliberately does not implement a new statistical validator.  It owns the
caller-side evidence that the trading repository can prove: timestamped observation
identity, complete-family common-time panels, interval-derived split manifests, and
explicit dependence-method provenance.  Generic PBO/DSR and resampling algorithms stay
owned by ``honest-backtest``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from src.research_lab.search_trial_evidence import validate_search_trial_evidence


OBSERVATION_SET_SCHEMA = "ValidationObservationSet.v2"
PANEL_SCHEMA = "SearchTrialPanel.v2"
PANEL_STATUS_SCHEMA = "SearchTrialPanelStatus.v2"
SPLIT_SCHEMA = "IntervalSplitManifest.v2"
DEPENDENCE_SCHEMA = "DependenceEvidence.v2"


class ValidationEvidenceError(ValueError):
    """Typed fail-closed evidence error with a stable machine reason code."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


def _content_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _identified(prefix: str, payload: Mapping[str, Any], id_field: str) -> dict[str, Any]:
    value = dict(payload)
    value[id_field] = f"{prefix}_{_content_hash(value)}"
    return value


def _timestamp_ms(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValidationEvidenceError("invalid_timestamp", field)
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ValidationEvidenceError("invalid_timestamp", field)
        # Numeric v2 timestamps are always epoch milliseconds.  Guessing seconds
        # from magnitude would make the same evidence change meaning over time and
        # would break deterministic synthetic epochs near zero.
        return int(number)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationEvidenceError("invalid_timestamp", field) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.astimezone(dt.timezone.utc).timestamp() * 1000)


def _finite_return(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationEvidenceError("invalid_return", field) from exc
    if not math.isfinite(result):
        raise ValidationEvidenceError("invalid_return", field)
    return result


def _required_int(value: Mapping[str, Any], field: str, *, code: str) -> int:
    try:
        raw = value[field]
        if isinstance(raw, bool):
            raise TypeError
        return int(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationEvidenceError(code, field) from exc


def _validate_time_grid(time_axis: Sequence[int], time_grid: Mapping[str, Any]) -> None:
    if time_grid.get("kind") != "fixed_step":
        raise ValidationEvidenceError("unsupported_time_grid_policy")
    try:
        step_ms = int(time_grid["step_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationEvidenceError("invalid_time_grid_step") from exc
    if step_ms <= 0:
        raise ValidationEvidenceError("invalid_time_grid_step")
    if len(set(time_axis)) != len(time_axis):
        raise ValidationEvidenceError("duplicate_period_ts")
    if list(time_axis) != sorted(time_axis):
        raise ValidationEvidenceError("non_monotonic_period_ts")
    if any(right - left != step_ms for left, right in zip(time_axis, time_axis[1:])):
        raise ValidationEvidenceError("time_grid_gap")


def build_validation_observation_set(
    trades: Sequence[Mapping[str, Any]],
    *,
    trial_id: str,
    symbol: str,
    strategy_family: str,
    timeframe: str,
    data_snapshot_id: str,
    data_evidence_hash: str,
    search_family_id: str,
    return_basis: str,
    time_grid: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a content-bound interval-bearing return series.

    ``period_ts`` is an explicit common-period coordinate.  It is never inferred from
    trade ordinal, and feature horizons are required rather than reconstructed from a
    current strategy configuration.
    """
    required_identity = {
        "trial_id": trial_id,
        "symbol": symbol,
        "strategy_family": strategy_family,
        "timeframe": timeframe,
        "data_snapshot_id": data_snapshot_id,
        "data_evidence_hash": data_evidence_hash,
        "search_family_id": search_family_id,
        "return_basis": return_basis,
    }
    if any(not str(value) for value in required_identity.values()):
        raise ValidationEvidenceError("observation_scope_missing")
    observations: list[dict[str, Any]] = []
    return_field = "pnl_pct" if return_basis == "gross_pct" else "net_pct"
    if return_basis not in {"gross_pct", "net_pct"}:
        raise ValidationEvidenceError("invalid_return_basis")
    for index, raw in enumerate(trades):
        period_ts = _timestamp_ms(raw.get("period_ts"), field="period_ts")
        entry_ts = _timestamp_ms(raw.get("entry_ts"), field="entry_ts")
        exit_ts = _timestamp_ms(raw.get("exit_ts"), field="exit_ts")
        feature_start_ts = _timestamp_ms(
            raw.get("feature_start_ts"), field="feature_start_ts"
        )
        feature_end_ts = _timestamp_ms(
            raw.get("feature_end_ts"), field="feature_end_ts"
        )
        if entry_ts > exit_ts:
            raise ValidationEvidenceError("reversed_holding_interval", str(index))
        if feature_start_ts > feature_end_ts:
            raise ValidationEvidenceError("reversed_feature_interval", str(index))
        if feature_end_ts > entry_ts:
            raise ValidationEvidenceError("feature_information_after_entry", str(index))
        body = {
            "trial_id": trial_id,
            "period_ts": period_ts,
            "entry_ts": entry_ts,
            "exit_ts": exit_ts,
            "feature_start_ts": feature_start_ts,
            "feature_end_ts": feature_end_ts,
            "return_value": _finite_return(raw.get(return_field), field=return_field),
            "return_basis": return_basis,
            "data_snapshot_id": data_snapshot_id,
            "data_evidence_hash": data_evidence_hash,
        }
        observations.append(_identified("vobs", body, "observation_id"))
    if not observations:
        raise ValidationEvidenceError("empty_observation_set")
    normalized_grid = {"kind": str(time_grid.get("kind") or "")}
    if "step_ms" in time_grid:
        normalized_grid["step_ms"] = int(time_grid["step_ms"])
    _validate_time_grid(
        [row["period_ts"] for row in observations], normalized_grid
    )
    payload = {
        "schema": OBSERVATION_SET_SCHEMA,
        **required_identity,
        "evaluation_window_id": "vwin_"
        + _content_hash(
            {
                "data_snapshot_id": data_snapshot_id,
                "data_evidence_hash": data_evidence_hash,
                "timeframe": timeframe,
                "time_grid": normalized_grid,
                "first_period_ts": observations[0]["period_ts"],
                "last_period_ts": observations[-1]["period_ts"],
            }
        ),
        "time_grid": normalized_grid,
        "observation_count": len(observations),
        "observations": observations,
    }
    result = _identified("vos", payload, "observation_set_id")
    validate_validation_observation_set(result)
    return result


def validate_validation_observation_set(value: Mapping[str, Any]) -> None:
    if value.get("schema") != OBSERVATION_SET_SCHEMA:
        raise ValidationEvidenceError("observation_schema_mismatch")
    observations = value.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ValidationEvidenceError("empty_observation_set")
    if int(value.get("observation_count") or -1) != len(observations):
        raise ValidationEvidenceError("observation_count_mismatch")
    trial_id = str(value.get("trial_id") or "")
    snapshot_id = str(value.get("data_snapshot_id") or "")
    evidence_hash = str(value.get("data_evidence_hash") or "")
    basis = str(value.get("return_basis") or "")
    if any(
        not str(value.get(field) or "")
        for field in (
            "trial_id",
            "symbol",
            "strategy_family",
            "timeframe",
            "data_snapshot_id",
            "data_evidence_hash",
            "evaluation_window_id",
            "search_family_id",
            "return_basis",
        )
    ):
        raise ValidationEvidenceError("observation_scope_missing")
    for row in observations:
        if not isinstance(row, dict):
            raise ValidationEvidenceError("invalid_observation_row")
        if (
            str(row.get("trial_id") or "") != trial_id
            or str(row.get("data_snapshot_id") or "") != snapshot_id
            or str(row.get("data_evidence_hash") or "") != evidence_hash
            or str(row.get("return_basis") or "") != basis
        ):
            raise ValidationEvidenceError("observation_scope_mismatch")
        entry_ts = _required_int(row, "entry_ts", code="invalid_observation_row")
        exit_ts = _required_int(row, "exit_ts", code="invalid_observation_row")
        feature_start_ts = _required_int(
            row, "feature_start_ts", code="invalid_observation_row"
        )
        feature_end_ts = _required_int(
            row, "feature_end_ts", code="invalid_observation_row"
        )
        _required_int(row, "period_ts", code="invalid_observation_row")
        if entry_ts > exit_ts:
            raise ValidationEvidenceError("reversed_holding_interval")
        if feature_start_ts > feature_end_ts:
            raise ValidationEvidenceError("reversed_feature_interval")
        if feature_end_ts > entry_ts:
            raise ValidationEvidenceError("feature_information_after_entry")
        _finite_return(row.get("return_value"), field="return_value")
        body = {key: item for key, item in row.items() if key != "observation_id"}
        if row.get("observation_id") != f"vobs_{_content_hash(body)}":
            raise ValidationEvidenceError("observation_identity_mismatch")
    _validate_time_grid(
        [
            _required_int(row, "period_ts", code="invalid_observation_row")
            for row in observations
        ],
        value.get("time_grid") if isinstance(value.get("time_grid"), dict) else {},
    )
    entry_axis = [
        _required_int(row, "entry_ts", code="invalid_observation_row")
        for row in observations
    ]
    if entry_axis != sorted(entry_axis):
        raise ValidationEvidenceError("non_monotonic_entry_ts")
    expected_window_id = "vwin_" + _content_hash(
        {
            "data_snapshot_id": snapshot_id,
            "data_evidence_hash": evidence_hash,
            "timeframe": str(value.get("timeframe") or ""),
            "time_grid": dict(value.get("time_grid") or {}),
            "first_period_ts": observations[0]["period_ts"],
            "last_period_ts": observations[-1]["period_ts"],
        }
    )
    if value.get("evaluation_window_id") != expected_window_id:
        raise ValidationEvidenceError("evaluation_window_identity_mismatch")
    body = {key: item for key, item in value.items() if key != "observation_set_id"}
    if value.get("observation_set_id") != f"vos_{_content_hash(body)}":
        raise ValidationEvidenceError("observation_set_identity_mismatch")


def classify_legacy_search_bias_evidence(trial_returns: Any) -> dict[str, Any]:
    """Classify old trial-major/per-trade vectors without trying to repair them."""
    rows = len(trial_returns) if isinstance(trial_returns, list) else 0
    lengths = [len(row) for row in trial_returns if isinstance(row, list)] if rows else []
    return {
        "schema": PANEL_STATUS_SCHEMA,
        "status": "invalid",
        "reason_codes": ["invalid_legacy_orientation"],
        "legacy_orientation": "trial_major_trade_ordinal",
        "legacy_outer_count": rows,
        "legacy_row_lengths": lengths,
    }


def _family_scope(evidence: Mapping[str, Any]) -> tuple[str, str, str]:
    definition = evidence.get("search_family_definition")
    if not isinstance(definition, dict):
        raise ValidationEvidenceError("family_definition_missing")
    timeframe = str(
        (definition.get("raw_sweep_spec") or {}).get("timeframe")
        or (definition.get("declared_grid") or {}).get("timeframe")
        or ""
    )
    snapshot_id = str((definition.get("data_binding") or {}).get("snapshot_id") or "")
    evidence_hash = str((definition.get("data_binding") or {}).get("evidence_hash") or "")
    return timeframe, snapshot_id, evidence_hash


def build_search_trial_panel(
    evidence: Mapping[str, Any],
    observation_sets: Sequence[Mapping[str, Any]],
    *,
    allow_unavailable: bool = False,
) -> dict[str, Any]:
    """Build an explicit ``(time, trials)`` matrix bound to Package 03 evidence."""
    try:
        counts = validate_search_trial_evidence(evidence, require_complete=True)
    except (TypeError, ValueError) as exc:
        raise ValidationEvidenceError("family_evidence_invalid", str(exc)) from exc
    family_id = str(evidence.get("search_family_id") or "")
    expected_timeframe, family_snapshot, family_evidence_hash = _family_scope(evidence)
    by_trial: dict[str, Mapping[str, Any]] = {}
    for observation_set in observation_sets:
        if not isinstance(observation_set, Mapping):
            raise ValidationEvidenceError("panel_observation_sets_invalid")
        trial_id = str(observation_set.get("trial_id") or "")
        if not trial_id or trial_id in by_trial:
            raise ValidationEvidenceError("duplicate_observation_set_trial")
        by_trial[trial_id] = observation_set

    scopes = {
        (
            str(value.get("symbol") or ""),
            str(value.get("strategy_family") or ""),
        )
        for value in observation_sets
    }
    if len(scopes) != 1:
        raise ValidationEvidenceError("panel_scope_mismatch")
    target_symbol, target_family = next(iter(scopes))
    if not target_symbol or not target_family:
        raise ValidationEvidenceError("panel_scope_mismatch")
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for trial in evidence.get("trials") or []:
        trial_id = str(trial.get("execution_id") or "")
        disposition = str(trial.get("terminal_disposition") or "")
        if (
            str(trial.get("symbol") or "") != target_symbol
            or str(trial.get("family") or "") != target_family
        ):
            excluded.append(
                {
                    "trial_id": trial_id,
                    "disposition": disposition,
                    "reason": "different_symbol_or_family_scope",
                }
            )
            continue
        if disposition != "evaluated":
            excluded.append(
                {
                    "trial_id": trial_id,
                    "disposition": disposition,
                    "reason": f"terminal_{disposition or 'unknown'}",
                }
            )
            continue
        observation_set = by_trial.get(trial_id)
        if observation_set is None:
            excluded.append(
                {
                    "trial_id": trial_id,
                    "disposition": disposition,
                    "reason": "observation_set_missing",
                }
            )
            continue
        expected_snapshot = str(trial.get("data_snapshot_id") or family_snapshot)
        expected_evidence_hash = str(
            trial.get("data_evidence_hash") or family_evidence_hash
        )
        if (
            str(observation_set.get("symbol") or "") != str(trial.get("symbol") or "")
            or str(observation_set.get("strategy_family") or "")
            != str(trial.get("family") or "")
            or str(observation_set.get("timeframe") or "") != expected_timeframe
            or str(observation_set.get("data_snapshot_id") or "") != expected_snapshot
            or str(observation_set.get("data_evidence_hash") or "")
            != expected_evidence_hash
            or str(observation_set.get("search_family_id") or "") != family_id
        ):
            raise ValidationEvidenceError("panel_scope_mismatch", trial_id)
        validate_validation_observation_set(observation_set)
        included.append(
            {
                "trial_id": trial_id,
                "observation_set_id": str(observation_set["observation_set_id"]),
                "disposition": disposition,
            }
        )
    unknown = sorted(set(by_trial) - {str(row.get("execution_id") or "") for row in evidence.get("trials") or []})
    if unknown:
        raise ValidationEvidenceError("panel_unknown_trial", ",".join(unknown))
    coverage = {
        "selected_count": len(evidence.get("trials") or []),
        "scope_selected_count": sum(
            str(row.get("symbol") or "") == target_symbol
            and str(row.get("family") or "") == target_family
            for row in evidence.get("trials") or []
        ),
        "included_count": len(included),
        "excluded_count": len(excluded),
        "included": sorted(included, key=lambda row: row["trial_id"]),
        "excluded": sorted(excluded, key=lambda row: row["trial_id"]),
    }
    blocking_exclusions = [
        row for row in excluded if row["reason"] != "different_symbol_or_family_scope"
    ]
    effective_family_trial_count = int(counts["effective_n_trials"])
    if effective_family_trial_count != len(included):
        blocking_exclusions.append(
            {
                "reason": "family_multiplicity_not_represented_in_panel",
                "effective_family_trial_count": effective_family_trial_count,
                "panel_trial_count": len(included),
            }
        )
    if blocking_exclusions:
        if not allow_unavailable:
            raise ValidationEvidenceError("incomplete_family_panel")
        return {
            "schema": PANEL_STATUS_SCHEMA,
            "status": "unavailable",
            "reason_codes": sorted(
                {str(row["reason"]) for row in blocking_exclusions}
            ),
            "search_family_id": family_id,
            "effective_family_trial_count": effective_family_trial_count,
            "coverage": coverage,
            "blocking_reasons": blocking_exclusions,
        }

    columns = sorted(by_trial)
    ordered_sets = [by_trial[trial_id] for trial_id in columns]
    axes = [
        [int(row["period_ts"]) for row in value["observations"]]
        for value in ordered_sets
    ]
    if not axes or any(axis != axes[0] for axis in axes[1:]):
        raise ValidationEvidenceError("common_time_axis_mismatch")
    target_window_ids = {
        str(value.get("evaluation_window_id") or "") for value in ordered_sets
    }
    if len(target_window_ids) != 1:
        raise ValidationEvidenceError("panel_window_identity_mismatch")
    target_window_id = next(iter(target_window_ids))
    if not target_window_id:
        raise ValidationEvidenceError("panel_window_identity_mismatch")
    basis = str(ordered_sets[0].get("return_basis") or "")
    grid = dict(ordered_sets[0].get("time_grid") or {})
    if any(
        str(value.get("return_basis") or "") != basis
        or dict(value.get("time_grid") or {}) != grid
        for value in ordered_sets[1:]
    ):
        raise ValidationEvidenceError("panel_basis_or_grid_mismatch")
    matrix = [
        [float(value["observations"][row_index]["return_value"]) for value in ordered_sets]
        for row_index in range(len(axes[0]))
    ]
    payload = {
        "schema": PANEL_SCHEMA,
        "status": "valid",
        "orientation": "time_major",
        "search_family_id": family_id,
        "symbol": target_symbol,
        "strategy_family": target_family,
        "timeframe": expected_timeframe,
        "data_snapshot_id": family_snapshot,
        "data_evidence_hash": family_evidence_hash,
        "evaluation_window_id": target_window_id,
        "return_basis": basis,
        "time_grid": grid,
        "time_axis": axes[0],
        "trial_columns": columns,
        "observation_set_ids": [str(value["observation_set_id"]) for value in ordered_sets],
        "observation_sets": ordered_sets,
        "matrix": matrix,
        "reported_time_count": len(axes[0]),
        "reported_trial_count": len(columns),
        "effective_family_trial_count": effective_family_trial_count,
        "coverage": coverage,
        "missing_value_policy": "reject",
    }
    return _identified("stp", payload, "search_trial_panel_id")


def validate_search_trial_panel(
    panel: Mapping[str, Any], evidence: Mapping[str, Any]
) -> None:
    try:
        counts = validate_search_trial_evidence(evidence, require_complete=True)
    except (TypeError, ValueError) as exc:
        raise ValidationEvidenceError("family_evidence_invalid", str(exc)) from exc
    if panel.get("schema") != PANEL_SCHEMA or panel.get("status") != "valid":
        raise ValidationEvidenceError("panel_schema_or_status_invalid")
    if panel.get("orientation") != "time_major":
        raise ValidationEvidenceError("panel_orientation_invalid")
    columns = panel.get("trial_columns")
    observation_set_ids = panel.get("observation_set_ids")
    source_observation_sets = panel.get("observation_sets")
    matrix = panel.get("matrix")
    time_axis = panel.get("time_axis")
    if not isinstance(columns, list) or columns != sorted(columns) or len(set(columns)) != len(columns):
        raise ValidationEvidenceError("panel_columns_invalid")
    if (
        not isinstance(observation_set_ids, list)
        or len(observation_set_ids) != len(columns)
        or len(set(observation_set_ids)) != len(observation_set_ids)
    ):
        raise ValidationEvidenceError("panel_observation_sets_invalid")
    if (
        not isinstance(source_observation_sets, list)
        or len(source_observation_sets) != len(columns)
        or any(not isinstance(value, Mapping) for value in source_observation_sets)
    ):
        raise ValidationEvidenceError("panel_observation_sets_invalid")
    if not isinstance(matrix, list) or not isinstance(time_axis, list):
        raise ValidationEvidenceError("panel_matrix_invalid")
    expected_trials = sorted(
        str(row.get("execution_id") or "")
        for row in evidence.get("trials") or []
        if row.get("terminal_disposition") == "evaluated"
        and str(row.get("symbol") or "") == str(panel.get("symbol") or "")
        and str(row.get("family") or "") == str(panel.get("strategy_family") or "")
    )
    if columns != expected_trials:
        raise ValidationEvidenceError("panel_family_columns_mismatch")
    if (
        _required_int(panel, "reported_trial_count", code="panel_trial_count_mismatch")
        != len(columns)
        or _required_int(
            panel, "reported_trial_count", code="panel_trial_count_mismatch"
        )
        != len(expected_trials)
        or _required_int(
            panel, "effective_family_trial_count", code="panel_trial_count_mismatch"
        )
        != int(counts["effective_n_trials"])
        or _required_int(
            panel, "reported_trial_count", code="panel_trial_count_mismatch"
        )
        != int(counts["effective_n_trials"])
    ):
        raise ValidationEvidenceError("panel_trial_count_mismatch")
    if _required_int(panel, "reported_time_count", code="panel_time_count_mismatch") != len(time_axis):
        raise ValidationEvidenceError("panel_time_count_mismatch")
    if len(matrix) != len(time_axis) or any(
        not isinstance(row, list) or len(row) != len(columns) for row in matrix
    ):
        raise ValidationEvidenceError("panel_matrix_shape_mismatch")
    for row in matrix:
        for value in row:
            _finite_return(value, field="panel_matrix")
    _validate_time_grid(
        [
            _required_int({"value": value}, "value", code="panel_time_axis_invalid")
            for value in time_axis
        ],
        panel.get("time_grid") if isinstance(panel.get("time_grid"), dict) else {},
    )
    if str(panel.get("search_family_id") or "") != str(evidence.get("search_family_id") or ""):
        raise ValidationEvidenceError("panel_family_identity_mismatch")
    expected_timeframe, expected_snapshot, expected_evidence_hash = _family_scope(evidence)
    if (
        str(panel.get("timeframe") or "") != expected_timeframe
        or str(panel.get("data_snapshot_id") or "") != expected_snapshot
        or str(panel.get("data_evidence_hash") or "") != expected_evidence_hash
    ):
        raise ValidationEvidenceError("panel_scope_mismatch")
    coverage = panel.get("coverage")
    if not isinstance(coverage, dict) or (
        _required_int(coverage, "selected_count", code="panel_coverage_mismatch")
        != len(evidence.get("trials") or [])
        or _required_int(coverage, "included_count", code="panel_coverage_mismatch")
        != len(columns)
        or _required_int(
            coverage, "scope_selected_count", code="panel_coverage_mismatch"
        )
        != len(expected_trials)
        or _required_int(coverage, "included_count", code="panel_coverage_mismatch")
        + _required_int(coverage, "excluded_count", code="panel_coverage_mismatch")
        != _required_int(coverage, "selected_count", code="panel_coverage_mismatch")
    ):
        raise ValidationEvidenceError("panel_coverage_mismatch")
    expected_included = [
        {
            "trial_id": trial_id,
            "observation_set_id": observation_set_id,
            "disposition": "evaluated",
        }
        for trial_id, observation_set_id in zip(
            columns, observation_set_ids, strict=True
        )
    ]
    expected_excluded = sorted(
        [
            {
                "trial_id": str(row.get("execution_id") or ""),
                "disposition": str(row.get("terminal_disposition") or ""),
                "reason": "different_symbol_or_family_scope",
            }
            for row in evidence.get("trials") or []
            if str(row.get("symbol") or "") != str(panel.get("symbol") or "")
            or str(row.get("family") or "")
            != str(panel.get("strategy_family") or "")
        ],
        key=lambda row: row["trial_id"],
    )
    if coverage.get("included") != expected_included or coverage.get("excluded") != expected_excluded:
        raise ValidationEvidenceError("panel_coverage_mismatch")
    body = {key: item for key, item in panel.items() if key != "search_trial_panel_id"}
    if panel.get("search_trial_panel_id") != f"stp_{_content_hash(body)}":
        raise ValidationEvidenceError("panel_identity_mismatch")
    rebuilt = build_search_trial_panel(evidence, source_observation_sets)
    if dict(panel) != rebuilt:
        raise ValidationEvidenceError("panel_recomputation_mismatch")


def build_interval_split_manifest(
    observation_set: Mapping[str, Any],
    *,
    train_count: int,
    embargo_ms: int,
) -> dict[str, Any]:
    """Build one forward split and record every interval-derived exclusion."""
    validate_validation_observation_set(observation_set)
    observations = list(observation_set["observations"])
    if train_count <= 0 or train_count >= len(observations):
        raise ValidationEvidenceError("invalid_train_count")
    if embargo_ms < 0:
        raise ValidationEvidenceError("invalid_embargo")
    initial_train = observations[:train_count]
    initial_test = observations[train_count:]
    test_start = int(initial_test[0]["entry_ts"])
    retained_train: list[str] = []
    purged: list[dict[str, Any]] = []
    for row in initial_train:
        reasons = []
        if int(row["exit_ts"]) >= test_start:
            reasons.append("holding_interval_overlaps_test")
        if int(row["feature_end_ts"]) >= test_start:
            reasons.append("feature_horizon_overlaps_test")
        if reasons:
            purged.append({"observation_id": row["observation_id"], "reasons": reasons})
        else:
            retained_train.append(row["observation_id"])
    embargo_boundary = test_start + embargo_ms
    embargoed: list[dict[str, Any]] = []
    retained_test: list[str] = []
    for row in initial_test:
        if int(row["entry_ts"]) < embargo_boundary:
            embargoed.append(
                {"observation_id": row["observation_id"], "reason": "declared_embargo"}
            )
        else:
            retained_test.append(row["observation_id"])
    if not retained_train:
        raise ValidationEvidenceError("empty_effective_train_partition")
    if not retained_test:
        raise ValidationEvidenceError("empty_effective_test_partition")
    payload = {
        "schema": SPLIT_SCHEMA,
        "observation_set_id": str(observation_set["observation_set_id"]),
        "policy": "forward_interval_purge_v2",
        "train_count": int(train_count),
        "embargo_ms": int(embargo_ms),
        "test_start_ts": test_start,
        "initial_train_ids": [row["observation_id"] for row in initial_train],
        "initial_test_ids": [row["observation_id"] for row in initial_test],
        "retained_train_ids": retained_train,
        "retained_test_ids": retained_test,
        "purged": purged,
        "embargoed": embargoed,
        "effective_train_count": len(retained_train),
        "effective_test_count": len(retained_test),
    }
    return _identified("ism", payload, "split_manifest_id")


def validate_interval_split_manifest(
    manifest: Mapping[str, Any], observation_set: Mapping[str, Any]
) -> None:
    if manifest.get("schema") != SPLIT_SCHEMA:
        raise ValidationEvidenceError("split_schema_mismatch")
    try:
        rebuilt = build_interval_split_manifest(
            observation_set,
            train_count=int(manifest["train_count"]),
            embargo_ms=int(manifest["embargo_ms"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValidationEvidenceError):
            raise
        raise ValidationEvidenceError("split_manifest_invalid") from exc
    if dict(manifest) != rebuilt:
        raise ValidationEvidenceError("split_manifest_mismatch")


def build_dependence_evidence(
    *, method: str, seed: int, block_length: int | None, effective_n: int
) -> dict[str, Any]:
    """Record method limits without claiming an unavailable upstream implementation."""
    if effective_n <= 0:
        raise ValidationEvidenceError("invalid_effective_n")
    if method == "iid_bootstrap_kill_test":
        authoritative_suitable = False
        reason = "iid_assumption_not_authoritative_for_dependent_trades"
    elif method == "interval_block_upstream_required":
        if block_length is None or block_length <= 0:
            raise ValidationEvidenceError("invalid_block_length")
        authoritative_suitable = False
        reason = "accepted_upstream_method_unavailable"
    else:
        raise ValidationEvidenceError("unsupported_dependence_method")
    payload = {
        "schema": DEPENDENCE_SCHEMA,
        "method": method,
        "seed": int(seed),
        "block_length": block_length,
        "effective_n": int(effective_n),
        "authoritative_suitable": authoritative_suitable,
        "reason": reason,
    }
    return _identified("dep", payload, "dependence_evidence_id")


def validate_dependence_evidence(value: Mapping[str, Any]) -> None:
    if value.get("schema") != DEPENDENCE_SCHEMA:
        raise ValidationEvidenceError("dependence_schema_mismatch")
    try:
        rebuilt = build_dependence_evidence(
            method=str(value["method"]),
            seed=int(value["seed"]),
            block_length=(
                None if value.get("block_length") is None else int(value["block_length"])
            ),
            effective_n=int(value["effective_n"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValidationEvidenceError):
            raise
        raise ValidationEvidenceError("dependence_evidence_invalid") from exc
    if dict(value) != rebuilt:
        raise ValidationEvidenceError("dependence_evidence_mismatch")
