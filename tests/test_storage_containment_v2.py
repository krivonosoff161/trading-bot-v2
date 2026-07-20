import sqlite3
from pathlib import Path

import pytest

from scripts.strategy_lab import farm_loop
from src.research_lab import storage_policy
from src.research_lab.farm_tasks_db import FarmTasksDB


def test_direct_lru_apply_is_report_only(tmp_path):
    root = tmp_path / "hot"
    root.mkdir()
    candidate = root / "old.json"
    candidate.write_bytes(b"x" * 2048)

    result = storage_policy.enforce_lru_budget(root, max_mb=0.001, apply=True)

    assert candidate.read_bytes() == b"x" * 2048
    assert result["applied"] is False
    assert result["reason"] == "report_only_protected_root"


def test_direct_rotation_apply_never_truncates_legacy_log(tmp_path):
    log = tmp_path / "scanner.jsonl"
    original = b'{"accepted":true}\n' * 100
    log.write_bytes(original)

    result = storage_policy.rotate_if_large(
        log,
        max_mb=0.0001,
        archive_root=tmp_path / "archive",
        apply=True,
    )

    assert log.read_bytes() == original
    assert not (tmp_path / "archive").exists()
    assert result["applied"] is False
    assert result["rotated"] is False
    assert result["would_rotate"] is True
    assert result["reason"] == "legacy_rotation_report_only"


def test_direct_event_spec_prune_apply_is_report_only(tmp_path):
    specs = tmp_path / "plans" / "event_specs"
    specs.mkdir(parents=True)
    for index in range(3):
        (specs / f"spec-{index}.json").write_text("{}", encoding="utf-8")

    result = storage_policy.prune_event_specs(tmp_path, keep=1, apply=True)

    assert len(list(specs.glob("*.json"))) == 3
    assert result["applied"] is False
    assert result["reason"] == "event_spec_apply_unsupported"


def test_direct_database_prune_apply_is_rejected_without_row_loss(tmp_path):
    db = FarmTasksDB(tmp_path / "farm_tasks.sqlite")
    for index in range(3):
        task_id, _ = db.enqueue_task(
            task_type="run_sweep",
            task_key=f"task-{index}",
            now=float(index + 1),
        )
        db.complete_task(task_id, now=float(index + 10))
    db.raw_connection.execute(
        """INSERT INTO intake_events(
           event_id,symbol,source,reason,observed_at,priority,asset_class,
           suggested_timeframes,evidence_json,raw_ref_json,ingested_at,consumed)
           VALUES('event-1','BTC','test','evidence',1,1,'crypto','[]','{}','{}',1,1)"""
    )
    db.raw_connection.execute(
        """INSERT INTO unique_candidates(uc_key,symbol,timeframe,family,updated_at)
           VALUES('candidate-1','BTC','1h','test',1)"""
    )
    db.raw_connection.commit()
    before = {
        table: db.raw_connection.execute(f"SELECT * FROM {table}").fetchall()
        for table in ("tasks", "intake_events", "unique_candidates")
    }

    with pytest.raises(ValueError, match="report-only"):
        db.prune_terminal_tasks(keep=1, apply=True)
    with pytest.raises(ValueError, match="report-only"):
        db.prune_unique_candidates(keep=1, apply=True)

    after = {
        table: db.raw_connection.execute(f"SELECT * FROM {table}").fetchall()
        for table in ("tasks", "intake_events", "unique_candidates")
    }
    assert after == before
    db.close()


def test_farm_apply_cycle_requests_storage_report_only(monkeypatch, tmp_path):
    calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        storage_policy,
        "maintain",
        lambda _paths, *, apply=False: calls.append(("maintain", apply)) or {},
    )
    monkeypatch.setattr(
        storage_policy,
        "bound_farm_artifacts",
        lambda _root, *, apply=False: calls.append(("bound", apply)) or {},
    )

    farm_loop._maybe_storage_maintain(tmp_path, apply=True)

    assert calls == [("maintain", False), ("bound", False)]


def test_scanner_storage_call_is_literal_report_only():
    source = (Path(__file__).parents[1] / "src" / "scout" / "scanner_v0.py").read_text(
        encoding="utf-8"
    )
    call = "SPOL.maintain([J.JOURNAL, J.INGEST, J.DROPS, J.BUDGET, J.EVENT_AUDIT, J.ROUTING_AUDIT],"
    assert call in source
    assert f"{call}\n                          apply=False)" in source


def test_named_storage_helpers_do_not_mutate_unrelated_database(tmp_path):
    database = tmp_path / "unrelated.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE evidence(value TEXT)")
        conn.execute("INSERT INTO evidence VALUES('preserve')")
    before = database.read_bytes()

    storage_policy.maintain([], apply=True)

    assert database.read_bytes() == before
