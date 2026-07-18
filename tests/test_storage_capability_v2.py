import json
import math
import shutil
from pathlib import Path
import pytest

from src.research_lab.storage_capability import (
    RESERVED,
    StorageCapabilityError,
    activate_synthetic_root,
    fixed_temp_anchor,
    load_capability,
    parse_positive_budget,
    safe_relative_file,
)


def _managed(tmp_path, name="managed"):
    root = tmp_path / name / "root"
    root.mkdir(parents=True)
    return root


def test_capability_round_trip_binds_fixed_policy_and_root(tmp_path):
    root = _managed(tmp_path)
    created = activate_synthetic_root(root)
    loaded = load_capability(root)

    assert loaded == created
    assert loaded.policy_id == "synthetic_temporary_storage.v2"
    assert loaded.allowed_subtree == "cache"
    assert loaded.allowed_extensions == (".json", ".jsonl", ".bin", ".parquet")
    assert (root / RESERVED / "capability.json").is_file()
    assert (root / RESERVED / "marker.json").is_file()


def test_copied_internally_consistent_manifest_and_marker_fail_at_other_root(tmp_path):
    first = _managed(tmp_path, "first")
    second = _managed(tmp_path, "second")
    activate_synthetic_root(first)
    shutil.copytree(first / RESERVED, second / RESERVED)

    with pytest.raises(StorageCapabilityError, match="fixed synthetic policy|marker mismatch"):
        load_capability(second)


def test_tampered_manifest_and_marker_fail_even_when_both_are_changed(tmp_path):
    root = _managed(tmp_path)
    activate_synthetic_root(root)
    manifest_path = root / RESERVED / "capability.json"
    marker_path = root / RESERVED / "marker.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    manifest["allowed_subtree"] = "anything"
    marker["canonical_root"] = str(tmp_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(StorageCapabilityError):
        load_capability(root)


@pytest.mark.parametrize(
    "value",
    [0, -1, True, "1", math.nan, math.inf, -math.inf, 1024 * 1024 + 1],
)
def test_budget_must_be_finite_positive_bounded_number(value):
    with pytest.raises(StorageCapabilityError):
        parse_positive_budget(value)


def test_reserved_subtree_and_disallowed_extension_are_not_candidates(tmp_path):
    root = _managed(tmp_path)
    capability = activate_synthetic_root(root)
    reserved = root / RESERVED / "marker.json"
    cache = root / "cache"
    cache.mkdir()
    disallowed = cache / "script.py"
    disallowed.write_text("pass", encoding="utf-8")

    with pytest.raises(StorageCapabilityError):
        safe_relative_file(capability, reserved)
    with pytest.raises(StorageCapabilityError, match="extension"):
        safe_relative_file(capability, disallowed)


def test_activation_requires_empty_dedicated_root(tmp_path):
    root = _managed(tmp_path)
    (root / "existing.json").write_text("{}", encoding="utf-8")
    with pytest.raises(StorageCapabilityError, match="empty"):
        activate_synthetic_root(root)


def test_activation_rejects_repository_root_outside_fixed_temp_policy():
    repository = Path(__file__).resolve().parents[1]

    with pytest.raises(StorageCapabilityError, match="outside the fixed temp policy"):
        activate_synthetic_root(repository)


def test_process_temp_environment_cannot_forge_fixed_anchor(monkeypatch, tmp_path):
    expected = fixed_temp_anchor()
    forged = str(tmp_path / "forged")
    for name in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(name, forged)

    assert fixed_temp_anchor() == expected
