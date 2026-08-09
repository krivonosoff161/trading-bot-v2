from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.project_brain.archive import (
    ProjectBrainArchiveError,
    load_archived_project_brain_events,
)
from src.project_brain.schema import ProjectGraph
from src.project_brain.store import ProjectBrainStore
from src.research_lab.archive_catalog import ArchiveCatalog, ArchiveCatalogError
from src.research_lab.archive_migration import (
    ArchiveMigrationError,
    ArchiveReadCutover,
    apply_migration_plan,
    build_migration_plan,
)
from src.research_lab.private_archive_capability import (
    AUTHORITY_SCHEMA,
    LEGACY_CAPABILITY_SCHEMA,
    DISASTER_RECOVERY_ROLE,
    RETENTION_RECLAMATION_ROLE,
    PrivateArchiveAuthority,
    PrivateArchiveCapabilityError,
    activate_private_archive_root,
    load_private_archive_capability,
)
from src.research_lab.storage_capability import canonical_json, content_digest


SHA = "1" * 40
NOW = 150.0
CREATED = "2026-07-28T00:00:00+00:00"


def _authority(
    source: Path,
    archive: Path,
    *,
    expires_at: float = 200.0,
    action: str = "activate_private_archive_storage",
    project_id: str = "trading-bot-v2",
    synthetic: bool = True,
    storage_role: str = RETENTION_RECLAMATION_ROLE,
) -> PrivateArchiveAuthority:
    return PrivateArchiveAuthority(
        schema=AUTHORITY_SCHEMA,
        project_id=project_id,
        action=action,
        source_root=str(source.resolve()),
        archive_root=str(archive.resolve()),
        allowed_kinds=(
            "farm_journal",
            "project_brain_events",
            "scout_journal",
        ),
        issued_at=100.0,
        expires_at=expires_at,
        turn_id="turn-phase0",
        authority_id="owner_" + "a" * 32,
        storage_role=storage_role,
        synthetic=synthetic,
    )


def _catalog(tmp_path: Path) -> tuple[ArchiveCatalog, Path, Path]:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()
    activate_private_archive_root(
        archive,
        source_root=source,
        authority=_authority(source, archive),
        now=NOW,
    )
    return ArchiveCatalog(archive), source, archive


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _memory_event(
    *,
    record_id: str,
    contour: str,
    commit_sha: str = SHA,
    summary: str = "safe archived checkpoint",
) -> dict:
    return {
        "event_schema": "ProjectBrainEvent.v1",
        "event": "record",
        "record": {
            "record_id": record_id,
            "contour": contour,
            "entity": "checkpoint",
            "type": "verification",
            "source": "synthetic-test",
            "evidence_refs": ["evidence:synthetic"],
            "repository": "trading-bot-v2",
            "branch": "codex/private-storage-foundation",
            "commit_sha": commit_sha,
            "content_hash": "b" * 64,
            "created_at": CREATED,
            "verified_at": CREATED,
            "freshness": "current",
            "confidence": "verified",
            "authority": "proposal_only",
            "supersedes": "",
            "summary": summary,
            "project_node_ids": [],
            "source_hashes": {},
            "causal_chain_id": "",
            "causal_stage": "",
            "task_id": "",
            "authority_id": "",
            "schema": "TradingProjectMemoryRecord.v1",
        },
    }


def test_private_archive_capability_requires_exact_fresh_owner_scope(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()

    with pytest.raises(PrivateArchiveCapabilityError, match="action mismatch"):
        activate_private_archive_root(
            archive,
            source_root=source,
            authority=_authority(source, archive, action="start_rcc"),
            now=NOW,
        )
    with pytest.raises(PrivateArchiveCapabilityError, match="not currently valid"):
        activate_private_archive_root(
            archive,
            source_root=source,
            authority=_authority(source, archive, expires_at=NOW),
            now=NOW,
        )
    with pytest.raises(PrivateArchiveCapabilityError, match="project mismatch"):
        activate_private_archive_root(
            archive,
            source_root=source,
            authority=_authority(source, archive, project_id="another-project"),
            now=NOW,
        )

    capability = activate_private_archive_root(
        archive,
        source_root=source,
        authority=_authority(source, archive),
        now=NOW,
    )
    assert capability.project_id == "trading-bot-v2"
    assert capability.synthetic is True
    assert load_private_archive_capability(archive) == capability


def test_owner_authorized_non_synthetic_retention_allows_same_filesystem(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()

    capability = activate_private_archive_root(
        archive,
        source_root=source,
        authority=_authority(
            source,
            archive,
            synthetic=False,
            storage_role=RETENTION_RECLAMATION_ROLE,
        ),
        now=NOW,
    )

    assert capability.storage_role == RETENTION_RECLAMATION_ROLE
    assert capability.synthetic is False
    assert capability.filesystem_identity == capability.source_filesystem_identity


@pytest.mark.parametrize("synthetic", [False, True])
def test_disaster_recovery_rejects_same_filesystem(
    tmp_path: Path, synthetic: bool
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()

    with pytest.raises(
        PrivateArchiveCapabilityError, match="disaster-recovery.*distinct filesystem"
    ):
        activate_private_archive_root(
            archive,
            source_root=source,
            authority=_authority(
                source,
                archive,
                synthetic=synthetic,
                storage_role=DISASTER_RECOVERY_ROLE,
            ),
            now=NOW,
        )


def test_disaster_recovery_accepts_only_distinct_filesystem(
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
    capability = activate_private_archive_root(
        archive,
        source_root=source,
        authority=_authority(
            source,
            archive,
            synthetic=False,
            storage_role=DISASTER_RECOVERY_ROLE,
        ),
        now=NOW,
    )

    assert capability.storage_role == DISASTER_RECOVERY_ROLE
    assert capability.filesystem_identity != capability.source_filesystem_identity


def test_unknown_archive_storage_role_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()

    with pytest.raises(PrivateArchiveCapabilityError, match="storage role"):
        activate_private_archive_root(
            archive,
            source_root=source,
            authority=_authority(source, archive, storage_role="unspecified"),
            now=NOW,
        )


def test_legacy_synthetic_capability_loads_as_retention_without_dr_claim(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    source.mkdir()
    archive.mkdir()
    activate_private_archive_root(
        archive,
        source_root=source,
        authority=_authority(source, archive),
        now=NOW,
    )
    capability_path = archive / ".archive-v1" / "capability.json"
    marker_path = archive / ".archive-v1" / "marker.json"
    manifest = json.loads(capability_path.read_text(encoding="utf-8"))
    manifest["schema"] = LEGACY_CAPABILITY_SCHEMA
    manifest.pop("storage_role")
    manifest.pop("capability_digest")
    digest = content_digest(manifest)
    capability_path.write_text(
        canonical_json({**manifest, "capability_digest": digest}),
        encoding="utf-8",
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["capability_digest"] = digest
    marker_path.write_text(canonical_json(marker), encoding="utf-8")

    loaded = load_private_archive_capability(archive)

    assert loaded.schema == LEGACY_CAPABILITY_SCHEMA
    assert loaded.storage_role == RETENTION_RECLAMATION_ROLE
    assert loaded.synthetic is True


def test_capability_has_no_default_path_or_environment_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "archive"
    other = tmp_path / "other"
    source.mkdir()
    archive.mkdir()
    other.mkdir()
    monkeypatch.setenv("TRADING_BOT_RESEARCH_ROOT", str(other))
    monkeypatch.setenv("TRADING_PROJECT_BRAIN_HOME", str(other))

    capability = activate_private_archive_root(
        archive,
        source_root=source,
        authority=_authority(source, archive),
        now=NOW,
    )

    assert Path(capability.canonical_root) == archive.resolve()
    assert Path(capability.source_root) == source.resolve()
    assert not any(other.iterdir())


def test_catalog_is_content_addressed_rebuildable_and_bounded(tmp_path: Path) -> None:
    catalog, source, archive = _catalog(tmp_path)
    rows = [
        _memory_event(record_id="memory:one", contour="active_work"),
        _memory_event(record_id="memory:two", contour="farm_and_runtime"),
    ]
    legacy = source / "brain" / "events.jsonl"
    _write_jsonl(legacy, rows)

    manifest = catalog.register_jsonl(
        legacy,
        stream_id="project_brain.events",
        kind="project_brain_events",
        contour="active_work",
        payload_schema="ProjectBrainEvent.v1",
        source_revision=SHA,
        sensitivity="public_safe_derived",
        first_observed_at=CREATED,
        last_observed_at=CREATED,
        created_at=CREATED,
        restore_verified=True,
    )

    assert manifest.copy_verified is True
    assert manifest.restore_verified is True
    assert manifest.record_count == 2
    assert manifest.object_ref.startswith("objects/")
    assert catalog.query(kinds=("project_brain_events",)) == [manifest]
    assert catalog.read_bounded_jsonl(
        manifest.artifact_id,
        max_records=1,
        max_uncompressed_bytes=1024 * 1024,
    ) == [rows[0]]
    assert catalog.rebuild_index() == 1
    assert (archive / ".archive-v1" / "catalog.sqlite3").is_file()


def test_private_payload_is_catalogued_but_never_returned(tmp_path: Path) -> None:
    catalog, source, _archive = _catalog(tmp_path)
    legacy = source / "farm" / "cycle.jsonl"
    _write_jsonl(legacy, [{"schema": "farm_journal.v1", "cycle": 1}])

    manifest = catalog.register_jsonl(
        legacy,
        stream_id="farm.cycle",
        kind="farm_journal",
        contour="farm_and_runtime",
        payload_schema="farm_journal.v1",
        source_revision=SHA,
        sensitivity="private_payload",
        first_observed_at=CREATED,
        last_observed_at=CREATED,
        created_at=CREATED,
    )

    assert manifest.load_policy == "metadata_only"
    with pytest.raises(ArchiveCatalogError, match="not permitted"):
        catalog.read_bounded_jsonl(
            manifest.artifact_id,
            max_records=10,
            max_uncompressed_bytes=1024,
        )


def test_sensitive_synthetic_memory_value_fails_closed_without_echo(
    tmp_path: Path,
) -> None:
    catalog, source, _archive = _catalog(tmp_path)
    row = _memory_event(record_id="memory:secret", contour="active_work")
    synthetic_sensitive_value = "gh" + "p_" + ("A" * 20)
    row["record"]["summary"] = synthetic_sensitive_value
    legacy = source / "brain" / "events.jsonl"
    _write_jsonl(legacy, [row])
    with pytest.raises(ArchiveCatalogError) as caught:
        catalog.register_jsonl(
            legacy,
            stream_id="project_brain.events",
            kind="project_brain_events",
            contour="active_work",
            payload_schema="ProjectBrainEvent.v1",
            source_revision=SHA,
            sensitivity="public_safe_derived",
            first_observed_at=CREATED,
            last_observed_at=CREATED,
            created_at=CREATED,
        )
    assert synthetic_sensitive_value not in str(caught.value)
    assert catalog.manifests() == []
    assert list(catalog.staging_root.iterdir()) == []


def test_migration_revalidates_source_and_cutover_is_reversible(
    tmp_path: Path,
) -> None:
    catalog, source, _archive = _catalog(tmp_path)
    legacy = source / "farm" / "cycle.jsonl"
    _write_jsonl(legacy, [{"schema": "farm_journal.v1", "cycle": 1}])
    plan = build_migration_plan(
        catalog,
        legacy,
        stream_id="farm.cycle",
        kind="farm_journal",
        contour="farm_and_runtime",
        payload_schema="farm_journal.v1",
        source_revision=SHA,
        sensitivity="private_payload",
        first_observed_at=CREATED,
        last_observed_at=CREATED,
        created_at=CREATED,
    )
    manifest = apply_migration_plan(catalog, plan, restore_verified=True)
    selector = ArchiveReadCutover(catalog)
    promoted = selector.promote(
        stream_id="farm.cycle",
        artifact_id=manifest.artifact_id,
        request_id="req_" + "1" * 32,
        created_at=CREATED,
    )
    assert promoted == selector.promote(
        stream_id="farm.cycle",
        artifact_id=manifest.artifact_id,
        request_id="req_" + "1" * 32,
        created_at=CREATED,
    )
    assert selector.status("farm.cycle")["read_source"] == "archive"
    selector.rollback(
        stream_id="farm.cycle",
        request_id="req_" + "2" * 32,
        created_at=CREATED,
    )
    assert selector.status("farm.cycle")["read_source"] == "legacy"
    assert legacy.is_file()
    assert catalog.manifests() == [manifest]


def test_cutover_compare_and_append_is_fail_closed_and_idempotent(
    tmp_path: Path,
) -> None:
    catalog, source, _archive = _catalog(tmp_path)
    legacy = source / "farm" / "cycle.jsonl"
    _write_jsonl(legacy, [{"schema": "farm_journal.v1", "cycle": 1}])
    plan = build_migration_plan(
        catalog,
        legacy,
        stream_id="farm.cycle",
        kind="farm_journal",
        contour="farm_and_runtime",
        payload_schema="farm_journal.v1",
        source_revision=SHA,
        sensitivity="private_payload",
        first_observed_at=CREATED,
        last_observed_at=CREATED,
        created_at=CREATED,
    )
    manifest = apply_migration_plan(catalog, plan, restore_verified=True)
    selector = ArchiveReadCutover(catalog)
    request_id = "req_" + "4" * 32
    with pytest.raises(ArchiveMigrationError, match="stream mismatch"):
        selector.promote(
            stream_id="farm.other",
            artifact_id=manifest.artifact_id,
            request_id="req_" + "6" * 32,
            created_at=CREATED,
        )
    first = selector.promote(
        stream_id="farm.cycle",
        artifact_id=manifest.artifact_id,
        request_id=request_id,
        created_at=CREATED,
    )
    assert (
        selector.promote(
            stream_id="farm.cycle",
            artifact_id=manifest.artifact_id,
            request_id=request_id,
            created_at=CREATED,
        )
        == first
    )
    with pytest.raises(ArchiveMigrationError, match="state changed"):
        selector.promote(
            stream_id="farm.cycle",
            artifact_id=manifest.artifact_id,
            request_id="req_" + "5" * 32,
            created_at=CREATED,
        )
    with pytest.raises(ArchiveMigrationError, match="reused"):
        selector.promote(
            stream_id="farm.cycle",
            artifact_id=manifest.artifact_id,
            request_id=request_id,
            created_at="2026-07-28T00:00:01+00:00",
        )


def test_migration_blocks_changed_source_and_unverified_promotion(
    tmp_path: Path,
) -> None:
    catalog, source, _archive = _catalog(tmp_path)
    legacy = source / "farm" / "cycle.jsonl"
    _write_jsonl(legacy, [{"schema": "farm_journal.v1", "cycle": 1}])
    plan = build_migration_plan(
        catalog,
        legacy,
        stream_id="farm.cycle",
        kind="farm_journal",
        contour="farm_and_runtime",
        payload_schema="farm_journal.v1",
        source_revision=SHA,
        sensitivity="private_payload",
        first_observed_at=CREATED,
        last_observed_at=CREATED,
        created_at=CREATED,
    )
    _write_jsonl(legacy, [{"schema": "farm_journal.v1", "cycle": 2}])
    with pytest.raises(ArchiveMigrationError, match="changed"):
        apply_migration_plan(catalog, plan, restore_verified=True)

    clean_plan = build_migration_plan(
        catalog,
        legacy,
        stream_id="farm.cycle",
        kind="farm_journal",
        contour="farm_and_runtime",
        payload_schema="farm_journal.v1",
        source_revision=SHA,
        sensitivity="private_payload",
        first_observed_at=CREATED,
        last_observed_at=CREATED,
        created_at=CREATED,
    )
    manifest = apply_migration_plan(catalog, clean_plan, restore_verified=False)
    with pytest.raises(ArchiveMigrationError, match="restore verification"):
        ArchiveReadCutover(catalog).promote(
            stream_id="farm.cycle",
            artifact_id=manifest.artifact_id,
            request_id="req_" + "3" * 32,
            created_at=CREATED,
        )


def test_object_or_manifest_tamper_blocks_catalog_reads(tmp_path: Path) -> None:
    catalog, source, archive = _catalog(tmp_path)
    legacy = source / "brain" / "events.jsonl"
    _write_jsonl(
        legacy,
        [_memory_event(record_id="memory:one", contour="active_work")],
    )
    manifest = catalog.register_jsonl(
        legacy,
        stream_id="project_brain.events",
        kind="project_brain_events",
        contour="active_work",
        payload_schema="ProjectBrainEvent.v1",
        source_revision=SHA,
        sensitivity="public_safe_derived",
        first_observed_at=CREATED,
        last_observed_at=CREATED,
        created_at=CREATED,
    )
    object_path = archive / Path(*manifest.object_ref.split("/"))
    object_path.write_bytes(object_path.read_bytes() + b"tamper")

    with pytest.raises(ArchiveCatalogError, match="size mismatch|digest mismatch"):
        catalog.manifests()


def test_project_brain_archive_loader_is_contour_and_revision_bound(
    tmp_path: Path,
) -> None:
    catalog, source, _archive = _catalog(tmp_path)
    rows = [
        _memory_event(record_id="memory:active", contour="active_work"),
        _memory_event(record_id="memory:farm", contour="farm_and_runtime"),
        _memory_event(
            record_id="memory:stale",
            contour="active_work",
            commit_sha="2" * 40,
        ),
    ]
    legacy = source / "brain" / "events.jsonl"
    _write_jsonl(legacy, rows)
    catalog.register_jsonl(
        legacy,
        stream_id="project_brain.events",
        kind="project_brain_events",
        contour="active_work",
        payload_schema="ProjectBrainEvent.v1",
        source_revision=SHA,
        sensitivity="public_safe_derived",
        first_observed_at=CREATED,
        last_observed_at=CREATED,
        created_at=CREATED,
    )

    selected = load_archived_project_brain_events(
        catalog,
        contours=("active_work",),
        allowed_commit_shas=(SHA,),
        max_records=10,
    )
    assert [row["record"]["record_id"] for row in selected] == ["memory:active"]
    with pytest.raises(ProjectBrainArchiveError, match="explicit contour"):
        load_archived_project_brain_events(
            catalog,
            contours=(),
            allowed_commit_shas=(SHA,),
        )

    graph = ProjectGraph("trading-bot-v2", SHA, "3" * 40, CREATED)
    brain_root = tmp_path / "brain-store"
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    store = ProjectBrainStore(
        brain_root,
        repository_root=repository_root,
        allow_test_root=True,
    )
    store.initialize(graph)
    store.rebuild_index(graph, archived_events=selected)
    assert [record["record_id"] for record in store.records()] == ["memory:active"]


def test_project_brain_archive_keeps_only_links_between_selected_records(
    tmp_path: Path,
) -> None:
    catalog, source, _archive = _catalog(tmp_path)
    rows = [
        _memory_event(record_id="memory:one", contour="active_work"),
        _memory_event(record_id="memory:two", contour="active_work"),
        _memory_event(record_id="memory:farm", contour="farm_and_runtime"),
        {
            "event_schema": "ProjectBrainEvent.v1",
            "event": "causal_link",
            "link_id": "causal:kept",
            "chain_id": "chain:one",
            "source_record_id": "memory:one",
            "target_record_id": "memory:two",
            "relation": "supports",
            "evidence_refs": ["evidence:synthetic"],
            "created_at": CREATED,
        },
        {
            "event_schema": "ProjectBrainEvent.v1",
            "event": "causal_link",
            "link_id": "causal:dropped",
            "chain_id": "chain:one",
            "source_record_id": "memory:two",
            "target_record_id": "memory:farm",
            "relation": "supports",
            "evidence_refs": ["evidence:synthetic"],
            "created_at": CREATED,
        },
    ]
    legacy = source / "brain" / "events.jsonl"
    _write_jsonl(legacy, rows)
    catalog.register_jsonl(
        legacy,
        stream_id="project_brain.events",
        kind="project_brain_events",
        contour="active_work",
        payload_schema="ProjectBrainEvent.v1",
        source_revision=SHA,
        sensitivity="public_safe_derived",
        first_observed_at=CREATED,
        last_observed_at=CREATED,
        created_at=CREATED,
    )

    selected = load_archived_project_brain_events(
        catalog,
        contours=("active_work",),
        allowed_commit_shas=(SHA,),
        max_records=10,
    )

    assert [row["event"] for row in selected] == ["record", "record", "causal_link"]
    assert selected[-1]["link_id"] == "causal:kept"


def test_memory_loader_never_accepts_private_archive_kind(tmp_path: Path) -> None:
    catalog, source, _archive = _catalog(tmp_path)
    legacy = source / "farm" / "cycle.jsonl"
    _write_jsonl(legacy, [{"schema": "farm_journal.v1", "cycle": 1}])
    catalog.register_jsonl(
        legacy,
        stream_id="farm.cycle",
        kind="farm_journal",
        contour="farm_and_runtime",
        payload_schema="farm_journal.v1",
        source_revision=SHA,
        sensitivity="private_payload",
        first_observed_at=CREATED,
        last_observed_at=CREATED,
        created_at=CREATED,
    )
    assert (
        load_archived_project_brain_events(
            catalog,
            contours=("farm_and_runtime",),
            allowed_commit_shas=(SHA,),
        )
        == []
    )
