"""Immutable, content-addressed definition of a complete strategy search family."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping


SCHEMA = "SearchFamilyDefinition.v2"
COMPILER_VERSION = "bounded-cartesian/v2"
DECLARED_GRID_VERSION = "declared-grid/v2"
FAMILY_POLICIES = {"independent", "cumulative", "confirmatory"}


def canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def normalize_snapshot_bindings(
    bindings: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized = sorted(
        (
            {
                "symbol": str(item.get("symbol") or ""),
                "timeframe": str(item.get("timeframe") or ""),
                "snapshot_id": str(item.get("snapshot_id") or ""),
                "evidence_hash": str(item.get("evidence_hash") or ""),
                "row_count": int(item.get("row_count") or 0),
            }
            for item in bindings
        ),
        key=lambda item: (item["symbol"], item["timeframe"]),
    )
    if not normalized or any(
        not item["symbol"] or not item["timeframe"]
        or not item["snapshot_id"] or not item["evidence_hash"]
        or item["row_count"] < 1
        for item in normalized
    ):
        raise ValueError("complete symbol-level candle snapshot bindings are required")
    keys = [(item["symbol"], item["timeframe"]) for item in normalized]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate symbol/timeframe candle snapshot binding")
    return normalized


def snapshot_set_identity(
    bindings: list[Mapping[str, Any]],
) -> tuple[str, str]:
    """Identity for the exact symbol-level candle manifests scheduled in a family."""
    normalized = normalize_snapshot_bindings(bindings)
    if len(normalized) == 1:
        return normalized[0]["snapshot_id"], normalized[0]["evidence_hash"]
    return (
        f"csmset_{content_hash({'schema': 'CandleSnapshotSet.v1', 'items': normalized})}",
        f"cse_{content_hash({'schema': 'CandleEvidenceSet.v1', 'items': normalized})}",
    )


def resolve_snapshot_set(
    *,
    private_root: Path,
    symbols: list[str],
    timeframe: str,
    data_glob: str,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Select canonical candles now and fail closed before a queued family is frozen."""
    from src.research_lab.candle_library import load_canonical_candles

    bindings: list[dict[str, str]] = []
    for index, symbol in enumerate(symbols, start=1):
        selected = load_canonical_candles(
            private_root,
            symbol,
            timeframe,
            fallback_glob=data_glob,
            purpose="experiment",
            coverage_policy="gap_free",
            progress=(
                None
                if progress is None
                else lambda stage, index=index: progress(f"snapshot_{index}:{stage}")
            ),
        )
        if not selected.rows:
            raise ValueError(f"no bounded candle snapshot for {symbol}@{timeframe}")
        bindings.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "snapshot_id": selected.manifest.snapshot_id,
                "evidence_hash": selected.manifest.evidence_hash,
                "row_count": selected.manifest.row_count,
            }
        )
        if progress is not None:
            progress(f"snapshot_{index}:bound")
    snapshot_id, evidence_hash = snapshot_set_identity(bindings)
    return snapshot_id, evidence_hash, normalize_snapshot_bindings(bindings)


def family_definition_id(definition: Mapping[str, Any]) -> str:
    return f"sfd_{content_hash(dict(definition))}"


def _source_digest(*names: str) -> str:
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in names:
        path = root / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def compiler_code_identity() -> str:
    return _source_digest(
        "search_family_definition.py",
        "sweep_compile.py",
        "sweep_spec.py",
        "param_schemas.py",
    )


def validity_code_identity() -> str:
    return _source_digest("param_schemas.py")


def sampler_code_identity() -> str:
    return _source_digest("sweep_compile.py")


def selection_policy_code_identity() -> str:
    return _source_digest(
        "resource_policy.py",
        "runtime_policy.py",
        "sweep_spec.py",
        "timeframes.py",
    )


def _family_policy(
    *,
    mode: str,
    parent_family_id: str = "",
    parent_trial_id: str = "",
    parent_effective_n_trials: int = 0,
) -> dict[str, Any]:
    normalized = str(mode or "").strip().lower()
    parent_family_id = str(parent_family_id or "").strip()
    parent_trial_id = str(parent_trial_id or "").strip()
    prior = int(parent_effective_n_trials or 0)
    if normalized not in FAMILY_POLICIES:
        raise ValueError(f"unknown cumulative-family policy: {normalized or '<empty>'}")
    if normalized == "independent" and (parent_family_id or parent_trial_id):
        raise ValueError("independent family cannot declare a parent")
    if normalized == "independent" and prior:
        raise ValueError("independent family cannot inherit prior trials")
    if normalized != "independent" and not (parent_family_id and parent_trial_id):
        raise ValueError("follow-up family requires parent_family_id and parent_trial_id")
    if normalized == "cumulative" and prior < 1:
        raise ValueError("cumulative family requires parent_effective_n_trials")
    if normalized == "confirmatory" and prior < 1:
        raise ValueError("confirmatory family requires parent trial accounting")
    return {
        "mode": normalized,
        "parent_family_id": parent_family_id,
        "parent_trial_id": parent_trial_id,
        "parent_effective_n_trials": prior,
    }


def _raw_sweep_payload(spec: Any) -> dict[str, Any]:
    return {
        "sweep_id": str(spec.sweep_id),
        "anchor_symbol": str(spec.anchor_symbol),
        "related_symbols": list(spec.related_symbols),
        "timeframe": str(spec.timeframe),
        "setup_family": str(spec.setup_family),
        "setup_grid": dict(spec.setup_grid),
        "entry_grid": dict(spec.entry_grid),
        "exit_grid": dict(spec.exit_grid),
        "filter_grid": dict(spec.filter_grid),
        "max_variants": int(spec.max_variants),
        "backend": str(spec.backend),
        "resource_class": str(spec.resource_class),
        "private_output_policy": str(spec.private_output_policy),
        "variant_tier": str(spec.variant_tier),
        "parent_family_id": str(getattr(spec, "parent_family_id", "") or ""),
        "parent_trial_id": str(getattr(spec, "parent_trial_id", "") or ""),
        "parent_effective_n_trials": int(
            getattr(spec, "parent_effective_n_trials", 0) or 0
        ),
        "cumulative_family_policy": str(
            getattr(spec, "cumulative_family_policy", "independent") or "independent"
        ),
    }


def build_sweep_family_definition(
    spec: Any,
    *,
    symbols: list[str],
    filters: dict[str, list[str]],
    search_space: Mapping[str, Any],
    effective_max_variants: int,
    execution_cap: int,
    seed_material: str,
    sampler_version: str,
    validity_version: str,
    resource_policy_contract: Any,
    timeframe_profile: Any,
    data_snapshot_id: str = "",
    data_evidence_hash: str = "",
    data_snapshot_bindings: list[Mapping[str, Any]] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], str]:
    points = [dict(point) for point in search_space.get("points", [])]
    selected = [int(value) for value in search_space.get("selected_flat_indices", [])]
    definition = {
        "schema": SCHEMA,
        "origin": "sweep",
        "raw_sweep_spec": _raw_sweep_payload(spec),
        "canonical_axis_order": "section_then_name/value_order_preserved/v1",
        "merged_axis_order": list(search_space.get("axis_order", [])),
        "points": points,
        "selected_flat_indices": selected,
        "symbols": [str(symbol) for symbol in symbols],
        "filters": {str(key): list(values) for key, values in sorted(filters.items())},
        "compiler": {
            "version": COMPILER_VERSION,
            "digest": compiler_code_identity(),
        },
        "validity": {
            "version": str(validity_version),
            "digest": validity_code_identity(),
        },
        "sampler": {
            "version": str(sampler_version),
            "digest": sampler_code_identity(),
            "seed_material": str(seed_material),
            "seed_sha256": hashlib.sha256(str(seed_material).encode("utf-8")).hexdigest(),
        },
        "resource_policy": {
            "requested_max_variants": int(spec.max_variants),
            "effective_max_variants": int(effective_max_variants),
            "selected_variant_count": len(selected),
            "selected_run_total": len(symbols) * len(selected),
            "execution_cap": int(execution_cap),
            "policy": asdict(resource_policy_contract),
            "timeframe_profile": asdict(timeframe_profile),
            "selection_policy_digest": selection_policy_code_identity(),
        },
        "data_binding": {
            "snapshot_id": str(data_snapshot_id or ""),
            "evidence_hash": str(data_evidence_hash or ""),
            "members": normalize_snapshot_bindings(data_snapshot_bindings or [])
            if data_snapshot_bindings
            else [],
            "status": "bound" if data_snapshot_id and data_evidence_hash else "selection_pending",
        },
        "family_policy": _family_policy(
            mode=getattr(spec, "cumulative_family_policy", "independent"),
            parent_family_id=getattr(spec, "parent_family_id", ""),
            parent_trial_id=getattr(spec, "parent_trial_id", ""),
            parent_effective_n_trials=getattr(spec, "parent_effective_n_trials", 0),
        ),
    }
    validate_search_family_definition(
        definition, check_code_identity=True, progress=progress,
    )
    return definition, family_definition_id(definition)


def build_declared_grid_definition(spec: Any) -> tuple[dict[str, Any], str]:
    points: list[dict[str, Any]] = []
    index = 0
    for family in spec.families:
        for params in spec.parameter_grid.get(family, []):
            points.append(
                {
                    "flat_index": index,
                    "family": str(family),
                    "params": dict(params or {}),
                    "pre_disposition": "selected",
                    "reason": "declared_grid_point",
                }
            )
            index += 1
    policy = dict((spec.plan_meta or {}).get("search_family_policy") or {})
    definition = {
        "schema": SCHEMA,
        "origin": "declared_grid",
        "declared_grid": {
            "experiment_id": str(spec.experiment_id),
            "symbols": list(spec.symbols),
            "families": list(spec.families),
            "parameter_grid": dict(spec.parameter_grid),
            "timeframe": str(spec.timeframe),
            "filters": dict(spec.filters),
            "max_runs": int(spec.max_runs),
            "backend": str(spec.backend),
        },
        "canonical_axis_order": "declared_family_then_row/v1",
        "merged_axis_order": [],
        "points": points,
        "selected_flat_indices": [point["flat_index"] for point in points],
        "symbols": list(spec.symbols),
        "filters": dict(spec.filters),
        "compiler": {
            "version": DECLARED_GRID_VERSION,
            "digest": compiler_code_identity(),
        },
        "validity": {"version": "declared-as-provided/v1", "digest": validity_code_identity()},
        "sampler": {"version": "none", "digest": "", "seed_material": "", "seed_sha256": ""},
        "resource_policy": {
            "requested_max_variants": len(points),
            "effective_max_variants": len(points),
            "selected_variant_count": len(points),
            "selected_run_total": len(spec.symbols) * len(points),
            "execution_cap": int(spec.max_runs),
        },
        "data_binding": {
            "snapshot_id": str(getattr(spec, "data_snapshot_id", "") or ""),
            "evidence_hash": str(getattr(spec, "data_evidence_hash", "") or ""),
            "members": normalize_snapshot_bindings(
                list(getattr(spec, "data_snapshot_bindings", []) or [])
            )
            if getattr(spec, "data_snapshot_bindings", None)
            else [],
            "status": (
                "bound"
                if getattr(spec, "data_snapshot_id", "") and getattr(spec, "data_evidence_hash", "")
                else "selection_pending"
            ),
        },
        "family_policy": _family_policy(
            mode=str(policy.get("mode") or "independent"),
            parent_family_id=str(policy.get("parent_family_id") or ""),
            parent_trial_id=str(policy.get("parent_trial_id") or ""),
            parent_effective_n_trials=int(policy.get("parent_effective_n_trials") or 0),
        ),
    }
    validate_search_family_definition(definition, check_code_identity=True)
    return definition, family_definition_id(definition)


def validate_search_family_definition(
    definition: Mapping[str, Any],
    *,
    expected_id: str = "",
    check_code_identity: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    value = dict(definition)
    if value.get("schema") != SCHEMA:
        raise ValueError("unsupported search family definition schema")
    if value.get("origin") not in {"sweep", "declared_grid"}:
        raise ValueError("unknown search family definition origin")
    points = value.get("points")
    if not isinstance(points, list) or not points:
        raise ValueError("search family definition has no points")
    indices = [point.get("flat_index") for point in points if isinstance(point, dict)]
    if indices != list(range(len(points))):
        raise ValueError("search family flat indices must be unique and contiguous")
    allowed = {"selected", "schema_invalid", "dependency_invalid", "omitted_variant_cap"}
    dispositions = [str(point.get("pre_disposition") or "") for point in points]
    if any(disposition not in allowed for disposition in dispositions):
        raise ValueError("invalid search family pre-execution disposition")
    if any(not str(point.get("reason") or "") for point in points):
        raise ValueError("search family point reason is required")
    selected = [int(value) for value in value.get("selected_flat_indices", [])]
    from_points = [
        int(point["flat_index"])
        for point in points
        if point.get("pre_disposition") == "selected"
    ]
    if selected != from_points:
        raise ValueError("selected flat indices disagree with point dispositions")
    resource = value.get("resource_policy")
    if not isinstance(resource, dict):
        raise ValueError("search family resource policy is missing")
    if int(resource.get("selected_variant_count", -1)) != len(selected):
        raise ValueError("selected variant count mismatch")
    symbols = value.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("search family symbol scope is missing")
    if int(resource.get("selected_run_total", -1)) != len(symbols) * len(selected):
        raise ValueError("selected run count mismatch")
    policy = value.get("family_policy")
    if not isinstance(policy, dict):
        raise ValueError("search family policy is missing")
    _family_policy(
        mode=str(policy.get("mode") or ""),
        parent_family_id=str(policy.get("parent_family_id") or ""),
        parent_trial_id=str(policy.get("parent_trial_id") or ""),
        parent_effective_n_trials=int(policy.get("parent_effective_n_trials") or 0),
    )
    if policy.get("mode") == "confirmatory" and int(
        resource.get("selected_run_total", -1)
    ) != 1:
        raise ValueError("confirmatory family must declare exactly one selected execution")
    binding = value.get("data_binding")
    if not isinstance(binding, dict) or binding.get("status") not in {"bound", "selection_pending"}:
        raise ValueError("invalid search family data binding")
    if binding.get("status") == "bound" and not (
        binding.get("snapshot_id") and binding.get("evidence_hash")
    ):
        raise ValueError("bound search family data identity is incomplete")
    members = binding.get("members") or []
    if not isinstance(members, list):
        raise ValueError("search family snapshot members are invalid")
    if members:
        normalized_members = normalize_snapshot_bindings(members)
        if members != normalized_members:
            raise ValueError("search family snapshot members are not canonical")
        snapshot_id, evidence_hash = snapshot_set_identity(members)
        if (
            snapshot_id != str(binding.get("snapshot_id") or "")
            or evidence_hash != str(binding.get("evidence_hash") or "")
        ):
            raise ValueError("search family snapshot set identity mismatch")
        if [item["symbol"] for item in members] != sorted(str(symbol) for symbol in symbols):
            raise ValueError("search family snapshot members disagree with symbol scope")
    elif binding.get("status") == "selection_pending" and (
        binding.get("snapshot_id") or binding.get("evidence_hash")
    ):
        raise ValueError("pending search family has partial data identity")
    if check_code_identity:
        compiler = value.get("compiler") or {}
        validity = value.get("validity") or {}
        sampler = value.get("sampler") or {}
        if compiler.get("digest") != compiler_code_identity():
            raise ValueError("historical search-family compiler is unavailable")
        if validity.get("digest") != validity_code_identity():
            raise ValueError("historical search-family validity contract is unavailable")
        if sampler.get("version") != "none" and sampler.get("digest") != sampler_code_identity():
            raise ValueError("historical search-family sampler is unavailable")
        if value.get("origin") == "sweep" and (
            resource.get("selection_policy_digest") != selection_policy_code_identity()
        ):
            raise ValueError("historical search-family selection policy is unavailable")
    if value.get("origin") == "sweep":
        _validate_sweep_derivation(value, progress=progress)
    else:
        _validate_declared_grid_derivation(value)
    actual_id = family_definition_id(value)
    if expected_id and actual_id != expected_id:
        raise ValueError("search family definition id mismatch")
    return {
        "raw_points": len(points),
        "schema_invalid": dispositions.count("schema_invalid"),
        "dependency_invalid": dispositions.count("dependency_invalid"),
        "eligible_points": sum(
            disposition in {"selected", "omitted_variant_cap"} for disposition in dispositions
        ),
        "selected_points": len(selected),
        "omitted_variant_cap": dispositions.count("omitted_variant_cap"),
    }


def _validate_sweep_derivation(
    definition: Mapping[str, Any],
    *,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Recompile the embedded raw sweep and require an identical point ledger."""
    from src.research_lab.param_schemas import (
        PARAMETER_SEARCH_CONTRACT_VERSION,
        SAMPLER_VERSION,
        executable_exit_params,
    )
    from src.research_lab.resource_policy import ResourcePolicy
    from src.research_lab.runtime_policy import effective_variant_cap
    from src.research_lab.strategy_registry import get_strategy
    from src.research_lab.sweep_compile import expand_grids_bounded
    from src.research_lab.sweep_spec import ABS_VARIANT_CAP, TIER_MULT, SweepSpec
    from src.research_lab.timeframes import TimeframeProfile

    raw = definition.get("raw_sweep_spec")
    resource = definition.get("resource_policy")
    if not isinstance(raw, dict) or not isinstance(resource, dict):
        raise ValueError("sweep family derivation inputs are missing")
    expected_raw_keys = {
        "sweep_id",
        "anchor_symbol",
        "related_symbols",
        "timeframe",
        "setup_family",
        "setup_grid",
        "entry_grid",
        "exit_grid",
        "filter_grid",
        "max_variants",
        "backend",
        "resource_class",
        "private_output_policy",
        "variant_tier",
        "parent_family_id",
        "parent_trial_id",
        "parent_effective_n_trials",
        "cumulative_family_policy",
    }
    if set(raw) != expected_raw_keys:
        raise ValueError("raw sweep specification fields disagree with v2 contract")
    try:
        raw_payload = dict(raw)
        raw_payload["related_symbols"] = tuple(raw_payload["related_symbols"])
        spec = SweepSpec(**raw_payload)
        policy = ResourcePolicy(**dict(resource.get("policy") or {}))
        profile = TimeframeProfile(**dict(resource.get("timeframe_profile") or {}))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid embedded sweep selection contract") from exc
    if profile.timeframe != spec.timeframe.lower():
        raise ValueError("timeframe profile disagrees with raw sweep")

    tier_multiplier = TIER_MULT.get(spec.variant_tier, 1)
    profile_cap = min(profile.max_variants_per_setup * tier_multiplier, ABS_VARIANT_CAP)
    effective_max = min(max(1, int(spec.max_variants)), profile_cap)
    if int(resource.get("requested_max_variants", -1)) != int(spec.max_variants):
        raise ValueError("requested variant cap disagrees with raw sweep")
    if int(resource.get("effective_max_variants", -1)) != effective_max:
        raise ValueError("effective variant cap disagrees with selection contract")

    defaults = executable_exit_params(
        spec.setup_family,
        get_strategy(spec.setup_family).parameter_defaults,
    )
    baseline = {
        key: defaults.get(key, "baseline" if key == "exit_mode" else values[0])
        for grid in (spec.setup_grid, spec.entry_grid, spec.exit_grid)
        for key, values in grid.items()
        if values
    }
    seed_material = f"{spec.sweep_id}|{spec.setup_family}|{spec.timeframe}"
    audit: dict[str, Any] = {}
    variants = expand_grids_bounded(
        spec.setup_grid,
        spec.entry_grid,
        spec.exit_grid,
        cap=effective_max,
        seed_material=seed_material,
        baseline=baseline,
        strategy_id=spec.setup_family,
        audit=audit,
        progress=progress,
    )
    if definition.get("points") != audit.get("points"):
        raise ValueError("search family points disagree with raw sweep derivation")
    if definition.get("merged_axis_order") != audit.get("axis_order"):
        raise ValueError("search family axis order disagrees with raw sweep derivation")
    if definition.get("selected_flat_indices") != audit.get("selected_flat_indices"):
        raise ValueError("selected indices disagree with raw sweep derivation")
    expected_symbols = [spec.anchor_symbol, *spec.related_symbols]
    if definition.get("symbols") != expected_symbols:
        raise ValueError("search family symbols disagree with raw sweep")
    expected_filters = {
        str(key): [str(item) for item in values]
        for key, values in sorted(spec.filter_grid.items())
        if values
    }
    if definition.get("filters") != expected_filters:
        raise ValueError("search family filters disagree with raw sweep")
    sampler = dict(definition.get("sampler") or {})
    if sampler.get("version") != SAMPLER_VERSION:
        raise ValueError("sampler version disagrees with compiler contract")
    if sampler.get("seed_material") != seed_material or sampler.get(
        "seed_sha256"
    ) != hashlib.sha256(seed_material.encode("utf-8")).hexdigest():
        raise ValueError("sampler seed identity disagrees with raw sweep")
    validity = dict(definition.get("validity") or {})
    if validity.get("version") != PARAMETER_SEARCH_CONTRACT_VERSION:
        raise ValueError("validity version disagrees with compiler contract")
    selected_runs = len(expected_symbols) * len(variants)
    execution_cap, _ = effective_variant_cap(policy, selected_runs)
    if int(resource.get("execution_cap", -1)) != execution_cap:
        raise ValueError("execution cap disagrees with resource policy")


def _validate_declared_grid_derivation(definition: Mapping[str, Any]) -> None:
    declared = definition.get("declared_grid")
    if not isinstance(declared, dict):
        raise ValueError("declared-grid family source is missing")
    families = list(declared.get("families") or [])
    grid = declared.get("parameter_grid")
    if not isinstance(grid, dict):
        raise ValueError("declared-grid parameter grid is missing")
    expected_points: list[dict[str, Any]] = []
    for family in families:
        for params in grid.get(family, []):
            expected_points.append(
                {
                    "flat_index": len(expected_points),
                    "family": str(family),
                    "params": dict(params or {}),
                    "pre_disposition": "selected",
                    "reason": "declared_grid_point",
                }
            )
    if definition.get("points") != expected_points:
        raise ValueError("declared-grid points disagree with declared source")
    if definition.get("symbols") != declared.get("symbols"):
        raise ValueError("declared-grid symbols disagree with declared source")
    if definition.get("filters") != declared.get("filters"):
        raise ValueError("declared-grid filters disagree with declared source")


def validate_experiment_spec_binding(spec: Any) -> None:
    definition = spec.search_family_definition
    if list(definition.get("symbols") or []) != list(spec.symbols):
        raise ValueError("ExperimentSpec symbols disagree with search family definition")
    if dict(definition.get("filters") or {}) != dict(spec.filters):
        raise ValueError("ExperimentSpec filters disagree with search family definition")
    resource = dict(definition.get("resource_policy") or {})
    if int(resource.get("execution_cap", -1)) != int(spec.max_runs):
        raise ValueError("ExperimentSpec execution cap disagrees with search family definition")
    expected_grid: dict[str, list[dict[str, Any]]] = {family: [] for family in spec.families}
    origin = definition.get("origin")
    default_family = str((definition.get("raw_sweep_spec") or {}).get("setup_family") or "")
    for point in definition.get("points") or []:
        if point.get("pre_disposition") != "selected":
            continue
        family = str(point.get("family") or default_family)
        expected_grid.setdefault(family, []).append(dict(point.get("params") or {}))
    actual_grid = {
        str(family): [dict(params or {}) for params in variants]
        for family, variants in spec.parameter_grid.items()
    }
    if expected_grid != actual_grid:
        raise ValueError("ExperimentSpec parameter grid disagrees with search family definition")
    binding = dict(definition.get("data_binding") or {})
    if binding.get("status") == "bound" and (
        str(binding.get("snapshot_id") or "") != str(spec.data_snapshot_id or "")
        or str(binding.get("evidence_hash") or "") != str(spec.data_evidence_hash or "")
        or list(binding.get("members") or [])
        != (
            normalize_snapshot_bindings(list(spec.data_snapshot_bindings))
            if spec.data_snapshot_bindings
            else []
        )
    ):
        raise ValueError("ExperimentSpec data identity disagrees with search family definition")
    if origin == "declared_grid":
        declared = dict(definition.get("declared_grid") or {})
        exact = {
            "experiment_id": str(spec.experiment_id),
            "symbols": list(spec.symbols),
            "families": list(spec.families),
            "parameter_grid": dict(spec.parameter_grid),
            "timeframe": str(spec.timeframe),
            "filters": dict(spec.filters),
            "max_runs": int(spec.max_runs),
            "backend": str(spec.backend),
        }
        if declared != exact:
            raise ValueError("declared-grid ExperimentSpec fields disagree with family definition")


def effective_family_n_trials(definition: Mapping[str, Any]) -> int:
    validate_search_family_definition(definition)
    policy = dict(definition.get("family_policy") or {})
    resource = dict(definition.get("resource_policy") or {})
    selected = int(resource.get("selected_run_total") or 0)
    if policy.get("mode") == "confirmatory":
        return 1
    if selected < 1 or not math.isfinite(float(selected)):
        raise ValueError("search family has no selected trials")
    if policy.get("mode") == "cumulative":
        return int(policy.get("parent_effective_n_trials") or 0) + selected
    return selected
