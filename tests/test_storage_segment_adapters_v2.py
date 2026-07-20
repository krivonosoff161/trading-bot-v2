from __future__ import annotations

from pathlib import Path

from src.research_lab.storage_capability import RESERVED, activate_synthetic_root
from src.research_lab.storage_segment_adapters import (
    append_farm_cycle,
    append_farm_error,
    append_farm_task_transition,
    append_scout_card,
    append_scout_drop,
    append_scout_event_audit,
    append_scout_ingest,
    append_scout_llm_budget,
    append_scout_routing_audit,
    read_farm_cycles,
    read_scout_cards,
    read_stream_records,
)
from src.research_lab.storage_segment_store import SegmentStore


def test_explicit_adapters_round_trip_without_resolving_legacy_paths(tmp_path: Path):
    root = tmp_path / "managed" / "root"
    root.mkdir(parents=True)
    activate_synthetic_root(root)
    store = SegmentStore(root)
    store.activate()

    append_farm_cycle(store, {"mode": "apply"}, request_id="req_" + "1" * 32)
    append_scout_card(store, {"card_id": "abc"}, request_id="req_" + "2" * 32)

    assert read_farm_cycles(store) == [{"mode": "apply"}]
    assert read_scout_cards(store) == [{"card_id": "abc"}]
    assert not (root / "logs").exists()
    assert (root / RESERVED / "segments").is_dir()


def test_all_nine_explicit_adapters_bind_the_fixed_registry(tmp_path: Path):
    root = tmp_path / "managed" / "root"
    root.mkdir(parents=True)
    activate_synthetic_root(root)
    store = SegmentStore(root)
    store.activate()
    adapters = [
        ("farm.cycle", append_farm_cycle),
        ("farm.task_transition", append_farm_task_transition),
        ("farm.error", append_farm_error),
        ("scout.card", append_scout_card),
        ("scout.drop", append_scout_drop),
        ("scout.llm_budget", append_scout_llm_budget),
        ("scout.ingest", append_scout_ingest),
        ("scout.event_audit", append_scout_event_audit),
        ("scout.routing_audit", append_scout_routing_audit),
    ]
    for index, (stream_id, adapter) in enumerate(adapters, 1):
        record = {"adapter_index": index, "stream": stream_id}
        adapter(store, record, request_id="req_" + f"{index:032x}")
        assert read_stream_records(store, stream_id) == [record]
    assert not (root / "logs").exists()
