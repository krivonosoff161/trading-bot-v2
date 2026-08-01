from __future__ import annotations

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

import src.research_lab.backup_retention as retention

from src.research_lab.backup_retention import (
    AUTHORITY_SCHEMA,
    BackupRetentionAuthority,
    BackupRetentionError,
    apply_retention_plan,
    build_retention_plan,
    load_authority,
    load_plan,
    storage_budget_status,
    verify_archive,
    write_plan,
)


NOW = time.time()


def _write_generation(
    root: Path,
    name: str,
    *,
    marker: bytes,
    complete: bool = True,
) -> None:
    subtrees = ("raw", "logical", "restore") if complete else ("raw",)
    for subtree in subtrees:
        path = root / name / subtree / "state" / "sample.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(marker + b":" + subtree.encode())


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    backup = tmp_path / "backups"
    archive = tmp_path / "archive"
    backup.mkdir(parents=True)
    archive.mkdir()
    _write_generation(backup, "phase0-precanary-001", marker=b"baseline")
    _write_generation(backup, "hardfail-002", marker=b"incident", complete=False)
    _write_generation(backup, "canary-003", marker=b"older")
    _write_generation(backup, "canary-004", marker=b"newest")
    old = time.time_ns() - 10_000_000_000
    for path in (backup / "canary-003").rglob("*"):
        if path.is_file():
            os.utime(path, ns=(old, old))
    return backup, archive


def _authority(plan, tmp_path: Path, *, expires: float = NOW + 600) -> Path:
    path = tmp_path / "authority.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    value = BackupRetentionAuthority(
        schema=AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="repair_and_apply_e_drive_storage_lifecycle",
        backup_root=plan.backup_root,
        archive_root=plan.archive_root,
        expected_plan_digest=plan.plan_digest,
        issued_at=NOW - 60,
        expires_at=expires,
        turn_id="synthetic-turn",
        authority_id="owner_" + "a" * 32,
    )
    path.write_text(json.dumps(asdict(value)), encoding="utf-8")
    return path


def _plan(tmp_path: Path):
    backup, archive = _fixture(tmp_path)
    plan = build_retention_plan(
        backup,
        archive,
        retain_generation="canary-004",
        retained_generation_evidence_sha256="e" * 64,
        max_backup_bytes=1024,
        min_free_bytes=1,
        created_at="2026-08-01T00:00:00Z",
    )
    return backup, archive, plan


def test_plan_keeps_exact_evidence_bound_generation_and_classifies_evidence(
    tmp_path: Path,
) -> None:
    backup, _, plan = _plan(tmp_path)

    assert plan.retain_unpacked_generations == ("canary-004",)
    assert set(plan.archive_remove_generations) == {
        "phase0-precanary-001",
        "hardfail-002",
        "canary-003",
    }
    classes = {item.generation: item.evidence_class for item in plan.generations}
    assert classes["phase0-precanary-001"] == "pre_cutover_baseline"
    assert classes["hardfail-002"] == "incident_evidence"
    assert classes["canary-003"] == "canary_evidence"
    assert plan.source_logical_bytes == sum(
        path.stat().st_size for path in backup.rglob("*") if path.is_file()
    )
    assert plan.reclaim_candidate_bytes > 0
    assert plan.plan_digest.startswith("sha256:")


def test_plan_roundtrip_and_digest_tamper_fail_closed(tmp_path: Path) -> None:
    _, _, plan = _plan(tmp_path)
    path = tmp_path / "plan.json"
    write_plan(plan, path)
    assert load_plan(path) == plan

    value = json.loads(path.read_text(encoding="utf-8"))
    value["reclaim_candidate_bytes"] += 1
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(BackupRetentionError, match="digest"):
        load_plan(path)

    write_plan(plan, path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["reclaim_candidate_bytes"] += 1
    payload = {key: nested for key, nested in value.items() if key != "plan_digest"}
    value["plan_digest"] = retention.content_digest(payload)
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(BackupRetentionError, match="byte aggregate"):
        load_plan(path)


def test_authority_is_exact_fresh_and_plan_bound(tmp_path: Path) -> None:
    _, _, plan = _plan(tmp_path)
    authority_path = _authority(plan, tmp_path)
    assert load_authority(authority_path, plan, now=NOW).expected_plan_digest == plan.plan_digest

    value = json.loads(authority_path.read_text(encoding="utf-8"))
    value["action"] = "start_rcc"
    authority_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(BackupRetentionError, match="scope"):
        load_authority(authority_path, plan, now=NOW)

    _authority(plan, tmp_path, expires=NOW)
    with pytest.raises(BackupRetentionError, match="currently valid"):
        load_authority(authority_path, plan, now=NOW)


def test_apply_archives_verifies_deduplicates_and_is_idempotent(tmp_path: Path) -> None:
    backup, _, plan = _plan(tmp_path)
    authority = load_authority(_authority(plan, tmp_path), plan, now=NOW)

    first = apply_retention_plan(plan, authority, expected_plan_digest=plan.plan_digest)

    assert first.changed_files == sum(
        item.file_count
        for item in plan.generations
        if item.disposition == "archive_then_remove"
    )
    assert first.logical_bytes_reclaimed == plan.reclaim_candidate_bytes
    assert (backup / "canary-004").is_dir()
    assert not (backup / "canary-003").exists()
    assert not (backup / "hardfail-002").exists()
    assert not (backup / "phase0-precanary-001").exists()
    verified = verify_archive(plan)
    assert verified["status"] == "ok"
    assert verified["verified_files"] == first.changed_files

    second = apply_retention_plan(plan, authority, expected_plan_digest=plan.plan_digest)
    assert second.changed_files == 0
    assert second.logical_bytes_reclaimed == 0
    assert second.already_applied_files == first.changed_files


def test_interrupted_apply_resumes_after_per_file_archive_proof(tmp_path: Path) -> None:
    backup, _, plan = _plan(tmp_path)
    authority = load_authority(_authority(plan, tmp_path), plan, now=NOW)

    with pytest.raises(BackupRetentionError, match="synthetic interruption"):
        apply_retention_plan(
            plan,
            authority,
            expected_plan_digest=plan.plan_digest,
            fail_after_files=2,
        )
    remaining_before = sum(path.is_file() for path in backup.rglob("*"))
    resumed = apply_retention_plan(plan, authority, expected_plan_digest=plan.plan_digest)
    assert resumed.already_applied_files == 2
    assert resumed.changed_files > 0
    assert sum(path.is_file() for path in backup.rglob("*")) < remaining_before


def test_source_drift_and_new_file_block_before_mutation(tmp_path: Path) -> None:
    backup, _, plan = _plan(tmp_path)
    authority = load_authority(_authority(plan, tmp_path), plan, now=NOW)
    target = next(
        backup / Path(*item.source_ref.split("/"))
        for item in plan.files
        if item.generation in plan.archive_remove_generations
    )
    target.write_bytes(b"changed")

    with pytest.raises(BackupRetentionError, match="metadata changed"):
        apply_retention_plan(plan, authority, expected_plan_digest=plan.plan_digest)
    assert all((backup / generation).exists() for generation in plan.archive_remove_generations)

    backup, _, plan = _plan(tmp_path / "extra")
    authority = load_authority(_authority(plan, tmp_path / "extra"), plan, now=NOW)
    extra = backup / "canary-003" / "raw" / "unexpected.bin"
    extra.write_bytes(b"unexpected")
    with pytest.raises(BackupRetentionError, match="unplanned"):
        apply_retention_plan(plan, authority, expected_plan_digest=plan.plan_digest)


def test_archive_tamper_blocks_resume_and_verify(tmp_path: Path) -> None:
    _, archive, plan = _plan(tmp_path)
    authority = load_authority(_authority(plan, tmp_path), plan, now=NOW)
    apply_retention_plan(plan, authority, expected_plan_digest=plan.plan_digest)
    object_path = next((archive / ".backup-retention-v1/objects/sha256").rglob("*.gz"))
    object_path.write_bytes(b"tampered")

    with pytest.raises(BackupRetentionError, match="restore verification"):
        verify_archive(plan)
    with pytest.raises(BackupRetentionError, match="restore verification"):
        apply_retention_plan(plan, authority, expected_plan_digest=plan.plan_digest)


def test_wrong_digest_and_root_identity_cannot_mutate(tmp_path: Path) -> None:
    backup, _, plan = _plan(tmp_path)
    authority = load_authority(_authority(plan, tmp_path), plan, now=NOW)
    before = {
        path.relative_to(backup).as_posix(): path.read_bytes()
        for path in backup.rglob("*")
        if path.is_file()
    }
    with pytest.raises(BackupRetentionError, match="expected cleanup plan"):
        apply_retention_plan(
            plan,
            authority,
            expected_plan_digest="sha256:" + "0" * 64,
        )
    assert before == {
        path.relative_to(backup).as_posix(): path.read_bytes()
        for path in backup.rglob("*")
        if path.is_file()
    }

    changed_identity = replace(
        plan,
        backup_filesystem={"device": -1, "volume_serial": -1},
    )
    with pytest.raises(BackupRetentionError, match="identity"):
        apply_retention_plan(
            changed_identity,
            authority,
            expected_plan_digest=plan.plan_digest,
        )


def test_budget_status_fails_closed_without_deleting(tmp_path: Path) -> None:
    backup = tmp_path / "backups"
    backup.mkdir()
    payload = backup / "generation" / "raw.bin"
    payload.parent.mkdir()
    payload.write_bytes(b"x" * 32)

    blocked = storage_budget_status(backup, max_backup_bytes=16, min_free_bytes=1)
    assert blocked["status"] == "blocked"
    assert blocked["within_budget"] is False
    assert payload.read_bytes() == b"x" * 32

    green = storage_budget_status(backup, max_backup_bytes=64, min_free_bytes=1)
    assert green["status"] == "ok"
    assert green["within_budget"] is True


def test_plan_rejects_sensitive_paths_without_reading_payload(tmp_path: Path) -> None:
    backup = tmp_path / "backups"
    archive = tmp_path / "archive"
    backup.mkdir()
    archive.mkdir()
    unsafe = backup / "canary-001" / "raw" / ".env"
    unsafe.parent.mkdir(parents=True)
    unsafe.write_text("synthetic", encoding="utf-8")
    _write_generation(backup, "canary-002", marker=b"newest")

    with pytest.raises(BackupRetentionError, match="sensitive"):
        build_retention_plan(
            backup,
            archive,
            retain_generation="canary-002",
            retained_generation_evidence_sha256="e" * 64,
            min_free_bytes=1,
        )


def test_retained_generation_requires_exact_complete_evidence_binding(tmp_path: Path) -> None:
    backup, archive = _fixture(tmp_path)
    with pytest.raises(BackupRetentionError, match="absent or incomplete"):
        build_retention_plan(
            backup,
            archive,
            retain_generation="hardfail-002",
            retained_generation_evidence_sha256="e" * 64,
            min_free_bytes=1,
        )
    with pytest.raises(BackupRetentionError, match="evidence digest"):
        build_retention_plan(
            backup,
            archive,
            retain_generation="canary-004",
            retained_generation_evidence_sha256="not-a-digest",
            min_free_bytes=1,
        )


def test_duplicate_source_digest_maps_to_one_object(tmp_path: Path) -> None:
    backup = tmp_path / "backups"
    archive = tmp_path / "archive"
    backup.mkdir()
    archive.mkdir()
    for generation in ("canary-001", "canary-002"):
        for subtree in ("raw", "logical", "restore"):
            path = backup / generation / subtree / "same.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"identical")
    plan = build_retention_plan(
        backup,
        archive,
        retain_generation="canary-002",
        retained_generation_evidence_sha256="e" * 64,
        min_free_bytes=1,
        created_at="2026-08-01T00:00:00Z",
    )
    authority = load_authority(_authority(plan, tmp_path), plan, now=NOW)
    report = apply_retention_plan(plan, authority, expected_plan_digest=plan.plan_digest)

    assert report.changed_files == 3
    assert report.archive_objects_created == 1
    assert len(list((archive / ".backup-retention-v1/objects").rglob("*.gz"))) == 1


def test_apply_capacity_models_sequential_peak_not_all_archive_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = tmp_path / "backups"
    archive = tmp_path / "archive"
    backup.mkdir()
    archive.mkdir()
    _write_generation(backup, "canary-retained", marker=b"retained")
    for index, subtree in enumerate(("raw", "logical", "restore")):
        path = backup / "canary-old" / subtree / f"unique-{index}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes([index + 1]) * 1024**2)
    plan = build_retention_plan(
        backup,
        archive,
        retain_generation="canary-retained",
        retained_generation_evidence_sha256="e" * 64,
        min_free_bytes=1024**2,
    )
    authority = load_authority(_authority(plan, tmp_path), plan, now=NOW)
    monkeypatch.setattr(
        retention.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=3 * 1024**2),
    )

    report = apply_retention_plan(plan, authority, expected_plan_digest=plan.plan_digest)

    assert report.changed_files == 3
    assert report.logical_bytes_reclaimed == 3 * 1024**2


def test_archive_ancestor_reparse_fails_before_source_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup, _, plan = _plan(tmp_path)
    authority = load_authority(_authority(plan, tmp_path), plan, now=NOW)
    real_detector = retention.is_link_or_reparse
    monkeypatch.setattr(
        retention,
        "is_link_or_reparse",
        lambda path: Path(path).name == "objects" or real_detector(path),
    )
    before = {
        path.relative_to(backup).as_posix(): path.read_bytes()
        for path in backup.rglob("*")
        if path.is_file()
    }

    with pytest.raises(BackupRetentionError, match="archive control path is unsafe"):
        apply_retention_plan(plan, authority, expected_plan_digest=plan.plan_digest)

    assert before == {
        path.relative_to(backup).as_posix(): path.read_bytes()
        for path in backup.rglob("*")
        if path.is_file()
    }
