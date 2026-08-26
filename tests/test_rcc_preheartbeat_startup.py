from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import research_control_center as rcc
from scripts.strategy_lab import rcc_startup_status
from src.research_lab import rcc_startup_evidence
from src.research_lab.rcc_startup_evidence import (
    RccRunIdentity,
    RccStartupEvidenceError,
    RccStartupEvidenceWriter,
    assess_rcc_preheartbeat_liveness,
    load_rcc_startup_evidence,
    rcc_run_identity_from_environment,
)


REVISION = "6" * 40


def writer(path: Path) -> RccStartupEvidenceWriter:
    return RccStartupEvidenceWriter(
        path,
        revision=REVISION,
        pid=4100,
        process_started_at=100.25,
        started_at=101.0,
    )


def test_revision_bound_startup_evidence_is_digest_verified(tmp_path: Path) -> None:
    path = tmp_path / "startup.json"
    expected = writer(path).transition("ui_initializing", now=102.0)

    loaded = load_rcc_startup_evidence(path)

    assert loaded == expected
    assert loaded["revision"] == REVISION
    assert loaded["paper_only"] is True
    assert loaded["execution_allowed"] is False


def test_startup_identity_round_trips_only_as_a_complete_child_envelope(
    tmp_path: Path,
) -> None:
    run = RccRunIdentity.from_startup_writer(writer(tmp_path / "startup.json"))

    assert rcc_run_identity_from_environment(run.to_child_environment()) == run
    assert rcc_run_identity_from_environment({}) is None
    with pytest.raises(ValueError, match="incomplete"):
        rcc_run_identity_from_environment(
            {"TRADING_BOT_RCC_ATTEMPT_ID": run.attempt_id}
        )


def test_failure_evidence_never_persists_exception_message(tmp_path: Path) -> None:
    path = tmp_path / "startup.json"
    startup = writer(path)
    secret_like = "synthetic-token-value-must-not-survive"

    startup.fail("ui_initializing", RuntimeError(secret_like), now=102.0)

    raw = path.read_text(encoding="utf-8")
    loaded = load_rcc_startup_evidence(path)
    assert secret_like not in raw
    assert loaded["failure_type"] == "RuntimeError"
    assert loaded["state"] == "failed"


def test_tampered_startup_evidence_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "startup.json"
    writer(path).transition("lock_acquired")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["pid"] = 9999
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RccStartupEvidenceError, match="digest"):
        load_rcc_startup_evidence(path)


def test_malformed_attempt_identity_is_rejected_even_with_valid_digest(
    tmp_path: Path,
) -> None:
    path = tmp_path / "startup.json"
    expected = writer(path).transition("lock_acquired")
    expected["attempt_id"] = "rccstartup_not-a-real-attempt"
    expected.pop("record_digest")
    expected["record_digest"] = rcc_startup_evidence._digest(expected)
    path.write_text(json.dumps(expected), encoding="utf-8")

    with pytest.raises(RccStartupEvidenceError, match="values"):
        load_rcc_startup_evidence(path)


def test_startup_stage_cannot_regress(tmp_path: Path) -> None:
    startup = writer(tmp_path / "startup.json")
    startup.transition("heartbeat_started")

    with pytest.raises(ValueError, match="backwards"):
        startup.transition("lock_acquired")


def test_terminal_startup_evidence_cannot_be_overwritten(tmp_path: Path) -> None:
    startup = writer(tmp_path / "startup.json")
    startup.fail("ui_initializing", RuntimeError("synthetic"))

    with pytest.raises(ValueError, match="terminal"):
        startup.transition("heartbeat_started")


def test_live_exact_process_is_starting_before_heartbeat(tmp_path: Path) -> None:
    payload = writer(tmp_path / "startup.json").transition("ui_initializing")

    result = assess_rcc_preheartbeat_liveness(
        payload,
        expected_revision=REVISION,
        previous_attempt_id=None,
        live_process_started_at=100.25,
    )

    assert result.state == "starting"
    assert result.reason == "rcc_preheartbeat:ui_initializing"


def test_dead_process_before_heartbeat_is_immediate_failure(tmp_path: Path) -> None:
    payload = writer(tmp_path / "startup.json").transition("ui_initializing")

    result = assess_rcc_preheartbeat_liveness(
        payload,
        expected_revision=REVISION,
        previous_attempt_id=None,
        live_process_started_at=None,
    )

    assert result.state == "failed"
    assert result.reason == "rcc_process_exited_before_heartbeat"


def test_pid_reuse_cannot_satisfy_preheartbeat_liveness(tmp_path: Path) -> None:
    payload = writer(tmp_path / "startup.json").transition("ui_initializing")

    result = assess_rcc_preheartbeat_liveness(
        payload,
        expected_revision=REVISION,
        previous_attempt_id=None,
        live_process_started_at=200.0,
    )

    assert result.state == "failed"
    assert result.reason == "rcc_process_generation_mismatch"


def test_stale_attempt_does_not_grant_current_startup_liveness(tmp_path: Path) -> None:
    payload = writer(tmp_path / "startup.json").transition("mainloop_running")

    result = assess_rcc_preheartbeat_liveness(
        payload,
        expected_revision=REVISION,
        previous_attempt_id=payload["attempt_id"],
        live_process_started_at=None,
        startup_requested_at=200.0,
        now=205.0,
    )

    assert result.state == "starting"
    assert result.reason == "startup_evidence_pending_new_attempt"


def test_stale_attempt_fails_after_new_attempt_grace(tmp_path: Path) -> None:
    payload = writer(tmp_path / "startup.json").transition("mainloop_running")

    result = assess_rcc_preheartbeat_liveness(
        payload,
        expected_revision=REVISION,
        previous_attempt_id=payload["attempt_id"],
        live_process_started_at=None,
        startup_requested_at=200.0,
        now=215.0,
    )

    assert result.state == "failed"
    assert result.reason == "startup_evidence_not_advanced"


def test_different_but_old_attempt_cannot_satisfy_current_launch(tmp_path: Path) -> None:
    payload = writer(tmp_path / "startup.json").transition("ui_initializing")

    result = assess_rcc_preheartbeat_liveness(
        payload,
        expected_revision=REVISION,
        previous_attempt_id="rccstartup_" + "f" * 32,
        live_process_started_at=100.25,
        startup_requested_at=200.0,
        now=201.0,
    )

    assert result.state == "failed"
    assert result.reason == "startup_evidence_predates_launch"


def test_recorded_preheartbeat_exception_is_immediate_failure(tmp_path: Path) -> None:
    path = tmp_path / "startup.json"
    startup = writer(path)
    payload = startup.fail("ui_initializing", RuntimeError("not persisted"))

    result = assess_rcc_preheartbeat_liveness(
        payload,
        expected_revision=REVISION,
        previous_attempt_id=None,
        live_process_started_at=None,
    )

    assert result.state == "failed"
    assert result.reason == "rcc_preheartbeat_failed:RuntimeError"


def test_revision_mismatch_is_fail_closed(tmp_path: Path) -> None:
    payload = writer(tmp_path / "startup.json").transition("lock_acquired")

    result = assess_rcc_preheartbeat_liveness(
        payload,
        expected_revision="7" * 40,
        previous_attempt_id=None,
        live_process_started_at=100.25,
    )

    assert result.state == "failed"
    assert result.reason == "startup_evidence_revision_mismatch"


def test_status_adapter_reports_failed_process_without_private_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "startup.json"
    writer(path).transition("heartbeat_starting")
    monkeypatch.setattr(rcc_startup_status, "probe_process_identity", lambda _pid: None)

    result = rcc_startup_status.assess_status(
        path,
        revision=REVISION,
        previous_attempt_id=None,
    )

    assert result.to_dict() == {
        "schema": "RccPreheartbeatAssessment.v1",
        "state": "failed",
        "reason": "rcc_process_exited_before_heartbeat",
        "attempt_id": result.attempt_id,
        "pid": 4100,
        "paper_only": True,
        "execution_allowed": False,
    }


def test_missing_startup_evidence_fails_after_launch_grace(tmp_path: Path) -> None:
    result = rcc_startup_status.assess_status(
        tmp_path / "missing.json",
        revision=REVISION,
        previous_attempt_id=None,
        startup_requested_at=100.0,
        now=115.0,
    )

    assert result.state == "failed"
    assert result.reason == "startup_evidence_missing_after_launch"


def test_invalid_existing_evidence_fails_immediately(tmp_path: Path) -> None:
    path = tmp_path / "startup.json"
    path.write_text("{}", encoding="utf-8")

    result = rcc_startup_status.assess_status(
        path,
        revision=REVISION,
        previous_attempt_id=None,
        startup_requested_at=100.0,
        now=101.0,
    )

    assert result.state == "failed"
    assert result.reason == "startup_evidence_invalid"


def test_process_probe_failure_is_sanitized_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "startup.json"
    writer(path).transition("heartbeat_starting")
    monkeypatch.setattr(
        rcc_startup_status,
        "probe_process_identity",
        lambda _pid: (_ for _ in ()).throw(RuntimeError("synthetic private detail")),
    )

    result = rcc_startup_status.assess_status(
        path,
        revision=REVISION,
        previous_attempt_id=None,
    )

    assert result.state == "failed"
    assert result.reason == "rcc_process_identity_probe_failed"


class _FakeInstance:
    closed = False

    def __init__(self, _path: Path) -> None:
        type(self).closed = False

    def close(self) -> None:
        type(self).closed = True


def _prepare_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    status = tmp_path / "control-center" / "startup.json"
    monkeypatch.setattr(rcc, "STATE_DIR", status.parent)
    monkeypatch.setattr(rcc, "STARTUP_STATUS_PATH", status)
    monkeypatch.setattr(rcc, "current_checkout_revision", lambda _root: REVISION)
    monkeypatch.setattr(
        rcc,
        "current_process_identity",
        lambda: SimpleNamespace(pid=4100, started_at=100.25),
    )
    monkeypatch.setattr(rcc, "SingleInstance", _FakeInstance)
    monkeypatch.setattr(rcc.sys, "argv", ["research_control_center.py"])
    return status


def test_main_persists_preheartbeat_failure_and_closes_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = _prepare_main(tmp_path, monkeypatch)
    secret_like = "synthetic-private-error-value"

    class FailingControlCenter:
        def __init__(
            self,
            _instance: object,
            _autostart: tuple[str, ...],
            *,
            rcc_run: RccRunIdentity,
        ) -> None:
            del rcc_run
            raise RuntimeError(secret_like)

    monkeypatch.setattr(rcc, "ControlCenter", FailingControlCenter)

    assert rcc.main() == 2
    loaded = load_rcc_startup_evidence(status)
    captured = capsys.readouterr()
    assert loaded["stage"] == "ui_initializing"
    assert loaded["failure_type"] == "RuntimeError"
    assert secret_like not in status.read_text(encoding="utf-8")
    assert secret_like not in captured.err
    assert _FakeInstance.closed is True


def test_evidence_write_failure_still_closes_lock_without_leaking_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare_main(tmp_path, monkeypatch)
    private_detail = "synthetic evidence write detail"

    class FailingControlCenter:
        def __init__(
            self,
            _instance: object,
            _autostart: tuple[str, ...],
            *,
            rcc_run: RccRunIdentity,
        ) -> None:
            del rcc_run
            raise RuntimeError("synthetic constructor detail")

    def fail_evidence(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise OSError(private_detail)

    monkeypatch.setattr(rcc, "ControlCenter", FailingControlCenter)
    monkeypatch.setattr(RccStartupEvidenceWriter, "fail", fail_evidence)

    assert rcc.main() == 2
    captured = capsys.readouterr()
    assert private_detail not in captured.err
    assert "OSError" in captured.err
    assert _FakeInstance.closed is True


def test_main_normal_stop_leaves_no_heartbeat_publisher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = _prepare_main(tmp_path, monkeypatch)

    class FakeControlCenter:
        stopped = 0

        def __init__(
            self,
            _instance: object,
            _autostart: tuple[str, ...],
            *,
            rcc_run: RccRunIdentity,
        ) -> None:
            del rcc_run
            pass

        def mainloop(self) -> None:
            return None

        def _stop_heartbeat_publisher(self) -> None:
            type(self).stopped += 1

    monkeypatch.setattr(rcc, "ControlCenter", FakeControlCenter)

    assert rcc.main() == 0
    loaded = load_rcc_startup_evidence(status)
    assert loaded["stage"] == "mainloop_stopped"
    assert loaded["state"] == "stopped"
    assert FakeControlCenter.stopped == 1
    assert _FakeInstance.closed is True


def test_mainloop_exception_stops_publisher_once_and_closes_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = _prepare_main(tmp_path, monkeypatch)

    class FailingMainloopControlCenter:
        stopped = 0

        def __init__(
            self,
            _instance: object,
            _autostart: tuple[str, ...],
            *,
            rcc_run: RccRunIdentity,
        ) -> None:
            del rcc_run
            pass

        def mainloop(self) -> None:
            raise RuntimeError("synthetic detail")

        def _stop_heartbeat_publisher(self) -> None:
            type(self).stopped += 1

    monkeypatch.setattr(rcc, "ControlCenter", FailingMainloopControlCenter)

    assert rcc.main() == 2
    loaded = load_rcc_startup_evidence(status)
    assert loaded["state"] == "failed"
    assert loaded["stage"] == "mainloop_running"
    assert FailingMainloopControlCenter.stopped == 1
    assert _FakeInstance.closed is True


def test_batch_preserves_failure_exit_code_and_allows_noninteractive_capture() -> None:
    source = (rcc.ROOT / "bat" / "research_control_center.bat").read_text(
        encoding="utf-8"
    )

    assert 'if not "%RC%"=="0" if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause' in source
    assert "endlocal & exit /b %RC%" in source
