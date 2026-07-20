from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.research_lab.storage_capability import RESERVED, activate_synthetic_root
from src.research_lab.storage_os_lock import StorageLockConflict, storage_root_lock
from src.research_lab.storage_segment_store import (
    DURABILITY_LINUX,
    DURABILITY_WINDOWS,
    FIXED_STREAMS,
    SegmentStore,
    SegmentStoreConflict,
    SegmentStoreError,
    SegmentStoreUnsupported,
)


def _child_abandon_root_lock(lock_path: str, ready_path: str) -> None:
    with storage_root_lock(Path(lock_path)):
        Path(ready_path).write_text("locked", encoding="ascii")
        os._exit(0)


def _child_crash_after_append_intent(root: str, request_id: str) -> None:
    def crash(phase, _path):
        if phase == "after_append_intent":
            os._exit(17)

    store = SegmentStore(
        Path(root), writer_id="writer_" + "9" * 32, fault_hook=crash
    )
    store.append("farm.cycle", {"child": True}, request_id=request_id)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "managed" / "root"
    root.mkdir(parents=True)
    activate_synthetic_root(root)
    return root


def _store(tmp_path: Path, *, fault_hook=None) -> tuple[Path, SegmentStore]:
    root = _root(tmp_path)
    store = SegmentStore(root, writer_id="writer_" + "1" * 32, fault_hook=fault_hook)
    store.activate()
    return root, store


def _events(root: Path) -> list[dict]:
    path = root / RESERVED / "segment_events.sqlite3"
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT event_json FROM events ORDER BY event_seq").fetchall()
    return [json.loads(row[0]) for row in rows]


def _rehash_event(event: dict) -> str:
    value = dict(event)
    value.pop("event_sha256")
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    value["event_sha256"] = hashlib.sha256(encoded).hexdigest()
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _last_intent_bytes(root: Path) -> bytes:
    path = root / RESERVED / "segment_events.sqlite3"
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT intent_bytes FROM events WHERE intent_bytes IS NOT NULL "
            "ORDER BY event_seq DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    return bytes(row[0])


def _stream_dir(root: Path, stream_id: str = "farm.cycle") -> Path:
    return root / RESERVED / "segments" / FIXED_STREAMS[stream_id].directory_token


def _read_path(path: Path) -> bytes:
    value = str(path.absolute())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\" + value
    with open(value, "rb") as handle:
        return handle.read()


def _write_path(path: Path, data: bytes) -> None:
    value = str(path.absolute())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\" + value
    with open(value, "wb") as handle:
        handle.write(data)


def _active(root: Path, stream_id: str = "farm.cycle") -> Path:
    matches = list(_stream_dir(root, stream_id).glob("*.open.jsonl"))
    assert len(matches) == 1
    return matches[0]


def _req(n: int) -> str:
    return "req_" + f"{n:032x}"


def test_readonly_absent_store_creates_nothing(tmp_path):
    root = _root(tmp_path)
    before = {p.relative_to(root).as_posix() for p in root.rglob("*")}

    assert SegmentStore.audit_readonly(root) == {
        "activated": False,
        "status": "absent",
        "events": 0,
    }
    assert {p.relative_to(root).as_posix() for p in root.rglob("*")} == before


def test_activation_binds_fixed_manifest_registry_and_sqlite_modes(tmp_path):
    root, store = _store(tmp_path)
    manifest = json.loads((root / RESERVED / "segment_store.json").read_text("utf-8"))

    assert manifest["schema"] == "SegmentStoreManifest.v2"
    assert manifest["protocol"] == "segmented-jsonl.v2"
    assert manifest["durability_mode"] in {DURABILITY_LINUX, DURABILITY_WINDOWS}
    assert set(p.name for p in (root / RESERVED / "segments").iterdir()) == {
        spec.directory_token for spec in FIXED_STREAMS.values()
    }
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # caller default is irrelevant
    assert store.audit()["status"] == "ok"


@pytest.mark.parametrize(
    "stream,payload",
    [
        ("unknown", {"x": 1}),
        ("farm.cycle", []),
        ("farm.cycle", {"x": math.nan}),
        ("farm.cycle", {"x": math.inf}),
        ("farm.cycle", {"x": 2**63}),
        ("farm.cycle", {"x": "\ud800"}),
    ],
)
def test_stream_and_payload_domain_fail_before_intent(tmp_path, stream, payload):
    root, store = _store(tmp_path)
    with pytest.raises((SegmentStoreError, ValueError, TypeError)):
        store.append(stream, payload, request_id=_req(1))
    assert _events(root) == []


def test_exact_header_record_hashes_and_idempotent_receipt(tmp_path):
    root, store = _store(tmp_path)
    receipt = store.append("farm.cycle", {"mode": "apply", "value": -0.0}, request_id=_req(1))
    retry = store.append("farm.cycle", {"mode": "apply", "value": -0.0}, request_id=_req(1))

    assert retry == receipt
    assert receipt["schema"] == "AppendReceipt.v2"
    lines = _active(root).read_bytes().splitlines(keepends=True)
    assert len(lines) == 2 and all(line.endswith(b"\n") for line in lines)
    header, record = [json.loads(line) for line in lines]
    assert set(header) == {
        "schema", "protocol", "canonicalization", "store_id", "root_id",
        "capability_digest", "registry_sha256", "durability_mode", "stream_id",
        "payload_schema", "segment_id", "segment_seq", "prior_segment_sha256",
        "frame_sha256",
    }
    assert set(record) == {
        "schema", "protocol", "store_id", "root_id", "capability_digest",
        "registry_sha256", "stream_id", "payload_schema", "segment_id",
        "request_id", "operation_id", "segment_seq", "stream_record_seq",
        "segment_record_seq", "prior_frame_sha256", "payload_sha256", "payload",
        "frame_sha256",
    }
    assert header["prior_segment_sha256"] is None
    assert record["prior_frame_sha256"] == header["frame_sha256"]
    assert len([event for event in _events(root) if event["event_type"] == "append_committed"]) == 1


def test_request_id_is_store_global_and_content_bound(tmp_path):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    before = _active(root).read_bytes()
    with pytest.raises(SegmentStoreConflict, match="operation_reuse_mismatch"):
        store.append("farm.error", {"x": 1}, request_id=_req(1))
    with pytest.raises(SegmentStoreConflict, match="operation_reuse_mismatch"):
        store.append("farm.cycle", {"x": 2}, request_id=_req(1))
    assert _active(root).read_bytes() == before


def test_concurrent_writers_each_commit_once_in_sequence(tmp_path):
    root, store = _store(tmp_path)

    def append(n: int):
        local = SegmentStore(root, writer_id="writer_" + f"{n + 2:032x}")
        return local.append("farm.cycle", {"n": n}, request_id=_req(n + 1))

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(append, range(16)))

    assert sorted(r["stream_record_seq"] for r in receipts) == list(range(1, 17))
    assert sorted(row["n"] for row in store.read_records("farm.cycle")) == list(range(16))


def test_package08a_and_segment_store_share_exact_os_lock(tmp_path):
    root, store = _store(tmp_path)
    lock_path = root / RESERVED / "locks" / "operation.lock"
    with storage_root_lock(lock_path):
        with pytest.raises((StorageLockConflict, SegmentStoreConflict)):
            store.append("farm.cycle", {"x": 1}, request_id=_req(1))


def test_observable_control_lock_type_replacement_fails_closed(tmp_path):
    root, store = _store(tmp_path)
    lock_path = root / RESERVED / "locks" / "operation.lock"
    lock_path.unlink()
    lock_path.mkdir()
    with pytest.raises(SegmentStoreConflict):
        store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    assert lock_path.is_dir()


def test_read_audit_contends_on_shared_root_lock(tmp_path):
    root, store = _store(tmp_path)
    lock_path = root / RESERVED / "locks" / "operation.lock"
    with storage_root_lock(lock_path):
        with pytest.raises(SegmentStoreConflict, match="already held"):
            store.audit()


def test_child_process_exit_releases_real_os_lock(tmp_path):
    root, store = _store(tmp_path)
    ready = tmp_path / "child-lock-ready"
    context = multiprocessing.get_context("spawn")
    child = context.Process(
        target=_child_abandon_root_lock,
        args=(str(root / RESERVED / "locks" / "operation.lock"), str(ready)),
    )
    child.start()
    child.join(timeout=15)
    assert child.exitcode == 0
    assert ready.read_text(encoding="ascii") == "locked"
    receipt = store.append("farm.cycle", {"parent": True}, request_id=_req(1))
    assert receipt["stream_record_seq"] == 1


def test_child_process_persisted_intent_recovers_exactly_once(tmp_path):
    root, _store_instance = _store(tmp_path)
    context = multiprocessing.get_context("spawn")
    child = context.Process(
        target=_child_crash_after_append_intent,
        args=(str(root), _req(1)),
    )
    child.start()
    child.join(timeout=15)
    assert child.exitcode == 17
    clean = SegmentStore(root, writer_id="writer_" + "8" * 32)
    receipt = clean.append(
        "farm.cycle", {"child": True}, request_id=_req(1)
    )
    assert receipt["stream_record_seq"] == 1
    assert clean.read_records("farm.cycle") == [{"child": True}]


def test_crash_after_append_intent_retries_persisted_frame_once(tmp_path):
    phases: list[str] = []

    def crash(phase, _path):
        phases.append(phase)
        if phase == "after_append_intent":
            raise RuntimeError("synthetic crash")

    root, store = _store(tmp_path, fault_hook=crash)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    clean = SegmentStore(root, writer_id="writer_" + "2" * 32)
    receipt = clean.append("farm.cycle", {"x": 1}, request_id=_req(1))
    assert receipt["stream_record_seq"] == 1
    assert clean.read_records("farm.cycle") == [{"x": 1}]


def test_full_frame_before_final_event_is_resynced_and_not_duplicated(tmp_path):
    crashed = [False]

    def crash(phase, _path):
        if phase == "after_append_fsync" and not crashed[0]:
            crashed[0] = True
            raise RuntimeError("synthetic crash")

    root, store = _store(tmp_path, fault_hook=crash)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    clean = SegmentStore(root, writer_id="writer_" + "2" * 32)
    clean.append("farm.cycle", {"x": 1}, request_id=_req(1))
    assert clean.read_records("farm.cycle") == [{"x": 1}]


def test_failed_append_fsync_with_visible_bytes_requires_later_fsync(
    tmp_path, monkeypatch
):
    root, store = _store(tmp_path)
    import src.research_lab.storage_segment_store as module

    original = module._sync_fd
    calls = 0

    def fail_second_sync(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic fsync failure")
        return original(fd)

    monkeypatch.setattr(module, "_sync_fd", fail_second_sync)
    with pytest.raises(OSError, match="synthetic fsync failure"):
        store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    assert _events(root)[-1]["event_type"] == "append_intent"
    monkeypatch.setattr(module, "_sync_fd", original)
    clean = SegmentStore(root, writer_id="writer_" + "7" * 32)
    clean.append("farm.cycle", {"x": 1}, request_id=_req(1))
    assert clean.read_records("farm.cycle") == [{"x": 1}]


def test_sqlite_terminal_failure_leaves_recoverable_intent(tmp_path, monkeypatch):
    root, store = _store(tmp_path)
    original = store._publish_terminal_from_intent
    failed = False

    def fail_terminal(intent, *, event_type, file_identity=None, reason_code=None):
        nonlocal failed
        if event_type == "append_committed" and not failed:
            failed = True
            raise sqlite3.OperationalError("synthetic terminal failure")
        return original(
            intent,
            event_type=event_type,
            file_identity=file_identity,
            reason_code=reason_code,
        )

    monkeypatch.setattr(store, "_publish_terminal_from_intent", fail_terminal)
    with pytest.raises(sqlite3.OperationalError, match="synthetic terminal failure"):
        store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    assert _events(root)[-1]["event_type"] == "append_intent"
    clean = SegmentStore(root, writer_id="writer_" + "7" * 32)
    clean.append("farm.cycle", {"x": 1}, request_id=_req(1))
    assert clean.read_records("farm.cycle") == [{"x": 1}]


def test_missing_receipt_after_terminal_is_derived_without_duplicate(
    tmp_path, monkeypatch
):
    root, store = _store(tmp_path)

    def lose_summary(_request_id, _receipt):
        raise sqlite3.OperationalError("synthetic summary failure")

    monkeypatch.setattr(store, "_set_receipt", lose_summary)
    with pytest.raises(sqlite3.OperationalError, match="synthetic summary failure"):
        store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    assert _events(root)[-1]["event_type"] == "append_committed"
    clean = SegmentStore(root, writer_id="writer_" + "7" * 32)
    first = clean.append("farm.cycle", {"x": 1}, request_id=_req(1))
    second = clean.append("farm.cycle", {"x": 1}, request_id=_req(1))
    assert first == second
    assert clean.read_records("farm.cycle") == [{"x": 1}]


@pytest.mark.parametrize("phase", ["after_open_intent", "after_open_fsync"])
def test_lazy_open_crash_recovers_without_sequence_reuse(tmp_path, phase):
    def crash(current, _path):
        if current == phase:
            raise RuntimeError("synthetic open crash")

    root, store = _store(tmp_path, fault_hook=crash)
    with pytest.raises(RuntimeError, match="synthetic open crash"):
        store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    clean = SegmentStore(root, writer_id="writer_" + "7" * 32)
    receipt = clean.append("farm.cycle", {"x": 1}, request_id=_req(1))
    assert receipt["segment_seq"] == 1
    assert clean.read_records("farm.cycle") == [{"x": 1}]


@pytest.mark.parametrize(
    "phase", ["after_seal_intent", "after_footer_bytes", "after_footer_fsync"]
)
def test_seal_pre_rename_crashes_recover_exact_footer(tmp_path, phase):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))

    def crash(current, _path):
        if current == phase:
            raise RuntimeError("synthetic seal crash")

    crashing = SegmentStore(
        root, writer_id="writer_" + "7" * 32, fault_hook=crash
    )
    with pytest.raises(RuntimeError, match="synthetic seal crash"):
        crashing.seal("farm.cycle", request_id=_req(2))
    clean = SegmentStore(root, writer_id="writer_" + "8" * 32)
    receipt = clean.seal("farm.cycle", request_id=_req(2))
    assert receipt["segment_seq"] == 1
    assert clean.read_records("farm.cycle") == [{"x": 1}]


def test_partial_footer_prefix_is_repaired_from_persisted_intent(tmp_path):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    crashing = SegmentStore(
        root,
        fault_hook=lambda phase, _path: (
            (_ for _ in ()).throw(RuntimeError("seal intent crash"))
            if phase == "after_seal_intent"
            else None
        ),
    )
    with pytest.raises(RuntimeError, match="seal intent crash"):
        crashing.seal("farm.cycle", request_id=_req(2))
    intended = _last_intent_bytes(root)
    with _active(root).open("ab") as handle:
        handle.write(intended[: len(intended) // 2])
        handle.flush()
        os.fsync(handle.fileno())
    clean = SegmentStore(root, writer_id="writer_" + "7" * 32)
    clean.seal("farm.cycle", request_id=_req(2))
    assert clean.read_records("farm.cycle") == [{"x": 1}]


@pytest.mark.parametrize("namespace", ["both", "none"])
def test_seal_ambiguous_namespace_states_conflict_without_deletion(
    tmp_path, namespace
):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    crashing = SegmentStore(
        root,
        fault_hook=lambda phase, _path: (
            (_ for _ in ()).throw(RuntimeError("seal intent crash"))
            if phase == "after_seal_intent"
            else None
        ),
    )
    with pytest.raises(RuntimeError):
        crashing.seal("farm.cycle", request_id=_req(2))
    intent = _events(root)[-1]
    source = _stream_dir(root) / intent["source_name"]
    target = _stream_dir(root) / intent["target_name"]
    if namespace == "both":
        _write_path(target, source.read_bytes())
    else:
        source.rename(tmp_path / "preserved-source.jsonl")
    clean = SegmentStore(root, writer_id="writer_" + "7" * 32)
    with pytest.raises(SegmentStoreConflict, match="namespace_ambiguous"):
        clean.recover()
    assert _events(root)[-1]["event_type"] == "conflict"
    if namespace == "both":
        assert source.exists() and _read_path(target)
    else:
        assert (tmp_path / "preserved-source.jsonl").exists()


def test_rename_failure_never_claims_sealed_and_retry_is_evidence_driven(
    tmp_path, monkeypatch
):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    import src.research_lab.storage_segment_store as module

    original = module._move_no_replace
    monkeypatch.setattr(
        module,
        "_move_no_replace",
        lambda *_args: (_ for _ in ()).throw(OSError("synthetic rename failure")),
    )
    with pytest.raises(OSError, match="synthetic rename failure"):
        store.seal("farm.cycle", request_id=_req(2))
    assert _events(root)[-1]["event_type"] == "seal_intent"
    monkeypatch.setattr(module, "_move_no_replace", original)
    clean = SegmentStore(root, writer_id="writer_" + "7" * 32)
    clean.seal("farm.cycle", request_id=_req(2))
    assert clean.read_records("farm.cycle") == [{"x": 1}]


def test_crash_after_seal_rename_is_ambiguous_on_windows(tmp_path):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    crashing = SegmentStore(
        root,
        fault_hook=lambda phase, _path: (
            (_ for _ in ()).throw(RuntimeError("post-rename crash"))
            if phase == "after_seal_rename"
            else None
        ),
    )
    with pytest.raises(RuntimeError, match="post-rename crash"):
        crashing.seal("farm.cycle", request_id=_req(2))
    clean = SegmentStore(root, writer_id="writer_" + "7" * 32)
    if os.name == "nt":
        with pytest.raises(SegmentStoreConflict, match="rename_result_ambiguous"):
            clean.recover()
        assert _events(root)[-1]["event_type"] == "conflict"
    else:
        clean.recover()
        assert clean.read_records("farm.cycle") == [{"x": 1}]


@pytest.mark.parametrize("fraction", [1, 2, 3])
def test_strict_torn_intended_suffix_is_truncated_and_retried(tmp_path, fraction):
    root, store = _store(tmp_path, fault_hook=lambda phase, _path: (
        (_ for _ in ()).throw(RuntimeError("synthetic crash"))
        if phase == "after_append_intent" else None
    ))
    with pytest.raises(RuntimeError):
        store.append("farm.cycle", {"x": "payload"}, request_id=_req(1))
    intended = _last_intent_bytes(root)
    active = _active(root)
    count = max(1, len(intended) * fraction // 4)
    with active.open("ab") as fh:
        fh.write(intended[:count])
        fh.flush()
        os.fsync(fh.fileno())

    clean = SegmentStore(root, writer_id="writer_" + "2" * 32)
    clean.append("farm.cycle", {"x": "payload"}, request_id=_req(1))
    assert clean.read_records("farm.cycle") == [{"x": "payload"}]


def test_every_strict_frame_prefix_is_classified_and_only_prefixes_are_repairable():
    import src.research_lab.storage_segment_store as module

    intended = module._frame_bytes(
        {"schema": "GoldenPrefix.v1", "payload": {"text": "\u00e9", "value": -0.0}}
    )
    assert all(
        module._is_strict_intended_prefix(intended[:size], intended)
        for size in range(1, len(intended))
    )
    assert not module._is_strict_intended_prefix(b"", intended)
    assert not module._is_strict_intended_prefix(intended, intended)
    assert not module._is_strict_intended_prefix(intended + b"x", intended)
    assert not module._is_strict_intended_prefix(b"not-a-prefix", intended)


def test_nonprefix_tail_becomes_stream_conflict_without_truncation(tmp_path):
    root, store = _store(tmp_path, fault_hook=lambda phase, _path: (
        (_ for _ in ()).throw(RuntimeError("synthetic crash"))
        if phase == "after_append_intent" else None
    ))
    with pytest.raises(RuntimeError):
        store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    active = _active(root)
    with active.open("ab") as fh:
        fh.write(b"not-the-intended-prefix")
        fh.flush()
        os.fsync(fh.fileno())
    before = active.read_bytes()

    clean = SegmentStore(root, writer_id="writer_" + "2" * 32)
    with pytest.raises(SegmentStoreConflict, match="tail_mismatch"):
        clean.recover()
    assert active.read_bytes() == before
    assert _events(root)[-1]["event_type"] == "conflict"
    clean.append("farm.error", {"ok": True}, request_id=_req(2))
    with pytest.raises(SegmentStoreConflict, match="blocked"):
        clean.append("farm.cycle", {"x": 2}, request_id=_req(3))


def test_seal_footer_and_cross_segment_digest_chain(tmp_path):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    sealed = store.seal("farm.cycle", request_id=_req(2))
    assert sealed["schema"] == "SealReceipt.v2"
    first = _stream_dir(root) / sealed["final_name"]
    footer = json.loads(_read_path(first).splitlines()[-1])
    assert footer["record_count"] == 1
    assert footer["prior_segment_sha256"] is None
    store.append("farm.cycle", {"x": 2}, request_id=_req(3))
    header = json.loads(_active(root).read_bytes().splitlines()[0])
    assert header["prior_segment_sha256"] == sealed["whole_file_sha256"]
    assert store.read_records("farm.cycle") == [{"x": 1}, {"x": 2}]


def test_event_transition_nullability_and_reason_vectors(tmp_path):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    store.seal("farm.cycle", request_id=_req(2))
    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            "SELECT event_json, intent_bytes FROM events ORDER BY event_seq"
        ).fetchall()
    events = [(json.loads(raw), blob) for raw, blob in rows]
    assert [event["event_type"] for event, _blob in events] == [
        "open_intent",
        "opened",
        "append_intent",
        "append_committed",
        "seal_intent",
        "sealed",
    ]
    for event, blob in events:
        is_intent = event["event_type"].endswith("_intent")
        assert (blob is not None) is is_intent
        assert (event["intent_size"] is not None) is is_intent
        assert (event["intent_sha256"] is not None) is is_intent
        assert event["reason_code"] is None
    assert events[0][0]["file_identity"] is None
    assert events[0][0]["pre_size"] is None
    assert events[1][0]["file_identity"] is not None
    assert events[2][0]["file_identity"] == events[3][0]["file_identity"]
    assert events[4][0]["file_identity"] == events[5][0]["file_identity"]
    assert events[4][0]["target_name"] == events[5][0]["target_name"]

    root2, crashing = _store(
        tmp_path / "conflict",
        fault_hook=lambda phase, _path: (
            (_ for _ in ()).throw(RuntimeError("append intent crash"))
            if phase == "after_append_intent"
            else None
        ),
    )
    with pytest.raises(RuntimeError):
        crashing.append("farm.error", {"x": 1}, request_id=_req(3))
    with _active(root2, "farm.error").open("ab") as handle:
        handle.write(b"not-intended")
    with pytest.raises(SegmentStoreConflict, match="tail_mismatch"):
        SegmentStore(root2).recover()
    conflict_events = _events(root2)
    intent, conflict = conflict_events[-2:]
    assert intent["event_type"] == "append_intent"
    assert conflict["event_type"] == "conflict"
    assert conflict["reason_code"] == "tail_mismatch"
    assert conflict["file_identity"] == intent["file_identity"]
    assert conflict["pre_size"] == intent["pre_size"]
    assert conflict["post_sha256"] == intent["post_sha256"]


def test_fixed_prefix_threshold_auto_seals_without_reclaiming_prior_bytes(tmp_path):
    root, store = _store(tmp_path)
    payload = "x" * 900_000
    for index in range(5):
        store.append(
            "farm.cycle",
            {"index": index, "payload": payload},
            request_id=_req(index + 1),
        )
    audit = store.audit()
    assert audit["streams"]["farm.cycle"]["sealed_segments"] == 1
    assert audit["streams"]["farm.cycle"]["active"] is True
    assert [row["index"] for row in store.read_records("farm.cycle")] == list(range(5))
    names = [path.name for path in _stream_dir(root).iterdir()]
    assert any(name.endswith(".sealed.jsonl") for name in names)
    assert any(name.endswith(".open.jsonl") for name in names)


def test_tamper_or_unknown_file_blocks_full_and_tail_reads(tmp_path):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    active = _active(root)
    active.write_bytes(active.read_bytes() + b"\n")
    with pytest.raises(SegmentStoreConflict):
        store.read_records("farm.cycle", limit=1)

    root2, store2 = _store(tmp_path / "other")
    store2.append("farm.cycle", {"x": 1}, request_id=_req(1))
    (_stream_dir(root2) / "undeclared.jsonl").write_bytes(b"{}\n")
    with pytest.raises(SegmentStoreConflict):
        store2.audit()


@pytest.mark.parametrize(
    "tamper",
    [
        lambda data: data + b"\n",
        lambda data: b"\xef\xbb\xbf" + data,
        lambda data: data.replace(b"\n", b"\r\n", 1),
        lambda data: data + b"\xff\n",
        lambda data: data + b'{"schema":"x","schema":"y"}\n',
        lambda data: data + b"{\n",
    ],
    ids=["blank", "bom", "crlf", "invalid-utf8", "duplicate-key", "malformed"],
)
def test_malformed_physical_frames_are_never_skipped(tmp_path, tamper):
    _root_path, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    active = _active(store.root)
    active.write_bytes(tamper(active.read_bytes()))
    with pytest.raises(SegmentStoreConflict):
        store.read_records("farm.cycle", limit=1)


def test_canonical_bytes_and_operation_id_golden_vectors():
    import src.research_lab.storage_segment_store as module

    value = {
        "float": 1.25,
        "negative_zero": -0.0,
        "schema": "Golden.v1",
        "text": "\u00e9",
    }
    encoded = module._canonical_bytes(value)
    assert encoded.hex() == (
        "7b22666c6f6174223a312e32352c226e656761746976655f7a65726f223a"
        "2d302e302c22736368656d61223a22476f6c64656e2e7631222c22746578"
        "74223a22c3a9227d"
    )
    assert hashlib.sha256(encoded).hexdigest() == (
        "185d44bc737678d520f9005e171fe487a842021818b76a13148db928164f5325"
    )
    assert module._operation_id(
        "segstore_" + "1" * 32,
        "req_" + "2" * 32,
        "append",
        "farm.cycle",
        7,
    ) == "op_9b4b9ac2dea9c98f3b7565696bdbbf8d"


def test_exact_header_record_footer_event_and_name_golden_vectors():
    import src.research_lab.storage_segment_store as module

    store_id = "segstore_" + "1" * 32
    root_id = "storageroot_" + "2" * 32
    capability_digest = "sha256:" + "3" * 64
    registry_digest = "4" * 64
    segment_id = "seg_" + "5" * 32
    request_id = "req_" + "6" * 32
    operation_id = "op_" + "7" * 32
    common = {
        "protocol": module.PROTOCOL,
        "store_id": store_id,
        "root_id": root_id,
        "capability_digest": capability_digest,
        "registry_sha256": registry_digest,
        "stream_id": "farm.cycle",
        "payload_schema": "farm_journal.v1",
        "segment_id": segment_id,
        "segment_seq": 1,
    }
    header = {
        "schema": "SegmentHeader.v2",
        **common,
        "canonicalization": module.CANONICALIZATION,
        "durability_mode": module.DURABILITY_WINDOWS,
        "prior_segment_sha256": None,
    }
    header_bytes = module._frame_bytes(header)
    header_hash = json.loads(header_bytes)["frame_sha256"]
    payload = {
        "record": {"float": 1.25, "negative_zero": -0.0, "text": "\u00e9"},
        "schema": "farm_journal.v1",
    }
    record = {
        "schema": "SegmentRecord.v2",
        **common,
        "request_id": request_id,
        "operation_id": operation_id,
        "stream_record_seq": 1,
        "segment_record_seq": 1,
        "prior_frame_sha256": header_hash,
        "payload_sha256": module._sha(module._canonical_bytes(payload)),
        "payload": payload,
    }
    record_bytes = module._frame_bytes(record)
    record_hash = json.loads(record_bytes)["frame_sha256"]
    prefix = header_bytes + record_bytes
    footer = {
        "schema": "SegmentFooter.v2",
        **common,
        "record_count": 1,
        "prefix_byte_size": len(prefix),
        "first_stream_record_seq": 1,
        "final_stream_record_seq": 1,
        "final_data_frame_sha256": record_hash,
        "prefix_sha256": module._sha(prefix),
        "prior_segment_sha256": None,
    }
    footer_bytes = module._frame_bytes(footer)
    whole = prefix + footer_bytes
    event = {
        "schema": "SegmentStoreEvent.v2",
        "protocol": module.PROTOCOL,
        "store_id": store_id,
        "root_id": root_id,
        "capability_digest": capability_digest,
        "registry_sha256": registry_digest,
        "durability_mode": module.DURABILITY_WINDOWS,
        "event_type": "append_intent",
        "event_id": "event_00000000000000000003",
        "request_id": request_id,
        "operation_id": operation_id,
        "operation_action": "append",
        "writer_id": "writer_" + "8" * 32,
        "stream_id": "farm.cycle",
        "payload_schema": "farm_journal.v1",
        "segment_id": segment_id,
        "source_name": f"{1:020d}.{segment_id}.open.jsonl",
        "target_name": None,
        "event_seq": 3,
        "segment_seq": 1,
        "file_identity": {"device": 9, "inode": 10, "volume_serial": 11},
        "pre_size": len(header_bytes),
        "post_size": len(prefix),
        "intent_size": len(record_bytes),
        "pre_sha256": module._sha(header_bytes),
        "post_sha256": module._sha(prefix),
        "intent_sha256": module._sha(record_bytes),
        "prior_event_sha256": "9" * 64,
        "reason_code": None,
    }
    event_bytes = module._canonical_bytes(module._with_hash(event, "event_sha256"))
    assert (len(header_bytes), module._sha(header_bytes), header_hash) == (
        693,
        "a8ac90ae37b74d5cfd83787f490e4f49ede70ef20b4c9ff098e9df926816943b",
        "aab73542cd4d6ea03f5b6a4107d25cce63234d8a7d41883b162ce5a64b363f78",
    )
    assert (len(record_bytes), module._sha(record_bytes), record_hash) == (
        981,
        "6d81cd4a41f72aec7d60f3ae42cdc1c21d4248d1de3e2258fe429ffe6920915c",
        "77bcc4cc175400e6297db5a0b0dcd7a402f6f0c90c187569fcdd06caf3a0806f",
    )
    assert (len(footer_bytes), module._sha(footer_bytes)) == (
        864,
        "1525be7a3e99467bf8828d37c2640bab8b6a446a32f6e5eedc29401f9f80ed2b",
    )
    assert (len(event_bytes), module._sha(event_bytes)) == (
        1461,
        "9bfcf1bd7b8da89baee1c172800f3a764a7ef055633bfba404e8871736c3f1cc",
    )
    whole_hash = module._sha(whole)
    assert whole_hash == "1bcfdf9fcca16fb075c396ba7437699bfd68bdd638c7af05604e440e2e85753d"
    assert f"{1:020d}.{segment_id}.{whole_hash}.sealed.jsonl" == (
        "00000000000000000001.seg_55555555555555555555555555555555."
        "1bcfdf9fcca16fb075c396ba7437699bfd68bdd638c7af05604e440e2e85753d."
        "sealed.jsonl"
    )


def test_sqlite_binding_or_mode_tamper_blocks_before_segment_mutation(tmp_path):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    before = _active(root).read_bytes()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE metadata SET value=? WHERE key='registry_sha256'",
            (json.dumps("tampered"),),
        )
        conn.commit()
    with pytest.raises(SegmentStoreConflict):
        store.append("farm.cycle", {"x": 2}, request_id=_req(2))
    assert _active(root).read_bytes() == before


@pytest.mark.parametrize(
    ("pragma", "value", "message"),
    [
        ("journal_mode", "wal", "journal mode"),
        ("synchronous", 2, "synchronous mode"),
        ("foreign_keys", 0, "foreign-key mode"),
        ("database_list", [(0, "main", "C:/wrong/database.sqlite3")], "canonical path"),
    ],
)
def test_sqlite_connection_binding_injections_fail_closed(
    tmp_path, pragma, value, message
):
    _root_path, store = _store(tmp_path)
    import src.research_lab.storage_segment_store as module

    held = module._open_file(store.db_path)
    identity = store._database_identity(held)
    conn = sqlite3.connect(store.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=EXTRA")

    class Result:
        def fetchone(self):
            return (value,)

        def fetchall(self):
            return value

    class Proxy:
        def execute(self, sql, *args):
            normalized = " ".join(sql.lower().split())
            if normalized == f"pragma {pragma}":
                return Result()
            return conn.execute(sql, *args)

    try:
        with pytest.raises(SegmentStoreConflict, match=message):
            store._verify_connection(Proxy(), identity)
    finally:
        conn.close()
        os.close(held)


def test_database_identity_drift_and_namespace_replacement_block_bytes(
    tmp_path, monkeypatch
):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    before = _active(root).read_bytes()
    original = store._database_identity
    calls = 0

    def drift(fd):
        nonlocal calls
        calls += 1
        identity = original(fd)
        if calls == 2:
            return {**identity, "inode": identity["inode"] + 1}
        return identity

    monkeypatch.setattr(store, "_database_identity", drift)
    with pytest.raises(SegmentStoreConflict, match="identity changed"):
        store.append("farm.cycle", {"x": 2}, request_id=_req(2))
    assert _active(root).read_bytes() == before
    monkeypatch.setattr(store, "_database_identity", original)

    backup = store.db_path.with_name("segment_events.sqlite3.preserved")
    store.db_path.rename(backup)
    store.db_path.mkdir()
    with pytest.raises((SegmentStoreConflict, OSError, sqlite3.Error)):
        store.append("farm.cycle", {"x": 2}, request_id=_req(2))
    assert backup.is_file()
    assert _active(root).read_bytes() == before


def test_event_head_change_inside_transaction_rolls_back_before_bytes(
    tmp_path, monkeypatch
):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    before = _active(root).read_bytes()
    original = store._verify_connection
    injected = False

    def alter_head(conn, identity):
        nonlocal injected
        original(conn, identity)
        if conn.in_transaction and not injected:
            request = conn.execute(
                "SELECT 1 FROM requests WHERE request_id=?", (_req(2),)
            ).fetchone()
            if request is not None:
                injected = True
                conn.execute(
                    "UPDATE events SET event_json=json_set("
                    "event_json, '$.prior_event_sha256', ?) "
                    "WHERE event_seq=(SELECT MAX(event_seq) FROM events)",
                    ("0" * 64,),
                )

    monkeypatch.setattr(store, "_verify_connection", alter_head)
    with pytest.raises(SegmentStoreConflict):
        store.append("farm.cycle", {"x": 2}, request_id=_req(2))
    assert injected is True
    assert _active(root).read_bytes() == before


def test_event_semantic_tamper_fails_even_with_recomputed_hash(tmp_path):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA ignore_check_constraints=ON")
        raw = conn.execute(
            "SELECT event_json FROM events ORDER BY event_seq DESC LIMIT 1"
        ).fetchone()[0]
        event = json.loads(raw)
        event["pre_size"] = None
        conn.execute(
            "UPDATE events SET event_json=? WHERE event_seq=?",
            (_rehash_event(event), event["event_seq"]),
        )
    with pytest.raises(SegmentStoreConflict, match="evidence"):
        store.audit()


def test_terminal_identity_tamper_fails_even_with_recomputed_hash(tmp_path):
    _root_path, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA ignore_check_constraints=ON")
        raw = conn.execute(
            "SELECT event_json FROM events ORDER BY event_seq DESC LIMIT 1"
        ).fetchone()[0]
        event = json.loads(raw)
        event["file_identity"]["inode"] += 1
        conn.execute(
            "UPDATE events SET event_json=? WHERE event_seq=?",
            (_rehash_event(event), event["event_seq"]),
        )
        conn.execute("UPDATE requests SET receipt_json=NULL")
    with pytest.raises(SegmentStoreConflict, match="identity differs"):
        store.audit()


def test_audit_rejects_any_event_after_conflict_for_same_stream(tmp_path):
    import src.research_lab.storage_segment_store as module

    root, crashing = _store(
        tmp_path,
        fault_hook=lambda phase, _path: (
            (_ for _ in ()).throw(RuntimeError("append intent crash"))
            if phase == "after_append_intent"
            else None
        ),
    )
    with pytest.raises(RuntimeError):
        crashing.append("farm.cycle", {"x": 1}, request_id=_req(1))
    with _active(root).open("ab") as handle:
        handle.write(b"not-intended")
    store = SegmentStore(root, writer_id="writer_" + "7" * 32)
    with pytest.raises(SegmentStoreConflict):
        store.recover()
    store.append("farm.error", {"ok": True}, request_id=_req(2))
    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            "SELECT event_json, intent_bytes FROM events ORDER BY event_seq"
        ).fetchall()
    conflict_index = next(
        index
        for index, row in enumerate(rows)
        if json.loads(row[0])["event_type"] == "conflict"
    )
    raw, blob = rows[conflict_index + 1]
    forged = json.loads(raw)
    forged["stream_id"] = "farm.cycle"
    forged["operation_id"] = module._operation_id(
        forged["store_id"],
        forged["request_id"],
        forged["operation_action"],
        forged["stream_id"],
        forged["segment_seq"],
    )
    forged_raw = _rehash_event(forged)
    truncated = rows[: conflict_index + 1] + [(forged_raw, blob)]
    with pytest.raises(SegmentStoreConflict, match="permanent stream conflict"):
        store._validate_event_rows(truncated, allow_pending=True)


def test_request_summary_tamper_blocks_audit(tmp_path):
    root, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE requests SET receipt_json=? WHERE request_id=?",
            ('{"schema":"forged"}', _req(1)),
        )
    with pytest.raises(SegmentStoreConflict, match="request summary"):
        store.audit()


def test_missing_request_summary_for_committed_event_blocks_audit(tmp_path):
    _root_path, store = _store(tmp_path)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DELETE FROM requests")
    with pytest.raises(SegmentStoreConflict, match="lacks its request summary"):
        store.audit()


def test_reader_reenforces_fixed_record_and_payload_limits(tmp_path, monkeypatch):
    _root_path, store = _store(tmp_path)
    store.append("farm.cycle", {"payload": "bounded"}, request_id=_req(1))
    import src.research_lab.storage_segment_store as module

    events, pending = store._events()
    monkeypatch.setattr(module, "MAX_SEGMENT_RECORDS", 0)
    with pytest.raises(SegmentStoreConflict, match="fixed protocol limits"):
        store._physical_streams(events, pending)
    monkeypatch.setattr(module, "MAX_SEGMENT_RECORDS", 4_096)
    monkeypatch.setattr(module, "MAX_PAYLOAD_BYTES", 1)
    with pytest.raises(SegmentStoreConflict, match="fixed byte limit"):
        store._physical_streams(events, pending)


def test_sqlite_schema_constraint_tamper_blocks_audit(tmp_path):
    _root_path, store = _store(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "UPDATE sqlite_master SET sql=replace(sql, ?, '') "
            "WHERE type='table' AND name='events'",
            ("CHECK(event_seq >= 1)",),
        )
        conn.commit()
    with pytest.raises(SegmentStoreConflict, match="exact schema digest"):
        store.audit()


def test_unsupported_platform_or_filesystem_fails_before_activation(tmp_path, monkeypatch):
    root = _root(tmp_path)
    import src.research_lab.storage_segment_store as module

    monkeypatch.setattr(module, "detect_durability_mode", lambda _root: (_ for _ in ()).throw(
        SegmentStoreUnsupported("unsupported filesystem")
    ))
    with pytest.raises(SegmentStoreUnsupported):
        SegmentStore(root).activate()
    assert not (root / RESERVED / "segment_store.json").exists()


def test_manifest_staging_crash_is_preserved_and_blocks_retry(tmp_path):
    root = _root(tmp_path)

    def crash(phase, _path):
        if phase == "after_manifest_fsync":
            raise RuntimeError("synthetic activation crash")

    store = SegmentStore(root, fault_hook=crash)
    with pytest.raises(RuntimeError, match="synthetic activation crash"):
        store.activate()
    artifacts = list((root / RESERVED / "staging").glob("segment-store-*"))
    assert len(artifacts) == 1
    with pytest.raises(SegmentStoreConflict, match="staging/ambiguity"):
        SegmentStore(root).activate()
    assert artifacts[0].exists()


def test_ambiguous_manifest_move_leaves_durable_block_marker(
    tmp_path, monkeypatch
):
    root = _root(tmp_path)
    store = SegmentStore(root)
    import src.research_lab.storage_segment_store as module

    def ambiguous_move(source, target, _mode):
        source.rename(target)
        raise OSError("synthetic ambiguous move result")

    monkeypatch.setattr(module, "_move_no_replace", ambiguous_move)
    with pytest.raises(OSError, match="ambiguous move"):
        store.activate()
    pending = list(
        (root / RESERVED / "staging").glob(
            "segment-store-*.activation-pending.json"
        )
    )
    assert len(pending) == 1
    with pytest.raises(SegmentStoreConflict, match="staging/ambiguity"):
        SegmentStore(root).activate()


def test_linux_no_replace_move_is_dirfd_relative(tmp_path, monkeypatch):
    import src.research_lab.storage_segment_store as module

    directory = tmp_path / "stream"
    directory.mkdir()
    source = directory / "source.open.jsonl"
    target = directory / "target.sealed.jsonl"
    source.write_bytes(b"source")
    calls: list[tuple] = []
    closes: list[int] = []

    monkeypatch.setattr(module.os, "O_DIRECTORY", 0x10000, raising=False)
    monkeypatch.setattr(module.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(module.os, "open", lambda *_args, **_kwargs: 41)
    monkeypatch.setattr(
        module.os,
        "fstat",
        lambda _fd: SimpleNamespace(st_mode=stat.S_IFDIR, st_dev=9),
    )
    monkeypatch.setattr(module.os, "close", closes.append)

    def renameat2(*args):
        calls.append(args)
        return 0

    monkeypatch.setattr(
        module.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: SimpleNamespace(renameat2=renameat2),
    )
    module._move_no_replace(source, target, DURABILITY_LINUX)
    assert calls == [(41, b"source.open.jsonl", 41, b"target.sealed.jsonl", 1)]
    assert closes == [41]


def test_linux_exact_target_recovery_repeats_directory_fsync(
    tmp_path, monkeypatch
):
    root, store = _store(tmp_path)
    import src.research_lab.storage_segment_store as module

    directory = _stream_dir(root)
    target_name = "00000000000000000001.synthetic-target.sealed.jsonl"
    target = directory / target_name
    target_bytes = b"exact-sealed-postimage"
    target.write_bytes(target_bytes)
    fd = module._open_file(target, read_only=True)
    try:
        identity = module._file_identity(fd, store.capability)
    finally:
        os.close(fd)
    intent = {
        "stream_id": "farm.cycle",
        "source_name": "00000000000000000001.synthetic-source.open.jsonl",
        "target_name": target_name,
        "file_identity": identity,
        "post_size": len(target_bytes),
        "post_sha256": hashlib.sha256(target_bytes).hexdigest(),
        "pre_size": 1,
        "pre_sha256": hashlib.sha256(b"x").hexdigest(),
        "request_id": _req(1),
    }
    synced: list[Path] = []
    terminals: list[str] = []
    store.durability_mode = DURABILITY_LINUX
    monkeypatch.setattr(
        module, "_sync_directory", lambda path, _mode: synced.append(path)
    )
    monkeypatch.setattr(
        store,
        "_publish_terminal_from_intent",
        lambda _intent, *, event_type, file_identity=None, reason_code=None: (
            terminals.append(event_type) or {"event_type": event_type}
        ),
    )
    monkeypatch.setattr(store, "_request_external_action", lambda _request: None)
    store._recover_seal_locked(intent, b"footer")
    assert synced == [directory]
    assert terminals == ["sealed"]


@pytest.mark.skipif(os.name != "nt", reason="Windows API contract branch")
def test_windows_move_uses_write_through_without_replace(tmp_path, monkeypatch):
    import src.research_lab.storage_segment_store as module

    source = tmp_path / "source.open.jsonl"
    target = tmp_path / "target.sealed.jsonl"
    source.write_bytes(b"source")
    calls: list[tuple[str, str, int]] = []

    def move(source_arg, target_arg, flags):
        calls.append((source_arg.value, target_arg.value, flags))
        return 1

    monkeypatch.setattr(
        module.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: SimpleNamespace(MoveFileExW=move),
    )
    module._move_no_replace(source, target, DURABILITY_WINDOWS)
    assert calls[0][2] == 0x00000008
    assert calls[0][0].endswith("source.open.jsonl")
    assert calls[0][1].endswith("target.sealed.jsonl")


@pytest.mark.skipif(os.name != "nt", reason="Windows API contract branch")
@pytest.mark.parametrize(
    ("drive_type", "filesystem", "message"),
    [
        (2, "NTFS", "fixed local drive"),
        (4, "NTFS", "fixed local drive"),
        (3, "ReFS", "fixed local NTFS"),
        (3, "FAT32", "fixed local NTFS"),
        (3, "exFAT", "fixed local NTFS"),
    ],
)
def test_windows_selector_rejects_nonfixed_or_non_ntfs(
    tmp_path, monkeypatch, drive_type, filesystem, message
):
    import src.research_lab.storage_segment_store as module

    class Kernel32:
        @staticmethod
        def GetDriveTypeW(_root):
            return drive_type

        @staticmethod
        def GetVolumeInformationW(
            _root, _name, _name_length, _serial, _max_component, _flags,
            filesystem_buffer, _filesystem_length,
        ):
            filesystem_buffer.value = filesystem
            return 1

    monkeypatch.setattr(
        module.ctypes, "WinDLL", lambda *_args, **_kwargs: Kernel32()
    )
    with pytest.raises(SegmentStoreUnsupported, match=message):
        module.detect_durability_mode(tmp_path)


def test_linux_selector_accepts_only_allowlisted_filesystems(tmp_path, monkeypatch):
    import src.research_lab.storage_segment_store as module

    monkeypatch.setattr(module.os, "O_NOFOLLOW", 1, raising=False)
    monkeypatch.setattr(module.os, "O_DIRECTORY", 2, raising=False)
    monkeypatch.setattr(
        module.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: SimpleNamespace(renameat2=lambda *_args: 0),
    )
    for filesystem in (0xEF53, 0x58465342, 0x9123683E):
        monkeypatch.setattr(module, "_statfs_type", lambda _root, fs=filesystem: fs)
        assert module._detect_linux_durability(tmp_path) == DURABILITY_LINUX
    for filesystem in (
        0x01021994,
        0x794C7630,
        0x65735546,
        0x6969,
        0xFF534D42,
        0xDEADBEEF,
    ):
        monkeypatch.setattr(module, "_statfs_type", lambda _root, fs=filesystem: fs)
        with pytest.raises(SegmentStoreUnsupported, match="allowlist"):
            module._detect_linux_durability(tmp_path)


def test_final_window_replacement_is_preserved_but_never_sealed(tmp_path):
    moved_original: list[Path] = []

    def replace(phase, path):
        if phase != "before_seal_rename":
            return
        backup = path.with_name(path.name + ".attacker-original")
        path.rename(backup)
        path.write_bytes(b"replacement")
        moved_original.append(backup)

    root, store = _store(tmp_path, fault_hook=replace)
    store.append("farm.cycle", {"x": 1}, request_id=_req(1))
    with pytest.raises(SegmentStoreConflict, match="file_identity_mismatch"):
        store.seal("farm.cycle", request_id=_req(2))
    assert moved_original[0].read_bytes().startswith(b"{")
    assert any(_read_path(path) == b"replacement" for path in _stream_dir(root).iterdir())
    assert not any(event["event_type"] == "sealed" for event in _events(root))
