from __future__ import annotations

import gc
import json
import os
from pathlib import Path
from typing import Any

import pytest

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


def _source_row(index: int, *, label: str, updated_at: float = 1.0) -> dict[str, Any]:
    return {
        "uc_key": f"uc-{index}",
        "symbol": "X",
        "timeframe": "1h",
        "family": "momentum_breakout",
        "params_hash": f"ph-{index}",
        "n_trades": 10,
        "avg_net_pct": -0.1,
        "regime_bucket": "",
        "hard_status": "",
        "validation_status": "REJECT",
        "decision": "REJECT",
        "run_dir_label": label,
        "updated_at": updated_at,
    }


def _write_run(tmp_path: Path, label: str, rows: list[dict[str, Any]]) -> None:
    target = tmp_path / label / "metrics.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "params": {"id": row["params_hash"]},
                        "metrics": {"n_trades": 10, "avg_net_pct": -0.1},
                        "trades": [],
                    }
                    for row in rows
                ]
            }
        ),
        encoding="utf-8",
    )


def test_incremental_reject_cache_skips_unchanged_historical_run_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = [
        _source_row(0, label="runs/a"),
        _source_row(1, label="runs/a"),
        _source_row(2, label="runs/b"),
    ]
    _write_run(tmp_path, "runs/a", source[:2])
    _write_run(tmp_path, "runs/b", source[2:])
    monkeypatch.setattr(diagnostics, "_load_rejected_uc", lambda _root: source)
    monkeypatch.setattr(diagnostics, "oi_micro_families", set)
    # Keep the result lookup deterministic without coupling this cache test to
    # the public params-hash implementation.
    calls: list[str] = []

    def load_run(_root: Path, label: str):
        calls.append(label)
        members = [row for row in source if row["run_dir_label"] == label]
        return {
            row["params_hash"]: {
                "metrics": {"n_trades": 10, "avg_net_pct": -0.1},
                "trades": [],
            }
            for row in members
        }

    monkeypatch.setattr(diagnostics, "_index_run_results", load_run)
    cache = tmp_path / "state" / "derived" / "reject-cache.json"

    first, first_stats = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=cache,
    )
    calls.clear()
    second, second_stats = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=cache,
    )

    assert [row["uc_key"] for row in first] == [row["uc_key"] for row in second]
    assert first_stats["recomputed"] == 3
    assert first_stats["run_artifacts_reread"] == 2
    assert second_stats["cache_hits"] == 3
    assert second_stats["recomputed"] == 0
    assert second_stats["run_artifacts_reread"] == 0
    assert second_stats["cache_written"] is False
    assert calls == []


def test_incremental_reject_cache_matches_full_characterization_on_cache_miss(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = [
        _source_row(0, label="runs/a"),
        _source_row(1, label="runs/a"),
    ]
    monkeypatch.setattr(diagnostics, "_load_rejected_uc", lambda _root: source)
    monkeypatch.setattr(diagnostics, "oi_micro_families", set)

    def load_run(_root: Path, _label: str):
        return {
            "ph-0": {
                "metrics": {"n_trades": 10, "avg_net_pct": -0.1},
                "trades": [],
            },
            "ph-1": {
                "metrics": {"n_trades": 2, "avg_net_pct": 0.2},
                "trades": [],
            },
        }

    monkeypatch.setattr(diagnostics, "_index_run_results", load_run)
    metrics = tmp_path / "runs" / "a" / "metrics.json"
    metrics.parent.mkdir(parents=True, exist_ok=True)
    metrics.write_text("{}", encoding="utf-8")

    full = diagnostics.characterize_rejects(tmp_path)
    incremental, stats = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=tmp_path / "state" / "derived" / "reject-cache.json",
    )

    assert incremental == full
    assert stats["recomputed"] == len(full)


def test_exact_26845_source_cold_cache_matches_warm_cache(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """The incident-sized cold corpus is semantically equal after a warm resume."""

    source = [
        _source_row(index, label="runs/a" if index < 13_423 else "runs/b")
        for index in range(26_845)
    ]
    monkeypatch.setattr(diagnostics, "_load_rejected_uc", lambda _root: source)
    monkeypatch.setattr(diagnostics, "oi_micro_families", set)

    calls: list[str] = []

    def load_run(_root: Path, label: str):
        calls.append(label)
        return {
            row["params_hash"]: {
                "metrics": {"n_trades": 10, "avg_net_pct": -0.1},
                "trades": [],
            }
            for row in source
            if row["run_dir_label"] == label
        }

    monkeypatch.setattr(diagnostics, "_index_run_results", load_run)
    cache = tmp_path / "state" / "derived" / "reject-cache.json"

    cold, cold_stats = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=cache,
    )
    calls.clear()
    warm, warm_stats = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=cache,
    )

    assert cold_stats["cache_input_state"] == "missing"
    assert cold_stats["recomputed"] == 26_845
    assert cold_stats["run_artifacts_reread"] == 2
    assert cold_stats["cache_complete"] is True
    assert warm_stats["cache_hits"] == 26_845
    assert warm_stats["recomputed"] == 0
    assert warm_stats["run_artifacts_reread"] == 0
    assert warm_stats["cache_input_state"] == "ready_complete"
    assert calls == []
    assert json.dumps(cold, sort_keys=True) == json.dumps(warm, sort_keys=True)


def test_incremental_reject_cache_checkpoint_resumes_after_interruption(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = [
        _source_row(0, label="runs/a"),
        _source_row(1, label="runs/a"),
        _source_row(2, label="runs/b"),
    ]
    monkeypatch.setattr(diagnostics, "_load_rejected_uc", lambda _root: source)
    monkeypatch.setattr(diagnostics, "oi_micro_families", set)
    calls: list[str] = []

    def load_run(_root: Path, label: str):
        calls.append(label)
        return {
            row["params_hash"]: {
                "metrics": {"n_trades": 10, "avg_net_pct": -0.1},
                "trades": [],
            }
            for row in source
            if row["run_dir_label"] == label
        }

    class Interrupted(RuntimeError):
        pass

    def stop_after_first_checkpoint(stage: str, _completed: int, _total: int) -> None:
        if stage == "incremental_cache_checkpointed":
            raise Interrupted("synthetic stop after safe checkpoint")

    monkeypatch.setattr(diagnostics, "_index_run_results", load_run)
    cache = tmp_path / "state" / "derived" / "reject-cache.json"
    with pytest.raises(Interrupted, match="safe checkpoint"):
        diagnostics.characterize_rejects_incremental(
            tmp_path,
            cache_path=cache,
            progress=stop_after_first_checkpoint,
        )

    partial = json.loads(cache.read_text(encoding="utf-8"))
    assert partial["complete"] is False
    assert sorted(partial["items"]) == ["uc-0", "uc-1"]
    calls.clear()
    rows, resumed = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=cache,
    )

    assert [row["uc_key"] for row in rows] == ["uc-0", "uc-1", "uc-2"]
    assert resumed["cache_input_state"] == "ready_partial"
    assert resumed["cache_hits"] == 2
    assert resumed["recomputed"] == 1
    assert calls == ["runs/b"]
    assert json.loads(cache.read_text(encoding="utf-8"))["complete"] is True


def test_incremental_reject_cache_corruption_and_one_source_delta_fail_safe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = [_source_row(index, label="") for index in range(3)]
    monkeypatch.setattr(diagnostics, "_load_rejected_uc", lambda _root: source)
    monkeypatch.setattr(diagnostics, "oi_micro_families", set)
    cache = tmp_path / "state" / "derived" / "reject-cache.json"

    diagnostics.characterize_rejects_incremental(tmp_path, cache_path=cache)
    cache.write_text("not-json", encoding="utf-8")
    _rows, corrupt = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=cache,
    )
    assert corrupt["cache_input_state"] == "unreadable"
    assert corrupt["recomputed"] == 3

    source[1] = {**source[1], "updated_at": 2.0}
    _rows, delta = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=cache,
    )
    assert delta["cache_hits"] == 2
    assert delta["recomputed"] == 1


def test_incremental_reject_cache_invalidates_classifier_context_change(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = [_source_row(0, label="")]
    monkeypatch.setattr(diagnostics, "_load_rejected_uc", lambda _root: source)
    monkeypatch.setattr(diagnostics, "oi_micro_families", lambda: set())
    cache = tmp_path / "state" / "derived" / "reject-cache.json"
    first, _first_stats = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=cache,
    )

    monkeypatch.setattr(
        diagnostics,
        "oi_micro_families",
        lambda: {"momentum_breakout"},
    )
    second, second_stats = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=cache,
    )

    assert first[0]["reject_subreason"] == "confirmed_bad"
    assert second[0]["reject_subreason"] == "missing_oi_micro"
    assert second_stats["cache_hits"] == 0
    assert second_stats["recomputed"] == 1


def test_incremental_reject_cache_bootstraps_old_snapshot_and_rereads_only_delta(
    monkeypatch,
    tmp_path: Path,
) -> None:
    old = [
        _source_row(index, label="runs/old", updated_at=1.0)
        for index in range(2_000)
    ]
    new = [_source_row(2_000, label="runs/new", updated_at=30.0)]
    source = old + new
    _write_run(tmp_path, "runs/old", old)
    _write_run(tmp_path, "runs/new", new)
    snapshot = tmp_path / "state" / "derived" / "setup_outcome_memory.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "uc_key": row["uc_key"],
                        "symbol": row["symbol"],
                        "timeframe": row["timeframe"],
                        "family": row["family"],
                        "regime_bucket": "",
                        "hard_status": "",
                        "rejection_reason": "confirmed_bad",
                        "n_trades": 10,
                        "baseline_net": -0.1,
                        "avg_mfe_pct": 0.0,
                        "avg_mae_pct": 0.0,
                        "avg_capture_ratio": 0.0,
                    }
                    for row in old
                ]
            }
        ),
        encoding="utf-8",
    )
    os.utime(tmp_path / "runs/old" / "metrics.json", (10.0, 10.0))
    os.utime(snapshot, (20.0, 20.0))
    os.utime(tmp_path / "runs/new" / "metrics.json", (30.0, 30.0))
    monkeypatch.setattr(diagnostics, "_load_rejected_uc", lambda _root: source)
    monkeypatch.setattr(diagnostics, "oi_micro_families", set)
    calls: list[str] = []

    def load_run(_root: Path, label: str):
        calls.append(label)
        return {
            "ph-2000": {
                "metrics": {"n_trades": 10, "avg_net_pct": -0.1},
                "trades": [],
            }
        }

    monkeypatch.setattr(diagnostics, "_index_run_results", load_run)

    rows, stats = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=tmp_path / "state" / "derived" / "reject-cache.json",
        bootstrap_snapshot_path=snapshot,
    )

    assert len(rows) == 2_001
    assert stats["snapshot_bootstrap_hits"] == 2_000
    assert stats["recomputed"] == 1
    assert stats["run_artifacts_reread"] == 1
    assert calls == ["runs/new"]


def test_incremental_reject_cache_invalidates_one_changed_run_group(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = [
        _source_row(0, label="runs/a"),
        _source_row(1, label="runs/a"),
        _source_row(2, label="runs/b"),
    ]
    _write_run(tmp_path, "runs/a", source[:2])
    _write_run(tmp_path, "runs/b", source[2:])
    monkeypatch.setattr(diagnostics, "_load_rejected_uc", lambda _root: source)
    monkeypatch.setattr(diagnostics, "oi_micro_families", set)
    calls: list[str] = []

    def load_run(_root: Path, label: str):
        calls.append(label)
        return {
            row["params_hash"]: {
                "metrics": {"n_trades": 10, "avg_net_pct": -0.1},
                "trades": [],
            }
            for row in source
            if row["run_dir_label"] == label
        }

    monkeypatch.setattr(diagnostics, "_index_run_results", load_run)
    cache = tmp_path / "state" / "derived" / "reject-cache.json"
    diagnostics.characterize_rejects_incremental(tmp_path, cache_path=cache)
    calls.clear()
    metrics_a = tmp_path / "runs/a" / "metrics.json"
    metrics_a.write_text(metrics_a.read_text(encoding="utf-8") + " ", encoding="utf-8")

    _rows, stats = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=cache,
    )

    assert stats["cache_hits"] == 1
    assert stats["invalidated"] == 2
    assert stats["recomputed"] == 2
    assert calls == ["runs/a"]


def test_incremental_reject_cache_publication_is_atomic_and_cancellable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = [_source_row(0, label="")]
    monkeypatch.setattr(diagnostics, "_load_rejected_uc", lambda _root: source)
    monkeypatch.setattr(diagnostics, "oi_micro_families", set)
    cache = tmp_path / "state" / "derived" / "reject-cache.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("preserved", encoding="utf-8")
    monkeypatch.setattr(
        diagnostics.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("synthetic replace failure")),
    )

    with pytest.raises(OSError, match="synthetic replace failure"):
        diagnostics.characterize_rejects_incremental(tmp_path, cache_path=cache)

    assert cache.read_text(encoding="utf-8") == "preserved"
    assert not list(cache.parent.glob(".reject-cache.json.*.tmp"))


def test_incremental_reject_cache_stop_prevents_publication(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = [_source_row(index, label="") for index in range(20)]
    monkeypatch.setattr(diagnostics, "_load_rejected_uc", lambda _root: source)
    monkeypatch.setattr(diagnostics, "oi_micro_families", set)
    checks = 0

    class Stopped(RuntimeError):
        pass

    def check_active() -> None:
        nonlocal checks
        checks += 1
        if checks == 10:
            raise Stopped("synthetic canonical stop")

    cache = tmp_path / "state" / "derived" / "reject-cache.json"
    with pytest.raises(Stopped, match="synthetic canonical stop"):
        diagnostics.characterize_rejects_incremental(
            tmp_path,
            cache_path=cache,
            check_active=check_active,
        )

    assert not cache.exists()


def test_incremental_reject_cache_yields_a_bounded_slice_and_resumes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = [_source_row(index, label="") for index in range(5)]
    monkeypatch.setattr(diagnostics, "_load_rejected_uc", lambda _root: source)
    monkeypatch.setattr(diagnostics, "oi_micro_families", set)
    cache = tmp_path / "state" / "derived" / "reject-cache.json"

    with pytest.raises(diagnostics.IncrementalRefreshDeferred) as deferred:
        diagnostics.characterize_rejects_incremental(
            tmp_path,
            cache_path=cache,
            max_recomputed_rows=2,
        )

    assert deferred.value.stats["cache_complete"] is False
    assert deferred.value.stats["recomputed"] == 2
    partial = json.loads(cache.read_text(encoding="utf-8"))
    assert partial["complete"] is False
    assert len(partial["items"]) == 2

    rows, resumed = diagnostics.characterize_rejects_incremental(
        tmp_path,
        cache_path=cache,
    )
    assert len(rows) == 5
    assert resumed["cache_complete"] is True
    assert resumed["cache_hits"] == 2
