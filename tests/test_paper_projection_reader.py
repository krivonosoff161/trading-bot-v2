import json

from src.research_lab.paper_projection_reader import read_projection_view


def test_legacy_file_is_display_only_before_v2_activation(tmp_path):
    legacy = tmp_path / "trades.json"
    legacy.write_text(json.dumps({"items": [{"runtime_id": "legacy"}]}), encoding="utf-8")

    view = read_projection_view(tmp_path, "trades", legacy_snapshot=legacy)

    assert view["current"] is False
    assert view["generation_status"] == "legacy_unversioned_projection"
    assert view["items"] == [{"runtime_id": "legacy"}]


def test_existing_invalid_authority_database_blocks_legacy_fallback(tmp_path):
    legacy = tmp_path / "trades.json"
    legacy.write_text(json.dumps({"items": [{"runtime_id": "legacy"}]}), encoding="utf-8")
    database = tmp_path / "paper-evidence.sqlite3"
    database.write_bytes(b"not-a-sqlite-database")

    view = read_projection_view(
        tmp_path,
        "trades",
        legacy_snapshot=legacy,
        evidence_database_path=database,
    )

    assert view["current"] is False
    assert view["authority_database_exists"] is True
    assert view["items"] == []
