from __future__ import annotations

import pytest

from src.research_lab import post_stop_integrity_targets


def test_post_stop_targets_use_canonical_manifest_and_root(monkeypatch, tmp_path) -> None:
    (tmp_path / "market_data").mkdir()
    (tmp_path / "state" / "derived").mkdir(parents=True)
    monkeypatch.setattr(
        post_stop_integrity_targets,
        "load_cutover_manifest",
        lambda *_a, **_k: {
            "authority_database_relative_path": "state/derived/paper_evidence.sqlite3"
        },
    )

    targets = post_stop_integrity_targets.resolve_post_stop_integrity_targets(tmp_path)

    assert targets == {
        "candles": tmp_path / "market_data" / "candles.sqlite3",
        "paper_evidence_v2": tmp_path / "state" / "derived" / "paper_evidence.sqlite3",
    }


def test_post_stop_targets_reject_manifest_path_drift(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        post_stop_integrity_targets,
        "load_cutover_manifest",
        lambda *_a, **_k: {"authority_database_relative_path": "state/paper_evidence.sqlite3"},
    )

    with pytest.raises(
        post_stop_integrity_targets.PostStopIntegrityTargetError,
        match="not canonical",
    ):
        post_stop_integrity_targets.resolve_post_stop_integrity_targets(tmp_path)
