from __future__ import annotations

import json

from src.research_lab.telegram_bot_health import assess_health, publish_health


def test_health_publisher_is_secret_safe_and_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_lab.telegram_bot_health.os.getpid", lambda: 1234)

    payload = publish_health(
        tmp_path,
        state="degraded",
        started_at=100.0,
        updated_at=110.0,
        last_success_at=105.0,
        consecutive_failures=2,
        failure_type="ClientConnectorError: synthetic-value-must-not-survive",
    )

    stored = json.loads(
        (tmp_path / "state" / "telegram_bot_health.json").read_text(
            encoding="utf-8"
        )
    )
    assert stored == payload
    assert stored["failure_type"] == "ClientConnectorError"
    assert "synthetic" not in json.dumps(stored)
    assert stored["paper_only"] is True
    assert stored["execution_allowed"] is False
    assert "token" not in stored
    assert "recipient" not in stored
    assert not list((tmp_path / "state").glob(".*.tmp"))


def test_health_requires_identity_matched_current_poll() -> None:
    payload = {
        "schema": "TelegramBotHealth.v1",
        "pid": 42,
        "state": "ready",
        "started_at": 101.0,
        "updated_at": 110.0,
        "last_success_at": 110.0,
        "paper_only": True,
        "execution_allowed": False,
    }

    ready = assess_health(
        payload,
        expected_pid=42,
        run_started_at=100.0,
        now=115.0,
        startup_budget_seconds=600.0,
        stale_seconds=120.0,
        require_ready=False,
    )
    wrong_pid = assess_health(
        payload,
        expected_pid=43,
        run_started_at=100.0,
        now=115.0,
        startup_budget_seconds=600.0,
        stale_seconds=120.0,
        require_ready=False,
    )

    assert ready.ready is True
    assert ready.hard_failure is None
    assert wrong_pid.ready is False
    assert wrong_pid.state == "starting"


def test_fresh_success_survives_one_transient_poll_error() -> None:
    payload = {
        "schema": "TelegramBotHealth.v1",
        "pid": 42,
        "state": "degraded",
        "started_at": 101.0,
        "updated_at": 119.0,
        "last_success_at": 118.0,
        "consecutive_failures": 1,
        "failure_type": "ClientConnectorError",
        "paper_only": True,
        "execution_allowed": False,
    }

    assessment = assess_health(
        payload,
        expected_pid=42,
        run_started_at=100.0,
        now=120.0,
        startup_budget_seconds=600.0,
        stale_seconds=120.0,
        require_ready=True,
    )

    assert assessment.ready is True
    assert assessment.state == "degraded"
    assert assessment.hard_failure is None


def test_stale_poll_fails_closed_after_t0() -> None:
    payload = {
        "schema": "TelegramBotHealth.v1",
        "pid": 42,
        "state": "degraded",
        "started_at": 101.0,
        "updated_at": 300.0,
        "last_success_at": 150.0,
        "consecutive_failures": 10,
        "failure_type": "ClientConnectorError",
        "paper_only": True,
        "execution_allowed": False,
    }

    assessment = assess_health(
        payload,
        expected_pid=42,
        run_started_at=100.0,
        now=300.0,
        startup_budget_seconds=600.0,
        stale_seconds=120.0,
        require_ready=True,
    )

    assert assessment.ready is False
    assert assessment.hard_failure == "telegram_bot_poll_stale"


def test_missing_poll_fails_only_after_bounded_startup_budget() -> None:
    early = assess_health(
        {},
        expected_pid=42,
        run_started_at=100.0,
        now=699.0,
        startup_budget_seconds=600.0,
        stale_seconds=120.0,
        require_ready=False,
    )
    late = assess_health(
        {},
        expected_pid=42,
        run_started_at=100.0,
        now=701.0,
        startup_budget_seconds=600.0,
        stale_seconds=120.0,
        require_ready=False,
    )

    assert early.state == "starting"
    assert early.hard_failure is None
    assert late.hard_failure == "telegram_bot_poll_unready"


def test_malformed_health_payload_fails_closed_without_raising() -> None:
    assessment = assess_health(
        {
            "schema": "TelegramBotHealth.v1",
            "pid": "not-a-pid",
            "started_at": "not-a-time",
            "last_success_at": "not-a-time",
            "paper_only": True,
            "execution_allowed": False,
        },
        expected_pid=42,
        run_started_at=100.0,
        now=701.0,
        startup_budget_seconds=600.0,
        stale_seconds=120.0,
        require_ready=False,
    )

    assert assessment.ready is False
    assert assessment.hard_failure == "telegram_bot_poll_unready"
