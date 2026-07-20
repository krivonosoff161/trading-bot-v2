from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
import src.research_lab.time_aware_validation as validation_module

from src.research_lab.experiment import ExperimentSpec, RunResult
from src.research_lab.honest_backtest_bridge import (
    _check_overfit,
    _check_search_family_evidence,
    _shadow_search_metrics,
)
from src.research_lab.search_trial_evidence import build_search_trial_evidence
from src.research_lab.time_aware_validation import (
    ValidationEvidenceError,
    build_dependence_evidence,
    build_interval_split_manifest,
    build_search_trial_panel,
    build_validation_observation_set,
    classify_legacy_search_bias_evidence,
    validate_interval_split_manifest,
    validate_search_trial_panel,
)


def _family_evidence(
    *,
    dispositions: tuple[str, ...] = ("evaluated", "evaluated"),
    parent_effective_n_trials: int = 0,
):
    lookbacks = [10 + index * 10 for index in range(len(dispositions))]
    spec = ExperimentSpec(
        experiment_id="pbo-family",
        data_glob="unused",
        symbols=["BTC"],
        families=["momentum_breakout"],
        parameter_grid={
            "momentum_breakout": [{"lookback": value} for value in lookbacks]
        },
        max_runs=len(dispositions),
        timeframe="1h",
        backend="cpu",
        data_snapshot_id="csnap-panel",
        data_evidence_hash="evidence-panel",
        plan_meta=(
            {
                "search_family_policy": {
                    "mode": "cumulative",
                    "parent_family_id": "sfd_parent",
                    "parent_trial_id": "stept_parent",
                    "parent_effective_n_trials": parent_effective_n_trials,
                }
            }
            if parent_effective_n_trials
            else {}
        ),
    )
    results = []
    for lookback, disposition in zip(lookbacks, dispositions, strict=True):
        error = disposition == "error"
        results.append(
            RunResult(
                run_id=f"run-{lookback}",
                symbol="BTC",
                family="momentum_breakout",
                params={"lookback": lookback},
                metrics={
                    "data_snapshot_id": spec.data_snapshot_id,
                    "data_evidence_hash": spec.data_evidence_hash,
                    "family_data_snapshot_id": spec.data_snapshot_id,
                    "family_data_evidence_hash": spec.data_evidence_hash,
                    "execution_identity": {
                        "requested_backend": "cpu",
                        "resolved_backend": "cpu",
                        "backend_name": "numpy",
                        "signal_backend": "cpu",
                        "signal_kernel": "strategy_generator",
                        "signal_backend_reason": "resolved_cpu",
                        "signal_candle_count": 100,
                        "signal_family_variant_count": len(dispositions),
                        "simulation_backend": "not_executed" if error else "cpu",
                        "simulator": (
                            "not_executed_before_simulation" if error else "cpu_simulator"
                        ),
                        "terminal_phase": "signal_generation" if error else "completed",
                    },
                },
                decision="ERROR" if error else "REJECT",
                reasons=["synthetic_error"] if error else [],
                validation_status="ERROR" if error else "REJECT",
            )
        )
    evidence = build_search_trial_evidence(
        spec,
        results,
        {
            "n_variants_evaluated": len(results),
            "effective_backend": "cpu",
            "resolved_backend": "cpu",
            "signal_backend": "cpu",
            "simulation_backend": "cpu",
        },
    )
    return spec, evidence


def _trades(*, shift_ms: int = 0, returns: tuple[float, ...] = (1.0, -0.5, 0.75, 0.25)):
    rows = []
    for index, value in enumerate(returns):
        period = shift_ms + index * 3_600_000
        rows.append(
            {
                "period_ts": period,
                "entry_ts": period + 600_000,
                "exit_ts": period + 1_200_000,
                "feature_start_ts": period - 3_600_000,
                "feature_end_ts": period + 300_000,
                "net_pct": value,
            }
        )
    return rows


def _sets(evidence, *, shifts=None, reverse=False, returns=(1.0, -0.5, 0.75, 0.25)):
    rows = []
    trials = list(evidence["trials"])
    if reverse:
        trials.reverse()
    if shifts is None:
        shifts = (0,) * len(trials)
    for trial, shift in zip(trials, shifts, strict=True):
        rows.append(
            build_validation_observation_set(
                _trades(shift_ms=shift, returns=returns),
                trial_id=trial["execution_id"],
                symbol="BTC",
                strategy_family="momentum_breakout",
                timeframe="1h",
                data_snapshot_id="csnap-panel",
                data_evidence_hash="evidence-panel",
                search_family_id=evidence["search_family_id"],
                return_basis="net_pct",
                time_grid={"kind": "fixed_step", "step_ms": 3_600_000},
            )
        )
    return rows


def test_square_trial_major_panel_is_rejected_even_when_numeric() -> None:
    legacy = classify_legacy_search_bias_evidence([[0.1, -0.1], [0.2, -0.2]])
    assert legacy["status"] == "invalid"
    assert legacy["reason_codes"] == ["invalid_legacy_orientation"]


def test_equal_length_shifted_timestamps_are_not_common_alignment() -> None:
    _, evidence = _family_evidence()
    with pytest.raises(ValidationEvidenceError, match="common_time_axis_mismatch"):
        build_search_trial_panel(evidence, _sets(evidence, shifts=(0, 3_600_000)))


def test_ragged_trial_periods_fail_at_panel_construction() -> None:
    _, evidence = _family_evidence()
    sets = _sets(evidence)
    shorter = build_validation_observation_set(
        _trades(returns=(1.0, -0.5, 0.75)),
        trial_id=evidence["trials"][1]["execution_id"],
        symbol="BTC",
        strategy_family="momentum_breakout",
        timeframe="1h",
        data_snapshot_id="csnap-panel",
        data_evidence_hash="evidence-panel",
        search_family_id=evidence["search_family_id"],
        return_basis="net_pct",
        time_grid={"kind": "fixed_step", "step_ms": 3_600_000},
    )
    with pytest.raises(ValidationEvidenceError, match="common_time_axis_mismatch"):
        build_search_trial_panel(evidence, [sets[0], shorter])


def test_panel_rejects_cross_timeframe_snapshot_and_family_bindings() -> None:
    _, evidence = _family_evidence()
    for field, value in (
        ("timeframe", "15m"),
        ("data_snapshot_id", "csnap-other"),
        ("data_evidence_hash", "evidence-other"),
        ("search_family_id", "sfd_other"),
        ("strategy_family", "other_family"),
    ):
        sets = _sets(evidence)
        sets[1] = {**sets[1], field: value}
        with pytest.raises(ValidationEvidenceError, match="panel_scope_mismatch"):
            build_search_trial_panel(evidence, sets)

    sets = _sets(evidence)
    sets[1] = {**sets[1], "evaluation_window_id": "vwin_other"}
    with pytest.raises(ValidationEvidenceError, match="evaluation_window_identity_mismatch"):
        build_search_trial_panel(evidence, sets)


def test_failed_trial_remains_in_family_coverage_and_panel_is_unavailable() -> None:
    _, evidence = _family_evidence(dispositions=("evaluated", "error"))
    only_evaluated = _sets({**evidence, "trials": [evidence["trials"][0]]}, shifts=(0,))
    panel = build_search_trial_panel(evidence, only_evaluated, allow_unavailable=True)
    assert panel["status"] == "unavailable"
    assert panel["coverage"]["selected_count"] == 2
    assert panel["coverage"]["included_count"] == 1
    assert panel["coverage"]["excluded"] == [
        {
            "trial_id": evidence["trials"][1]["execution_id"],
            "disposition": "error",
            "reason": "terminal_error",
        }
    ]


def test_other_symbol_trials_remain_explicit_without_contaminating_panel() -> None:
    spec = ExperimentSpec(
        experiment_id="multi-symbol-panel",
        data_glob="unused",
        symbols=["BTC", "ETH"],
        families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{"lookback": 10}]},
        max_runs=2,
        timeframe="1h",
        backend="cpu",
        data_snapshot_id="csnap-panel",
        data_evidence_hash="evidence-panel",
    )
    results = []
    for symbol in spec.symbols:
        results.append(
            RunResult(
                run_id=f"run-{symbol}",
                symbol=symbol,
                family="momentum_breakout",
                params={"lookback": 10},
                metrics={
                    "data_snapshot_id": spec.data_snapshot_id,
                    "data_evidence_hash": spec.data_evidence_hash,
                    "family_data_snapshot_id": spec.data_snapshot_id,
                    "family_data_evidence_hash": spec.data_evidence_hash,
                    "execution_identity": {
                        "requested_backend": "cpu",
                        "resolved_backend": "cpu",
                        "backend_name": "numpy",
                        "signal_backend": "cpu",
                        "signal_kernel": "strategy_generator",
                        "signal_backend_reason": "resolved_cpu",
                        "signal_candle_count": 100,
                        "signal_family_variant_count": 1,
                        "simulation_backend": "cpu",
                        "simulator": "cpu_simulator",
                        "terminal_phase": "completed",
                    },
                },
                decision="REJECT",
                reasons=[],
            )
        )
    evidence = build_search_trial_evidence(
        spec,
        results,
        {
            "n_variants_evaluated": 2,
            "effective_backend": "cpu",
            "resolved_backend": "cpu",
            "signal_backend": "cpu",
            "simulation_backend": "cpu",
        },
    )
    btc_trial = next(row for row in evidence["trials"] if row["symbol"] == "BTC")
    btc_set = build_validation_observation_set(
        _trades(),
        trial_id=btc_trial["execution_id"],
        symbol="BTC",
        strategy_family="momentum_breakout",
        timeframe="1h",
        data_snapshot_id="csnap-panel",
        data_evidence_hash="evidence-panel",
        search_family_id=evidence["search_family_id"],
        return_basis="net_pct",
        time_grid={"kind": "fixed_step", "step_ms": 3_600_000},
    )
    panel = build_search_trial_panel(evidence, [btc_set], allow_unavailable=True)
    assert panel["status"] == "unavailable"
    assert panel["coverage"]["selected_count"] == 2
    assert panel["coverage"]["scope_selected_count"] == 1
    assert panel["coverage"]["excluded"][0]["reason"] == (
        "different_symbol_or_family_scope"
    )
    assert panel["blocking_reasons"][-1]["reason"] == (
        "family_multiplicity_not_represented_in_panel"
    )


def test_cumulative_parent_multiplicity_makes_current_only_panel_unavailable() -> None:
    _, evidence = _family_evidence(parent_effective_n_trials=4)
    panel = build_search_trial_panel(
        evidence, _sets(evidence), allow_unavailable=True
    )
    assert panel["status"] == "unavailable"
    assert panel["effective_family_trial_count"] == 6
    assert panel["blocking_reasons"] == [
        {
            "reason": "family_multiplicity_not_represented_in_panel",
            "effective_family_trial_count": 6,
            "panel_trial_count": 2,
        }
    ]


def test_pbo_trial_count_is_recomputed_from_columns_and_family() -> None:
    _, evidence = _family_evidence()
    panel = build_search_trial_panel(evidence, _sets(evidence))
    forged = copy.deepcopy(panel)
    forged["reported_trial_count"] = 99
    with pytest.raises(ValidationEvidenceError, match="panel_trial_count_mismatch"):
        validate_search_trial_panel(forged, evidence)


def test_recomputed_panel_id_cannot_hide_matrix_source_mismatch() -> None:
    _, evidence = _family_evidence()
    panel = build_search_trial_panel(evidence, _sets(evidence))
    forged = copy.deepcopy(panel)
    forged["matrix"][0][0] += 10.0
    body = {key: value for key, value in forged.items() if key != "search_trial_panel_id"}
    forged["search_trial_panel_id"] = f"stp_{validation_module._content_hash(body)}"
    with pytest.raises(ValidationEvidenceError, match="panel_recomputation_mismatch"):
        validate_search_trial_panel(forged, evidence)


def test_malformed_observation_row_has_typed_failure() -> None:
    _, evidence = _family_evidence(dispositions=("evaluated",))
    observations = _sets(evidence, shifts=(0,))[0]
    forged = copy.deepcopy(observations)
    forged["observations"][0].pop("exit_ts")
    with pytest.raises(ValidationEvidenceError, match="invalid_observation_row"):
        validation_module.validate_validation_observation_set(forged)


def test_malformed_embedded_panel_source_is_invalid_without_changing_psr() -> None:
    _, evidence = _family_evidence()
    panel = build_search_trial_panel(evidence, _sets(evidence))
    forged = copy.deepcopy(panel)
    forged["observation_sets"][0] = None
    body = {key: value for key, value in forged.items() if key != "search_trial_panel_id"}
    forged["search_trial_panel_id"] = f"stp_{validation_module._content_hash(body)}"
    candidate = SimpleNamespace(
        metrics={"search_trial_evidence": evidence, "search_trial_panel": forged}
    )
    family_check = _check_search_family_evidence(candidate)
    assert family_check["passed"] is False
    assert family_check["details"]["status"] == "invalid"
    assert _shadow_search_metrics(candidate)["pbo"] == {
        "status": "invalid",
        "reason": "panel_observation_sets_invalid",
    }
    strong_returns = [0.5, 1.0, 0.8, 1.2, 0.9, 1.1] * 4
    assert _check_overfit(candidate, strong_returns)["passed"] is True


def test_trial_column_permutation_preserves_stable_panel_identity() -> None:
    _, evidence = _family_evidence()
    first = build_search_trial_panel(evidence, _sets(evidence))
    second = build_search_trial_panel(evidence, _sets(evidence, reverse=True))
    assert first == second
    assert first["orientation"] == "time_major"


def test_valid_time_major_panel_computes_shadow_pbo_without_hard_authority() -> None:
    _, evidence = _family_evidence(
        dispositions=("evaluated", "evaluated", "evaluated", "evaluated")
    )
    returns = tuple(1.0 if index % 3 else -0.25 for index in range(16))
    sets = _sets(evidence, returns=returns)
    panel = build_search_trial_panel(evidence, sets)
    shadow = _shadow_search_metrics(
        SimpleNamespace(
            metrics={
                "search_trial_evidence": evidence,
                "search_trial_panel": panel,
                "validation_observation_set": sets[0],
            }
        )
    )
    assert shadow["mode"] == "shadow_only"
    assert shadow["pbo"]["status"] == "valid"
    assert shadow["pbo"]["value"]["n_trials"] == 4
    assert shadow["dsr"]["status"] == "valid"


def test_duplicate_and_gapped_timestamps_fail_declared_fixed_grid() -> None:
    _, evidence = _family_evidence()
    trial_id = evidence["trials"][0]["execution_id"]
    duplicate = _trades()
    duplicate[1]["period_ts"] = duplicate[0]["period_ts"]
    with pytest.raises(ValidationEvidenceError, match="duplicate_period_ts"):
        build_validation_observation_set(
            duplicate,
            trial_id=trial_id,
            symbol="BTC",
            strategy_family="momentum_breakout",
            timeframe="1h",
            data_snapshot_id="csnap-panel",
            data_evidence_hash="evidence-panel",
            search_family_id=evidence["search_family_id"],
            return_basis="net_pct",
            time_grid={"kind": "fixed_step", "step_ms": 3_600_000},
        )
    gapped = _trades()
    gapped[2]["period_ts"] += 1_800_000
    with pytest.raises(ValidationEvidenceError, match="time_grid_gap"):
        build_validation_observation_set(
            gapped,
            trial_id=trial_id,
            symbol="BTC",
            strategy_family="momentum_breakout",
            timeframe="1h",
            data_snapshot_id="csnap-panel",
            data_evidence_hash="evidence-panel",
            search_family_id=evidence["search_family_id"],
            return_basis="net_pct",
            time_grid={"kind": "fixed_step", "step_ms": 3_600_000},
        )


def test_holding_interval_is_purged_at_split_boundary() -> None:
    _, evidence = _family_evidence(dispositions=("evaluated",))
    trial_id = evidence["trials"][0]["execution_id"]
    trades = _trades(returns=(1, 1, 1, 1, 1, 1))
    trades[2]["exit_ts"] = trades[3]["entry_ts"] + 1
    observations = build_validation_observation_set(
        trades,
        trial_id=trial_id,
        symbol="BTC",
        strategy_family="momentum_breakout",
        timeframe="1h",
        data_snapshot_id="csnap-panel",
        data_evidence_hash="evidence-panel",
        search_family_id=evidence["search_family_id"],
        return_basis="net_pct",
        time_grid={"kind": "fixed_step", "step_ms": 3_600_000},
    )
    manifest = build_interval_split_manifest(observations, train_count=3, embargo_ms=0)
    reasons = {row["observation_id"]: row["reasons"] for row in manifest["purged"]}
    assert observations["observations"][2]["observation_id"] in reasons
    assert "holding_interval_overlaps_test" in reasons[
        observations["observations"][2]["observation_id"]
    ]


def test_feature_information_after_entry_is_rejected() -> None:
    _, evidence = _family_evidence(dispositions=("evaluated",))
    trial_id = evidence["trials"][0]["execution_id"]
    trades = _trades()
    trades[1]["feature_end_ts"] = trades[1]["entry_ts"] + 1
    with pytest.raises(ValidationEvidenceError, match="feature_information_after_entry"):
        build_validation_observation_set(
            trades,
            trial_id=trial_id,
            symbol="BTC",
            strategy_family="momentum_breakout",
            timeframe="1h",
            data_snapshot_id="csnap-panel",
            data_evidence_hash="evidence-panel",
            search_family_id=evidence["search_family_id"],
            return_basis="net_pct",
            time_grid={"kind": "fixed_step", "step_ms": 3_600_000},
        )


def test_split_manifest_reconciles_and_tampering_fails() -> None:
    _, evidence = _family_evidence(dispositions=("evaluated",))
    observations = _sets(evidence, shifts=(0,))[0]
    manifest = build_interval_split_manifest(observations, train_count=2, embargo_ms=0)
    validate_interval_split_manifest(manifest, observations)
    forged = copy.deepcopy(manifest)
    forged["retained_test_ids"].pop()
    with pytest.raises(ValidationEvidenceError, match="split_manifest_mismatch"):
        validate_interval_split_manifest(forged, observations)


def test_declared_embargo_records_each_excluded_test_observation() -> None:
    _, evidence = _family_evidence(dispositions=("evaluated",))
    observations = _sets(evidence, shifts=(0,))[0]
    manifest = build_interval_split_manifest(
        observations, train_count=2, embargo_ms=1_800_000
    )
    assert manifest["embargoed"] == [
        {
            "observation_id": observations["observations"][2]["observation_id"],
            "reason": "declared_embargo",
        }
    ]
    assert manifest["retained_test_ids"] == [
        observations["observations"][3]["observation_id"]
    ]


def test_empty_effective_train_and_test_partitions_are_rejected() -> None:
    _, evidence = _family_evidence(dispositions=("evaluated",))
    trial_id = evidence["trials"][0]["execution_id"]
    trades = _trades()
    trades[0]["exit_ts"] = trades[2]["entry_ts"] + 1
    trades[1]["exit_ts"] = trades[2]["entry_ts"] + 1
    observations = build_validation_observation_set(
        trades,
        trial_id=trial_id,
        symbol="BTC",
        strategy_family="momentum_breakout",
        timeframe="1h",
        data_snapshot_id="csnap-panel",
        data_evidence_hash="evidence-panel",
        search_family_id=evidence["search_family_id"],
        return_basis="net_pct",
        time_grid={"kind": "fixed_step", "step_ms": 3_600_000},
    )
    with pytest.raises(ValidationEvidenceError, match="empty_effective_train_partition"):
        build_interval_split_manifest(observations, train_count=2, embargo_ms=0)

    clean = _sets(evidence, shifts=(0,))[0]
    with pytest.raises(ValidationEvidenceError, match="empty_effective_test_partition"):
        build_interval_split_manifest(clean, train_count=2, embargo_ms=7_200_000)


def test_iid_and_interval_block_provenance_are_distinct_and_iid_is_non_authoritative() -> None:
    iid = build_dependence_evidence(
        method="iid_bootstrap_kill_test", seed=42, block_length=None, effective_n=4
    )
    blocked = build_dependence_evidence(
        method="interval_block_upstream_required", seed=42, block_length=2, effective_n=2
    )
    assert iid["dependence_evidence_id"] != blocked["dependence_evidence_id"]
    assert iid["authoritative_suitable"] is False
    assert blocked["authoritative_suitable"] is False
    assert blocked["reason"] == "accepted_upstream_method_unavailable"
