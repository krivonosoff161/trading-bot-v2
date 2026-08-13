from __future__ import annotations

import json
import hashlib
import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from src.research_lab import paper_telegram_preview
from src.research_lab import runtime_storage_rotation
from src.research_lab.paper_signals.training_export import _card_refs_from_snapshot
from src.research_lab.paper_telegram_preview import (
    PaperTelegramPreview,
    _write_card_ledger,
)
from src.research_lab.runtime_storage_rotation import (
    RuntimeStorageError,
    write_bounded_rebuildable_snapshot,
)


def _process_snapshot_writer(path: str, barrier, value: str) -> None:
    barrier.wait(timeout=10)
    write_bounded_rebuildable_snapshot(
        Path(path),
        {"schema": "SyntheticDerived.v1", "value": value},
        max_bytes=4096,
    )


def _preview(card_id: str, signal_id: str, *, text: str = "paper") -> PaperTelegramPreview:
    return PaperTelegramPreview(
        telegram_card_id=card_id,
        preview_id=f"preview-{card_id}",
        instruction_id="",
        source_signal_id=signal_id,
        pair="BTC-USDT-SWAP",
        timeframe="1h",
        side="long",
        setup_family="continuation",
        consumer_status="accepted_for_paper_watch",
        validation_tier="validated_pfr",
        text=text,
    )


def test_identical_rebuildable_snapshot_is_not_rewritten(tmp_path) -> None:
    path = tmp_path / "state" / "derived" / "snapshot.json"
    payload = {"schema": "SyntheticDerived.v1", "records": [{"id": 1}]}

    first = write_bounded_rebuildable_snapshot(path, payload, max_bytes=4096)
    first_mtime = path.stat().st_mtime_ns
    second = write_bounded_rebuildable_snapshot(path, payload, max_bytes=4096)

    assert first["changed"] is True
    assert second == {**first, "changed": False}
    assert path.stat().st_mtime_ns == first_mtime
    assert json.loads(path.read_text(encoding="utf-8")) == payload


def test_rebuildable_snapshot_fails_closed_above_budget(tmp_path) -> None:
    path = tmp_path / "snapshot.json"

    with pytest.raises(RuntimeStorageError, match="bounded budget"):
        write_bounded_rebuildable_snapshot(
            path,
            {"schema": "SyntheticDerived.v1", "payload": "x" * 512},
            max_bytes=64,
        )

    assert not path.exists()
    assert not path.with_suffix(".json.sha256").exists()


def test_same_size_external_snapshot_change_is_repaired(tmp_path) -> None:
    path = tmp_path / "snapshot.json"
    payload = {"schema": "SyntheticDerived.v1", "value": "aaaa"}
    write_bounded_rebuildable_snapshot(path, payload, max_bytes=4096)
    original = path.read_bytes()
    path.write_bytes(original.replace(b"aaaa", b"bbbb"))

    repaired = write_bounded_rebuildable_snapshot(path, payload, max_bytes=4096)

    assert repaired["changed"] is True
    assert path.read_bytes() == original


def test_snapshot_lock_is_not_visible_until_initialization_is_durable(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "snapshot.json"
    lock_path = path.with_suffix(".json.lock")
    write_started = threading.Event()
    release_write = threading.Event()
    real_write = runtime_storage_rotation.os.write

    def delayed_write(descriptor: int, payload: bytes) -> int:
        if payload == b"0":
            write_started.set()
            assert release_write.wait(timeout=5)
        return real_write(descriptor, payload)

    monkeypatch.setattr(runtime_storage_rotation.os, "write", delayed_write)
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            write_bounded_rebuildable_snapshot(
                path, {"schema": "SyntheticDerived.v1"}, max_bytes=4096
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    assert write_started.wait(timeout=5)
    time.sleep(0.05)
    assert not lock_path.exists()
    release_write.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert errors == []
    assert lock_path.read_bytes() == b"0"
    assert list(tmp_path.glob(".*.init")) == []


def test_corrupt_sidecar_is_repaired_from_rehashed_target_without_payload_rewrite(
    tmp_path,
) -> None:
    path = tmp_path / "snapshot.json"
    payload = {"schema": "SyntheticDerived.v1", "value": "stable"}
    write_bounded_rebuildable_snapshot(path, payload, max_bytes=4096)
    target_mtime = path.stat().st_mtime_ns
    path.with_suffix(".json.sha256").write_text("0 0 0\n", encoding="ascii")

    repaired = write_bounded_rebuildable_snapshot(path, payload, max_bytes=4096)

    digest, size, mtime = path.with_suffix(".json.sha256").read_text(
        encoding="ascii"
    ).split()
    assert repaired["changed"] is True
    assert path.stat().st_mtime_ns == target_mtime
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert int(size) == len(path.read_bytes())
    assert int(mtime) == target_mtime


def test_cross_process_snapshot_writers_leave_one_digest_bound_payload(tmp_path) -> None:
    path = tmp_path / "snapshot.json"
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    processes = [
        context.Process(
            target=_process_snapshot_writer,
            args=(str(path), barrier, value),
        )
        for value in ("alpha", "bravo")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)

    assert [process.exitcode for process in processes] == [0, 0]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["value"] in {"alpha", "bravo"}
    digest, size, mtime = path.with_suffix(".json.sha256").read_text(
        encoding="ascii"
    ).split()
    target_bytes = path.read_bytes()
    assert digest == hashlib.sha256(target_bytes).hexdigest()
    assert int(size) == len(target_bytes)
    assert int(mtime) == path.stat().st_mtime_ns
    assert list(tmp_path.glob(".*.tmp")) == []


def test_empty_card_refresh_does_not_rewrite_large_ledger(tmp_path) -> None:
    first = _write_card_ledger(tmp_path, [])
    path = tmp_path / "state" / "derived" / "paper_telegram_card_ledger.json"
    first_mtime = path.stat().st_mtime_ns
    second = _write_card_ledger(tmp_path, [])

    assert first["snapshot_write"]["changed"] is True
    assert second["snapshot_write"]["changed"] is False
    assert path.stat().st_mtime_ns == first_mtime
    assert second["cards"] == 0


def test_card_ledger_concurrent_read_modify_write_loses_no_card(tmp_path) -> None:
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def writer(preview: PaperTelegramPreview) -> None:
        try:
            barrier.wait(timeout=5)
            _write_card_ledger(tmp_path, [preview])
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(_preview("card-a", "signal-a"),)),
        threading.Thread(target=writer, args=(_preview("card-b", "signal-b"),)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    path = tmp_path / "state" / "derived" / "paper_telegram_card_ledger.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert {item["telegram_card_id"] for item in payload["items"]} == {
        "card-a",
        "card-b",
    }
    digest = path.with_suffix(".json.sha256").read_text(encoding="ascii").split()[0]
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert list(path.parent.glob(".*.tmp")) == []


def test_card_ledger_prunes_by_bytes_but_keeps_current_and_newest_per_signal(
    tmp_path, monkeypatch
) -> None:
    clock = iter(float(value) for value in range(1, 20))
    monkeypatch.setattr(paper_telegram_preview.time, "time", lambda: next(clock))
    monkeypatch.setattr(paper_telegram_preview, "MAX_CARD_LEDGER_BYTES", 7000)
    for index in range(8):
        _write_card_ledger(
            tmp_path,
            [_preview(f"card-a-{index}", "signal-a", text="x" * 300)],
        )
    _write_card_ledger(
        tmp_path,
        [_preview("card-b-current", "signal-b", text="y" * 300)],
    )

    path = tmp_path / "state" / "derived" / "paper_telegram_card_ledger.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    retained = {item["telegram_card_id"] for item in payload["items"]}
    assert "card-a-7" in retained
    assert "card-b-current" in retained
    assert payload["pruned"] > 0
    assert len(path.read_bytes()) <= 7000


def test_training_card_lookup_selects_newest_last_seen(tmp_path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "source_signal_id": "signal-a",
                        "telegram_card_id": "card-new",
                        "last_seen_at": 20.0,
                    },
                    {
                        "source_signal_id": "signal-a",
                        "telegram_card_id": "card-old",
                        "last_seen_at": 10.0,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    assert _card_refs_from_snapshot(path)["signal-a"]["telegram_card_id"] == "card-new"
