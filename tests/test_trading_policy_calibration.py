from __future__ import annotations

import json

from src.research_lab import trading_policy_calibration
from src.research_lab.trading_policy_calibration import (
    build_trading_policy_calibration,
    profile_verdict,
)


def _write_rows(tmp_path, rows):
    path = tmp_path / "state" / "derived" / "paper_signal_training.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _row(*, profile="base", net=0.5, lifecycle="PaperSignalLifecycle.v2", capture=0.6):
    return {
        "paper_only": True,
        "execution_allowed": False,
        "lifecycle_schema": lifecycle,
        "farm_geometry_profile_id": profile,
        "symbol": "X_USDT_SWAP",
        "timeframe": "15m",
        "family": "continuation",
        "boundary_ts": 100,
        "side": "long",
        "exit_mode": "fixed",
        "net_pct": net,
        "capture": capture,
        "diagnosis": "good_signal" if net > 0 else "valid_loss",
        "immutable_terminal_evidence": True,
        "paper_generation_run_id": "run-v2",
        "terminal_lifecycle_event_id": "lifecycle-v2",
        "account_generation_id": "account-v2",
    }


def _enable_current(monkeypatch):
    monkeypatch.setattr(
        trading_policy_calibration,
        "read_projection_view",
        lambda *_args, **_kwargs: {
            "current": True,
            "paper_generation_run_id": "run-v2",
            "generation_status": "completed",
        },
    )


def test_legacy_rows_are_visible_but_cannot_calibrate(tmp_path, monkeypatch):
    _enable_current(monkeypatch)
    _write_rows(tmp_path, [_row(lifecycle="legacy") for _ in range(50)])

    report = build_trading_policy_calibration(tmp_path)

    assert report["source_rows"] == 50
    assert report["legacy_rows_excluded"] == 50
    assert report["trusted_terminal_rows"] == 0
    assert report["calibration_ready"] is False
    assert report["by_profile"] == {}


def test_profile_requires_sample_and_reports_uncertainty(tmp_path, monkeypatch):
    _enable_current(monkeypatch)
    rows = [_row(net=0.7) for _ in range(18)] + [_row(net=-0.3) for _ in range(2)]
    _write_rows(tmp_path, rows)

    report = build_trading_policy_calibration(tmp_path)
    base = report["by_profile"]["base"]

    assert report["calibration_ready"] is True
    assert base["terminal_rows"] == 20
    assert base["wins"] == 18
    assert base["verdict"] == "retain_probe"
    assert len(base["win_rate_wilson_95"]) == 2
    assert report["comparison_kind"] == "observational_paper_outcomes_not_causal_attribution"


def test_losing_profile_is_demoted_only_after_enough_evidence(tmp_path, monkeypatch):
    _enable_current(monkeypatch)
    rows = [_row(profile="runner_probe", net=-0.8) for _ in range(18)]
    rows += [_row(profile="runner_probe", net=0.2) for _ in range(2)]
    _write_rows(tmp_path, rows)

    report = build_trading_policy_calibration(tmp_path)

    assert profile_verdict(report, "runner_probe") == "demote"
    assert report["paper_only"] is True
    assert report["execution_allowed"] is False


def test_untrusted_or_execution_rows_are_excluded(tmp_path, monkeypatch):
    _enable_current(monkeypatch)
    rows = [_row() for _ in range(20)]
    rows[0]["paper_only"] = False
    rows[1]["execution_allowed"] = True
    _write_rows(tmp_path, rows)

    report = build_trading_policy_calibration(tmp_path)

    assert report["trusted_terminal_rows"] == 18
    assert report["by_profile"]["base"]["verdict"] == "insufficient_evidence"


def test_reports_horizon_conflicts_account_and_kaito_acceptance(tmp_path, monkeypatch):
    _enable_current(monkeypatch)
    rows = [_row() for _ in range(12)]
    for row in rows:
        row["symbol"] = "KAITO_USDT_SWAP"
    rows[0]["paper_pnl_usdt"] = 2.5
    rows[1]["side"] = "short"
    _write_rows(tmp_path, rows)
    report = build_trading_policy_calibration(tmp_path)

    assert report["by_horizon"]["tactical"]["terminal_rows"] == 12
    assert report["by_exit_mode"]["fixed"]["terminal_rows"] == 12
    assert report["opposing_side_conflicts"]["opposing_side_cohorts"] == 1
    assert report["shared_account_primary"]["total_pnl_usdt"] == 2.5
    assert report["acceptance_cases"]["KAITO_USDT_SWAP"]["ready"] is True
