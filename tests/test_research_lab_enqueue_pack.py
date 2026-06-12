# -*- coding: utf-8 -*-

import json
from pathlib import Path

from scripts.strategy_lab.enqueue_pack import discover_specs, enqueue_pack
from src.research_lab.state_db import connect, default_db_path, init_db


def _write_spec(path: Path, experiment_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "experiment_id": experiment_id,
                "data_glob": "data/{symbol}.json",
                "symbols": ["ABC_USDT_SWAP"],
                "families": ["momentum_breakout"],
                "parameter_grid": {"momentum_breakout": [{"lookback": 3, "hold_bars": 2}]},
            }
        ),
        encoding="utf-8",
    )


def test_discover_specs_sorts_json_files(tmp_path):
    _write_spec(tmp_path / "b.json", "b")
    _write_spec(tmp_path / "a.json", "a")
    (tmp_path / "README.md").write_text("ignored", encoding="utf-8")

    specs = discover_specs(tmp_path)

    assert [p.name for p in specs] == ["a.json", "b.json"]


def test_enqueue_pack_validates_and_deduplicates_pending_jobs(tmp_path):
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_spec(pack / "a.json", "a")
    _write_spec(pack / "b.json", "b")
    private = tmp_path / "private"

    first = enqueue_pack(discover_specs(pack), private_root=private)
    second = enqueue_pack(discover_specs(pack), private_root=private)

    assert first["queued"] == 2
    assert first["already_pending"] == 0
    assert second["queued"] == 0
    assert second["already_pending"] == 2

    conn = connect(default_db_path(private))
    init_db(conn)
    rows = conn.execute("SELECT spec_path, status FROM queue ORDER BY job_id").fetchall()
    conn.close()
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"queued"}


def test_enqueue_pack_rejects_invalid_spec(tmp_path):
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "bad.json").write_text("{}", encoding="utf-8")

    try:
        enqueue_pack(discover_specs(pack), private_root=tmp_path / "private")
    except KeyError as exc:
        assert str(exc).strip("'") == "experiment_id"
    else:
        raise AssertionError("invalid spec should fail before queueing")

