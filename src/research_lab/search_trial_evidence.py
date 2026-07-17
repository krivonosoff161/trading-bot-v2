"""Immutable complete-family and terminal-execution evidence for bounded searches."""

from __future__ import annotations

import hashlib
import inspect
import json
import platform
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping

from src.research_lab.experiment import ExperimentSpec, RunResult
from src.research_lab.gpu_runtime import GPU_SUPPORTED_FAMILIES, auto_gpu_worthwhile
from src.research_lab.research_envelope import research_code_identity
from src.research_lab.search_family_definition import (
    content_hash,
    effective_family_n_trials,
    validate_search_family_definition,
)
from src.research_lab.strategy_registry import get_strategy


SCHEMA = "SearchTrialEvidence.v2"
LEGACY_SCHEMA = "SearchTrialEvidence.v1"
TERMINAL_STATUSES = {
    "evaluated",
    "data_gate",
    "error",
    "execution_cap",
    "missing_terminal",
}


def _normalized_params(family: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
    defaults = dict(get_strategy(family).parameter_defaults)
    return {**defaults, **dict(params or {})}


def _selected_points(spec: ExperimentSpec) -> list[dict[str, Any]]:
    definition = spec.search_family_definition
    origin = definition.get("origin")
    default_family = str((definition.get("raw_sweep_spec") or {}).get("setup_family") or "")
    points = []
    for point in definition.get("points") or []:
        if point.get("pre_disposition") != "selected":
            continue
        family = str(point.get("family") or default_family)
        if not family:
            raise ValueError("selected search-family point has no family")
        points.append(
            {
                "flat_index": int(point["flat_index"]),
                "family": family,
                "params": _normalized_params(family, point.get("params") or {}),
                "origin": origin,
            }
        )
    return points


def _execution_key(symbol: str, family: str, params: Mapping[str, Any]) -> str:
    return content_hash(
        {"symbol": str(symbol), "family": str(family), "params": dict(params)}
    )


def _expected_execution_identity_rows(
    definition: Mapping[str, Any],
    family_id: str,
) -> list[dict[str, Any]]:
    default_family = str((definition.get("raw_sweep_spec") or {}).get("setup_family") or "")
    selected = [
        point
        for point in definition.get("points") or []
        if point.get("pre_disposition") == "selected"
    ]
    expected: list[dict[str, Any]] = []
    for symbol in definition.get("symbols") or []:
        for point in selected:
            family = str(point.get("family") or default_family)
            flat_index = int(point["flat_index"])
            expected.append(
                {
                    "ordinal": len(expected),
                    "execution_id": f"stept_{content_hash({'family_id': family_id, 'symbol': symbol, 'flat_index': flat_index})}",
                    "flat_index": flat_index,
                    "symbol": str(symbol),
                    "family": family,
                    "params": _normalized_params(family, point.get("params") or {}),
                }
            )
    return expected


def _runtime_identity(spec: ExperimentSpec, runtime_meta: Mapping[str, Any]) -> dict[str, Any]:
    backend_name = str(runtime_meta.get("backend_name") or "numpy")
    if backend_name in {"none", "cpu"}:
        backend_name = "numpy"
    return {
        "requested_backend": spec.backend,
        "effective_backend": str(runtime_meta.get("effective_backend") or "unknown"),
        "resolved_backend": str(runtime_meta.get("resolved_backend") or "unknown"),
        "signal_backend": str(runtime_meta.get("signal_backend") or "unknown"),
        "simulation_backend": str(runtime_meta.get("simulation_backend") or "unknown"),
        "backend_name": backend_name,
        "backend_library_version": _library_version(backend_name),
        "numpy_version": _library_version("numpy"),
        "python_version": platform.python_version(),
        "simulator_identity": research_code_identity(),
    }


def _library_version(name: str) -> str:
    candidates = {
        "cupy": ("cupy-cuda12x", "cupy-cuda11x", "cupy"),
        "numba.cuda": ("numba",),
    }.get(name, (name,))
    for candidate in candidates:
        try:
            return metadata.version(candidate)
        except metadata.PackageNotFoundError:
            continue
    return "unknown"


def _full_file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


def execution_code_identity(families: list[str]) -> dict[str, Any]:
    root = Path(__file__).resolve().parent
    sources = {
        name: _full_file_digest(root / name)
        for name in (
            "experiment.py",
            "gpu_kernels.py",
            "gpu_runtime.py",
            "gpu_simulator.py",
            "search_trial_evidence.py",
            "strategy_registry.py",
            "trade_math.py",
        )
    }
    generators: dict[str, dict[str, str]] = {}
    for family in sorted(set(families)):
        source = inspect.getsourcefile(get_strategy(family).generate_signals)
        path = Path(source).resolve() if source else Path("<missing>")
        generators[family] = {
            "module": str(get_strategy(family).generate_signals.__module__),
            "source_file": path.name,
            "sha256": _full_file_digest(path),
        }
    return {
        "schema": "SearchExecutionCodeIdentity.v1",
        "runtime_sources": sources,
        "strategy_generators": generators,
    }


def _per_execution_identity(
    raw: Mapping[str, Any] | None,
    *,
    family: str,
    code_identity: Mapping[str, Any],
) -> dict[str, Any]:
    value = dict(raw or {})
    backend_name = str(value.get("backend_name") or "unknown")
    signal_kernel = str(value.get("signal_kernel") or "unknown")
    simulator = str(value.get("simulator") or "unknown")
    sources = dict(code_identity.get("runtime_sources") or {})
    generators = dict(code_identity.get("strategy_generators") or {})
    if signal_kernel == "gpu_kernels":
        signal_digest = str(sources.get("gpu_kernels.py") or "missing")
    elif signal_kernel == "strategy_generator":
        signal_digest = str((generators.get(family) or {}).get("sha256") or "missing")
    elif signal_kernel.startswith("not_executed"):
        signal_digest = "not_executed"
    else:
        signal_digest = "unknown"
    if simulator == "gpu_simulator":
        simulator_digest = str(sources.get("gpu_simulator.py") or "missing")
    elif simulator == "cpu_simulator":
        simulator_digest = str(sources.get("experiment.py") or "missing")
    elif simulator.startswith("not_executed"):
        simulator_digest = "not_executed"
    else:
        simulator_digest = "unknown"
    return {
        "requested_backend": str(value.get("requested_backend") or "unknown"),
        "resolved_backend": str(value.get("resolved_backend") or "unknown"),
        "backend_name": backend_name,
        "backend_library_version": (
            "not_executed"
            if backend_name == "not_executed"
            else _library_version(backend_name)
        ),
        "numpy_version": _library_version("numpy"),
        "python_version": platform.python_version(),
        "signal_backend": str(value.get("signal_backend") or "unknown"),
        "signal_kernel": signal_kernel,
        "signal_kernel_sha256": signal_digest,
        "signal_backend_reason": str(value.get("signal_backend_reason") or "unknown"),
        "signal_candle_count": int(value.get("signal_candle_count") or 0),
        "signal_family_variant_count": int(
            value.get("signal_family_variant_count") or 0
        ),
        "simulation_backend": str(value.get("simulation_backend") or "unknown"),
        "simulator": simulator,
        "simulator_sha256": simulator_digest,
        "terminal_phase": str(value.get("terminal_phase") or "unknown"),
    }


def _validate_per_execution_semantics(
    identity: Mapping[str, Any],
    *,
    status: str,
    family: str,
    expected_candle_count: int,
    expected_family_variant_count: int,
) -> None:
    requested = str(identity.get("requested_backend") or "")
    resolved = str(identity.get("resolved_backend") or "")
    backend_name = str(identity.get("backend_name") or "")
    signal_backend = str(identity.get("signal_backend") or "")
    signal_kernel = str(identity.get("signal_kernel") or "")
    signal_reason = str(identity.get("signal_backend_reason") or "")
    candle_count = int(identity.get("signal_candle_count") or 0)
    family_variant_count = int(identity.get("signal_family_variant_count") or 0)
    simulation_backend = str(identity.get("simulation_backend") or "")
    simulator = str(identity.get("simulator") or "")
    phase = str(identity.get("terminal_phase") or "")
    if requested not in {"cpu", "gpu", "auto"}:
        raise ValueError("search trial requested backend is invalid")
    if status in {"execution_cap", "missing_terminal"}:
        expected_suffix = "resource_cap" if status == "execution_cap" else "missing_terminal"
        if (
            resolved != "not_executed"
            or backend_name != "not_executed"
            or signal_backend != "not_executed"
            or signal_kernel != f"not_executed_{expected_suffix}"
            or signal_reason
            != ("resource_execution_cap" if status == "execution_cap" else "missing_terminal")
            or simulation_backend != "not_executed"
            or simulator != f"not_executed_{expected_suffix}"
            or phase != status
        ):
            raise ValueError("unattempted trial has inconsistent execution identity")
        return
    allowed_resolved = {"cpu"} if requested == "cpu" else {"gpu"} if requested == "gpu" else {"cpu", "gpu"}
    if resolved not in allowed_resolved:
        raise ValueError("search trial resolved backend contradicts requested backend")
    if status == "data_gate":
        if (
            backend_name != "not_executed"
            or signal_backend != "not_executed"
            or signal_kernel != "not_executed_data_gate"
            or signal_reason != "data_gate"
            or simulation_backend != "not_executed"
            or simulator != "not_executed_data_gate"
            or phase != "data_gate"
        ):
            raise ValueError("data-gated trial has inconsistent execution identity")
    else:
        if candle_count != expected_candle_count or family_variant_count != expected_family_variant_count:
            raise ValueError("search trial signal workload disagrees with immutable family/data")
    if status == "data_gate":
        if candle_count != expected_candle_count or family_variant_count != expected_family_variant_count:
            raise ValueError("data-gated signal workload disagrees with immutable family/data")
        return
    valid_signal = {
        "cpu": "strategy_generator",
        "gpu": "gpu_kernels",
    }
    if signal_backend not in valid_signal or signal_kernel != valid_signal[signal_backend]:
        raise ValueError("search trial signal backend/kernel identity mismatch")
    gpu_supported = family in GPU_SUPPORTED_FAMILIES
    if resolved == "cpu":
        expected_signal, expected_reason = "cpu", "resolved_cpu"
    elif not gpu_supported:
        expected_signal, expected_reason = "cpu", "unsupported_family"
    elif requested == "gpu":
        expected_signal, expected_reason = "gpu", "gpu_eligible"
    elif auto_gpu_worthwhile(candle_count, family_variant_count):
        expected_signal, expected_reason = "gpu", "gpu_eligible"
    else:
        expected_signal, expected_reason = "cpu", "auto_batch_too_small"
    if signal_backend != expected_signal or signal_reason != expected_reason:
        raise ValueError("search trial signal path contradicts deterministic GPU eligibility")
    valid_simulator = {"cpu": "cpu_simulator", "gpu": "gpu_simulator"}
    if status == "evaluated":
        if (
            simulation_backend not in valid_simulator
            or simulator != valid_simulator[simulation_backend]
            or phase != "completed"
        ):
            raise ValueError("evaluated trial has inconsistent execution identity")
    elif status == "error":
        if phase == "signal_generation":
            if (
                simulation_backend != "not_executed"
                or simulator != "not_executed_before_simulation"
            ):
                raise ValueError("signal-error trial has inconsistent execution identity")
        elif phase == "simulation":
            if (
                simulation_backend not in valid_simulator
                or simulator != valid_simulator[simulation_backend]
            ):
                raise ValueError("simulation-error trial has inconsistent execution identity")
        else:
            raise ValueError("error trial has inconsistent terminal phase")
    else:
        raise ValueError("unsupported terminal status for execution identity")
    uses_gpu = signal_backend == "gpu" or simulation_backend == "gpu"
    if uses_gpu and (resolved != "gpu" or backend_name != "cupy"):
        raise ValueError("GPU execution identity has inconsistent runtime backend")
    if not uses_gpu and backend_name != "numpy":
        raise ValueError("CPU execution identity has inconsistent runtime backend")


def build_search_trial_evidence(
    spec: ExperimentSpec,
    results: list[RunResult],
    runtime_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_search_family_definition(
        spec.search_family_definition,
        expected_id=spec.search_family_id,
    )
    runtime = dict(runtime_meta or {})
    code_identity = execution_code_identity(spec.families)
    selected_points = _selected_points(spec)
    result_by_key: dict[str, RunResult] = {}
    gated_scopes: set[tuple[str, str]] = set()
    for result in results:
        if result.decision.startswith("NEEDS_") and not result.params:
            gated_scopes.add((result.symbol, result.family))
            continue
        key = _execution_key(
            result.symbol,
            result.family,
            _normalized_params(result.family, result.params),
        )
        if key in result_by_key:
            raise ValueError("duplicate terminal search-trial result")
        result_by_key[key] = result

    executions: list[dict[str, Any]] = []
    ordinal = 0
    execution_cap = int(spec.max_runs or 0)
    family_binding = dict(spec.search_family_definition.get("data_binding") or {})
    for symbol in spec.symbols:
        for point in selected_points:
            family = point["family"]
            if family not in spec.families:
                raise ValueError("search family point is outside ExperimentSpec families")
            params = dict(point["params"])
            key = _execution_key(symbol, family, params)
            result = result_by_key.pop(key, None)
            row = {
                "execution_id": f"stept_{content_hash({'family_id': spec.search_family_id, 'symbol': symbol, 'flat_index': point['flat_index']})}",
                "ordinal": ordinal,
                "flat_index": point["flat_index"],
                "symbol": symbol,
                "family": family,
                "params": params,
                "run_id": "",
                "terminal_disposition": "",
                "reason": "",
                "decision": "",
                "validation_status": "",
                "data_snapshot_id": "",
                "data_evidence_hash": "",
                "family_data_snapshot_id": str(family_binding.get("snapshot_id") or ""),
                "family_data_evidence_hash": str(family_binding.get("evidence_hash") or ""),
                "data_binding_status": "legacy_unknown",
                "execution_identity": {},
            }
            if result is not None:
                is_error = result.decision == "ERROR" or result.validation_status == "ERROR"
                is_data_gate = result.decision.startswith("NEEDS_")
                row.update(
                    {
                        "run_id": result.run_id,
                        "terminal_disposition": (
                            "error" if is_error else "data_gate" if is_data_gate else "evaluated"
                        ),
                        "reason": (
                            ";".join(result.reasons) or "execution_error"
                            if is_error
                            else result.decision.lower()
                            if is_data_gate
                            else "completed_evaluation"
                        ),
                        "decision": result.decision,
                        "validation_status": result.validation_status,
                        "data_snapshot_id": str(result.metrics.get("data_snapshot_id") or ""),
                        "data_evidence_hash": str(
                            result.metrics.get("data_evidence_hash")
                            or result.metrics.get("data_fingerprint")
                            or ""
                        ),
                        "family_data_snapshot_id": str(
                            result.metrics.get("family_data_snapshot_id") or ""
                        ),
                        "family_data_evidence_hash": str(
                            result.metrics.get("family_data_evidence_hash") or ""
                        ),
                        "execution_identity": _per_execution_identity(
                            result.metrics.get("execution_identity"),
                            family=family,
                            code_identity=code_identity,
                        ),
                    }
                )
            elif (symbol, family) in gated_scopes:
                gate = next(
                    result
                    for result in results
                    if result.symbol == symbol
                    and result.family == family
                    and result.decision.startswith("NEEDS_")
                    and not result.params
                )
                row.update(
                    {
                        "run_id": gate.run_id,
                        "terminal_disposition": "data_gate",
                        "reason": gate.decision.lower(),
                        "decision": gate.decision,
                        "validation_status": gate.validation_status,
                        "data_snapshot_id": str(gate.metrics.get("data_snapshot_id") or ""),
                        "data_evidence_hash": str(
                            gate.metrics.get("data_evidence_hash")
                            or gate.metrics.get("data_fingerprint")
                            or ""
                        ),
                        "family_data_snapshot_id": str(
                            gate.metrics.get("family_data_snapshot_id") or ""
                        ),
                        "family_data_evidence_hash": str(
                            gate.metrics.get("family_data_evidence_hash") or ""
                        ),
                        "execution_identity": _per_execution_identity(
                            gate.metrics.get("execution_identity"),
                            family=family,
                            code_identity=code_identity,
                        ),
                    }
                )
            elif execution_cap and ordinal >= execution_cap:
                row.update(
                    {
                        "terminal_disposition": "execution_cap",
                        "reason": "resource_execution_cap",
                        "execution_identity": _per_execution_identity(
                            {
                                "requested_backend": spec.backend,
                                "resolved_backend": "not_executed",
                                "backend_name": "not_executed",
                                "signal_backend": "not_executed",
                                "signal_kernel": "not_executed_resource_cap",
                                "signal_backend_reason": "resource_execution_cap",
                                "simulation_backend": "not_executed",
                                "simulator": "not_executed_resource_cap",
                                "terminal_phase": "execution_cap",
                            },
                            family=family,
                            code_identity=code_identity,
                        ),
                    }
                )
            else:
                row.update(
                    {
                        "terminal_disposition": "missing_terminal",
                        "reason": "selected_execution_has_no_terminal_evidence",
                        "execution_identity": _per_execution_identity(
                            {
                                "requested_backend": spec.backend,
                                "resolved_backend": "not_executed",
                                "backend_name": "not_executed",
                                "signal_backend": "not_executed",
                                "signal_kernel": "not_executed_missing_terminal",
                                "signal_backend_reason": "missing_terminal",
                                "simulation_backend": "not_executed",
                                "simulator": "not_executed_missing_terminal",
                                "terminal_phase": "missing_terminal",
                            },
                            family=family,
                            code_identity=code_identity,
                        ),
                    }
                )
            if (
                row["data_snapshot_id"]
                and row["data_evidence_hash"]
                and row["family_data_snapshot_id"]
                and row["family_data_evidence_hash"]
            ):
                row["data_binding_status"] = "bound"
            executions.append(row)
            ordinal += 1
    if result_by_key:
        raise ValueError("result is outside the immutable search family")

    pre_counts = validate_search_family_definition(spec.search_family_definition)
    counts = {
        **pre_counts,
        "selected_executions": len(executions),
        "attempted_executions": sum(
            row["terminal_disposition"] in {"evaluated", "data_gate", "error"}
            for row in executions
        ),
        "evaluated": sum(row["terminal_disposition"] == "evaluated" for row in executions),
        "data_gates": sum(row["terminal_disposition"] == "data_gate" for row in executions),
        "errors": sum(row["terminal_disposition"] == "error" for row in executions),
        "execution_cap": sum(
            row["terminal_disposition"] == "execution_cap" for row in executions
        ),
        "missing_terminal": sum(
            row["terminal_disposition"] == "missing_terminal" for row in executions
        ),
    }
    counts["not_evaluated"] = (
        counts["data_gates"]
        + counts["errors"]
        + counts["execution_cap"]
        + counts["missing_terminal"]
    )
    counts["effective_n_trials"] = effective_family_n_trials(spec.search_family_definition)
    payload = {
        "schema": SCHEMA,
        "legacy_classification": "complete_family",
        "experiment_id": spec.experiment_id,
        "search_family_id": spec.search_family_id,
        "multiple_testing_family_hash": spec.search_family_id,
        "search_family_definition": spec.search_family_definition,
        "search_space": counts,
        "runtime": runtime,
        "execution_identity": _runtime_identity(spec, runtime),
        "code_identity": code_identity,
        "trials": executions,
        "paper_only": True,
        "execution_allowed": False,
    }
    payload["search_trial_evidence_id"] = f"ste_{content_hash(payload)}"
    validate_search_trial_evidence(payload)
    return payload


def validate_search_trial_evidence(
    evidence: Mapping[str, Any],
    *,
    require_complete: bool = False,
) -> dict[str, int]:
    value = dict(evidence)
    if value.get("schema") == LEGACY_SCHEMA:
        raise ValueError("legacy search evidence is compiled_subspace_only")
    if value.get("schema") != SCHEMA:
        raise ValueError("unsupported search trial evidence schema")
    definition = value.get("search_family_definition")
    family_id = str(value.get("search_family_id") or "")
    if not isinstance(definition, dict) or not family_id:
        raise ValueError("search trial evidence has no bound family definition")
    validate_search_family_definition(definition, expected_id=family_id)
    families = sorted(
        {
            str(point.get("family") or "")
            for point in definition.get("points") or []
            if point.get("family")
        }
    )
    default_family = str((definition.get("raw_sweep_spec") or {}).get("setup_family") or "")
    if default_family:
        families.append(default_family)
    if value.get("code_identity") != execution_code_identity(families):
        raise ValueError("historical search execution code is unavailable")
    expected_evidence_id = str(value.pop("search_trial_evidence_id", ""))
    actual_evidence_id = f"ste_{content_hash(value)}"
    if expected_evidence_id != actual_evidence_id:
        raise ValueError("search trial evidence id mismatch")
    trials = evidence.get("trials")
    if not isinstance(trials, list):
        raise ValueError("search trial ledger is missing")
    ids = [str(row.get("execution_id") or "") for row in trials if isinstance(row, dict)]
    if len(ids) != len(trials) or not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("search trial execution ids are invalid")
    expected_rows = _expected_execution_identity_rows(definition, family_id)
    if len(trials) != len(expected_rows):
        raise ValueError("search trial ledger does not cover every selected execution")
    identity_fields = (
        "ordinal",
        "execution_id",
        "flat_index",
        "symbol",
        "family",
        "params",
    )
    for actual, expected in zip(trials, expected_rows):
        if not isinstance(actual, dict) or any(
            actual.get(field) != expected[field] for field in identity_fields
        ):
            raise ValueError("search trial row identity disagrees with immutable family")
    requested_backend = str(
        (definition.get("raw_sweep_spec") or {}).get("backend")
        or (definition.get("declared_grid") or {}).get("backend")
        or "unknown"
    )
    binding_members = {
        str(item.get("symbol") or ""): int(item.get("row_count") or 0)
        for item in (definition.get("data_binding") or {}).get("members") or []
    }
    selected_variant_counts: dict[str, int] = {}
    default_selected_family = str(
        (definition.get("raw_sweep_spec") or {}).get("setup_family") or ""
    )
    for point in definition.get("points") or []:
        if point.get("pre_disposition") != "selected":
            continue
        selected_family = str(point.get("family") or default_selected_family)
        selected_variant_counts[selected_family] = (
            selected_variant_counts.get(selected_family, 0) + 1
        )
    current_code_identity = execution_code_identity(families)
    for row in trials:
        execution_identity = row.get("execution_identity")
        if not isinstance(execution_identity, dict):
            raise ValueError("search trial has no per-execution runtime identity")
        if str(execution_identity.get("requested_backend") or "") != requested_backend:
            raise ValueError("search trial requested backend disagrees with family")
        expected_identity = _per_execution_identity(
            execution_identity,
            family=str(row.get("family") or ""),
            code_identity=current_code_identity,
        )
        if execution_identity != expected_identity:
            raise ValueError("search trial backend/kernel/simulator identity mismatch")
        _validate_per_execution_semantics(
            execution_identity,
            status=str(row.get("terminal_disposition") or ""),
            family=str(row.get("family") or ""),
            expected_candle_count=(
                binding_members.get(str(row.get("symbol") or ""))
                or int(execution_identity.get("signal_candle_count") or 0)
            ),
            expected_family_variant_count=selected_variant_counts.get(
                str(row.get("family") or ""), 0
            ),
        )
    statuses = [str(row.get("terminal_disposition") or "") for row in trials]
    if any(status not in TERMINAL_STATUSES for status in statuses):
        raise ValueError("invalid terminal search-trial disposition")
    pre_counts = validate_search_family_definition(definition, expected_id=family_id)
    expected_counts = {
        **pre_counts,
        "selected_executions": len(trials),
        "attempted_executions": sum(
            status in {"evaluated", "data_gate", "error"} for status in statuses
        ),
        "evaluated": statuses.count("evaluated"),
        "data_gates": statuses.count("data_gate"),
        "errors": statuses.count("error"),
        "execution_cap": statuses.count("execution_cap"),
        "missing_terminal": statuses.count("missing_terminal"),
    }
    expected_counts["not_evaluated"] = (
        expected_counts["data_gates"]
        + expected_counts["errors"]
        + expected_counts["execution_cap"]
        + expected_counts["missing_terminal"]
    )
    expected_counts["effective_n_trials"] = effective_family_n_trials(definition)
    counts = evidence.get("search_space")
    if not isinstance(counts, dict):
        raise ValueError("search trial aggregate counts are missing")
    for key, expected in expected_counts.items():
        if int(counts.get(key, -1)) != int(expected):
            raise ValueError(f"search trial aggregate mismatch: {key}")
    for row in trials:
        status = str(row.get("terminal_disposition") or "")
        per_execution = dict(row.get("execution_identity") or {})
        if status in {"evaluated", "data_gate", "error"} and not str(
            row.get("run_id") or ""
        ):
            raise ValueError("attempted search trial has no terminal run id")
        if status in {"execution_cap", "missing_terminal"} and any(
            row.get(key) for key in ("run_id", "decision", "validation_status")
        ):
            raise ValueError("unattempted search trial carries terminal execution fields")
        if status == "evaluated" and (
            per_execution.get("terminal_phase") != "completed"
            or per_execution.get("signal_backend") not in {"cpu", "gpu"}
            or per_execution.get("simulation_backend") not in {"cpu", "gpu"}
        ):
            raise ValueError("evaluated trial has inconsistent execution identity")
        if status == "data_gate" and (
            per_execution.get("terminal_phase") != "data_gate"
            or per_execution.get("signal_backend") != "not_executed"
            or per_execution.get("simulation_backend") != "not_executed"
        ):
            raise ValueError("data-gated trial has inconsistent execution identity")
        if status in {"execution_cap", "missing_terminal"} and (
            per_execution.get("terminal_phase") != status
            or per_execution.get("signal_backend") != "not_executed"
            or per_execution.get("simulation_backend") != "not_executed"
        ):
            raise ValueError("unattempted trial has inconsistent execution identity")
    runtime = evidence.get("runtime") if isinstance(evidence.get("runtime"), dict) else {}
    identity = (
        evidence.get("execution_identity")
        if isinstance(evidence.get("execution_identity"), dict)
        else {}
    )
    if identity.get("simulator_identity") != research_code_identity():
        raise ValueError("historical simulator identity is unavailable")
    backend_name = str(identity.get("backend_name") or "unknown")
    if backend_name not in {"", "unknown"} and str(
        identity.get("backend_library_version") or ""
    ) != _library_version(backend_name):
        raise ValueError("aggregate backend library identity mismatch")
    if str(identity.get("numpy_version") or "") not in {"", "unknown"} and str(
        identity.get("numpy_version")
    ) != _library_version("numpy"):
        raise ValueError("aggregate numpy library identity mismatch")
    if str(identity.get("python_version") or "") not in {"", "unknown"} and str(
        identity.get("python_version")
    ) != platform.python_version():
        raise ValueError("aggregate Python runtime identity mismatch")
    producer_count = runtime.get("n_variants_evaluated")
    if producer_count is not None and int(producer_count) != expected_counts["attempted_executions"]:
        raise ValueError("producer attempted-trial count mismatch")
    if require_complete:
        if dict(definition.get("data_binding") or {}).get("status") != "bound":
            raise ValueError("search family data snapshot identity is incomplete")
        if requested_backend == "auto" and not binding_members:
            raise ValueError("auto backend evidence requires bound candle row counts")
        if expected_counts["missing_terminal"]:
            raise ValueError("search family has missing terminal executions")
        incomplete = [
            row["execution_id"]
            for row in trials
            if row["terminal_disposition"] != "execution_cap"
            and row.get("data_binding_status") != "bound"
        ]
        if incomplete:
            raise ValueError("search trial data snapshot identity is incomplete")
        incomplete_execution_identity = [
            row["execution_id"]
            for row in trials
            if row["terminal_disposition"] != "execution_cap"
            and any(
                str((row.get("execution_identity") or {}).get(key) or "")
                in {"", "unknown"}
                for key in (
                    "requested_backend",
                    "resolved_backend",
                    "backend_name",
                    "backend_library_version",
                    "numpy_version",
                    "python_version",
                    "signal_backend",
                    "signal_kernel",
                    "signal_kernel_sha256",
                    "signal_backend_reason",
                    "signal_candle_count",
                    "signal_family_variant_count",
                    "simulation_backend",
                    "simulator",
                    "simulator_sha256",
                    "terminal_phase",
                )
            )
        ]
        if incomplete_execution_identity:
            raise ValueError("search trial per-execution identity is incomplete")
        if any(
            str(identity.get(key) or "") in {"", "unknown"}
            for key in ("effective_backend", "signal_backend", "simulation_backend")
        ):
            raise ValueError("search trial backend/simulator identity is incomplete")
        if any(
            str(identity.get(key) or "") in {"", "unknown"}
            for key in (
                "backend_name",
                "backend_library_version",
                "numpy_version",
                "python_version",
            )
        ):
            raise ValueError("search trial runtime library identity is incomplete")
    binding = dict(definition.get("data_binding") or {})
    if binding.get("status") == "bound":
        members = {
            (str(item.get("symbol") or ""), str(item.get("timeframe") or "")): item
            for item in binding.get("members") or []
        }
        timeframe = str(
            (definition.get("raw_sweep_spec") or {}).get("timeframe")
            or (definition.get("declared_grid") or {}).get("timeframe")
            or ""
        )
        for row in trials:
            if (
                str(row.get("family_data_snapshot_id") or "")
                != str(binding.get("snapshot_id") or "")
                or str(row.get("family_data_evidence_hash") or "")
                != str(binding.get("evidence_hash") or "")
            ):
                raise ValueError("search trial data identity disagrees with bound family")
            if row["terminal_disposition"] == "execution_cap":
                continue
            expected_member = members.get((str(row.get("symbol") or ""), timeframe))
            expected_snapshot_id = (
                str(expected_member.get("snapshot_id") or "")
                if expected_member
                else str(binding.get("snapshot_id") or "")
            )
            expected_evidence_hash = (
                str(expected_member.get("evidence_hash") or "")
                if expected_member
                else str(binding.get("evidence_hash") or "")
            )
            if (
                str(row.get("data_snapshot_id") or "") != expected_snapshot_id
                or str(row.get("data_evidence_hash") or "") != expected_evidence_hash
            ):
                raise ValueError("search trial actual data identity disagrees with family member")
    return {key: int(value) for key, value in expected_counts.items()}


def effective_n_trials_from_evidence(
    evidence: Mapping[str, Any],
    *,
    require_complete: bool = True,
) -> int:
    counts = validate_search_trial_evidence(evidence, require_complete=require_complete)
    return counts["effective_n_trials"]


def classify_search_trial_evidence(evidence: Mapping[str, Any]) -> str:
    if evidence.get("schema") == LEGACY_SCHEMA:
        return "compiled_subspace_only"
    if evidence.get("schema") == SCHEMA:
        validate_search_trial_evidence(evidence)
        return "complete_family"
    return "unknown_schema"


def search_trial_evidence_migration_report(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Classify synthetic legacy input without inventing unavailable family history."""
    classification = classify_search_trial_evidence(evidence)
    missing = []
    if classification == "compiled_subspace_only":
        missing = [
            "raw_axes",
            "sampler_seed_and_digest",
            "invalid_point_dispositions",
            "family_parentage",
        ]
    return {
        "schema": "SearchTrialEvidenceMigrationReport.v1",
        "source_schema": str(evidence.get("schema") or ""),
        "classification": classification,
        "v2_consumable": classification == "complete_family",
        "missing_not_inferred": missing,
    }


def read_search_trial_evidence(
    path: Path,
    *,
    accepted_schema: str,
) -> dict[str, Any]:
    """Read only the explicitly selected schema, enabling deletion-free rollback."""
    if accepted_schema not in {SCHEMA, LEGACY_SCHEMA}:
        raise ValueError("unsupported explicit search evidence reader schema")
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != accepted_schema:
        raise ValueError("search evidence schema does not match explicit reader selection")
    if accepted_schema == SCHEMA:
        validate_search_trial_evidence(value)
    return value


def write_search_trial_evidence(run_dir: Path, evidence: dict[str, Any]) -> Path:
    validate_search_trial_evidence(evidence)
    path = Path(run_dir) / "search_trial_evidence.json"
    payload = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != payload:
        raise ValueError("immutable search trial evidence collision")
    if not path.exists():
        temporary = path.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
    return path
