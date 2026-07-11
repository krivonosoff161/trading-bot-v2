from __future__ import annotations

import json

from src.research_lab.role_environment import (
    accept_role_request,
    gate_role_environment,
    materialize_role_environment,
)
from src.research_lab.paths import PROJECT_ROOT
from src.research_lab.system_analyst_feedback import (
    build_feedback,
    pending_feedback,
    route_feedback,
)
from tests.test_system_analyst_feedback import NOW, _payload


def _gate_artifacts(tmp_path, environment_id: str, accepted: bool = True) -> tuple[str, str]:
    artifacts = tmp_path / "state" / "gate_evidence"
    artifacts.mkdir(parents=True, exist_ok=True)
    gate = artifacts / f"{environment_id}.gate.json"
    evaluation = artifacts / f"{environment_id}.evaluation.json"
    gate.write_text(json.dumps({
        "schema": "DeterministicRoleGate.v1", "environment_id": environment_id,
        "accepted": accepted,
    }))
    selection = [{
        "entry_ts": "2026-07-11T08:00:00+00:00",
        "exit_ts": "2026-07-11T08:30:00+00:00", "side": "long", "net_pct": 0.2,
    }]
    observed = [{
        "entry_ts": "2026-07-11T10:00:00+00:00",
        "exit_ts": "2026-07-11T10:30:00+00:00", "side": "long", "net_pct": 0.3,
    }]
    from src.research_lab.hard_validation_contract import trade_evidence_hash
    evaluation.write_text(json.dumps({
        "schema": "ValidationEpoch.v1", "environment_id": environment_id,
        "evidence_stage": "untouched_evaluation",
        "selection_data_fingerprint": "sha256:selection",
        "evaluation_data_fingerprint": "sha256:evaluation",
        "selection_evidence": selection,
        "selection_evidence_hash": trade_evidence_hash(selection),
        "evaluation_evidence": observed,
        "evaluation_evidence_hash": trade_evidence_hash(observed),
        "hypothesis_frozen_at": "2026-07-11T09:00:00+00:00",
        "evaluation_started_at": "2026-07-11T10:00:00+00:00",
        "quality_gate_passed": accepted,
    }))
    return (
        str(gate.relative_to(tmp_path)).replace("\\", "/"),
        str(evaluation.relative_to(tmp_path)).replace("\\", "/"),
    )


def test_feedback_materializes_one_bounded_candidate_per_role(tmp_path):
    route_feedback(tmp_path, build_feedback(_payload(), now=NOW))

    farm = materialize_role_environment(tmp_path, recipient="farm")
    validator = materialize_role_environment(tmp_path, recipient="validator")
    trader = materialize_role_environment(tmp_path, recipient="trader")

    assert farm[0]["request_kind"] == "bounded_experiment_request"
    assert validator[0]["request_kind"] == "validation_review_request"
    assert trader[0]["request_kind"] == "paper_decision_review"
    for row in (farm[0], validator[0], trader[0]):
        assert row["status"] == "candidate"
        assert row["requires_deterministic_gate"] is True
        assert row["mutates_code"] is False
        assert row["mutates_model_weights"] is False
        assert row["execution_allowed"] is False

    assert len(pending_feedback(tmp_path, "farm")) == 1
    assert len(pending_feedback(tmp_path, "validator")) == 1
    assert len(pending_feedback(tmp_path, "trader")) == 1

    accepted = accept_role_request(tmp_path, recipient="farm", environment_id=farm[0]["environment_id"])
    gate_ref, evaluation_ref = _gate_artifacts(tmp_path, farm[0]["environment_id"])
    candidate_path = tmp_path / "state" / "role_environments" / "farm" / f"{farm[0]['environment_id']}.json"
    immutable_candidate = candidate_path.read_bytes()
    gated = gate_role_environment(
        tmp_path,
        recipient="farm",
        environment_id=farm[0]["environment_id"],
        accepted=True,
        gate_result_ref=gate_ref,
        untouched_evaluation_ref=evaluation_ref,
    )
    assert gated["status"] == "accepted"
    assert accepted["status"] == "request_accepted"
    assert candidate_path.read_bytes() == immutable_candidate
    assert pending_feedback(tmp_path, "farm") == []

    import pytest
    with pytest.raises(ValueError, match="gated only once"):
        gate_role_environment(
            tmp_path, recipient="farm", environment_id=farm[0]["environment_id"],
            accepted=False, gate_result_ref=gate_ref,
            untouched_evaluation_ref=evaluation_ref,
        )


def test_role_environment_materialization_is_idempotent(tmp_path):
    route_feedback(tmp_path, build_feedback(_payload(), now=NOW))
    first = materialize_role_environment(tmp_path, recipient="farm")
    second = materialize_role_environment(tmp_path, recipient="farm")
    assert len(first) == 1
    assert second[0]["environment_id"] == first[0]["environment_id"]


def test_gate_requires_untouched_evaluation_evidence(tmp_path):
    route_feedback(tmp_path, build_feedback(_payload(), now=NOW))
    row = materialize_role_environment(tmp_path, recipient="farm")[0]
    import pytest

    accept_role_request(tmp_path, recipient="farm", environment_id=row["environment_id"])
    with pytest.raises(ValueError, match="gate and untouched evaluation"):
        gate_role_environment(
            tmp_path, recipient="farm", environment_id=row["environment_id"],
            accepted=True, gate_result_ref="gate:farm-1", untouched_evaluation_ref="",
        )


def test_role_environment_rejects_public_repository_as_private_root():
    import pytest

    with pytest.raises(ValueError, match="private Strategy Lab output root"):
        materialize_role_environment(PROJECT_ROOT, recipient="farm")


def test_role_environment_rejects_untrusted_environment_ids(tmp_path):
    import pytest

    for environment_id in ("../../../outside", r"..\..\outside", "C:/outside", "env_not_hex"):
        with pytest.raises(ValueError, match="invalid role environment id"):
            accept_role_request(tmp_path, recipient="farm", environment_id=environment_id)


def test_request_state_does_not_advance_when_ledger_ack_fails(tmp_path, monkeypatch):
    route_feedback(tmp_path, build_feedback(_payload(), now=NOW))
    row = materialize_role_environment(tmp_path, recipient="farm")[0]

    def fail_ack(*args, **kwargs):
        raise OSError("ledger unavailable")

    monkeypatch.setattr("src.research_lab.role_environment.acknowledge_feedback", fail_ack)
    import pytest
    with pytest.raises(OSError, match="ledger unavailable"):
        accept_role_request(tmp_path, recipient="farm", environment_id=row["environment_id"])
    state_path = (
        tmp_path / "state" / "role_environments" / "farm" / "_state"
        / f"{row['environment_id']}.json"
    )
    assert not state_path.exists()


def test_gate_ack_binds_artifact_bytes_before_state_projection(tmp_path, monkeypatch):
    route_feedback(tmp_path, build_feedback(_payload(), now=NOW))
    row = materialize_role_environment(tmp_path, recipient="farm")[0]
    accept_role_request(tmp_path, recipient="farm", environment_id=row["environment_id"])
    gate_ref, evaluation_ref = _gate_artifacts(tmp_path, row["environment_id"])
    original_write = __import__(
        "src.research_lab.role_environment", fromlist=["_write_state"]
    )._write_state

    monkeypatch.setattr(
        "src.research_lab.role_environment._write_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("projection failed")),
    )
    import pytest
    with pytest.raises(OSError, match="projection failed"):
        gate_role_environment(
            tmp_path, recipient="farm", environment_id=row["environment_id"], accepted=True,
            gate_result_ref=gate_ref, untouched_evaluation_ref=evaluation_ref,
        )
    evaluation_path = tmp_path / evaluation_ref
    payload = json.loads(evaluation_path.read_text())
    payload["changed_after_ack"] = True
    evaluation_path.write_text(json.dumps(payload))
    monkeypatch.setattr("src.research_lab.role_environment._write_state", original_write)
    with pytest.raises(ValueError, match="ack_id conflict"):
        gate_role_environment(
            tmp_path, recipient="farm", environment_id=row["environment_id"], accepted=True,
            gate_result_ref=gate_ref, untouched_evaluation_ref=evaluation_ref,
        )
