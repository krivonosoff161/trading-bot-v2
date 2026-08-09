# -*- coding: utf-8 -*-
"""End-to-end: intake -> plan -> run_sweep -> worker compute -> sync -> classify (real compute),
plus validation auto stamp-back, storage bounds, and the no-live-trading boundary."""
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.farm_coordinator import run_coordinator_cycle  # noqa: E402
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402

PROFILES = load_timeframe_profiles()
POLICY = load_resource_policy()
_HOUR_MS = 3_600_000


def _upsert_validation_candidate(tasks: FarmTasksDB, uc_key: str, *, now: float = 1.0) -> None:
    tasks.upsert_unique_candidate(
        {
            "uc_key": uc_key,
            "symbol": "X",
            "timeframe": "1h",
            "family": "momentum_breakout",
            "params_hash": "ph",
            "data_fingerprint": "fp",
            "decision": "OBSERVE",
            "validation_status": "FORWARD_PAPER",
            "hard_status": "",
            "candidate_id": "source",
            "params": {},
        },
        now=now,
    )


def _write_1h_candles(private_root: Path, symbol: str, n: int = 200) -> None:
    d = private_root / "market_data" / "1h"
    d.mkdir(parents=True, exist_ok=True)
    start = 1_700_000_000_000
    rows = []
    for i in range(n):
        ts = start + i * _HOUR_MS
        base = 100.0 + 10.0 * math.sin(i / 7.0) + (i * 0.05)  # drift + waves -> some breakouts
        rows.append({"ts": ts, "date": str(ts), "open": base, "high": base * 1.02,
                     "low": base * 0.98, "close": base * (1.01 if i % 5 else 0.99), "vol": 1000.0 + i})
    end = start + (n - 1) * _HOUR_MS
    (d / f"{symbol}_{start}_{end}_1h.json").write_text(json.dumps(rows), encoding="utf-8")


def _event(symbol):
    return {"event_id": f"{symbol}:test", "symbol": symbol, "source": "okx_announcement",
            "reason": "listing", "observed_at": 1000.0, "priority": 2, "asset_class": "crypto_major",
            "suggested_timeframes": ["1h"], "evidence": {}, "raw_ref": {}}


def test_end_to_end_real_compute_closes_the_loop(tmp_path):
    symbol = "AAA-USDT-SWAP"
    _write_1h_candles(tmp_path, "AAA_USDT_SWAP")
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    out = run_coordinator_cycle(
        tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY, intake_events=[_event(symbol)],
        families=("momentum_breakout",), provider=None, apply=True, now=2000.0,
        run_worker=True, max_worker_jobs=4, backend="cpu",
    )
    c = out["counters"]
    assert c["sweeps_materialized"] >= 1          # the 1h run_sweep materialized into the compute queue
    assert c["runs_completed"] >= 1               # the worker actually computed it (real compute)
    assert c["classified"] >= 1 and c["unique_upserted"] >= 1
    latest = tasks.latest_unique_candidates()
    assert latest and latest[0]["symbol"] == "AAA_USDT_SWAP" and latest[0]["timeframe"] == "1h"
    assert latest[0]["data_fingerprint"] and latest[0]["data_fingerprint"] != "nofp"
    # the completed run_sweep is recorded; re-running the SAME data does NOT recompute (no saturation)
    out2 = run_coordinator_cycle(
        tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY, intake_events=[_event(symbol)],
        families=("momentum_breakout",), provider=None, apply=True, now=2100.0,
        run_worker=True, max_worker_jobs=4, backend="cpu",
    )
    assert out2["counters"]["sweeps_materialized"] == 0   # identical fingerprint -> not re-armed
    assert out2["pivot"] in ("blocked:no_eligible_tasks", "work_available", "advanced_lifecycle")
    tasks.close()


def test_prepare_chains_to_run_sweep_in_one_cycle(tmp_path):
    # data MISSING initially -> prepare fetches it -> run_sweep is planned + computed same cycle
    from src.research_lab.market_data_provider import get_provider
    provider = get_provider("synthetic", allow_synthetic=True)
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    out = run_coordinator_cycle(
        tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
        intake_events=[_event("ZZZ-USDT-SWAP")], families=("momentum_breakout",),
        provider=provider, apply=True, now=1_700_000_000.0, run_worker=True, max_worker_jobs=6,
        max_prepares=6, max_sweeps=4, backend="cpu",  # realistic now -> sane fetch window
    )
    c = out["counters"]
    assert c["prepared_ok"] >= 1                # synthetic candles fetched on demand
    assert c["sweeps_materialized"] >= 1        # prepare CHAINED into run_sweep in the same cycle
    assert c["runs_completed"] >= 1 and c["unique_upserted"] >= 1
    tasks.close()


def test_validation_orchestrator_auto_stampback(monkeypatch, tmp_path):
    from src.research_lab.hard_validation_export import validation_id_for_unique_candidate
    from src.research_lab.honest_backtest_bridge import _artifact_stem
    from src.research_lab.validation_orchestrator import run_due_validations
    from src.research_lab.simulator_contract import legacy_fixture_manifest
    manifest = legacy_fixture_manifest()
    uc_key = "X::1h::momentum_breakout::ph::fp"
    validation_id = validation_id_for_unique_candidate({"uc_key": uc_key})
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    tasks.upsert_unique_candidate({
        "uc_key": uc_key, "symbol": "X", "timeframe": "1h", "family": "momentum_breakout",
        "params_hash": "ph", "data_fingerprint": "fp", "decision": "OBSERVE",
        "validation_status": "FORWARD_PAPER", "hard_status": "", "candidate_id": "c1",
        "params": {
            "direction": "long",
            "lookback": 20,
            "stop_pct": 2,
            "take_pct": 4,
            "hold_bars": 3,
        },
    }, now=1.0)
    tasks.enqueue_task(task_type="export_validation", task_key=f"export::{uc_key}", symbol="X",
                       timeframe="1h", family="momentum_breakout",
                       payload={"candidate_id": "c1", "uc_key": uc_key}, now=1.0)
    # a verdict already on disk -> the stamp-back must mirror it into unique_candidates
    vdir = tmp_path / "hard_validation" / "verdicts"
    vdir.mkdir(parents=True)
    (vdir / f"{_artifact_stem(validation_id)}.json").write_text(
        json.dumps({"candidate_id": validation_id, "hard_status": "PAPER_FORWARD_READY"}),
        encoding="utf-8")
    rdir = tmp_path / "hard_validation" / "requests"
    rdir.mkdir(parents=True)
    (rdir / f"{_artifact_stem(validation_id)}.json").write_text(
        json.dumps({
            "candidate_id": validation_id,
            "symbol": "X",
            "timeframe": "1h",
            "strategy_id": "momentum_breakout",
            "lite_status": "FORWARD_PAPER",
            "params": {"direction": "long", "lookback": 20, "stop_pct": 2, "take_pct": 4, "hold_bars": 3},
            "data_window": {"fingerprint": "fp", "n_bars": 10, "start_ts": 1, "end_ts": 10},
            "metrics": {"uc_key": uc_key},
            "simulator_manifest": manifest,
            "unsupported_simulator_dimensions": manifest["unsupported_dimensions"],
        }), encoding="utf-8")
    reports = tmp_path / "hard_validation" / "reports"
    reports.mkdir(parents=True)
    (reports / f"{_artifact_stem(validation_id)}.json").write_text(json.dumps({
        "candidate_id": validation_id,
        "source_run_id": "run",
        "symbol": "X",
        "timeframe": "1h",
        "strategy_id": "momentum_breakout",
        "simulator_manifest": manifest,
        "unsupported_simulator_dimensions": manifest["unsupported_dimensions"],
        "simulator_claim_ceiling": manifest["claim_ceiling"],
        "verdict": {
            "candidate_id": validation_id,
            "hard_status": "PAPER_FORWARD_READY",
            "checks": [],
            "failed_checks": [],
            "reason_codes": [],
        },
        "checks_summary": {"total": 0, "passed": 0, "failed": 0},
    }), encoding="utf-8")

    def fake_validation_batch(*args, **kwargs):
        return {"total": 1, "validated": 1, "errors": 0,
                "results": [{"candidate_id": validation_id, "hard_status": "PAPER_FORWARD_READY"}]}

    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.prepare_requests",
        lambda *args, **kwargs: (
            {"skipped_no_artifact": 0},
            [SimpleNamespace(candidate_id=validation_id)],
        ),
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.write_prepared_requests",
        lambda *args, **kwargs: [validation_id],
    )
    monkeypatch.setattr("src.research_lab.validation_orchestrator.run_validation_batch", fake_validation_batch)
    out = run_due_validations(tasks, tmp_path, apply=True, limit=10, now=2.0)
    assert out["export_tasks"] == 1, out
    assert out["stamped_unique"] == 1, out
    assert out["setup_cards"] == 1
    assert tasks.latest_unique_candidates()[0]["hard_status"] == "PAPER_FORWARD_READY"
    card = tmp_path / "setup_library" / "cards" / f"setup-{validation_id}.json"
    assert json.loads(card.read_text(encoding="utf-8"))["paper_forward_ready"] is True
    manifest = json.loads(
        (tmp_path / "hard_validation" / "current_generation.json").read_text(encoding="utf-8")
    )
    assert manifest["schema"] == "HardValidationGeneration.v1"
    assert manifest["producer_complete"] is True
    assert manifest["task_inputs"][0]["payload_sha256"]
    assert "src/research_lab/validation_orchestrator.py" in manifest["producer_code"]
    assert "vendor/honest-backtest/VENDOR.md" in manifest["producer_code"]
    assert list(manifest["active"]) == [validation_id]
    from src.research_lab.paper_runtime import load_ready_setup_cards
    assert [item.candidate_id for item in load_ready_setup_cards(tmp_path)] == [validation_id]

    # A card edited after producer completion no longer matches the current generation.
    card.write_text(card.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert load_ready_setup_cards(tmp_path) == []
    assert not tasks.tasks_in_state("queued", task_type="export_validation")  # task completed
    tasks.close()


def test_validation_orchestrator_rejects_stale_generation_when_current_export_is_empty(
    monkeypatch, tmp_path
):
    """A failed current producer must not promote artifacts from an older run."""
    from src.research_lab.hard_validation_export import validation_id_for_unique_candidate
    from src.research_lab.honest_backtest_bridge import _artifact_stem
    from src.research_lab.paper_runtime import load_ready_setup_cards
    from src.research_lab.setup_library import build_setup_card, write_setup_library
    from src.research_lab.simulator_contract import legacy_fixture_manifest
    from src.research_lab.validation_orchestrator import run_due_validations

    simulator_manifest = legacy_fixture_manifest()
    uc_key = "STALE::1h::momentum_breakout::ph::fp"
    validation_id = validation_id_for_unique_candidate({"uc_key": uc_key})
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    tasks.upsert_unique_candidate({
        "uc_key": uc_key,
        "symbol": "STALE",
        "timeframe": "1h",
        "family": "momentum_breakout",
        "params_hash": "ph",
        "data_fingerprint": "fp",
        "decision": "OBSERVE",
        "validation_status": "FORWARD_PAPER",
        "hard_status": "",
        "candidate_id": "old-candidate",
        "params": {
            "direction": "long",
            "lookback": 20,
            "stop_pct": 2,
            "take_pct": 4,
            "hold_bars": 3,
        },
    }, now=1.0)
    tasks.enqueue_task(
        task_type="export_validation",
        task_key=f"export::{uc_key}",
        symbol="STALE",
        timeframe="1h",
        family="momentum_breakout",
        payload={"candidate_id": "old-candidate", "uc_key": uc_key},
        now=1.0,
    )

    request = {
        "candidate_id": validation_id,
        "symbol": "STALE",
        "timeframe": "1h",
        "strategy_id": "momentum_breakout",
        "lite_status": "FORWARD_PAPER",
        "params": {
            "direction": "long",
            "lookback": 20,
            "stop_pct": 2,
            "take_pct": 4,
            "hold_bars": 3,
        },
        "data_window": {"fingerprint": "fp", "n_bars": 10, "start_ts": 1, "end_ts": 10},
        "metrics": {"uc_key": uc_key},
        "simulator_manifest": simulator_manifest,
        "unsupported_simulator_dimensions": simulator_manifest["unsupported_dimensions"],
    }
    report = {
        "candidate_id": validation_id,
        "source_run_id": "old-run",
        "symbol": "STALE",
        "timeframe": "1h",
        "strategy_id": "momentum_breakout",
        "created_at": "2026-01-01T00:00:00+00:00",
        "simulator_manifest": simulator_manifest,
        "unsupported_simulator_dimensions": simulator_manifest["unsupported_dimensions"],
        "simulator_claim_ceiling": simulator_manifest["claim_ceiling"],
        "verdict": {
            "candidate_id": validation_id,
            "hard_status": "PAPER_FORWARD_READY",
            "checks": [],
            "failed_checks": [],
            "reason_codes": [],
        },
        "checks_summary": {"total": 0, "passed": 0, "failed": 0},
    }
    for subdir, payload in (
        ("requests", request),
        ("reports", report),
        ("verdicts", {"candidate_id": validation_id, "hard_status": "PAPER_FORWARD_READY"}),
    ):
        directory = tmp_path / "hard_validation" / subdir
        directory.mkdir(parents=True, exist_ok=True)
        name = _artifact_stem(validation_id) if subdir == "reports" else validation_id
        (directory / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    write_setup_library(
        tmp_path,
        [build_setup_card(report, request)],
        dry_run=False,
    )
    assert [card.candidate_id for card in load_ready_setup_cards(tmp_path)] == [validation_id]

    from src.research_lab.validation_generation import write_current_generation

    queued = tasks.tasks_in_state("queued", task_type="export_validation")[0]
    write_current_generation(
        tmp_path,
        tasks=[queued],
        exported_ids=[validation_id],
        completed_ids=[validation_id],
        producer_time=1.5,
    )
    generation_path = tmp_path / "hard_validation" / "current_generation.json"
    previous_generation = generation_path.read_bytes()
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.prepare_requests",
        lambda *args, **kwargs: ({"skipped_no_artifact": 1}, []),
    )
    batch_calls = []

    def stale_batch(*args, **kwargs):
        batch_calls.append(kwargs)
        return {
            "total": 1,
            "validated": 1,
            "errors": 0,
            "results": [{"candidate_id": validation_id, "hard_status": "PAPER_FORWARD_READY"}],
        }

    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.run_validation_batch", stale_batch
    )

    out = run_due_validations(tasks, tmp_path, apply=True, limit=10, now=2.0)

    assert out["exported"] == 0
    assert out["validated"] == 0
    assert out["stamped_unique"] == 0
    assert out["setup_cards"] == 0
    assert out["generation_unchanged"] == 1
    assert batch_calls == []
    assert tasks.latest_unique_candidates()[0]["hard_status"] == ""
    assert tasks.tasks_in_state("deferred", task_type="export_validation")
    assert generation_path.read_bytes() == previous_generation
    tasks.close()


def test_validation_orchestrator_replaces_code_stale_generation_with_empty_authority(
    monkeypatch, tmp_path
):
    from src.research_lab import validation_generation as generation
    from src.research_lab.validation_orchestrator import run_due_validations

    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    generation.write_current_generation(
        tmp_path,
        tasks=[],
        exported_ids=[],
        completed_ids=[],
        producer_time=1.0,
    )
    current_code = generation._producer_code_manifest()
    replacement_code = dict(current_code)
    replacement_code[sorted(replacement_code)[0]] = "0" * 64
    monkeypatch.setattr(
        generation, "_producer_code_manifest", lambda: replacement_code
    )
    progress = []

    out = run_due_validations(
        tasks,
        tmp_path,
        apply=True,
        limit=2,
        now=2.0,
        progress=lambda stage, completed, total: progress.append(
            (stage, completed, total)
        ),
    )

    assert out["generation_status_before"] == "code_stale"
    assert out["generation_empty_published"] == 1
    assert out["generation_unchanged"] == 0
    assert ("empty_generation_published", 1, 1) in progress
    assert generation.load_current_generation_snapshot(tmp_path).status == "ready_empty"
    assert not tasks.tasks_in_state("queued", task_type="export_validation")
    assert not tasks.tasks_in_state("running", task_type="export_validation")
    first_publication = generation.manifest_path(tmp_path).read_bytes()

    repeated = run_due_validations(tasks, tmp_path, apply=True, limit=2, now=3.0)
    assert repeated["generation_status_before"] == "code_current"
    assert repeated["generation_empty_published"] == 0
    assert repeated["generation_unchanged"] == 1
    assert generation.manifest_path(tmp_path).read_bytes() == first_publication
    tasks.close()


def test_validation_orchestrator_does_not_replace_stale_generation_after_stop(
    monkeypatch, tmp_path
):
    from src.research_lab import validation_generation as generation
    from src.research_lab.validation_orchestrator import run_due_validations

    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    generation.write_current_generation(
        tmp_path,
        tasks=[],
        exported_ids=[],
        completed_ids=[],
        producer_time=1.0,
    )
    replacement_code = dict(generation._producer_code_manifest())
    replacement_code[sorted(replacement_code)[0]] = "0" * 64
    monkeypatch.setattr(
        generation, "_producer_code_manifest", lambda: replacement_code
    )
    before = generation.manifest_path(tmp_path).read_bytes()
    stopped = False

    def progress(stage, _completed, _total):
        nonlocal stopped
        if stage == "tasks_claimed":
            stopped = True

    def check_active():
        if stopped:
            raise RuntimeError("synthetic owner/fence loss")

    with pytest.raises(RuntimeError, match="synthetic owner/fence loss"):
        run_due_validations(
            tasks,
            tmp_path,
            apply=True,
            limit=2,
            now=2.0,
            progress=progress,
            check_active=check_active,
        )

    assert generation.manifest_path(tmp_path).read_bytes() == before
    tasks.close()


def test_validation_orchestrator_publishes_pending_before_export_side_effects(
    monkeypatch, tmp_path
):
    from src.research_lab.hard_validation_export import (
        validation_id_for_unique_candidate,
    )
    from src.research_lab.validation_generation import load_current_generation
    from src.research_lab.validation_orchestrator import run_due_validations

    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    _upsert_validation_candidate(tasks, "crash-window")
    tasks.enqueue_task(
        task_type="export_validation",
        task_key="export::crash-window",
        symbol="X",
        timeframe="1h",
        family="momentum_breakout",
        payload={"candidate_id": "source", "uc_key": "crash-window"},
        now=1.0,
    )
    observed = []

    def crash_export(*args, **kwargs):
        manifest = load_current_generation(tmp_path)
        observed.append(manifest)
        assert manifest["producer_complete"] is False
        assert manifest["active"] == {}
        raise RuntimeError("synthetic producer crash")

    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.prepare_requests",
        lambda *args, **kwargs: (
            {"skipped_no_artifact": 0},
            [
                SimpleNamespace(
                    candidate_id=validation_id_for_unique_candidate(
                        {"uc_key": "crash-window"}
                    )
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.write_prepared_requests", crash_export
    )

    with pytest.raises(RuntimeError, match="synthetic producer crash"):
        run_due_validations(tasks, tmp_path, apply=True, limit=10, now=2.0)

    manifest = load_current_generation(tmp_path)
    assert observed
    assert manifest["producer_complete"] is False
    assert manifest["active"] == {}
    tasks.close()


def test_validation_orchestrator_missing_uc_key_cannot_scan_unrelated_candidates(
    monkeypatch, tmp_path
):
    from src.research_lab.validation_orchestrator import run_due_validations

    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    tasks.enqueue_task(
        task_type="export_validation",
        task_key="export::missing-uc-key",
        symbol="X",
        timeframe="1h",
        family="momentum_breakout",
        payload={"candidate_id": "source-only"},
        now=1.0,
    )
    out = run_due_validations(tasks, tmp_path, apply=True, limit=10, now=2.0)

    assert out["export_tasks"] == 1
    assert out["exported"] == 0
    assert out["validated"] == 0
    assert out["generation_unchanged"] == 1
    assert not (tmp_path / "hard_validation" / "current_generation.json").exists()
    skipped = tasks.tasks_in_state("skipped", task_type="export_validation")
    assert skipped[0]["machine_reason"] == "validation_task_missing_uc_key"
    tasks.close()


def test_validation_orchestrator_empty_current_batch_skips_historical_artifact_scans(
    monkeypatch, tmp_path
):
    """The exact canary failure path must be O(current batch), not O(history)."""
    from src.research_lab.validation_orchestrator import run_due_validations

    tasks = FarmTasksDB(tasks_db_path(tmp_path), clock=lambda: 1.0)
    tasks.enqueue_task(
        task_type="export_validation",
        task_key="export::stale-uc-key",
        payload={"candidate_id": "source", "uc_key": "stale-uc-key"},
        now=1.0,
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator._verdict_map",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty batch must not scan historical verdicts")
        ),
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator._request_map",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty batch must not scan historical requests")
        ),
    )
    milestones: list[tuple[str, int, int]] = []

    out = run_due_validations(
        tasks,
        tmp_path,
        apply=True,
        limit=1,
        now=1_000.0,
        progress=lambda stage, completed, total: milestones.append(
            (stage, completed, total)
        ),
        check_active=lambda: None,
    )

    assert out["export_tasks"] == 1
    assert out["exported"] == 0
    assert [stage for stage, _, _ in milestones] == [
        "task_dispositioned",
        "tasks_claimed",
    ]
    assert out["orphan_tasks_skipped"] == 1
    assert out["generation_unchanged"] == 1
    assert not (tmp_path / "hard_validation" / "current_generation.json").exists()
    assert len(tasks.tasks_in_state("skipped", task_type="export_validation")) == 1
    tasks.close()


def test_validation_artifact_lookup_is_exact_and_never_globs_history(
    monkeypatch, tmp_path
):
    from src.research_lab.honest_backtest_bridge import _artifact_stem
    from src.research_lab.validation_orchestrator import _request_map, _verdict_map

    candidate_id = "fv_exact"
    artifact_name = f"{_artifact_stem(candidate_id)}.json"
    for kind, payload in (
        ("requests", {"candidate_id": candidate_id, "metrics": {}}),
        ("verdicts", {"candidate_id": candidate_id, "hard_status": "NEEDS_MORE_DATA"}),
    ):
        directory = tmp_path / "hard_validation" / kind
        directory.mkdir(parents=True)
        (directory / artifact_name).write_text(json.dumps(payload), encoding="utf-8")
        (directory / "unrelated.json").write_text(
            json.dumps({"candidate_id": "unrelated"}), encoding="utf-8"
        )

    monkeypatch.setattr(
        Path,
        "glob",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("exact current-batch lookup must not glob")
        ),
    )

    assert list(_request_map(tmp_path, [candidate_id])) == [candidate_id]
    assert _verdict_map(tmp_path, [candidate_id]) == {
        candidate_id: "NEEDS_MORE_DATA"
    }
    assert _request_map(tmp_path, []) == {}
    assert _verdict_map(tmp_path, []) == {}


def test_validation_lease_failure_after_export_blocks_later_side_effects(
    monkeypatch, tmp_path
):
    from src.research_lab.hard_validation_export import validation_id_for_unique_candidate
    from src.research_lab.validation_generation import load_current_generation
    from src.research_lab.validation_orchestrator import run_due_validations

    uc_key = "FAIL::1h::momentum_breakout::ph::fp"
    validation_id = validation_id_for_unique_candidate({"uc_key": uc_key})
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    _upsert_validation_candidate(tasks, uc_key)
    tasks.enqueue_task(
        task_type="export_validation",
        task_key=f"export::{uc_key}",
        payload={"candidate_id": "source", "uc_key": uc_key},
        now=1.0,
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.prepare_requests",
        lambda *_args, **_kwargs: (
            {"skipped_no_artifact": 0},
            [SimpleNamespace(candidate_id=validation_id)],
        ),
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.write_prepared_requests",
        lambda *_args, **_kwargs: [validation_id],
    )
    validation_calls: list[bool] = []
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.run_validation_batch",
        lambda *_args, **_kwargs: validation_calls.append(True),
    )
    failed = False

    def progress(stage: str, _completed: int, _total: int) -> None:
        nonlocal failed
        if stage == "requests_exported":
            failed = True

    def check_active() -> None:
        if failed:
            raise RuntimeError("synthetic process lease lost")

    with pytest.raises(RuntimeError, match="synthetic process lease lost"):
        run_due_validations(
            tasks,
            tmp_path,
            apply=True,
            limit=1,
            now=2.0,
            progress=progress,
            check_active=check_active,
        )

    assert validation_calls == []
    assert load_current_generation(tmp_path)["producer_complete"] is False
    assert len(tasks.tasks_in_state("running", task_type="export_validation")) == 1
    tasks.close()


def test_final_generation_failure_leaves_running_task_for_orphan_recovery(
    monkeypatch, tmp_path
):
    from src.research_lab.hard_validation_export import validation_id_for_unique_candidate
    from src.research_lab.validation_generation import load_current_generation
    from src.research_lab.validation_orchestrator import run_due_validations

    uc_key = "X::1h::momentum_breakout::ph::fp"
    validation_id = validation_id_for_unique_candidate({"uc_key": uc_key})
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    _upsert_validation_candidate(tasks, uc_key)
    tasks.enqueue_task(
        task_type="export_validation",
        task_key=f"export::{uc_key}",
        symbol="X",
        timeframe="1h",
        family="momentum_breakout",
        payload={"candidate_id": "source", "uc_key": uc_key},
        now=1.0,
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.prepare_requests",
        lambda *args, **kwargs: (
            {"skipped_no_artifact": 0},
            [SimpleNamespace(candidate_id=validation_id)],
        ),
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.write_prepared_requests",
        lambda *args, **kwargs: [validation_id],
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.run_validation_batch",
        lambda *args, **kwargs: {
            "total": 1,
            "validated": 1,
            "errors": 0,
            "results": [{
                "candidate_id": validation_id,
                "hard_status": "PAPER_FORWARD_READY",
            }],
        },
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.write_current_generation",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("synthetic final publication failure")
        ),
    )

    with pytest.raises(OSError, match="synthetic final publication failure"):
        run_due_validations(tasks, tmp_path, apply=True, limit=10, now=2.0)

    assert load_current_generation(tmp_path)["producer_complete"] is False
    running = tasks.tasks_in_state("running", task_type="export_validation")
    assert len(running) == 1
    claim_expires_at = float(running[0]["claim_expires_at"])
    assert tasks.reconcile_orphan_running(now=claim_expires_at - 0.001) == 0
    assert tasks.reconcile_orphan_running(now=claim_expires_at) == 1
    assert len(tasks.tasks_in_state("queued", task_type="export_validation")) == 1
    tasks.close()


def test_validation_dry_run_is_noop(tmp_path):
    from src.research_lab.validation_orchestrator import run_due_validations
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    tasks.enqueue_task(task_type="export_validation", task_key="export::c1",
                       payload={"candidate_id": "c1"}, now=1.0)
    out = run_due_validations(tasks, tmp_path, apply=False, now=2.0)
    assert out["export_tasks"] == 1 and out["exported"] == 0
    assert not (tmp_path / "hard_validation" / "requests").exists()  # wrote nothing
    tasks.close()


def test_storage_reports_event_specs_and_terminal_tasks_without_deleting(tmp_path):
    from src.research_lab.storage_policy import bound_farm_artifacts, prune_event_specs
    spec_dir = tmp_path / "plans" / "event_specs"
    spec_dir.mkdir(parents=True)
    for i in range(10):
        (spec_dir / f"s{i}.json").write_text("{}", encoding="utf-8")
    dry = prune_event_specs(tmp_path, keep=4, apply=False)
    assert dry["present"] == 10 and dry["removed"] == 6
    assert len(list(spec_dir.glob("*.json"))) == 10  # dry-run removed nothing
    applied = prune_event_specs(tmp_path, keep=4, apply=True)
    assert applied["removed"] == 6 and len(list(spec_dir.glob("*.json"))) == 10
    assert applied["reason"] == "event_spec_apply_unsupported"
    # terminal-task retention
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    for i in range(6):
        tid, _ = tasks.enqueue_task(task_type="run_sweep", task_key=f"k{i}", now=float(i))
        tasks.complete_task(tid, now=float(i))
    tasks.close()
    res = bound_farm_artifacts(tmp_path, keep_specs=4, keep_terminal=3, apply=True)
    assert res["terminal_tasks_pruned"] == 3
    assert res["applied"] is False
    tasks2 = FarmTasksDB(tasks_db_path(tmp_path))
    assert len(tasks2.tasks_in_state("completed")) == 6
    tasks2.close()


# ── no-live-trading boundary (AST import scan + code-token scan over new modules) ─
NEW_MODULES = [
    "src/research_lab/data_fingerprint.py", "src/research_lab/farm_tasks_db.py",
    "src/research_lab/intake_adapter.py", "src/research_lab/data_planner.py",
    "src/research_lab/farm_data_state.py", "src/research_lab/farm_sweep_runner.py",
    "src/research_lab/farm_classifier.py", "src/research_lab/farm_coordinator.py",
    "src/research_lab/validation_orchestrator.py", "scripts/strategy_lab/farm_loop.py",
    "src/research_lab/farm_journal.py", "src/research_lab/providers/okx_flow.py",
    "src/research_lab/paper_contract.py",
    "src/research_lab/paper_journal.py", "src/research_lab/paper_runtime.py",
    "src/research_lab/paper_readiness.py",
    "src/research_lab/param_schemas.py", "src/research_lab/setup_lifecycle.py",
    "src/research_lab/main_paper_runtime.py",
    "scripts/strategy_lab/paper_loop.py",
    "scripts/strategy_lab/main_paper_runtime.py",
]
# Module paths the research farm must NEVER import (the live/money/secrets/Telegram path).
FORBIDDEN_IMPORTS = (
    "src.exchange", "src.exchange.okx_client", "scripts.auto_execute",
    "src.data.impulse_pump_engine", "src.data.main_impulse_engine",
    "src.utils.telegram", "src.scout.scanner_v0", "src.config",
)
# Call tokens that would mean an order/live path (never legitimately in research code).
FORBIDDEN_TOKENS = ("place_market_order", "place_order", "execute_signal", "set_leverage",
                    'getenv("AUTO_TRADE"', "getenv('AUTO_TRADE'")


def _imported_modules(tree) -> set[str]:
    import ast
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_new_modules_have_no_live_trading_coupling():
    import ast
    for rel in NEW_MODULES:
        text = (_ROOT / rel).read_text(encoding="utf-8")
        mods = _imported_modules(ast.parse(text))
        for mod in mods:
            for bad in FORBIDDEN_IMPORTS:
                assert not (mod == bad or mod.startswith(bad + ".")), f"{rel} imports {mod}"
        # strip the module docstring so safety prose ('no AUTO_TRADE') is not a false hit
        body = ast.parse(text).body
        doc = body[0].value.value if (body and isinstance(body[0], ast.Expr)
                                      and isinstance(getattr(body[0], "value", None), ast.Constant)) else ""
        code = text.replace(str(doc), "", 1) if doc else text
        for token in FORBIDDEN_TOKENS:
            assert token not in code, f"{rel} must not use {token}"


# ── recursive deny-by-default guard (0.6) — whole farm/scanner surface, not an allowlist ─
RECURSIVE_FARM_FORBIDDEN = (
    "src.exchange", "scripts.auto_execute", "src.utils.telegram",
    "src.scout.scanner_v0", "src.config", "main",
)
# The scanner intake surface may legitimately use Telegram (it sends cards), but must
# never reach the order/account/live-money path.
RECURSIVE_SCOUT_FORBIDDEN = ("src.exchange.okx_client", "scripts.auto_execute", "main")
# Call-shaped tokens (with paren/quote) so safety prose like "never AUTO_TRADE" never hits.
CALL_TOKENS = (
    "place_market_order(", "place_order(", "execute_signal(", "set_leverage(",
    'environ["AUTO_TRADE', "environ['AUTO_TRADE",
    'getenv("AUTO_TRADE', "getenv('AUTO_TRADE",
)


def _py_files(*rel_dirs: str):
    files: list[Path] = []
    for rel in rel_dirs:
        files += sorted((_ROOT / rel).rglob("*.py"))
    return [f for f in files if "__pycache__" not in str(f)]


def _is_money_path(mod: str, forbidden: tuple) -> bool:
    if any(mod == b or mod.startswith(b + ".") for b in forbidden):
        return True
    return mod.startswith("src.data.") and mod.endswith("_engine")


def _strip_module_docstring(text: str) -> str:
    import ast
    body = ast.parse(text).body
    doc = body[0].value.value if (body and isinstance(body[0], ast.Expr)
                                  and isinstance(getattr(body[0], "value", None), ast.Constant)) else ""
    return text.replace(str(doc), "", 1) if doc else text


def _assert_surface_clean(files, forbidden) -> None:
    import ast
    for f in files:
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        for mod in _imported_modules(ast.parse(text)):
            assert not _is_money_path(mod, forbidden), f"{f} imports money-path {mod}"
        code = _strip_module_docstring(text)
        for token in CALL_TOKENS:
            assert token not in code, f"{f} uses forbidden call token {token}"


def test_farm_surface_recursive_no_money_path():
    """Deny-by-default: NO file in the farm research surface may reach the money path.

    Unlike the NEW_MODULES allowlist above, this scans the entire src/research_lab tree
    plus the farm entry scripts, so a newly added farm module is auto-guarded.
    """
    farm_scripts = [
        "farm_loop.py",
        "paper_loop.py",
        "worker_once.py",
        "farm_status_report.py",
        "discover_okx_universe.py",
        "enrich_oi_data.py",
        "enrich_flow_data.py",
        "main_paper_bridge.py",
        "main_paper_consumer.py",
        "main_paper_runtime.py",
        "main_paper_runtime_adapter.py",
        "paper_signal_training_export.py",
        "paper_telegram_preview.py",
    ]
    files = _py_files("src/research_lab")
    files += [_ROOT / "scripts" / "strategy_lab" / s for s in farm_scripts]
    _assert_surface_clean(files, RECURSIVE_FARM_FORBIDDEN)


def test_scout_surface_recursive_no_money_path():
    """The scanner intake surface may use Telegram (cards) but never the order/money path."""
    _assert_surface_clean(_py_files("src/scout"), RECURSIVE_SCOUT_FORBIDDEN)
