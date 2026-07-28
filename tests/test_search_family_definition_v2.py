from __future__ import annotations

import json

import pytest
import src.research_lab.search_trial_evidence as evidence_module

from scripts.strategy_lab.apply_feedback_recommendations import _exp_to_dict as feedback_exp_to_dict
from scripts.strategy_lab.generate_event_sweeps import _exp_to_dict as event_exp_to_dict
from scripts.strategy_lab.queue_validated_proposals import _exp_to_dict as proposal_exp_to_dict
from src.research_lab.experiment import ExperimentSpec, RunResult
from src.research_lab.farm_sweep_runner import build_sweep_spec
from src.research_lab.hard_validation_export import (
    _build_candidate,
    _comparable_trial_panel,
    export_requests,
)
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.search_family_definition import (
    LEGACY_SCHEMA,
    content_hash,
    effective_family_n_trials,
    family_definition_id,
    validate_search_family_definition,
)
from src.research_lab.search_trial_evidence import (
    build_search_trial_evidence,
    classify_search_trial_evidence,
    read_search_trial_evidence,
    search_trial_evidence_migration_report,
    validate_search_trial_evidence,
)
from src.research_lab.sweep_compile import compile_sweep, expand_grids_bounded
from src.research_lab.sweep_spec import SweepSpec
from src.research_lab.timeframes import load_timeframe_profiles


DATA_GLOB = "data/{symbol}_*.json"


def _compile(spec: SweepSpec):
    return compile_sweep(
        spec,
        data_glob=DATA_GLOB,
        timeframe_profiles=load_timeframe_profiles(),
        resource_policy=load_resource_policy(),
    )


def _rsi_spec(sweep_id: str, oversold: list[int]) -> SweepSpec:
    return SweepSpec(
        sweep_id=sweep_id,
        anchor_symbol="BTC_USDT_SWAP",
        related_symbols=(),
        timeframe="15m",
        setup_family="rsi_reversal",
        setup_grid={"oversold": oversold, "overbought": [70]},
        max_variants=1,
        backend="cpu",
        resource_class="light",
    )


def test_raw_axis_change_with_same_selected_rows_changes_family_id() -> None:
    with_invalid = _compile(_rsi_spec("same", [30, 80]))
    without_invalid = _compile(_rsi_spec("same", [30]))

    assert with_invalid.parameter_grid == without_invalid.parameter_grid
    assert with_invalid.search_family_id != without_invalid.search_family_id


def test_baseline_only_sweep_has_one_complete_point() -> None:
    spec = SweepSpec(
        sweep_id="baseline-only",
        anchor_symbol="BTC_USDT_SWAP",
        related_symbols=(),
        timeframe="15m",
        setup_family="momentum_breakout",
        max_variants=1,
        backend="cpu",
        resource_class="light",
    )
    exp = _compile(spec)
    assert exp.parameter_grid == {"momentum_breakout": [{}]}
    assert exp.search_family_definition["points"] == [
        {
            "flat_index": 0,
            "params": {},
            "pre_disposition": "selected",
            "reason": "selected_by_sampler",
        }
    ]


def test_invalid_points_preserve_flat_index_and_reason() -> None:
    exp = _compile(_rsi_spec("invalid-ledger", [30, 80]))

    ledger = exp.search_family_definition["point_ledger"]
    assert ledger["record_count"] == 2
    assert ledger["disposition_counts"]["dependency_invalid"] == 1
    assert ledger["reason_counts"]["oversold_must_be_less_than_overbought"] == 1


def test_raw_sweep_tampering_fails_even_with_recomputed_family_id() -> None:
    exp = _compile(_rsi_spec("raw-tamper", [30, 80]))
    definition = json.loads(json.dumps(exp.search_family_definition))
    definition["raw_sweep_spec"]["setup_grid"]["oversold"] = [30]

    with pytest.raises(ValueError, match="point ledger disagrees with raw sweep"):
        validate_search_family_definition(
            definition,
            expected_id=family_definition_id(definition),
        )


def test_compact_point_ledger_tampering_fails_with_recomputed_family_id() -> None:
    exp = _compile(_rsi_spec("ledger-tamper", [30, 80]))
    definition = json.loads(json.dumps(exp.search_family_definition))
    definition["point_ledger"]["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="point ledger disagrees with raw sweep"):
        validate_search_family_definition(
            definition,
            expected_id=family_definition_id(definition),
        )


def test_legacy_v2_full_ledger_remains_replayable_after_v3_compaction() -> None:
    spec = _rsi_spec("legacy-v2", [30, 80])
    exp = _compile(spec)
    audit: dict = {}
    expand_grids_bounded(
        spec.setup_grid,
        spec.entry_grid,
        spec.exit_grid,
        cap=1,
        seed_material=f"{spec.sweep_id}|{spec.setup_family}|{spec.timeframe}",
        baseline={"oversold": 30, "overbought": 70},
        strategy_id=spec.setup_family,
        audit=audit,
        audit_format="legacy_full",
    )
    definition = json.loads(json.dumps(exp.search_family_definition))
    definition["schema"] = LEGACY_SCHEMA
    definition["compiler"]["version"] = "bounded-cartesian/v2"
    definition["points"] = audit["points"]
    definition.pop("point_ledger")
    definition.pop("selected_points", None)

    counts = validate_search_family_definition(
        definition,
        expected_id=family_definition_id(definition),
    )

    assert counts["raw_points"] == 2
    assert counts["dependency_invalid"] == 1


def test_large_production_sweep_uses_bounded_compact_ledger() -> None:
    spec = build_sweep_spec(
        "BTC_USDT_SWAP",
        "1h",
        "bb_volume_fade",
        fingerprint="fingerprint",
        tier="normal",
    )
    exp = _compile(spec)
    encoded = json.dumps(
        exp.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    ledger = exp.search_family_definition["point_ledger"]

    assert ledger["record_count"] > 100_000
    assert ledger["disposition_counts"]["selected"] <= 48
    assert len(exp.search_family_definition["points"]) <= 48
    assert "selected_points" not in exp.search_family_definition
    assert "selected_points" not in exp.plan_meta["search_space"]["point_ledger"]
    assert len(encoded) < 250_000


def test_selection_policy_and_timeframe_profile_are_fully_bound() -> None:
    exp = _compile(_rsi_spec("policy-binding", [30]))
    resource = exp.search_family_definition["resource_policy"]

    assert resource["policy"] == load_resource_policy().__dict__
    assert resource["timeframe_profile"] == load_timeframe_profiles().get("15m").__dict__
    assert len(resource["selection_policy_digest"]) == 64

    definition = json.loads(json.dumps(exp.search_family_definition))
    definition["resource_policy"]["timeframe_profile"]["max_variants_per_setup"] = 0
    with pytest.raises(ValueError, match="effective variant cap"):
        validate_search_family_definition(
            definition,
            expected_id=family_definition_id(definition),
        )


def test_supported_serializer_preserves_exact_family_definition() -> None:
    exp = _compile(_rsi_spec("serializer", [30, 80]))
    for serializer in (event_exp_to_dict, feedback_exp_to_dict, proposal_exp_to_dict):
        payload = serializer(exp)
        assert payload["search_family_id"] == exp.search_family_id
        assert payload["search_family_definition"] == exp.search_family_definition


def test_unknown_raw_lineage_extra_cannot_be_silently_dropped(tmp_path) -> None:
    exp = _compile(_rsi_spec("unknown-extra", [30]))
    payload = event_exp_to_dict(exp)
    payload.pop("search_family_id", None)
    payload.pop("search_family_definition", None)
    payload["raw_grids"] = {"setup": {"oversold": [30, 80], "overbought": [70]}}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unbound search-family lineage"):
        ExperimentSpec.from_json(path)


def test_all_supported_serializers_round_trip_one_family_id(tmp_path) -> None:
    exp = _compile(_rsi_spec("round-trip", [30, 80]))
    for index, serializer in enumerate(
        (event_exp_to_dict, feedback_exp_to_dict, proposal_exp_to_dict)
    ):
        path = tmp_path / f"spec-{index}.json"
        path.write_text(json.dumps(serializer(exp)), encoding="utf-8")
        loaded = ExperimentSpec.from_json(path)
        assert loaded.search_family_id == exp.search_family_id
        assert loaded.search_family_definition == exp.search_family_definition


def test_v2_loader_rejects_unknown_fields_instead_of_dropping_lineage(tmp_path) -> None:
    exp = _compile(_rsi_spec("unknown-v2", [30]))
    payload = exp.to_dict()
    payload["future_raw_axis"] = {"oversold": [20, 30]}
    path = tmp_path / "unknown-v2.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="cannot be dropped: future_raw_axis"):
        ExperimentSpec.from_json(path)


def test_legacy_compiled_json_is_not_inferred_as_complete_v2(tmp_path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "experiment_id": "legacy",
                "data_glob": "unused",
                "symbols": ["BTC"],
                "families": ["momentum_breakout"],
                "parameter_grid": {"momentum_breakout": [{"lookback": 10}]},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="compiled_subspace_only"):
        ExperimentSpec.from_json(path)


def test_sampler_and_compiler_digests_are_bound(monkeypatch) -> None:
    import src.research_lab.search_family_definition as family_module

    first = _compile(_rsi_spec("digest", [30, 80]))
    monkeypatch.setattr(family_module, "sampler_code_identity", lambda: "f" * 64)
    second = _compile(_rsi_spec("digest", [30, 80]))
    assert first.parameter_grid == second.parameter_grid
    assert first.search_family_id != second.search_family_id

    monkeypatch.setattr(family_module, "compiler_code_identity", lambda: "0" * 64)
    with pytest.raises(ValueError, match="historical search-family compiler"):
        validate_search_family_definition(
            first.search_family_definition,
            expected_id=first.search_family_id,
        )


def test_same_rows_with_different_sampler_seed_have_distinct_family_ids() -> None:
    first = _compile(_rsi_spec("seed-a", [30]))
    second = _compile(_rsi_spec("seed-b", [30]))

    assert first.parameter_grid == second.parameter_grid
    assert first.search_family_definition["selected_flat_indices"] == [0]
    assert second.search_family_definition["selected_flat_indices"] == [0]
    assert first.search_family_id != second.search_family_id
    assert (
        first.search_family_definition["sampler"]["seed_sha256"]
        != second.search_family_definition["sampler"]["seed_sha256"]
    )


def _declared_spec(*, max_runs: int = 2) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id="terminal-ledger",
        data_glob="unused",
        symbols=["BTC"],
        families=["momentum_breakout"],
        parameter_grid={
            "momentum_breakout": [
                {"lookback": 10},
                {"lookback": 20},
                {"lookback": 30},
            ]
        },
        max_runs=max_runs,
        timeframe="1h",
        backend="cpu",
        data_snapshot_id="csnap_test",
        data_evidence_hash="evidence-test",
    )


def _execution_metrics(spec: ExperimentSpec, *, error: bool = False) -> dict:
    return {
            "data_snapshot_id": spec.data_snapshot_id,
            "data_evidence_hash": spec.data_evidence_hash,
            "family_data_snapshot_id": spec.data_snapshot_id,
            "family_data_evidence_hash": spec.data_evidence_hash,
            "execution_identity": {
                "requested_backend": spec.backend,
                "resolved_backend": "cpu",
                "backend_name": "numpy",
                "signal_backend": "cpu",
                "signal_kernel": "strategy_generator",
                "signal_backend_reason": "resolved_cpu",
                "signal_candle_count": 100,
                "signal_family_variant_count": len(
                    spec.parameter_grid["momentum_breakout"]
                ),
                "simulation_backend": "not_executed" if error else "cpu",
                "simulator": (
                    "not_executed_before_simulation" if error else "cpu_simulator"
                ),
                "terminal_phase": "signal_generation" if error else "completed",
            },
        }


def _result(spec: ExperimentSpec, lookback: int, *, error: bool = False) -> RunResult:
    params = {"lookback": lookback}
    return RunResult(
        run_id=f"run-{lookback}",
        symbol="BTC",
        family="momentum_breakout",
        params=params,
        metrics=_execution_metrics(spec, error=error),
        decision="ERROR" if error else "REJECT",
        reasons=["synthetic_error"] if error else [],
        validation_status="ERROR" if error else "REJECT",
    )


def test_execution_cap_data_gate_and_error_have_distinct_dispositions() -> None:
    spec = _declared_spec(max_runs=2)
    evidence = build_search_trial_evidence(
        spec,
        [_result(spec, 10), _result(spec, 20, error=True)],
        {
            "n_variants_evaluated": 2,
            "effective_backend": "cpu",
            "resolved_backend": "cpu",
            "signal_backend": "cpu",
            "simulation_backend": "cpu",
        },
    )
    assert [row["terminal_disposition"] for row in evidence["trials"]] == [
        "evaluated",
        "error",
        "execution_cap",
    ]
    validate_search_trial_evidence(evidence, require_complete=True)
    for row_index, field, forged_value in (
        (0, "signal_backend", "gpu"),
        (1, "terminal_phase", "forged_error_phase"),
        (2, "resolved_backend", "gpu"),
    ):
        forged = json.loads(json.dumps(evidence))
        forged["trials"][row_index]["execution_identity"][field] = forged_value
        forged.pop("search_trial_evidence_id")
        forged["search_trial_evidence_id"] = f"ste_{content_hash(forged)}"
        with pytest.raises(ValueError, match="execution identity|backend|terminal"):
            validate_search_trial_evidence(forged, require_complete=True)

    gate_spec = _declared_spec(max_runs=0)
    gate = RunResult(
        run_id="gate",
        symbol="BTC",
        family="momentum_breakout",
        params={},
        metrics={
            "data_snapshot_id": gate_spec.data_snapshot_id,
            "data_evidence_hash": gate_spec.data_evidence_hash,
            "family_data_snapshot_id": gate_spec.data_snapshot_id,
            "family_data_evidence_hash": gate_spec.data_evidence_hash,
            "execution_identity": {
                "requested_backend": gate_spec.backend,
                "resolved_backend": "cpu",
                "backend_name": "not_executed",
                "signal_backend": "not_executed",
                "signal_kernel": "not_executed_data_gate",
                "signal_backend_reason": "data_gate",
                "signal_candle_count": 100,
                "signal_family_variant_count": len(
                    gate_spec.parameter_grid["momentum_breakout"]
                ),
                "simulation_backend": "not_executed",
                "simulator": "not_executed_data_gate",
                "terminal_phase": "data_gate",
            },
        },
        decision="NEEDS_OI_DATA",
        reasons=["missing_required_data"],
        validation_status="NEEDS_OI_DATA",
    )
    gated = build_search_trial_evidence(
        gate_spec,
        [gate],
        {
            "n_variants_evaluated": 3,
            "effective_backend": "cpu",
            "resolved_backend": "cpu",
            "signal_backend": "cpu",
            "simulation_backend": "cpu",
        },
    )
    assert {row["terminal_disposition"] for row in gated["trials"]} == {"data_gate"}
    forged_gate = json.loads(json.dumps(gated))
    forged_gate["trials"][0]["execution_identity"]["resolved_backend"] = (
        "forged_backend"
    )
    forged_gate.pop("search_trial_evidence_id")
    forged_gate["search_trial_evidence_id"] = f"ste_{content_hash(forged_gate)}"
    with pytest.raises(ValueError, match="resolved backend"):
        validate_search_trial_evidence(forged_gate, require_complete=True)


def test_trial_evidence_and_family_tampering_fail_before_export() -> None:
    spec = _declared_spec(max_runs=2)
    evidence = build_search_trial_evidence(
        spec,
        [_result(spec, 10), _result(spec, 20)],
        {"n_variants_evaluated": 2},
    )
    evidence["search_space"]["evaluated"] = 99
    with pytest.raises(ValueError, match="evidence id mismatch|aggregate mismatch"):
        validate_search_trial_evidence(evidence)

    definition = json.loads(json.dumps(spec.search_family_definition))
    definition["selected_flat_indices"] = [0]
    with pytest.raises(ValueError, match="selected flat indices"):
        validate_search_family_definition(definition, expected_id=spec.search_family_id)


def test_bound_data_identity_cannot_be_replaced_with_recomputed_evidence_id() -> None:
    spec = _declared_spec(max_runs=2)
    evidence = build_search_trial_evidence(
        spec,
        [_result(spec, 10), _result(spec, 20)],
        {
            "n_variants_evaluated": 2,
            "effective_backend": "cpu",
            "signal_backend": "cpu",
            "simulation_backend": "cpu",
        },
    )
    evidence["trials"][0]["data_snapshot_id"] = "csnap-forged"
    evidence.pop("search_trial_evidence_id")
    evidence["search_trial_evidence_id"] = f"ste_{content_hash(evidence)}"

    with pytest.raises(ValueError, match="data identity disagrees"):
        validate_search_trial_evidence(evidence, require_complete=True)


def test_coherent_cpu_signal_cannot_replace_required_gpu_family_kernel(
    monkeypatch,
) -> None:
    real_library_version = evidence_module._library_version
    monkeypatch.setattr(
        evidence_module,
        "_library_version",
        lambda name: "synthetic-cupy" if name == "cupy" else real_library_version(name),
    )
    spec = ExperimentSpec(
        experiment_id="gpu-path-binding",
        data_glob="unused",
        symbols=["BTC"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 10}]},
        max_runs=1,
        backend="gpu",
        data_snapshot_id="csnap-gpu-path",
        data_evidence_hash="evidence-gpu-path",
    )
    result = RunResult(
        run_id="run-gpu-path",
        symbol="BTC",
        family="momentum_breakout",
        params={"lookback": 10},
        metrics={
            "data_snapshot_id": spec.data_snapshot_id,
            "data_evidence_hash": spec.data_evidence_hash,
            "family_data_snapshot_id": spec.data_snapshot_id,
            "family_data_evidence_hash": spec.data_evidence_hash,
            "execution_identity": {
                "requested_backend": "gpu",
                "resolved_backend": "gpu",
                "backend_name": "cupy",
                "signal_backend": "gpu",
                "signal_kernel": "gpu_kernels",
                "signal_backend_reason": "gpu_eligible",
                "signal_candle_count": 100,
                "signal_family_variant_count": 1,
                "simulation_backend": "gpu",
                "simulator": "gpu_simulator",
                "terminal_phase": "completed",
            },
        },
        decision="REJECT",
        reasons=[],
    )
    evidence = build_search_trial_evidence(
        spec,
        [result],
        {
            "n_variants_evaluated": 1,
            "effective_backend": "gpu",
            "resolved_backend": "gpu",
            "signal_backend": "gpu",
            "simulation_backend": "gpu",
            "backend_name": "cupy",
        },
    )
    validate_search_trial_evidence(evidence, require_complete=True)

    forged = json.loads(json.dumps(evidence))
    identity = forged["trials"][0]["execution_identity"]
    identity["signal_backend"] = "cpu"
    identity["signal_kernel"] = "strategy_generator"
    identity["signal_kernel_sha256"] = forged["code_identity"][
        "strategy_generators"
    ]["momentum_breakout"]["sha256"]
    identity["signal_backend_reason"] = "unsupported_family"
    forged.pop("search_trial_evidence_id")
    forged["search_trial_evidence_id"] = f"ste_{content_hash(forged)}"
    with pytest.raises(ValueError, match="deterministic GPU eligibility"):
        validate_search_trial_evidence(forged, require_complete=True)


def test_auto_signal_path_recomputes_batch_eligibility_from_bound_row_count(
    monkeypatch,
) -> None:
    real_library_version = evidence_module._library_version
    monkeypatch.setattr(
        evidence_module,
        "_library_version",
        lambda name: "synthetic-cupy" if name == "cupy" else real_library_version(name),
    )
    binding = {
        "symbol": "BTC",
        "timeframe": "1d",
        "snapshot_id": "csnap-auto-path",
        "evidence_hash": "evidence-auto-path",
        "row_count": 10,
    }
    spec = ExperimentSpec(
        experiment_id="auto-path-binding",
        data_glob="unused",
        symbols=["BTC"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 10}]},
        max_runs=1,
        backend="auto",
        data_snapshot_id=binding["snapshot_id"],
        data_evidence_hash=binding["evidence_hash"],
        data_snapshot_bindings=[binding],
    )
    result = RunResult(
        run_id="run-auto-path",
        symbol="BTC",
        family="momentum_breakout",
        params={"lookback": 10},
        metrics={
            "data_snapshot_id": binding["snapshot_id"],
            "data_evidence_hash": binding["evidence_hash"],
            "family_data_snapshot_id": binding["snapshot_id"],
            "family_data_evidence_hash": binding["evidence_hash"],
            "execution_identity": {
                "requested_backend": "auto",
                "resolved_backend": "gpu",
                "backend_name": "cupy",
                "signal_backend": "cpu",
                "signal_kernel": "strategy_generator",
                "signal_backend_reason": "auto_batch_too_small",
                "signal_candle_count": 10,
                "signal_family_variant_count": 1,
                "simulation_backend": "gpu",
                "simulator": "gpu_simulator",
                "terminal_phase": "completed",
            },
        },
        decision="REJECT",
        reasons=[],
    )
    evidence = build_search_trial_evidence(
        spec,
        [result],
        {
            "n_variants_evaluated": 1,
            "effective_backend": "gpu",
            "resolved_backend": "gpu",
            "signal_backend": "cpu",
            "simulation_backend": "gpu",
            "backend_name": "cupy",
        },
    )
    validate_search_trial_evidence(evidence, require_complete=True)

    forged = json.loads(json.dumps(evidence))
    identity = forged["trials"][0]["execution_identity"]
    identity["signal_backend"] = "gpu"
    identity["signal_kernel"] = "gpu_kernels"
    identity["signal_kernel_sha256"] = forged["code_identity"]["runtime_sources"][
        "gpu_kernels.py"
    ]
    identity["signal_backend_reason"] = "gpu_eligible"
    forged.pop("search_trial_evidence_id")
    forged["search_trial_evidence_id"] = f"ste_{content_hash(forged)}"
    with pytest.raises(ValueError, match="deterministic GPU eligibility"):
        validate_search_trial_evidence(forged, require_complete=True)


def test_complete_evidence_requires_backend_and_simulator_identity() -> None:
    spec = _declared_spec(max_runs=2)
    evidence = build_search_trial_evidence(
        spec,
        [_result(spec, 10), _result(spec, 20)],
        {"n_variants_evaluated": 2},
    )
    with pytest.raises(ValueError, match="backend/simulator identity is incomplete"):
        validate_search_trial_evidence(evidence, require_complete=True)


def test_recomputed_id_cannot_hide_a_shortened_execution_ledger() -> None:
    spec = _declared_spec(max_runs=2)
    evidence = build_search_trial_evidence(
        spec,
        [_result(spec, 10), _result(spec, 20)],
        {
            "n_variants_evaluated": 2,
            "effective_backend": "cpu",
            "signal_backend": "cpu",
            "simulation_backend": "cpu",
        },
    )
    evidence["trials"] = evidence["trials"][:1]
    evidence["search_space"].update(
        {
            "selected_executions": 1,
            "attempted_executions": 1,
            "evaluated": 1,
            "data_gates": 0,
            "errors": 0,
            "execution_cap": 0,
            "missing_terminal": 0,
            "not_evaluated": 0,
        }
    )
    evidence["runtime"]["n_variants_evaluated"] = 1
    evidence.pop("search_trial_evidence_id")
    evidence["search_trial_evidence_id"] = f"ste_{content_hash(evidence)}"
    with pytest.raises(ValueError, match="cover every selected execution"):
        validate_search_trial_evidence(evidence, require_complete=True)


def test_multi_symbol_family_uses_selected_execution_denominator() -> None:
    spec = ExperimentSpec(
        experiment_id="multi-symbol",
        data_glob="unused",
        symbols=["BTC", "ETH"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 10}, {"lookback": 20}]},
        max_runs=3,
        data_snapshot_id="csnap-multi",
        data_evidence_hash="evidence-multi",
    )
    results = [
        RunResult(
            run_id=f"run-{symbol}-{lookback}",
            symbol=symbol,
            family="momentum_breakout",
            params={"lookback": lookback},
            metrics={
                **_execution_metrics(spec),
            },
            decision="REJECT",
            reasons=[],
        )
        for symbol, lookback in (("BTC", 10), ("BTC", 20), ("ETH", 10))
    ]
    evidence = build_search_trial_evidence(
        spec,
        results,
        {
            "n_variants_evaluated": 3,
            "effective_backend": "cpu",
            "signal_backend": "cpu",
            "simulation_backend": "cpu",
        },
    )
    counts = validate_search_trial_evidence(evidence, require_complete=True)
    assert counts["selected_points"] == 2
    assert counts["selected_executions"] == 4
    assert counts["attempted_executions"] == 3
    assert counts["execution_cap"] == 1
    assert counts["effective_n_trials"] == 4


def test_confirmatory_family_requires_exactly_one_selected_execution() -> None:
    with pytest.raises(ValueError, match="exactly one selected execution"):
        ExperimentSpec(
            experiment_id="bad-confirmatory",
            data_glob="unused",
            symbols=["BTC", "ETH"],
            families=["momentum_breakout"],
            parameter_grid={"momentum_breakout": [{"lookback": 10}]},
            plan_meta={
                "search_family_policy": {
                    "mode": "confirmatory",
                    "parent_family_id": "sfd_parent",
                    "parent_trial_id": "stept_parent",
                    "parent_effective_n_trials": 4,
                }
            },
        )


def test_pbo_coverage_accounts_for_other_symbol_executions() -> None:
    spec = ExperimentSpec(
        experiment_id="coverage-scope",
        data_glob="unused",
        symbols=["BTC", "ETH"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 10}]},
        max_runs=2,
        data_snapshot_id="csnap-coverage",
        data_evidence_hash="evidence-coverage",
    )
    results = [
        RunResult(
            run_id=f"run-{symbol}",
            symbol=symbol,
            family="momentum_breakout",
            params={"lookback": 10},
            metrics={
                **_execution_metrics(spec),
            },
            decision="REJECT",
            reasons=[],
        )
        for symbol in spec.symbols
    ]
    evidence = build_search_trial_evidence(
        spec,
        results,
        {
            "n_variants_evaluated": 2,
            "effective_backend": "cpu",
            "signal_backend": "cpu",
            "simulation_backend": "cpu",
        },
    )
    rows = [
        {
            "run_id": result.run_id,
            "symbol": result.symbol,
            "family": result.family,
            "trades": [
                {"net_pct": 1.0},
                {"net_pct": -0.5},
                {"net_pct": 0.75},
            ],
        }
        for result in results
    ]
    panel = _comparable_trial_panel(
        rows,
        evidence,
        symbol="BTC",
        family="momentum_breakout",
    )
    assert panel["coverage"]["selected_executions"] == 2
    assert panel["coverage"]["complete"] is True
    assert panel["coverage"]["included_count"] == 1
    assert panel["coverage"]["excluded"] == [
        {
            "execution_id": evidence["trials"][1]["execution_id"],
            "run_id": "run-ETH",
            "reason": "different_symbol_scope",
        }
    ]


def test_adaptive_family_declares_parent_and_cumulative_count() -> None:
    spec = _rsi_spec("adaptive", [30, 40])
    spec = SweepSpec(
        **{
            **spec.__dict__,
            "parent_family_id": "sfd_parent",
            "parent_trial_id": "stept_parent",
            "parent_effective_n_trials": 4,
            "cumulative_family_policy": "cumulative",
        }
    )
    exp = _compile(spec)
    assert exp.search_family_definition["family_policy"] == {
        "mode": "cumulative",
        "parent_family_id": "sfd_parent",
        "parent_trial_id": "stept_parent",
        "parent_effective_n_trials": 4,
    }
    assert effective_family_n_trials(exp.search_family_definition) == 5


def test_legacy_v1_is_compiled_subspace_only() -> None:
    legacy = {"schema": "SearchTrialEvidence.v1"}
    assert classify_search_trial_evidence(legacy) == "compiled_subspace_only"
    report = search_trial_evidence_migration_report(legacy)
    assert report == {
        "schema": "SearchTrialEvidenceMigrationReport.v1",
        "source_schema": "SearchTrialEvidence.v1",
        "classification": "compiled_subspace_only",
        "v2_consumable": False,
        "missing_not_inferred": [
            "raw_axes",
            "sampler_seed_and_digest",
            "invalid_point_dispositions",
            "family_parentage",
        ],
    }


def test_explicit_reader_supports_deletion_free_v2_rollback(tmp_path) -> None:
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(
        json.dumps({"schema": "SearchTrialEvidence.v1"}),
        encoding="utf-8",
    )
    assert read_search_trial_evidence(
        legacy_path,
        accepted_schema="SearchTrialEvidence.v1",
    )["schema"] == "SearchTrialEvidence.v1"
    with pytest.raises(ValueError, match="explicit reader selection"):
        read_search_trial_evidence(
            legacy_path,
            accepted_schema="SearchTrialEvidence.v2",
        )


def test_adaptive_trial_identity_binds_hypothesis_and_parentage() -> None:
    from src.research_lab.adaptive_trial import adaptive_trial_id

    base = {
        "schema": "RoleTaskSpec.v1",
        "kind": "bounded_sweep",
        "subject": {"symbol": "BTC", "family": "momentum_breakout"},
        "source_ref": "source:1",
        "source_content_sha256": "a" * 64,
        "producer_completion_id": "completion-1",
        "generation": 1,
        "dimensions": ["lookback"],
        "tests": ["test-a"],
        "hypotheses": ["hypothesis-a"],
        "parent_family_id": "sfd_parent",
        "parent_trial_id": "stept_parent",
        "parent_effective_n_trials": 4,
        "cumulative_family_policy": "cumulative",
    }
    first = adaptive_trial_id(base)
    assert first != adaptive_trial_id({**base, "hypotheses": ["hypothesis-b"]})
    assert first != adaptive_trial_id({**base, "parent_trial_id": "stept_other"})


def test_hard_export_recomputes_evidence_and_rejects_tampering(tmp_path) -> None:
    from src.research_lab.simulator_contract import legacy_fixture_manifest

    manifest = legacy_fixture_manifest()
    spec = ExperimentSpec(
        experiment_id="hard-export-family",
        data_glob="unused",
        symbols=["BTC"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 10}, {"lookback": 20}]},
        timeframe="1h",
        data_snapshot_id="csnap-export",
        data_evidence_hash="evidence-export",
    )
    results = [_result(spec, 10), _result(spec, 20)]
    runtime = {
        "n_variants_evaluated": 2,
        "effective_backend": "cpu",
        "resolved_backend": "cpu",
        "signal_backend": "cpu",
        "simulation_backend": "cpu",
    }
    evidence = build_search_trial_evidence(spec, results, runtime)
    run_dir = tmp_path / "experiments" / "completed" / "run-family"
    run_dir.mkdir(parents=True)
    (run_dir / "search_trial_evidence.json").write_text(
        json.dumps(evidence), encoding="utf-8"
    )
    (run_dir / "metrics.json").write_text(
        json.dumps(
            {
                "experiment_id": spec.experiment_id,
                "timeframe": spec.timeframe,
                "runtime": runtime,
                "search_trial_evidence_id": evidence["search_trial_evidence_id"],
                "multiple_testing_family_hash": evidence["multiple_testing_family_hash"],
                "results": [
                    {
                        "run_id": result.run_id,
                        "symbol": result.symbol,
                        "family": result.family,
                        "params": result.params,
                        "metrics": {
                            **result.metrics,
                            "data_fingerprint": spec.data_evidence_hash,
                            "simulator_manifest": manifest,
                            "simulator_model_id": manifest["simulator_model_id"],
                            "simulator_evidence_tier": manifest["evidence_tier"],
                            "unsupported_simulator_dimensions": manifest["unsupported_dimensions"],
                        },
                        "trades": [],
                    }
                    for result in results
                ],
            }
        ),
        encoding="utf-8",
    )
    entry = {
        "candidate_id": "run-10",
        "experiment_id": spec.experiment_id,
        "artifact_label": "run-family",
        "symbol": "BTC",
        "timeframe": "1h",
        "strategy_id": "momentum_breakout",
        "params": {"lookback": 10},
        "validation_status": "FORWARD_PAPER",
        "data_fingerprint": spec.data_evidence_hash,
    }
    candidate = _build_candidate(entry, tmp_path)
    assert candidate is not None
    assert candidate.metrics["search_trial_evidence_id"] == evidence["search_trial_evidence_id"]
    assert candidate.metrics["search_trial_evidence"] == evidence
    coverage = candidate.metrics["pbo_dsr_family_coverage"]
    assert coverage["complete"] is True
    assert coverage["included_count"] == 0
    assert [row["reason"] for row in coverage["excluded"]] == [
        "fewer_than_3_trades",
        "fewer_than_3_trades",
    ]
    assert candidate.metrics["search_trial_panel"]["status"] == "invalid"
    assert candidate.metrics["search_trial_panel"]["reason_codes"] == [
        "invalid_legacy_orientation"
    ]
    assert "trial_returns" not in candidate.metrics
    assert "trial_sharpes" not in candidate.metrics

    registry = tmp_path / "candidate-registry" / "candidates.jsonl"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps(entry), encoding="utf-8")
    summary = export_requests(
        tmp_path,
        dry_run=False,
        source="legacy_registry",
    )
    assert summary["exported"] == 1
    request_path = next((tmp_path / "hard_validation" / "requests").glob("*.json"))
    request_payload = request_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in request_payload

    evidence["search_space"]["evaluated"] = 99
    (run_dir / "search_trial_evidence.json").write_text(
        json.dumps(evidence), encoding="utf-8"
    )
    assert _build_candidate(entry, tmp_path) is None


def test_per_trial_error_is_preserved_and_next_variant_runs(tmp_path, monkeypatch) -> None:
    import src.research_lab.experiment as experiment_module

    rows = [
        {
            "ts": index * 3_600_000,
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100.5 + index,
            "vol": 10,
        }
        for index in range(80)
    ]
    data_file = tmp_path / "BTC_USDT_SWAP_1h.json"
    data_file.write_text(json.dumps(rows), encoding="utf-8")
    candle_bundle = experiment_module._load_experiment_candles(
        str(tmp_path / "{symbol}_*.json"),
        "BTC_USDT_SWAP",
        timeframe="1h",
        candle_store=None,
    )
    assert candle_bundle is not None
    _candles, _label, evidence_hash, snapshot_id = candle_bundle
    spec = ExperimentSpec(
        experiment_id="trial-error",
        data_glob=str(tmp_path / "{symbol}_*.json"),
        symbols=["BTC_USDT_SWAP"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 10}, {"lookback": 20}]},
        timeframe="1h",
        data_snapshot_id=snapshot_id,
        data_evidence_hash=evidence_hash,
        data_snapshot_bindings=[{
            "symbol": "BTC_USDT_SWAP",
            "timeframe": "1h",
            "snapshot_id": snapshot_id,
            "evidence_hash": evidence_hash,
            "row_count": len(rows),
        }],
    )
    original = experiment_module.generate_signals

    def flaky(candles, family, params):
        if params["lookback"] == 10:
            raise RuntimeError("synthetic variant failure")
        return original(candles, family, params)

    monkeypatch.setattr(experiment_module, "generate_signals", flaky)
    runtime: dict = {}
    results = experiment_module.evaluate_spec(spec, runtime)

    assert len(results) == 2
    assert results[0].decision == "ERROR"
    assert results[0].reasons == ["execution_error:RuntimeError"]
    assert results[1].decision != "ERROR"
    evidence = build_search_trial_evidence(spec, results, runtime)
    assert [row["terminal_disposition"] for row in evidence["trials"]] == [
        "error",
        "evaluated",
    ]
    snapshot_ids = {row["data_snapshot_id"] for row in evidence["trials"]}
    evidence_hashes = {row["data_evidence_hash"] for row in evidence["trials"]}
    assert len(snapshot_ids) == 1 and next(iter(snapshot_ids)).startswith("csm_")
    assert len(evidence_hashes) == 1 and next(iter(evidence_hashes))
    identity = evidence["execution_identity"]
    assert identity["backend_name"] == "numpy"
    assert identity["backend_library_version"] != "unknown"
    assert identity["numpy_version"] != "unknown"
    assert identity["python_version"]
    code_identity = evidence["code_identity"]
    assert code_identity["runtime_sources"]["gpu_simulator.py"] != "missing"
    assert code_identity["strategy_generators"]["momentum_breakout"]["sha256"] != (
        "missing"
    )
    per_execution = [row["execution_identity"] for row in evidence["trials"]]
    assert per_execution[0]["terminal_phase"] == "signal_generation"
    assert per_execution[0]["simulation_backend"] == "not_executed"
    assert per_execution[1]["terminal_phase"] == "completed"
    assert per_execution[1]["signal_backend"] == "cpu"
    assert per_execution[1]["simulation_backend"] == "cpu"
    assert all(item["signal_kernel_sha256"] != "missing" for item in per_execution)
    assert all(item["backend_library_version"] != "unknown" for item in per_execution)
    validate_search_trial_evidence(evidence, require_complete=True)

    forged = json.loads(json.dumps(evidence))
    forged["trials"][1]["execution_identity"]["backend_library_version"] = "forged"
    forged.pop("search_trial_evidence_id")
    forged["search_trial_evidence_id"] = f"ste_{content_hash(forged)}"
    with pytest.raises(ValueError, match="backend/kernel/simulator identity mismatch"):
        validate_search_trial_evidence(forged, require_complete=True)
