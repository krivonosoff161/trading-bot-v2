from __future__ import annotations

import gc
from pathlib import Path
from typing import Any

from src.research_lab import trade_path_diagnostics as diagnostics


def test_characterize_rejects_keeps_one_run_artifact_index_at_a_time(
    monkeypatch,
    tmp_path: Path,
) -> None:
    tracker = {"alive": 0, "max_alive": 0}

    class TrackedRunIndex(dict[str, dict[str, Any]]):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            tracker["alive"] += 1
            tracker["max_alive"] = max(tracker["max_alive"], tracker["alive"])

        def __del__(self) -> None:
            tracker["alive"] -= 1

    source = [
        {
            "uc_key": "uc-0",
            "symbol": "X",
            "timeframe": "1h",
            "family": "momentum_breakout",
            "params_hash": "ph-0",
            "n_trades": 10,
            "avg_net_pct": -0.1,
            "regime_bucket": "",
            "hard_status": "",
            "run_dir_label": "run-a",
        },
        {
            "uc_key": "uc-1",
            "symbol": "X",
            "timeframe": "1h",
            "family": "momentum_breakout",
            "params_hash": "ph-1",
            "n_trades": 10,
            "avg_net_pct": -0.1,
            "regime_bucket": "",
            "hard_status": "",
            "run_dir_label": "run-b",
        },
        {
            "uc_key": "uc-2",
            "symbol": "X",
            "timeframe": "1h",
            "family": "momentum_breakout",
            "params_hash": "ph-2",
            "n_trades": 10,
            "avg_net_pct": -0.1,
            "regime_bucket": "",
            "hard_status": "",
            "run_dir_label": "run-a",
        },
        {
            "uc_key": "uc-3",
            "symbol": "X",
            "timeframe": "1h",
            "family": "momentum_breakout",
            "params_hash": "ph-3",
            "n_trades": 10,
            "avg_net_pct": -0.1,
            "regime_bucket": "",
            "hard_status": "",
            "run_dir_label": "run-c",
        },
    ]
    by_run = {
        "run-a": ("ph-0", "ph-2"),
        "run-b": ("ph-1",),
        "run-c": ("ph-3",),
    }

    def load_run(_private_root: Path, label: str):
        return TrackedRunIndex(
            {
                params_hash: {
                    "metrics": {"n_trades": 10, "avg_net_pct": -0.1},
                    "trades": [],
                }
                for params_hash in by_run[label]
            }
        )

    monkeypatch.setattr(diagnostics, "_load_rejected_uc", lambda _root: source)
    monkeypatch.setattr(diagnostics, "_index_run_results", load_run)
    monkeypatch.setattr(diagnostics, "oi_micro_families", set)
    milestones: list[tuple[str, int, int]] = []
    active_checks = 0

    def check_active() -> None:
        nonlocal active_checks
        active_checks += 1

    rows = diagnostics.characterize_rejects(
        tmp_path,
        progress=lambda stage, completed, total: milestones.append(
            (stage, completed, total)
        ),
        check_active=check_active,
    )
    gc.collect()

    assert [row["uc_key"] for row in rows] == [
        "uc-0",
        "uc-1",
        "uc-2",
        "uc-3",
    ]
    assert tracker == {"alive": 0, "max_alive": 1}
    assert ("run_artifacts_released", 3, 3) in milestones
    assert ("rejects_characterized", 4, 4) in milestones
    assert active_checks >= len(source) + len(by_run) * 2
