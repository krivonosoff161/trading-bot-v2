import hashlib
import os
import sqlite3

import pytest

from src.research_lab.storage_reachability import event_spec_reachability_snapshot


OUTBOX_SQL = """CREATE TABLE materialization_outbox(
    materialization_id TEXT,task_id INTEGER,task_fencing_token INTEGER,
    spec_path TEXT,spec_digest TEXT,spec_json TEXT,priority INTEGER,state TEXT,
    queue_job_id INTEGER,created_at REAL,updated_at REAL)"""
QUEUE_SQL = """CREATE TABLE queue(
    job_id INTEGER,spec_path TEXT,status TEXT,materialization_id TEXT,
    materialization_digest TEXT)"""
MATERIALIZATIONS_SQL = """CREATE TABLE queue_materializations(
    materialization_id TEXT,spec_digest TEXT,job_id INTEGER,spec_path TEXT)"""


def _spec_digest(payload="{}"):
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _databases(root, spec_path):
    state = root / "state"
    state.mkdir(parents=True)
    digest = _spec_digest()
    task = sqlite3.connect(state / "farm_tasks.sqlite")
    task.execute(OUTBOX_SQL)
    queue = sqlite3.connect(state / "strategy_lab.sqlite")
    queue.execute(QUEUE_SQL)
    queue.execute(MATERIALIZATIONS_SQL)
    for index, state_name in enumerate(
        ("pending", "dispatched", "acknowledged", "ambiguous", "superseded"), 1
    ):
        mid = f"m-{state_name}"
        task.execute(
            "INSERT INTO materialization_outbox VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (mid, index, index, str(spec_path), digest, "{}", 100, state_name,
             index, 1.0, 1.0),
        )
        queue.execute(
            "INSERT INTO queue VALUES(?,?,?,?,?)",
            (index, str(spec_path), "completed", mid, digest),
        )
        queue.execute(
            "INSERT INTO queue_materializations VALUES(?,?,?,?)",
            (mid, digest, index, str(spec_path)),
        )
    task.commit()
    task.close()
    queue.commit()
    queue.close()


def test_reachability_reports_every_named_source_as_protection(tmp_path):
    spec = tmp_path / "plans" / "event_specs" / "old.json"
    spec.parent.mkdir(parents=True)
    spec.write_text("{}", encoding="utf-8")
    _databases(tmp_path, spec)

    report = event_spec_reachability_snapshot(tmp_path)

    assert report["complete"] is True
    assert report["status"] == "report_only"
    assert report["mutation_authority"] is False
    assert os.path.normcase(str(spec.resolve())) in report["protected_paths"]
    assert _spec_digest() in report["protected_digests"]
    assert all(source["complete"] for source in report["sources"])
    assert report["integrity_errors"] == []


def test_missing_reference_database_blocks_whole_class_without_creation(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    task = sqlite3.connect(state / "farm_tasks.sqlite")
    task.execute(OUTBOX_SQL)
    task.commit()
    task.close()
    queue_path = state / "strategy_lab.sqlite"

    report = event_spec_reachability_snapshot(tmp_path)

    assert report["complete"] is False
    assert report["status"] == "incomplete_blocked"
    assert report["protected_paths"] == []
    assert not queue_path.exists()


def test_unexpected_schema_blocks_whole_class(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    sqlite3.connect(state / "farm_tasks.sqlite").close()
    sqlite3.connect(state / "strategy_lab.sqlite").close()

    report = event_spec_reachability_snapshot(tmp_path)

    assert report["complete"] is False
    assert {source["reason"] for source in report["sources"]} == {"unexpected_schema"}


def test_missing_real_queue_materialization_spec_path_column_blocks(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    task = sqlite3.connect(state / "farm_tasks.sqlite")
    task.execute(OUTBOX_SQL)
    task.commit()
    task.close()
    queue = sqlite3.connect(state / "strategy_lab.sqlite")
    queue.execute(QUEUE_SQL)
    queue.execute(
        "CREATE TABLE queue_materializations(materialization_id TEXT,spec_digest TEXT,job_id INTEGER)"
    )
    queue.commit()
    queue.close()

    report = event_spec_reachability_snapshot(tmp_path)

    assert report["complete"] is False
    assert report["sources"][1]["missing_columns"] == {
        "queue_materializations": ["spec_path"]
    }


def test_outbox_payload_digest_mismatch_blocks_whole_class(tmp_path):
    spec = tmp_path / "plans" / "event_specs" / "old.json"
    spec.parent.mkdir(parents=True)
    _databases(tmp_path, spec)
    conn = sqlite3.connect(tmp_path / "state" / "farm_tasks.sqlite")
    conn.execute("UPDATE materialization_outbox SET spec_json='tampered' WHERE task_id=1")
    conn.commit()
    conn.close()

    report = event_spec_reachability_snapshot(tmp_path)

    assert report["complete"] is False
    assert report["status"] == "incomplete_blocked"
    assert any("outbox digest mismatch" in error for error in report["integrity_errors"])
    assert report["protected_paths"] == []


def test_cross_table_path_or_digest_mismatch_blocks_whole_class(tmp_path):
    spec = tmp_path / "plans" / "event_specs" / "old.json"
    spec.parent.mkdir(parents=True)
    _databases(tmp_path, spec)
    conn = sqlite3.connect(tmp_path / "state" / "strategy_lab.sqlite")
    conn.execute(
        "UPDATE queue_materializations SET spec_path=? WHERE materialization_id='m-pending'",
        (str(tmp_path / "other.json"),),
    )
    conn.commit()
    conn.close()

    report = event_spec_reachability_snapshot(tmp_path)

    assert report["complete"] is False
    assert any("mismatch" in error for error in report["integrity_errors"])


def test_corrupt_reference_database_returns_blocked_report(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "farm_tasks.sqlite").write_bytes(b"not sqlite")
    (state / "strategy_lab.sqlite").write_bytes(b"not sqlite either")

    report = event_spec_reachability_snapshot(tmp_path)

    assert report["complete"] is False
    assert report["status"] == "incomplete_blocked"
    assert {source["reason"] for source in report["sources"]} == {
        "unreadable_or_corrupt"
    }


@pytest.mark.skipif(os.name == "nt", reason="unprivileged Windows symlinks are not portable")
def test_symlink_reference_database_is_rejected_without_following(tmp_path):
    real = tmp_path / "real.sqlite"
    sqlite3.connect(real).close()
    state = tmp_path / "state"
    state.mkdir()
    (state / "farm_tasks.sqlite").symlink_to(real)
    (state / "strategy_lab.sqlite").symlink_to(real)

    report = event_spec_reachability_snapshot(tmp_path)

    assert report["complete"] is False
    assert {source["reason"] for source in report["sources"]} == {"unsafe_path"}
