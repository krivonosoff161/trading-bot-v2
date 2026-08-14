from __future__ import annotations

import json
from types import SimpleNamespace

from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
from src.research_lab.hard_validation_export import (
    validation_id_for_unique_candidate,
)
from src.research_lab.honest_backtest_bridge import _artifact_stem
from src.research_lab.setup_outcome_memory import (
    build_memory_index,
    write_memory_snapshot,
)
from src.research_lab.validation_generation import (
    load_pending_generation,
    manifest_path,
    write_current_generation,
)
from src.research_lab.validation_orchestrator import run_due_validations


def test_historical_validation_reaches_memory_without_replacing_current_generation(
    monkeypatch, tmp_path
) -> None:
    uc_key = "X::1h::momentum_breakout::historical_params::historical_data"
    validation_id = validation_id_for_unique_candidate({"uc_key": uc_key})
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    tasks.upsert_unique_candidate(
        {
            "uc_key": uc_key,
            "symbol": "X",
            "timeframe": "1h",
            "family": "momentum_breakout",
            "params_hash": "historical_params",
            "data_fingerprint": "historical_data",
            "decision": "OBSERVE",
            "validation_status": "FORWARD_PAPER",
            "hard_status": "",
            "candidate_id": "source_historical",
            "params": {},
        },
        now=1.0,
    )
    tasks.enqueue_task(
        task_type="export_validation",
        task_key=f"export::{uc_key}",
        symbol="X",
        timeframe="1h",
        family="momentum_breakout",
        payload={"candidate_id": "source_historical", "uc_key": uc_key},
        now=1.0,
    )
    write_current_generation(
        tmp_path,
        tasks=[],
        exported_ids=[],
        completed_ids=[],
        producer_time=2.0,
    )
    previous_generation = manifest_path(tmp_path).read_bytes()

    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.prepare_requests",
        lambda *args, **kwargs: (
            {"skipped_no_artifact": 0},
            [SimpleNamespace(candidate_id=validation_id)],
        ),
    )

    def write_prepared(*args, **kwargs):
        request_dir = kwargs["artifact_root"] / "hard_validation" / "requests"
        request_dir.mkdir(parents=True, exist_ok=True)
        (request_dir / f"{_artifact_stem(validation_id)}.json").write_text(
            json.dumps(
                {
                    "candidate_id": validation_id,
                    "symbol": "X",
                    "timeframe": "1h",
                    "strategy_id": "momentum_breakout",
                    "metrics": {"uc_key": uc_key},
                }
            ),
            encoding="utf-8",
        )
        return [validation_id]

    def validate(*args, **kwargs):
        verdict_dir = kwargs["artifact_root"] / "hard_validation" / "verdicts"
        verdict_dir.mkdir(parents=True, exist_ok=True)
        (verdict_dir / f"{_artifact_stem(validation_id)}.json").write_text(
            json.dumps(
                {
                    "candidate_id": validation_id,
                    "hard_status": "FAILED_COSTS",
                }
            ),
            encoding="utf-8",
        )
        return {
            "total": 1,
            "validated": 1,
            "errors": 0,
            "results": [
                {"candidate_id": validation_id, "hard_status": "FAILED_COSTS"}
            ],
        }

    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.write_prepared_requests",
        write_prepared,
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.run_validation_batch", validate
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator._stamp_farm_results_from_contexts",
        lambda *args, **kwargs: 0,
    )

    progress: list[tuple[str, int, int]] = []
    result = run_due_validations(
        tasks,
        tmp_path,
        apply=True,
        limit=1,
        now=7202.0,
        progress=lambda stage, completed, total: progress.append(
            (stage, completed, total)
        ),
    )

    assert result["fresh_tasks"] == 0
    assert result["historical_tasks"] == 1
    assert result["historical_validated"] == 1
    assert result["historical_evidence_stamped"] == 1
    assert result["historical_generation_suppressed"] == 1
    assert result["generation_unchanged"] == 1
    assert result["setup_cards"] == 0
    assert manifest_path(tmp_path).read_bytes() == previous_generation
    assert load_pending_generation(tmp_path) is None
    assert not tasks.tasks_in_state("queued", task_type="export_validation")
    assert tasks.latest_unique_candidates()[0]["hard_status"] == "FAILED_COSTS"
    memory_records = build_memory_index(tmp_path)
    assert memory_records[0]["hard_status"] == "FAILED_COSTS"
    write_memory_snapshot(
        tmp_path,
        records=memory_records,
        product_paper_memory={
            "schema": "product_paper_memory.v1",
            "summary": {},
            "paper_only": True,
            "execution_allowed": False,
        },
    )
    from scripts.strategy_lab.agent_role_review_cycle import _load_validator_memory

    analyst_backlog: dict[str, int] = {}
    analyst_rows = _load_validator_memory(tmp_path, 1, stats=analyst_backlog)
    assert analyst_rows[0]["uc_key"] == uc_key
    assert analyst_rows[0]["hard_status"] == "FAILED_COSTS"
    assert analyst_backlog["pending"] == 1
    assert ("historical_evidence_stamped", 1, 1) in progress

    from src.research_lab import validation_generation

    replacement_code = dict(validation_generation._producer_code_manifest())
    stale_code_path = sorted(replacement_code)[0]
    replacement_code[stale_code_path] = "0" * 64
    monkeypatch.setattr(
        validation_generation, "_producer_code_manifest", lambda: replacement_code
    )
    tasks.enqueue_task(
        task_type="export_validation",
        task_key=f"historical_recheck::{uc_key}",
        symbol="X",
        timeframe="1h",
        family="momentum_breakout",
        payload={"candidate_id": "source_historical", "uc_key": uc_key},
        now=7202.0,
    )
    repeated = run_due_validations(
        tasks, tmp_path, apply=True, limit=1, now=10_803.0
    )
    assert repeated["generation_status_before"] == "code_stale"
    assert repeated["generation_empty_published"] == 1
    assert repeated["historical_generation_suppressed"] == 1
    from src.research_lab.validation_generation import load_current_generation_snapshot

    assert load_current_generation_snapshot(tmp_path).status == "ready_empty"
    assert load_pending_generation(tmp_path) is None
    tasks.close()


def test_recent_task_is_product_current_but_analyst_followup_is_research_only() -> None:
    from src.research_lab.validation_orchestrator import _is_current_product_task

    task = {"created_at": 100.0, "payload_json": "{}"}
    assert _is_current_product_task(task, reference_time=101.0)
    assert not _is_current_product_task(task, reference_time=3701.0)
    assert not _is_current_product_task(
        {
            "created_at": 100.0,
            "payload_json": json.dumps({"role_environment_id": "env_123"}),
        },
        reference_time=101.0,
    )


def test_mixed_batch_publishes_only_fresh_product_authority(
    monkeypatch, tmp_path
) -> None:
    old_uc = "OLD::1h::momentum_breakout::old_params::old_data"
    fresh_uc = "NEW::1h::momentum_breakout::fresh_params::fresh_data"
    candidate_ids = {
        uc_key: validation_id_for_unique_candidate({"uc_key": uc_key})
        for uc_key in (old_uc, fresh_uc)
    }
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    for uc_key, created_at in ((old_uc, 1.0), (fresh_uc, 7201.0)):
        symbol = uc_key.split("::", 1)[0]
        tasks.upsert_unique_candidate(
            {
                "uc_key": uc_key,
                "symbol": symbol,
                "timeframe": "1h",
                "family": "momentum_breakout",
                "params_hash": f"params_{symbol}",
                "data_fingerprint": f"data_{symbol}",
                "decision": "OBSERVE",
                "validation_status": "FORWARD_PAPER",
                "hard_status": "",
                "candidate_id": f"source_{symbol}",
                "params": {},
            },
            now=created_at,
        )
        tasks.enqueue_task(
            task_type="export_validation",
            task_key=f"export::{uc_key}",
            symbol=symbol,
            timeframe="1h",
            family="momentum_breakout",
            payload={"candidate_id": f"source_{symbol}", "uc_key": uc_key},
            now=created_at,
        )

    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.prepare_requests",
        lambda *args, **kwargs: (
            {"skipped_no_artifact": 0},
            [
                SimpleNamespace(candidate_id=candidate_ids[uc_key])
                for uc_key in kwargs["uc_keys"]
            ],
        ),
    )

    def write_prepared(*args, **kwargs):
        request_dir = kwargs["artifact_root"] / "hard_validation" / "requests"
        request_dir.mkdir(parents=True, exist_ok=True)
        exported = []
        for prepared in args[1]:
            candidate_id = prepared.candidate_id
            uc_key = next(
                key for key, value in candidate_ids.items() if value == candidate_id
            )
            (request_dir / f"{_artifact_stem(candidate_id)}.json").write_text(
                json.dumps(
                    {
                        "candidate_id": candidate_id,
                        "metrics": {"uc_key": uc_key},
                    }
                ),
                encoding="utf-8",
            )
            exported.append(candidate_id)
        return exported

    def validate(*args, **kwargs):
        verdict_dir = kwargs["artifact_root"] / "hard_validation" / "verdicts"
        verdict_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for candidate_id in kwargs["candidate_ids"]:
            (verdict_dir / f"{_artifact_stem(candidate_id)}.json").write_text(
                json.dumps(
                    {
                        "candidate_id": candidate_id,
                        "hard_status": "PAPER_FORWARD_READY",
                    }
                ),
                encoding="utf-8",
            )
            results.append(
                {
                    "candidate_id": candidate_id,
                    "hard_status": "PAPER_FORWARD_READY",
                }
            )
        return {"total": 2, "validated": 2, "errors": 0, "results": results}

    publication: dict[str, object] = {}

    def publish(*args, **kwargs):
        publication.update(kwargs)
        return {"schema": "HardValidationGeneration.v1"}

    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.write_prepared_requests",
        write_prepared,
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.run_validation_batch", validate
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator._write_setup_cards",
        lambda *args, **kwargs: len(args[1]),
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator._stamp_farm_results_from_contexts",
        lambda *args, **kwargs: 0,
    )
    monkeypatch.setattr(
        "src.research_lab.validation_orchestrator.write_current_generation", publish
    )

    result = run_due_validations(
        tasks, tmp_path, apply=True, limit=2, now=7202.0
    )

    assert result["fresh_validated"] == 1
    assert result["historical_validated"] == 1
    assert result["historical_generation_suppressed"] == 1
    assert publication["completed_ids"] == [candidate_ids[fresh_uc]]
    assert publication["exported_ids"] == [candidate_ids[fresh_uc]]
    published_tasks = publication["tasks"]
    assert isinstance(published_tasks, list)
    assert [json.loads(task["payload_json"])["uc_key"] for task in published_tasks] == [
        fresh_uc
    ]
    tasks.close()
