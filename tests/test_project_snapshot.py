from __future__ import annotations

import json
import time

from scripts import project_snapshot


def test_classify_canonical_farm_paper_loop() -> None:
    cmd = (
        "python -m scripts.strategy_lab.farm_loop --apply --loop "
        "--run-worker --run-validation --run-paper --run-paper-signals"
    )

    assert project_snapshot.classify_process(cmd) == "canonical_farm_paper_loop"


def test_classify_main_engine() -> None:
    assert project_snapshot.classify_process(r"python C:\repo\main.py") == "main_engine"


def test_bot_status_ignores_unrelated_python() -> None:
    report = project_snapshot.bot_status(
        [
            {"ProcessId": 1, "CommandLine": "python scripts/project_snapshot.py"},
            {"ProcessId": 2, "CommandLine": "python -m pytest tests/test_x.py"},
        ]
    )

    assert report["relevant"] == []
    assert report["ignored_python"] == 2
    assert report["by_kind"] == {}


def test_bot_status_counts_only_relevant_processes() -> None:
    report = project_snapshot.bot_status(
        [
            {
                "ProcessId": 10,
                "CommandLine": (
                    "python -m scripts.strategy_lab.farm_loop --apply "
                    "--run-paper-signals --run-paper"
                ),
            },
            {"ProcessId": 11, "CommandLine": "python -m scripts.strategy_lab.paper_signals_run --status"},
            {"ProcessId": 12, "CommandLine": "python scripts/project_snapshot.py"},
        ]
    )

    assert report["ignored_python"] == 1
    assert report["by_kind"] == {
        "canonical_farm_paper_loop": 1,
        "paper_signals_runner": 1,
    }
    assert [row["pid"] for row in report["relevant"]] == [10, 11]


def test_bot_status_recovers_farm_loop_from_fresh_status(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "farm_loop_status.json").write_text(
        json.dumps({
            "pid": 12345,
            "stage": "sleep",
            "updated_at": time.time(),
            "paper_only": True,
            "execution_allowed": False,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADING_BOT_RESEARCH_ROOT", str(tmp_path))
    monkeypatch.setattr(project_snapshot, "python_processes", lambda: [])
    monkeypatch.setattr(project_snapshot, "_pid_exists", lambda pid: pid == 12345)

    report = project_snapshot.bot_status()

    assert report["by_kind"] == {"canonical_farm_paper_loop": 1}
    assert report["relevant"][0]["pid"] == 12345
