from __future__ import annotations

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
