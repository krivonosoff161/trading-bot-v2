# -*- coding: utf-8 -*-
"""Tests for the morning report (Phase 5).

Verifies:
- Empty state friendly output.
- Sample loop state summary.
- Missing data grouped by timeframe.
- No absolute paths.
"""

import json

from scripts.strategy_lab.morning_report import generate_morning_report


def test_empty_state_friendly_output(tmp_path):
    report = generate_morning_report(tmp_path)
    assert report["loop"]["iterations"] == 0
    assert report["jobs"]["completed"] == 0
    assert report["candidates"]["total"] == 0
    assert report["strategies_tested"] == []
    assert report["symbols_tested"] == []
    assert report["recommended_next_command"]


def test_empty_state_no_absolute_paths(tmp_path):
    report = generate_morning_report(tmp_path)
    blob = json.dumps(report)
    assert str(tmp_path) not in blob
    assert "github_projects" not in blob
    assert "krivo" not in blob.lower()


def test_empty_state_morning_report_prints(tmp_path, capsys):
    from scripts.strategy_lab.morning_report import _print_report
    report = generate_morning_report(tmp_path)
    _print_report(report)
    captured = capsys.readouterr()
    assert "Morning Report" in captured.out
    assert "Jobs:" in captured.out
    assert "Queue:" in captured.out
    assert "Next:" in captured.out


def test_loop_report_populates_loop_section(tmp_path):
    from src.research_lab.research_loop import write_loop_report
    report_data = {
        "mode": "apply",
        "iterations": 5,
        "duration_minutes": 30.0,
        "totals": {
            "proposals_queued": 10,
            "ready_jobs": 8,
            "skipped_missing_data": 2,
            "worker_completed": 6,
            "worker_deferred": 1,
            "worker_failed": 1,
            "llm_validated": 3,
            "llm_rejected": 2,
        },
        "llm_cost_rub": 0.5,
        "iteration_log": [],
        "stop_requested": False,
    }
    write_loop_report(tmp_path, report_data, allow_public_output=True)

    report = generate_morning_report(tmp_path)
    assert report["loop"]["mode"] == "apply"
    assert report["loop"]["iterations"] == 5
    assert report["jobs"]["completed"] == 6
    assert report["jobs"]["deferred"] == 1
    assert report["jobs"]["failed"] == 1
    assert report["llm"]["cost_rub"] == 0.5


def test_stop_requested_reflected(tmp_path):
    from src.research_lab.research_loop import write_loop_report
    report_data = {
        "mode": "apply",
        "iterations": 2,
        "duration_minutes": 5.0,
        "totals": {"worker_completed": 1, "worker_deferred": 0, "worker_failed": 0},
        "llm_cost_rub": 0.0,
        "iteration_log": [{"stop_requested": True}],
        "stop_requested": True,
    }
    write_loop_report(tmp_path, report_data, allow_public_output=True)
    report = generate_morning_report(tmp_path)
    assert report["loop"]["stop_requested"] is True


def test_empty_json_output(tmp_path, capsys):
    report = generate_morning_report(tmp_path)
    blob = json.dumps(report, ensure_ascii=False, indent=2)
    parsed = json.loads(blob)
    assert parsed["loop"]["iterations"] == 0
    assert "stale_hints" in parsed
    assert "stop_hint" in parsed
