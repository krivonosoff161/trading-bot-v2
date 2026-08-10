from __future__ import annotations

import gzip
import json
import multiprocessing
from pathlib import Path
import threading
from dataclasses import replace
import time

import pytest

from src.research_lab import runtime_storage_rotation

from scripts.strategy_lab.runtime_storage_rotation import (
    _archive_authority,
    main as storage_cli_main,
)

from src.research_lab.archive_catalog import ArchiveCatalog, ArchiveCatalogError
from src.research_lab.private_archive_capability import (
    AUTHORITY_SCHEMA as ARCHIVE_AUTHORITY_SCHEMA,
    DISASTER_RECOVERY_ROLE,
    RETENTION_RECLAMATION_ROLE,
    PrivateArchiveAuthority,
    activate_private_archive_root,
)
from src.research_lab.runtime_storage_rotation import (
    AUTHORITY_SCHEMA,
    DEFAULT_STREAMS,
    RuntimeStorageAuthority,
    RuntimeStorageError,
    RuntimeStreamPolicy,
    activate_runtime_storage,
    append_runtime_jsonl,
    append_runtime_lines,
    archive_pending_segments,
    load_runtime_storage_capability,
    llm_invocation_summary,
    read_runtime_tail,
    recent_semantic_statuses,
    semantic_key_exists,
    semantic_key_values_for_path,
    semantic_counts_for_path,
    semantic_status_count,
    seal_oversized_active_streams,
    storage_budget_status,
)


SHA = "7" * 40


def _hold_runtime_storage_lock(
    lock_path: str, ready_path: str, hold_seconds: float
) -> None:
    from src.research_lab.storage_os_lock import storage_root_lock

    with storage_root_lock(Path(lock_path)):
        ready = Path(ready_path)
        temporary = ready.with_suffix(".tmp")
        temporary.write_text("locked", encoding="ascii")
        temporary.replace(ready)
        time.sleep(hold_seconds)


def _policies(*, cap: int = 180) -> tuple[RuntimeStreamPolicy, ...]:
    return (
        RuntimeStreamPolicy(
            "farm.cycle",
            "logs/farm/cycle_log.jsonl",
            "farm_journal",
            "farm_and_runtime",
            "farm_journal.v1",
            "private_payload",
            cap,
            20,
        ),
        RuntimeStreamPolicy(
            "llm.invocations",
            "state/llm_advice/invocations.jsonl",
            "llm_invocation",
            "models_and_llm",
            "LLMInvocation.v1",
            "private_metadata",
            cap,
            20,
        ),
        RuntimeStreamPolicy(
            "lineage.links",
            "state/lineage/cycle_links.jsonl",
            "lineage",
            "data_and_lineage",
            "LineageLink.v1",
            "private_payload",
            cap,
            20,
        ),
        RuntimeStreamPolicy(
            "farm.stdout",
            "logs/farm_full_cycle_loop.log",
            "runtime_stdout",
            "farm_and_runtime",
            "RuntimeStdoutLine.v1",
            "private_payload",
            cap,
            20,
        ),
    )


def _activated(tmp_path: Path, *, cap: int = 180, budget: int = 10_000_000):
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()
    policies = _policies(cap=cap)
    kinds = tuple(sorted({item.kind for item in policies}))
    archive_authority = PrivateArchiveAuthority(
        schema=ARCHIVE_AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="activate_private_archive_storage",
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        allowed_kinds=kinds,
        issued_at=100.0,
        expires_at=300.0,
        turn_id="synthetic-storage-test",
        authority_id="owner_" + "a" * 32,
        storage_role=RETENTION_RECLAMATION_ROLE,
        synthetic=True,
    )
    activate_private_archive_root(
        archive,
        source_root=source,
        authority=archive_authority,
        now=150.0,
    )
    authority = RuntimeStorageAuthority(
        schema=AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="activate_runtime_storage_rotation",
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        source_revision=SHA,
        issued_at=100.0,
        expires_at=300.0,
        turn_id="synthetic-storage-test",
        authority_id="owner_" + "b" * 32,
        source_budget_bytes=budget,
        archive_budget_bytes=10_000_000,
        minimum_source_free_bytes=0,
        minimum_archive_free_bytes=0,
        synthetic=True,
    )
    capability = activate_runtime_storage(
        source,
        archive_root=archive,
        authority=authority,
        now=150.0,
        streams=policies,
    )
    assert activate_runtime_storage(
        source,
        archive_root=archive,
        authority=authority,
        now=151.0,
        streams=policies,
    ) == capability
    status = storage_budget_status(capability)
    assert status["archive_storage_role"] == RETENTION_RECLAMATION_ROLE
    assert status["disaster_recovery_claim"] is False
    return capability, source, archive


def _rows_from_archive(archive: Path) -> list[dict]:
    rows: list[dict] = []
    catalog = ArchiveCatalog(archive)
    for manifest in catalog.manifests():
        target = archive / Path(*manifest.object_ref.split("/"))
        with gzip.open(target, "rt", encoding="utf-8") as stream:
            for line in stream:
                if line.strip().startswith("{"):
                    rows.append(json.loads(line))
    return rows


def test_runtime_storage_rejects_disaster_recovery_archive_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()

    def identity(path: Path) -> dict[str, int]:
        return (
            {"device": 1, "volume_serial": 1}
            if Path(path).name == "source"
            else {"device": 2, "volume_serial": 2}
        )

    monkeypatch.setattr(
        "src.research_lab.private_archive_capability.filesystem_identity", identity
    )
    archive_authority = PrivateArchiveAuthority(
        schema=ARCHIVE_AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="activate_private_archive_storage",
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        allowed_kinds=tuple(sorted({item.kind for item in _policies()})),
        issued_at=100.0,
        expires_at=300.0,
        turn_id="synthetic-storage-test",
        authority_id="owner_" + "9" * 32,
        storage_role=DISASTER_RECOVERY_ROLE,
        synthetic=True,
    )
    activate_private_archive_root(
        archive,
        source_root=source,
        authority=archive_authority,
        now=150.0,
    )
    authority = RuntimeStorageAuthority(
        schema=AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="activate_runtime_storage_rotation",
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        source_revision=SHA,
        issued_at=100.0,
        expires_at=300.0,
        turn_id="synthetic-storage-test",
        authority_id="owner_" + "8" * 32,
        source_budget_bytes=10_000_000,
        archive_budget_bytes=10_000_000,
        minimum_source_free_bytes=0,
        minimum_archive_free_bytes=0,
        synthetic=True,
    )

    with pytest.raises(RuntimeStorageError, match="retention-reclamation"):
        activate_runtime_storage(
            source,
            archive_root=archive,
            authority=authority,
            now=150.0,
            streams=_policies(),
        )


def test_production_runtime_accepts_same_filesystem_retention_role(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()
    policies = _policies()
    archive_authority = PrivateArchiveAuthority(
        schema=ARCHIVE_AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="activate_private_archive_storage",
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        allowed_kinds=tuple(sorted({item.kind for item in policies})),
        issued_at=100.0,
        expires_at=300.0,
        turn_id="production-retention-test",
        authority_id="owner_" + "7" * 32,
        storage_role=RETENTION_RECLAMATION_ROLE,
        synthetic=False,
    )
    archive_capability = activate_private_archive_root(
        archive,
        source_root=source,
        authority=archive_authority,
        now=150.0,
    )
    authority = RuntimeStorageAuthority(
        schema=AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="activate_runtime_storage_rotation",
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        source_revision=SHA,
        issued_at=100.0,
        expires_at=300.0,
        turn_id="production-retention-test",
        authority_id="owner_" + "6" * 32,
        source_budget_bytes=10_000_000,
        archive_budget_bytes=10_000_000,
        minimum_source_free_bytes=0,
        minimum_archive_free_bytes=0,
        synthetic=False,
    )

    capability = activate_runtime_storage(
        source,
        archive_root=archive,
        authority=authority,
        now=150.0,
        streams=policies,
    )
    status = storage_budget_status(capability)

    assert archive_capability.storage_role == RETENTION_RECLAMATION_ROLE
    assert archive_capability.synthetic is False
    assert status["state"] == "ready"
    assert status["archive_storage_role"] == RETENTION_RECLAMATION_ROLE
    assert status["disaster_recovery_claim"] is False


def test_production_runtime_rejects_synthetic_retention_archive(
    tmp_path: Path,
) -> None:
    _synthetic_capability, source, archive = _activated(tmp_path)
    production_authority = RuntimeStorageAuthority(
        schema=AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="activate_runtime_storage_rotation",
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        source_revision=SHA,
        issued_at=100.0,
        expires_at=300.0,
        turn_id="production-retention-test",
        authority_id="owner_" + "5" * 32,
        source_budget_bytes=10_000_000,
        archive_budget_bytes=10_000_000,
        minimum_source_free_bytes=0,
        minimum_archive_free_bytes=0,
        synthetic=False,
    )

    with pytest.raises(RuntimeStorageError, match="synthetic archive"):
        activate_runtime_storage(
            source,
            archive_root=archive,
            authority=production_authority,
            now=150.0,
            streams=_policies(),
        )


def test_writer_coordinated_rotation_preserves_every_completed_row(tmp_path: Path) -> None:
    _capability, source, archive = _activated(tmp_path, cap=120)
    path = source / "logs" / "farm" / "cycle_log.jsonl"
    expected = [{"schema": "farm_journal.v1", "sequence": index} for index in range(25)]
    for row in expected:
        append_runtime_jsonl(path, row)

    active = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    archived = _rows_from_archive(archive)
    assert sorted(row["sequence"] for row in active + archived) == list(range(25))
    assert len(active) + len(archived) == 25
    assert read_runtime_tail(path, limit=25) == expected[-20:]
    assert not list((source / ".runtime-storage-v1" / "pending").rglob("*.sealed"))
    assert all(item.restore_verified for item in ArchiveCatalog(archive).manifests())


def test_archive_failure_retains_sealed_segment_and_retry_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability, source, archive = _activated(tmp_path, cap=80)
    path = source / "logs" / "farm" / "cycle_log.jsonl"
    original = ArchiveCatalog.register_jsonl

    def fail(*args, **kwargs):
        raise ArchiveCatalogError("synthetic interruption")

    monkeypatch.setattr(ArchiveCatalog, "register_jsonl", fail)
    with pytest.raises(RuntimeStorageError, match="failed closed"):
        append_runtime_jsonl(path, {"schema": "farm_journal.v1", "payload": "x" * 100})
    pending = list((source / ".runtime-storage-v1" / "pending").rglob("*.sealed"))
    assert len(pending) == 1
    assert ArchiveCatalog(archive).manifests() == []

    monkeypatch.setattr(ArchiveCatalog, "register_jsonl", original)
    first = archive_pending_segments(capability)
    second = archive_pending_segments(capability)
    assert first["archived"] == 1
    assert second["archived"] == 0
    assert not pending[0].exists()
    assert len(ArchiveCatalog(archive).manifests()) == 1


def test_concurrent_writers_rotate_without_loss_or_duplicate(tmp_path: Path) -> None:
    _capability, source, archive = _activated(tmp_path, cap=260)
    path = source / "logs" / "farm" / "cycle_log.jsonl"

    def writer(offset: int) -> None:
        for index in range(20):
            append_runtime_jsonl(
                path,
                {"schema": "farm_journal.v1", "sequence": offset + index},
            )

    threads = [threading.Thread(target=writer, args=(offset,)) for offset in (0, 100)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    active = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    rows = active + _rows_from_archive(archive)
    assert len(rows) == 40
    assert {row["sequence"] for row in rows} == set(range(20)) | set(range(100, 120))


def test_tail_replace_retries_bounded_windows_sharing_transient(
    tmp_path: Path, monkeypatch
) -> None:
    _capability, source, _archive = _activated(tmp_path, cap=4096)
    path = source / "logs" / "farm" / "cycle_log.jsonl"
    real_replace = runtime_storage_rotation.os.replace
    attempts = 0

    def contended_replace(source_path, target_path):
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            exc = PermissionError("synthetic sharing denial")
            exc.winerror = 5
            raise exc
        real_replace(source_path, target_path)

    monkeypatch.setattr(runtime_storage_rotation.os, "replace", contended_replace)
    monkeypatch.setattr(runtime_storage_rotation.time, "sleep", lambda _delay: None)

    append_runtime_jsonl(path, {"schema": "farm_journal.v1", "sequence": 1})

    assert attempts == 4
    assert json.loads(path.read_text(encoding="utf-8"))["sequence"] == 1
    tail_root = source / ".runtime-storage-v1" / "tails"
    assert not list(tail_root.glob(".*.tmp"))


def test_semantic_index_survives_rotation_for_lineage_and_llm_policy(tmp_path: Path) -> None:
    _capability, source, _archive = _activated(tmp_path, cap=120)
    llm = source / "state" / "llm_advice" / "invocations.jsonl"
    for sequence, status in enumerate(("provider_error", "provider_error", "provider_error", "accepted")):
        append_runtime_jsonl(
            llm,
            {
                "schema": "LLMInvocation.v1",
                "invocation_id": "llminv_stable",
                "status": status,
                "role_id": "calculator",
                "provider": "synthetic",
                "completed_at": f"2026-08-09T00:00:0{sequence}+00:00",
                "padding": "x" * 80,
            },
        )
    assert semantic_key_exists(source, stream_id="llm.invocations", key_type="invocation_id", key_value="llminv_stable", status="accepted")
    assert semantic_status_count(source, stream_id="llm.invocations", key_type="invocation_id", key_value="llminv_stable", statuses=("provider_error",)) == 3
    assert recent_semantic_statuses(source, stream_id="llm.invocations", role_id="calculator", provider="synthetic", limit=2) == ["provider_error", "accepted"]
    assert llm_invocation_summary(source)["by_status"] == {"accepted": 1}

    lineage = source / "state" / "lineage" / "cycle_links.jsonl"
    append_runtime_jsonl(lineage, {"schema": "LineageLink.v1", "lineage_link_id": "link_one", "source": "scanner"})
    append_runtime_jsonl(lineage, {"schema": "LineageLink.v1", "lineage_link_id": "link_two", "source": "paper"})
    assert semantic_key_values_for_path(lineage, key_type="lineage_link_id") == {"link_one", "link_two"}
    assert semantic_counts_for_path(lineage) == {
        "exists": True,
        "rows": 2,
        "by_key": {"paper": 1, "scanner": 1},
    }


def test_stdout_is_line_bounded_and_private_payload_is_not_retrievable(tmp_path: Path) -> None:
    _capability, source, archive = _activated(tmp_path, cap=64)
    path = source / "logs" / "farm_full_cycle_loop.log"
    append_runtime_lines(path, tuple(f"line-{index}\n".encode() for index in range(30)))
    manifests = ArchiveCatalog(archive).query(kinds=("runtime_stdout",))
    assert manifests
    assert all(item.load_policy == "metadata_only" for item in manifests)
    with pytest.raises(ArchiveCatalogError, match="not permitted"):
        ArchiveCatalog(archive).read_bounded_jsonl(
            manifests[0].artifact_id,
            max_records=10,
            max_uncompressed_bytes=1024,
        )


def test_budget_and_capability_drift_fail_closed(tmp_path: Path) -> None:
    capability, source, _archive = _activated(tmp_path, cap=64)
    path = source / "logs" / "farm_full_cycle_loop.log"
    append_runtime_lines(path, (b"bounded-line\n",))
    assert storage_budget_status(replace(capability, source_budget_bytes=10))["state"] == "failed"

    capability_path = source / ".runtime-storage-v1" / "capability.json"
    value = json.loads(capability_path.read_text(encoding="utf-8"))
    value["source_budget_bytes"] = 999
    capability_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RuntimeStorageError, match="digest mismatch"):
        load_runtime_storage_capability(source)


def test_no_capability_preserves_legacy_append_without_archive_discovery(tmp_path: Path) -> None:
    path = tmp_path / "plain" / "cycle.jsonl"
    append_runtime_jsonl(path, {"schema": "farm_journal.v1", "sequence": 1})
    assert json.loads(path.read_text(encoding="utf-8"))["sequence"] == 1
    assert not (tmp_path / ".runtime-storage-v1").exists()


def test_runtime_append_waits_for_transient_interprocess_writer(tmp_path: Path) -> None:
    _capability, source, _archive = _activated(tmp_path, cap=10_000)
    path = source / "logs" / "farm" / "cycle_log.jsonl"
    ready = tmp_path / "runtime-lock-ready"
    context = multiprocessing.get_context("spawn")
    child = context.Process(
        target=_hold_runtime_storage_lock,
        args=(str(source / ".runtime-storage-v1" / "rotation.lock"), str(ready), 0.5),
    )
    child.start()
    for _ in range(200):
        if ready.exists():
            break
        time.sleep(0.01)
    assert ready.read_text(encoding="ascii") == "locked"

    started = time.monotonic()
    append_runtime_jsonl(path, {"schema": "farm_journal.v1", "sequence": 1})
    elapsed = time.monotonic() - started

    child.join(timeout=15)
    assert child.exitcode == 0
    assert 0.1 <= elapsed < 5.0
    assert read_runtime_tail(path, limit=10) == [
        {"schema": "farm_journal.v1", "sequence": 1}
    ]


def test_activation_backfills_tail_and_semantic_index_before_sealing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()
    policies = _policies(cap=90)
    llm = source / "state" / "llm_advice" / "invocations.jsonl"
    llm.parent.mkdir(parents=True)
    llm.write_text(
        "".join(
            json.dumps(
                {
                    "schema": "LLMInvocation.v1",
                    "invocation_id": f"llminv_{index}",
                    "status": "accepted",
                    "role_id": "calculator",
                    "provider": "synthetic",
                    "completed_at": f"2026-08-09T00:00:{index:02d}+00:00",
                }
            )
            + "\n"
            for index in range(5)
        ),
        encoding="utf-8",
    )
    archive_authority = PrivateArchiveAuthority(
        schema=ARCHIVE_AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="activate_private_archive_storage",
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        allowed_kinds=tuple(sorted({item.kind for item in policies})),
        issued_at=100.0,
        expires_at=300.0,
        turn_id="synthetic-storage-test",
        authority_id="owner_" + "c" * 32,
        storage_role=RETENTION_RECLAMATION_ROLE,
        synthetic=True,
    )
    activate_private_archive_root(archive, source_root=source, authority=archive_authority, now=150.0)
    authority = RuntimeStorageAuthority(
        schema=AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="activate_runtime_storage_rotation",
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        source_revision=SHA,
        issued_at=100.0,
        expires_at=300.0,
        turn_id="synthetic-storage-test",
        authority_id="owner_" + "d" * 32,
        source_budget_bytes=10_000_000,
        archive_budget_bytes=10_000_000,
        minimum_source_free_bytes=0,
        minimum_archive_free_bytes=0,
        synthetic=True,
    )
    activate_runtime_storage(source, archive_root=archive, authority=authority, now=150.0, streams=policies)
    assert semantic_key_exists(source, stream_id="llm.invocations", key_type="invocation_id", key_value="llminv_0", status="accepted")
    assert [row["invocation_id"] for row in read_runtime_tail(llm, limit=10)] == [f"llminv_{index}" for index in range(5)]
    assert not llm.exists()
    assert len(ArchiveCatalog(archive).query(kinds=("llm_invocation",))) == 1


def test_stale_archiving_claim_recovers_after_simulated_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capability, source, archive = _activated(tmp_path, cap=64)
    path = source / "logs" / "farm" / "cycle_log.jsonl"
    original = ArchiveCatalog.register_jsonl
    monkeypatch.setattr(ArchiveCatalog, "register_jsonl", lambda *args, **kwargs: (_ for _ in ()).throw(ArchiveCatalogError("interrupted")))
    with pytest.raises(RuntimeStorageError, match="failed closed"):
        append_runtime_jsonl(path, {"schema": "farm_journal.v1", "payload": "x" * 100})
    sealed = next((source / ".runtime-storage-v1" / "pending").rglob("*.sealed"))
    claimed = sealed.with_suffix(".archiving")
    sealed.replace(claimed)
    import os

    os.utime(claimed, (1.0, 1.0))
    monkeypatch.setattr(ArchiveCatalog, "register_jsonl", original)
    result = archive_pending_segments(capability)
    assert result["state"] == "ready"
    assert result["archived"] == 1
    assert not claimed.exists()
    assert len(ArchiveCatalog(archive).manifests()) == 1


def test_seal_pass_is_idempotent(tmp_path: Path) -> None:
    capability, source, _archive = _activated(tmp_path, cap=64)
    path = source / "logs" / "farm_full_cycle_loop.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 100)
    assert seal_oversized_active_streams(capability) == 1
    assert seal_oversized_active_streams(capability) == 0


def test_operator_cli_bootstraps_only_from_two_exact_authority_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()
    now = time.time()
    runtime_authority = RuntimeStorageAuthority(
        schema=AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="activate_runtime_storage_rotation",
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        source_revision=SHA,
        issued_at=now - 10,
        expires_at=now + 300,
        turn_id="synthetic-cli-test",
        authority_id="owner_" + "e" * 32,
        source_budget_bytes=10_000_000,
        archive_budget_bytes=10_000_000,
        minimum_source_free_bytes=0,
        minimum_archive_free_bytes=0,
        synthetic=True,
    )
    archive_authority = PrivateArchiveAuthority(
        schema=ARCHIVE_AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="activate_private_archive_storage",
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        allowed_kinds=tuple(sorted({item.kind for item in DEFAULT_STREAMS})),
        issued_at=now - 10,
        expires_at=now + 300,
        turn_id="synthetic-cli-test",
        authority_id="owner_" + "f" * 32,
        storage_role=RETENTION_RECLAMATION_ROLE,
        synthetic=True,
    )
    runtime_file = tmp_path / "runtime-authority.json"
    archive_file = tmp_path / "archive-authority.json"
    runtime_file.write_text(json.dumps(runtime_authority.__dict__), encoding="utf-8")
    archive_payload = archive_authority.payload()
    archive_file.write_text(json.dumps(archive_payload), encoding="utf-8")

    assert storage_cli_main(
        [
            "--source-root",
            str(source),
            "activate",
            "--archive-root",
            str(archive),
            "--authority-file",
            str(runtime_file),
            "--archive-authority-file",
            str(archive_file),
        ]
    ) == 0
    activation = json.loads(capsys.readouterr().out)
    assert activation["state"] == "active"
    assert activation["stream_count"] == len(DEFAULT_STREAMS)
    assert activation["archive_storage_role"] == RETENTION_RECLAMATION_ROLE
    assert activation["disaster_recovery_claim"] is False
    assert str(source) not in json.dumps(activation)
    assert str(archive) not in json.dumps(activation)

    assert storage_cli_main(["--source-root", str(source), "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"] == "ready"
    assert storage_cli_main(["--source-root", str(source), "maintain"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ready"
    assert storage_cli_main(["--source-root", str(source), "verify"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ready"


def test_operator_cli_failure_does_not_echo_authority_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()
    authority = tmp_path / "bad-authority.json"
    synthetic_sensitive = "synthetic-credential-value-must-not-echo"
    authority.write_text(json.dumps({"unexpected": synthetic_sensitive}), encoding="utf-8")
    result = storage_cli_main(
        [
            "--source-root",
            str(source),
            "activate",
            "--archive-root",
            str(archive),
            "--authority-file",
            str(authority),
        ]
    )
    output = capsys.readouterr().out
    assert result == 2
    assert synthetic_sensitive not in output
    assert str(source) not in output
    assert str(archive) not in output


def test_operator_archive_authority_requires_explicit_storage_role(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()
    authority = PrivateArchiveAuthority(
        schema=ARCHIVE_AUTHORITY_SCHEMA,
        project_id="trading-bot-v2",
        action="activate_private_archive_storage",
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        allowed_kinds=("farm_journal",),
        issued_at=100.0,
        expires_at=300.0,
        turn_id="missing-role-test",
        authority_id="owner_" + "4" * 32,
        storage_role=RETENTION_RECLAMATION_ROLE,
        synthetic=True,
    ).payload()
    authority.pop("storage_role")
    authority_path = tmp_path / "archive-authority.json"
    authority_path.write_text(json.dumps(authority), encoding="utf-8")

    with pytest.raises(RuntimeStorageError, match="shape is invalid"):
        _archive_authority(authority_path)
