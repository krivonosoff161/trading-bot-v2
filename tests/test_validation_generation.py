from __future__ import annotations

import json

import pytest

from src.research_lab.honest_backtest_bridge import _artifact_stem
from src.research_lab import validation_generation as generation


def _write_chain(tmp_path, candidate_id: str = "fv_test") -> dict[str, object]:
    stem = _artifact_stem(candidate_id)
    base = tmp_path / "hard_validation"
    for subdir in ("requests", "reports", "verdicts"):
        (base / subdir).mkdir(parents=True, exist_ok=True)
    cards = tmp_path / "setup_library" / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    status = "PAPER_FORWARD_READY"
    identity = {"symbol": "X", "timeframe": "1h", "strategy_id": "momentum_breakout"}
    payloads = {
        "request": {
            "candidate_id": candidate_id,
            **identity,
            "params": {"lookback": 20},
        },
        "report": {
            "candidate_id": candidate_id,
            **identity,
            "verdict": {"candidate_id": candidate_id, "hard_status": status},
        },
        "verdict": {"candidate_id": candidate_id, "hard_status": status},
        "setup_card": {
            "setup_id": f"setup-{candidate_id}",
            "candidate_id": candidate_id,
            **identity,
            "params": {"lookback": 20},
            "hard_status": status,
            "paper_forward_ready": True,
            "main_engine_ready": False,
        },
    }
    paths = {
        "request": base / "requests" / f"{stem}.json",
        "report": base / "reports" / f"{stem}.json",
        "verdict": base / "verdicts" / f"{stem}.json",
        "setup_card": cards / f"setup-{candidate_id}.json",
    }
    for kind, path in paths.items():
        path.write_text(json.dumps(payloads[kind]), encoding="utf-8")
    generation.write_current_generation(
        tmp_path,
        tasks=[{
            "task_id": 1,
            "task_type": "export_validation",
            "task_key": "test",
            "payload_json": '{"uc_key":"test"}',
        }],
        exported_ids=[candidate_id],
        completed_ids=[candidate_id],
        producer_time=2.0,
    )
    return {"candidate_id": candidate_id, "paths": paths, "payloads": payloads}


@pytest.mark.parametrize("kind", ["request", "report", "verdict", "setup_card"])
def test_any_link_tamper_revokes_the_complete_chain(tmp_path, kind):
    chain = _write_chain(tmp_path)
    candidate_id = str(chain["candidate_id"])
    assert generation.current_candidate_ids(tmp_path) == {candidate_id}

    path = chain["paths"][kind]
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert generation.current_candidate_ids(tmp_path) == set()
    assert generation.read_current_setup_card_for_candidate(tmp_path, candidate_id) is None


def test_generation_identity_and_current_code_are_reverified(tmp_path, monkeypatch):
    chain = _write_chain(tmp_path)
    candidate_id = str(chain["candidate_id"])
    manifest_path = generation.manifest_path(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generation_id"] = "hvg_tampered"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert generation.current_candidate_ids(tmp_path) == set()

    _write_chain(tmp_path, candidate_id)
    actual = generation._producer_code_manifest()
    monkeypatch.setattr(
        generation,
        "_producer_code_manifest",
        lambda: {**actual, "src/research_lab/validation_generation.py": "changed"},
    )
    assert generation.current_candidate_ids(tmp_path) == set()


def test_missing_required_code_file_fails_closed(tmp_path, monkeypatch):
    _write_chain(tmp_path)
    actual_paths = generation._producer_code_paths()
    missing = actual_paths[0].parent / "_missing_required_test.py"
    monkeypatch.setattr(
        generation,
        "_producer_code_paths",
        lambda: (*actual_paths, missing),
    )

    assert generation.current_candidate_ids(tmp_path) == set()
    with pytest.raises(FileNotFoundError, match="required validation code missing"):
        generation.write_pending_generation(
            tmp_path,
            tasks=[{
                "task_id": 2,
                "task_type": "export_validation",
                "task_key": "missing-code",
                "payload_json": "{}",
            }],
            producer_time=3.0,
        )


def test_canonical_card_wins_over_duplicate_and_pending_revokes_it(tmp_path):
    candidate_id = "fv_duplicate"
    duplicate = tmp_path / "setup_library" / "cards" / "000-stale.json"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_text(
        json.dumps({
            "candidate_id": candidate_id,
            "hard_status": "PAPER_FORWARD_READY",
            "paper_forward_ready": True,
        }),
        encoding="utf-8",
    )
    chain = _write_chain(tmp_path, candidate_id)
    canonical = chain["paths"]["setup_card"]
    manifest = generation.load_current_generation(tmp_path)
    assert manifest["active"][candidate_id]["setup_card"]["path"] == (
        f"setup_library/cards/setup-{candidate_id}.json"
    )
    assert generation.read_current_setup_card(tmp_path, duplicate) is None
    assert generation.read_current_setup_card(tmp_path, canonical) is not None

    generation.write_pending_generation(
        tmp_path,
        tasks=[{
            "task_id": 2,
            "task_type": "export_validation",
            "task_key": "next",
            "payload_json": "{}",
        }],
        producer_time=3.0,
    )
    assert generation.current_candidate_ids(tmp_path) == set()
    assert generation.read_current_setup_card(tmp_path, canonical) is None


def test_generation_snapshot_hashes_producer_once_and_reports_real_progress(
    tmp_path,
    monkeypatch,
):
    chain = _write_chain(tmp_path)
    candidate_id = str(chain["candidate_id"])
    original = generation._producer_code_manifest
    calls = 0
    progress: list[tuple[str, int, int]] = []

    def counted_manifest():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(generation, "_producer_code_manifest", counted_manifest)
    snapshot = generation.load_current_generation_snapshot(
        tmp_path,
        progress=lambda stage, completed, total: progress.append(
            (stage, completed, total)
        ),
    )

    assert snapshot.status == "ready"
    assert set(snapshot.payloads) == {candidate_id}
    assert calls == 1
    assert progress == [
        ("generation_manifest_verified", 1, 1),
        ("generation_candidate_verified", 1, 1),
    ]


def test_current_generation_card_load_does_not_scan_historical_catalog(
    tmp_path,
    monkeypatch,
):
    from types import SimpleNamespace

    from src.research_lab import paper_runtime

    chain = _write_chain(tmp_path)
    candidate_id = str(chain["candidate_id"])

    def forbidden_glob(*_args, **_kwargs):
        raise AssertionError("current generation must not scan historical cards")

    monkeypatch.setattr(generation.Path, "glob", forbidden_glob)
    monkeypatch.setattr(
        paper_runtime.SetupCard,
        "from_dict",
        staticmethod(
            lambda payload: SimpleNamespace(
                candidate_id=payload["candidate_id"],
                paper_forward_ready=True,
            )
        ),
    )
    cards = paper_runtime.load_ready_setup_cards(tmp_path)

    assert [card.candidate_id for card in cards] == [candidate_id]


def test_generation_snapshot_is_atomic_when_one_active_chain_is_corrupt(tmp_path):
    _write_chain(tmp_path, "fv_one")
    _write_chain(tmp_path, "fv_two")
    generation.write_current_generation(
        tmp_path,
        tasks=[{
            "task_id": 1,
            "task_type": "export_validation",
            "task_key": "batch",
            "payload_json": "{}",
        }],
        exported_ids=["fv_one", "fv_two"],
        completed_ids=["fv_one", "fv_two"],
        producer_time=3.0,
    )
    bad = tmp_path / "setup_library" / "cards" / "setup-fv_two.json"
    bad.write_text(bad.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    snapshot = generation.load_current_generation_snapshot(tmp_path)

    assert snapshot.status == "invalid_candidate_chain"
    assert snapshot.payloads == {}
    assert snapshot.invalid_candidates == ("fv_two",)


def test_generation_snapshot_cancellation_is_fail_closed(tmp_path):
    _write_chain(tmp_path)
    checks = 0

    def check_active():
        nonlocal checks
        checks += 1
        if checks >= 3:
            raise RuntimeError("lost fence")

    with pytest.raises(RuntimeError, match="lost fence"):
        generation.load_current_generation_snapshot(
            tmp_path,
            check_active=check_active,
        )


def test_generation_snapshot_distinguishes_pending_and_code_stale(
    tmp_path,
    monkeypatch,
):
    generation.write_pending_generation(
        tmp_path,
        tasks=[{
            "task_id": 3,
            "task_type": "export_validation",
            "task_key": "pending",
            "payload_json": "{}",
        }],
        producer_time=4.0,
    )
    assert generation.load_current_generation_snapshot(tmp_path).status == "pending"

    _write_chain(tmp_path)
    actual = generation._producer_code_manifest()
    monkeypatch.setattr(
        generation,
        "_producer_code_manifest",
        lambda: {**actual, "src/research_lab/paper_runtime.py": "new-code"},
    )
    assert generation.load_current_generation_snapshot(tmp_path).status == "code_stale"
