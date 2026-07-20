import os
import json
import shutil
import sqlite3

import pytest

from src.research_lab.ownership import ProcessIdentity
from src.research_lab.storage_capability import RESERVED, activate_synthetic_root
from src.research_lab.storage_maintenance_store import (
    StorageMaintenanceConflict,
    StorageMaintenanceStore,
)


def _identity(pid=101):
    return ProcessIdentity(pid, float(pid), f"python-{pid}.exe", f"sha256:cmd-{pid}")


def _store(tmp_path, clock=lambda: 100.0):
    root = tmp_path / "managed" / "root"
    root.mkdir(parents=True)
    activate_synthetic_root(root)
    store = StorageMaintenanceStore(root, clock=clock)
    store.activate()
    lease = store.acquire_writer(owner_id="owner-a", identity=_identity(), lease_seconds=30)
    return root, store, lease


def test_quarantine_and_restore_preserve_exact_path_and_bytes(tmp_path):
    root, store, lease = _store(tmp_path)
    source = root / "cache" / "nested" / "same.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"first-evidence")

    result = store.quarantine_to_budget(lease, max_mb=0.000001)
    item = result["items"][0]

    assert item["relative_path"] == "cache/nested/same.json"
    assert item["state"] == "quarantined"
    assert not source.exists()
    assert result["physical_bytes_reclaimed"] == 0
    restored = store.restore(lease, item_id=item["item_id"])
    assert restored["state"] == "restored"
    assert source.read_bytes() == b"first-evidence"
    assert StorageMaintenanceStore.audit_readonly(root)["events"] == 5


def test_same_basename_in_different_paths_never_collides(tmp_path):
    root, store, lease = _store(tmp_path)
    first = root / "cache" / "a" / "same.json"
    second = root / "cache" / "b" / "same.json"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"a" * 100)
    second.write_bytes(b"b" * 100)

    result = store.quarantine_to_budget(lease, max_mb=0.000001)

    assert {item["relative_path"] for item in result["items"]} == {
        "cache/a/same.json",
        "cache/b/same.json",
    }
    assert len(list((root / RESERVED / "quarantine").rglob("same.json"))) == 2
    for item in result["items"]:
        assert store.restore(lease, item_id=item["item_id"])["state"] == "restored"
    assert first.read_bytes() == b"a" * 100
    assert second.read_bytes() == b"b" * 100


def test_final_instruction_replacement_is_preserved_as_conflict(tmp_path):
    root, store, lease = _store(tmp_path)
    source = root / "cache" / "target.json"
    source.parent.mkdir()
    source.write_bytes(b"planned")

    def replace_after_validation(path):
        path.write_bytes(b"replacement")

    result = store.quarantine_to_budget(
        lease,
        max_mb=0.000001,
        after_validate=replace_after_validation,
    )

    assert result["items"][0]["state"] == "conflict"
    assert source.read_bytes() == b"replacement"
    audit = StorageMaintenanceStore.audit_readonly(root)
    assert audit["events"] == 2


def test_restore_rejects_occupied_destination_and_tampered_quarantine(tmp_path):
    root, store, lease = _store(tmp_path)
    source = root / "cache" / "target.json"
    source.parent.mkdir()
    source.write_bytes(b"evidence")
    item = store.quarantine_to_budget(lease, max_mb=0.000001)["items"][0]
    source.write_bytes(b"new-owner")
    with pytest.raises(StorageMaintenanceConflict, match="occupied"):
        store.restore(lease, item_id=item["item_id"])
    source.unlink()
    quarantined = next((root / RESERVED / "quarantine").rglob("target.json"))
    quarantined.write_bytes(b"tampered")
    with pytest.raises(StorageMaintenanceConflict, match="do not match"):
        store.restore(lease, item_id=item["item_id"])


def test_expiry_and_takeover_reject_old_writer(tmp_path):
    now = [100.0]
    root, store, first = _store(tmp_path, clock=lambda: now[0])
    source = root / "cache" / "target.json"
    source.parent.mkdir()
    source.write_bytes(b"evidence")
    now[0] = 131.0
    second = store.acquire_writer(owner_id="owner-b", identity=_identity(202), lease_seconds=30)

    with pytest.raises(StorageMaintenanceConflict, match="stale|expired"):
        store.quarantine_to_budget(first, max_mb=0.000001)
    result = store.quarantine_to_budget(second, max_mb=0.000001)
    assert result["items"][0]["state"] == "quarantined"


def test_same_owner_renewal_invalidates_prior_mutation_sequence(tmp_path):
    root, store, first = _store(tmp_path)
    second = store.acquire_writer(
        owner_id="owner-a", identity=_identity(), lease_seconds=30
    )
    source = root / "cache" / "target.json"
    source.parent.mkdir()
    source.write_bytes(b"evidence")

    with pytest.raises(StorageMaintenanceConflict, match="stale|expired"):
        store.quarantine_to_budget(first, max_mb=0.000001)
    assert store.quarantine_to_budget(second, max_mb=0.000001)["items"][0][
        "state"
    ] == "quarantined"


def test_partial_operation_keeps_truthful_durable_item_state(tmp_path):
    root, store, lease = _store(tmp_path)
    for name in ("a.json", "b.json"):
        path = root / "cache" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(name.encode() * 100)

    with pytest.raises(StorageMaintenanceConflict, match="synthetic failure"):
        store.quarantine_to_budget(lease, max_mb=0.000001, fail_after_items=1)

    audit = StorageMaintenanceStore.audit_readonly(root)
    assert audit["events"] == 3
    assert len(list((root / RESERVED / "quarantine").rglob("*.json"))) == 1


@pytest.mark.parametrize(
    "phase,events_before",
    [
        ("after_claim_move", 1),
        ("after_claim_event", 2),
        ("after_quarantine_move", 2),
    ],
)
def test_recovery_finishes_only_unambiguous_crash_phases(tmp_path, phase, events_before):
    root, store, lease = _store(tmp_path)
    source = root / "cache" / "target.json"
    source.parent.mkdir()
    source.write_bytes(b"recovery-evidence")
    with pytest.raises(StorageMaintenanceConflict, match="synthetic crash"):
        store.quarantine_to_budget(lease, max_mb=0.000001, fail_phase=phase)
    assert StorageMaintenanceStore.audit_readonly(root)["events"] == events_before

    recovered = store.recover(lease)

    assert recovered == {"recovered": 1, "conflicts": 0, "failed": 0}
    assert not source.exists()
    assert next((root / RESERVED / "quarantine").rglob("target.json")).read_bytes() == (
        b"recovery-evidence"
    )
    assert StorageMaintenanceStore.audit_readonly(root)["events"] == 3


def test_readonly_audit_does_not_activate_missing_journal(tmp_path):
    root = tmp_path / "managed" / "root"
    root.mkdir(parents=True)
    activate_synthetic_root(root)
    journal = root / RESERVED / "operations.sqlite3"
    before = set(root.rglob("*"))

    assert StorageMaintenanceStore.audit_readonly(root) == {"activated": False, "events": 0}
    assert not journal.exists()
    assert set(root.rglob("*")) == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only symlink fixture")
def test_symlink_candidate_is_not_followed(tmp_path):
    root, store, lease = _store(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")
    cache = root / "cache"
    cache.mkdir()
    (cache / "linked.json").symlink_to(outside)
    with pytest.raises(Exception):
        store.quarantine_to_budget(lease, max_mb=0.000001)
    assert outside.read_bytes() == b"outside"


def test_capability_is_reloaded_immediately_before_mutation(tmp_path):
    root, store, lease = _store(tmp_path)
    source = root / "cache" / "target.json"
    source.parent.mkdir()
    source.write_bytes(b"evidence")
    marker_path = root / RESERVED / "marker.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["root_id"] = "tampered"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(StorageMaintenanceConflict, match="capability"):
        store.quarantine_to_budget(lease, max_mb=0.000001)
    assert source.read_bytes() == b"evidence"


def test_copied_operation_journal_cannot_activate_at_another_root(tmp_path):
    first, first_store, _lease = _store(tmp_path / "first")
    second = tmp_path / "second" / "managed" / "root"
    second.mkdir(parents=True)
    activate_synthetic_root(second)
    second_store = StorageMaintenanceStore(second)
    shutil.copyfile(first_store.path, second_store.path)

    with pytest.raises(StorageMaintenanceConflict, match="binding mismatch"):
        second_store.activate()


def test_lease_expiry_between_validation_and_move_preserves_source(tmp_path):
    now = [100.0]
    root, store, lease = _store(tmp_path, clock=lambda: now[0])
    source = root / "cache" / "target.json"
    source.parent.mkdir()
    source.write_bytes(b"evidence")

    def expire(_path):
        now[0] = 131.0

    with pytest.raises(StorageMaintenanceConflict, match="stale|expired"):
        store.quarantine_to_budget(
            lease, max_mb=0.000001, after_validate=expire
        )
    assert source.read_bytes() == b"evidence"
    assert StorageMaintenanceStore.audit_readonly(root)["state_counts"]["planned"] == 1


def test_recovery_rejects_content_identical_replacement_identity(tmp_path):
    root, store, lease = _store(tmp_path)
    source = root / "cache" / "target.json"
    source.parent.mkdir()
    source.write_bytes(b"same-bytes")
    with pytest.raises(StorageMaintenanceConflict, match="synthetic crash"):
        store.quarantine_to_budget(
            lease, max_mb=0.000001, fail_phase="after_claim_move"
        )
    staging = next((root / RESERVED / "staging").rglob("*.claimed"))
    replacement = staging.with_suffix(".replacement")
    replacement.write_bytes(b"same-bytes")
    os.replace(replacement, staging)

    assert store.recover(lease) == {"recovered": 0, "conflicts": 1, "failed": 0}
    assert staging.read_bytes() == b"same-bytes"
    assert not list((root / RESERVED / "quarantine").rglob("target.json"))


def test_stale_writer_cannot_restore_after_takeover(tmp_path):
    now = [100.0]
    root, store, first = _store(tmp_path, clock=lambda: now[0])
    source = root / "cache" / "target.json"
    source.parent.mkdir()
    source.write_bytes(b"evidence")
    item = store.quarantine_to_budget(first, max_mb=0.000001)["items"][0]
    now[0] = 131.0
    store.acquire_writer(owner_id="owner-b", identity=_identity(202), lease_seconds=30)

    with pytest.raises(StorageMaintenanceConflict, match="stale|expired"):
        store.restore(first, item_id=item["item_id"])
    assert not source.exists()


def test_readonly_audit_rejects_corrupt_absolute_event_path(tmp_path):
    root, store, lease = _store(tmp_path)
    source = root / "cache" / "target.json"
    source.parent.mkdir()
    source.write_bytes(b"evidence")
    store.quarantine_to_budget(lease, max_mb=0.000001)
    with sqlite3.connect(store.path) as conn:
        conn.execute("DROP TRIGGER immutable_item_events_update")
        conn.execute("UPDATE item_events SET relative_path='C:/escape.json'")

    with pytest.raises(Exception, match="relative|cache|canonical|digest|binding"):
        StorageMaintenanceStore.audit_readonly(root)


def test_recovery_retry_after_completion_is_idempotent(tmp_path):
    root, store, lease = _store(tmp_path)
    source = root / "cache" / "target.json"
    source.parent.mkdir()
    source.write_bytes(b"evidence")
    with pytest.raises(StorageMaintenanceConflict, match="synthetic crash"):
        store.quarantine_to_budget(
            lease, max_mb=0.000001, fail_phase="after_claim_event"
        )
    assert store.recover(lease)["recovered"] == 1
    events = StorageMaintenanceStore.audit_readonly(root)["events"]

    assert store.recover(lease) == {"recovered": 0, "conflicts": 0, "failed": 0}
    assert StorageMaintenanceStore.audit_readonly(root)["events"] == events


@pytest.mark.parametrize("phase", ["after_restore_intent", "after_restore_move"])
def test_recovery_finishes_unambiguous_restore_crash_phases(tmp_path, phase):
    root, store, lease = _store(tmp_path)
    source = root / "cache" / "target.json"
    source.parent.mkdir()
    source.write_bytes(b"restore-evidence")
    item = store.quarantine_to_budget(lease, max_mb=0.000001)["items"][0]

    with pytest.raises(StorageMaintenanceConflict, match="synthetic crash"):
        store.restore(lease, item_id=item["item_id"], fail_phase=phase)

    assert store.recover(lease) == {"recovered": 1, "conflicts": 0, "failed": 0}
    assert source.read_bytes() == b"restore-evidence"
    assert not list((root / RESERVED / "quarantine").rglob("target.json"))
    audit = StorageMaintenanceStore.audit_readonly(root)
    assert audit["state_counts"]["restored"] == 1
