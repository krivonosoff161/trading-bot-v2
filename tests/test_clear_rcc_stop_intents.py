from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from scripts.strategy_lab.clear_rcc_stop_intents import (
    LEGACY_MIGRATABLE_MARKERS,
    RCC_STOP_MARKERS,
    StopIntentClearError,
    clear_expected_rcc_stop_intents,
    main,
    migrate_exact_legacy_stop_intents,
)


def _write_generation(root: Path) -> dict[str, str]:
    state = root / "state"
    state.mkdir(parents=True)
    expected: dict[str, str] = {}
    for offset, name in enumerate(RCC_STOP_MARKERS, start=1):
        payload = f"control center stop requested at 1000.{offset}\n".encode()
        (state / name).write_bytes(payload)
        expected[name] = hashlib.sha256(payload).hexdigest()
    return expected


def _write_mixed_generation(root: Path) -> dict[str, str]:
    expected = _write_generation(root)
    farm = root / "state" / "STOP_FARM_FULL_CYCLE.txt"
    farm.write_bytes(b"stop requested at Thu 07/31/2026 18:22:03.41 \r\n")
    expected[farm.name] = hashlib.sha256(farm.read_bytes()).hexdigest()
    return expected


def test_dry_run_validates_without_clearing(tmp_path: Path) -> None:
    expected = _write_generation(tmp_path)

    result = clear_expected_rcc_stop_intents(
        tmp_path, expected_sha256=expected
    )

    assert result["eligible_count"] == 3
    assert result["cleared_count"] == 0
    assert result["remaining_count"] == 3
    assert all((tmp_path / "state" / name).exists() for name in RCC_STOP_MARKERS)


def test_apply_clears_exact_generation_and_repeat_changes_zero(
    tmp_path: Path,
) -> None:
    expected = _write_generation(tmp_path)

    first = clear_expected_rcc_stop_intents(
        tmp_path, expected_sha256=expected, apply=True
    )
    repeated = clear_expected_rcc_stop_intents(
        tmp_path, expected_sha256=expected, apply=True
    )

    assert first["eligible_count"] == first["cleared_count"] == 3
    assert first["remaining_count"] == 0
    assert first["idempotent"] is True
    assert repeated["eligible_count"] == repeated["cleared_count"] == 0
    assert repeated["remaining_count"] == 0
    assert repeated["idempotent"] is True


def test_mixed_documented_generation_is_name_and_hash_bound(
    tmp_path: Path,
) -> None:
    expected = _write_mixed_generation(tmp_path)

    dry_run = clear_expected_rcc_stop_intents(
        tmp_path, expected_sha256=expected
    )
    first = clear_expected_rcc_stop_intents(
        tmp_path, expected_sha256=expected, apply=True
    )
    repeated = clear_expected_rcc_stop_intents(
        tmp_path, expected_sha256=expected, apply=True
    )

    assert dry_run["eligible_count"] == 3
    assert dry_run["cleared_count"] == 0
    assert first["eligible_count"] == first["cleared_count"] == 3
    assert first["remaining_count"] == 0
    assert repeated["eligible_count"] == repeated["cleared_count"] == 0


@pytest.mark.parametrize(
    "name",
    ["STOP_NEWS_SCANNER.txt", "STOP_PUBLIC_NEWS.txt"],
)
def test_documented_farm_payload_is_rejected_for_other_markers(
    tmp_path: Path,
    name: str,
) -> None:
    expected = _write_generation(tmp_path)
    target = tmp_path / "state" / name
    target.write_bytes(b"stop requested at Thu 07/31/2026 18:22:03.41 \r\n")
    expected[name] = hashlib.sha256(target.read_bytes()).hexdigest()

    with pytest.raises(
        StopIntentClearError, match="stop_marker_provenance_mismatch"
    ):
        clear_expected_rcc_stop_intents(
            tmp_path, expected_sha256=expected, apply=True
        )

    assert all((tmp_path / "state" / marker).exists() for marker in RCC_STOP_MARKERS)


@pytest.mark.parametrize(
    "payload",
    [
        b"stop requested at synthetic authority\r\n",
        b"stop requested at Thu 07/31/2026 18:22:03.41\r\nsecond line\r\n",
    ],
)
def test_foreign_farm_payload_is_rejected_without_partial_clear(
    tmp_path: Path,
    payload: bytes,
) -> None:
    expected = _write_generation(tmp_path)
    target = tmp_path / "state" / "STOP_FARM_FULL_CYCLE.txt"
    target.write_bytes(payload)
    expected[target.name] = hashlib.sha256(payload).hexdigest()

    with pytest.raises(
        StopIntentClearError, match="stop_marker_provenance_mismatch"
    ) as raised:
        clear_expected_rcc_stop_intents(
            tmp_path, expected_sha256=expected, apply=True
        )

    assert "synthetic authority" not in str(raised.value)
    assert all((tmp_path / "state" / marker).exists() for marker in RCC_STOP_MARKERS)


def test_hash_mismatch_fails_before_any_marker_is_cleared(tmp_path: Path) -> None:
    expected = _write_generation(tmp_path)
    expected[RCC_STOP_MARKERS[-1]] = "0" * 64

    with pytest.raises(StopIntentClearError, match="stop_marker_hash_mismatch"):
        clear_expected_rcc_stop_intents(
            tmp_path, expected_sha256=expected, apply=True
        )

    assert all((tmp_path / "state" / name).exists() for name in RCC_STOP_MARKERS)


def test_non_rcc_payload_fails_without_echoing_payload(tmp_path: Path) -> None:
    expected = _write_generation(tmp_path)
    target = tmp_path / "state" / RCC_STOP_MARKERS[0]
    target.write_text("synthetic foreign stop authority\n", encoding="utf-8")
    expected[RCC_STOP_MARKERS[0]] = hashlib.sha256(target.read_bytes()).hexdigest()

    with pytest.raises(
        StopIntentClearError, match="stop_marker_provenance_mismatch"
    ) as raised:
        clear_expected_rcc_stop_intents(
            tmp_path, expected_sha256=expected, apply=True
        )

    assert "synthetic foreign" not in str(raised.value)
    assert all((tmp_path / "state" / name).exists() for name in RCC_STOP_MARKERS)


def test_unrelated_stop_or_state_file_is_never_touched(tmp_path: Path) -> None:
    expected = _write_generation(tmp_path)
    unrelated = tmp_path / "state" / "strategy_lab_stop_requested.json"
    unrelated.write_text('{"schema":"synthetic"}', encoding="utf-8")

    result = clear_expected_rcc_stop_intents(
        tmp_path, expected_sha256=expected, apply=True
    )

    assert result["cleared_count"] == 3
    assert unrelated.read_text(encoding="utf-8") == '{"schema":"synthetic"}'


def _write_legacy_pair(root: Path) -> dict[str, tuple[str, int]]:
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    expected: dict[str, tuple[str, int]] = {}
    for offset, name in enumerate(LEGACY_MIGRATABLE_MARKERS, start=1):
        payload = bytes((0x80 + offset, 0x20, 0x7F, 0x00 + offset)) * 9
        payload = payload[: 34 + offset]
        (state / name).write_bytes(payload)
        expected[name] = (hashlib.sha256(payload).hexdigest(), len(payload))
    return expected


def test_legacy_migration_dry_run_archives_before_exact_clear(
    tmp_path: Path,
) -> None:
    expected = _write_legacy_pair(tmp_path)
    archive = tmp_path / "private-evidence"

    dry = migrate_exact_legacy_stop_intents(
        tmp_path,
        expected_legacy=expected,
        archive_root=archive,
        authority_id="owner-stop-marker-20260822",
    )
    assert dry["eligible_count"] == 2
    assert dry["archived_count"] == 0
    assert dry["cleared_count"] == 0
    assert all((tmp_path / "state" / name).exists() for name in expected)

    first = migrate_exact_legacy_stop_intents(
        tmp_path,
        expected_legacy=expected,
        archive_root=archive,
        authority_id="owner-stop-marker-20260822",
        apply=True,
    )
    assert first["archived_count"] == first["cleared_count"] == 2
    assert first["remaining_count"] == 0
    evidence = archive / "trading-bot-v2" / "stop-marker-provenance-v1" / "owner-stop-marker-20260822"
    assert (evidence / "migration_manifest.json").is_file()
    for name, (digest, size) in expected.items():
        target = evidence / f"{name}.bin"
        assert target.stat().st_size == size
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest

    repeated = migrate_exact_legacy_stop_intents(
        tmp_path,
        expected_legacy=expected,
        archive_root=archive,
        authority_id="owner-stop-marker-20260822",
        apply=True,
    )
    assert repeated["archived_count"] == repeated["cleared_count"] == 0
    assert repeated["idempotent"] is True


def test_legacy_migration_hash_drift_fails_before_archive_or_clear(
    tmp_path: Path,
) -> None:
    expected = _write_legacy_pair(tmp_path)
    name = LEGACY_MIGRATABLE_MARKERS[0]
    expected[name] = ("0" * 64, expected[name][1])

    with pytest.raises(StopIntentClearError, match="legacy_stop_marker_hash_mismatch"):
        migrate_exact_legacy_stop_intents(
            tmp_path,
            expected_legacy=expected,
            archive_root=tmp_path / "private-evidence",
            authority_id="owner-stop-marker-20260822",
            apply=True,
        )
    assert all((tmp_path / "state" / marker).exists() for marker in LEGACY_MIGRATABLE_MARKERS)
    assert not (tmp_path / "private-evidence").exists()


def test_legacy_migration_requires_full_exact_pair_and_absolute_archive_root(
    tmp_path: Path,
) -> None:
    expected = _write_legacy_pair(tmp_path)
    incomplete = {LEGACY_MIGRATABLE_MARKERS[0]: expected[LEGACY_MIGRATABLE_MARKERS[0]]}
    with pytest.raises(StopIntentClearError, match="invalid_legacy_stop_marker_binding"):
        migrate_exact_legacy_stop_intents(
            tmp_path,
            expected_legacy=incomplete,
            archive_root=tmp_path / "private-evidence",
            authority_id="owner-stop-marker-20260822",
            apply=True,
        )
    with pytest.raises(StopIntentClearError, match="legacy_archive_root_not_absolute"):
        migrate_exact_legacy_stop_intents(
            tmp_path,
            expected_legacy=expected,
            archive_root=Path("relative-evidence"),
            authority_id="owner-stop-marker-20260822",
            apply=True,
        )
    assert all((tmp_path / "state" / marker).exists() for marker in LEGACY_MIGRATABLE_MARKERS)


def test_legacy_migration_partial_archive_failure_keeps_all_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _write_legacy_pair(tmp_path)
    import scripts.strategy_lab.clear_rcc_stop_intents as module

    original = module._write_once_exact
    calls = 0

    def fail_second(path: Path, payload: bytes, *, digest: str, size: int) -> bool:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise StopIntentClearError("synthetic_archive_interruption")
        return original(path, payload, digest=digest, size=size)

    monkeypatch.setattr(module, "_write_once_exact", fail_second)
    with pytest.raises(StopIntentClearError, match="synthetic_archive_interruption"):
        migrate_exact_legacy_stop_intents(
            tmp_path,
            expected_legacy=expected,
            archive_root=tmp_path / "private-evidence",
            authority_id="owner-stop-marker-20260822",
            apply=True,
        )
    assert all((tmp_path / "state" / marker).exists() for marker in LEGACY_MIGRATABLE_MARKERS)

    # A later replay may reuse only the already verified first archive object;
    # it must finish the interrupted operation without duplicating evidence.
    monkeypatch.setattr(module, "_write_once_exact", original)
    resumed = migrate_exact_legacy_stop_intents(
        tmp_path,
        expected_legacy=expected,
        archive_root=tmp_path / "private-evidence",
        authority_id="owner-stop-marker-20260822",
        apply=True,
    )
    assert resumed["archived_count"] == 1
    assert resumed["cleared_count"] == 2
    assert resumed["remaining_count"] == 0


def test_legacy_migration_rejects_unarchived_missing_source(tmp_path: Path) -> None:
    expected = _write_legacy_pair(tmp_path)
    (tmp_path / "state" / LEGACY_MIGRATABLE_MARKERS[0]).unlink()
    with pytest.raises(StopIntentClearError, match="legacy_stop_marker_missing_unarchived"):
        migrate_exact_legacy_stop_intents(
            tmp_path,
            expected_legacy=expected,
            archive_root=tmp_path / "private-evidence",
            authority_id="owner-stop-marker-20260822",
            apply=True,
        )


def test_legacy_migration_rejects_state_reparse_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _write_legacy_pair(tmp_path)
    import scripts.strategy_lab.clear_rcc_stop_intents as module

    def reject_child(*_args: object, **_kwargs: object) -> Path:
        raise ValueError("synthetic reparse escape")

    monkeypatch.setattr(module, "resolve_private_child", reject_child)
    with pytest.raises(ValueError, match="synthetic reparse escape"):
        migrate_exact_legacy_stop_intents(
            tmp_path,
            expected_legacy=expected,
            archive_root=tmp_path / "private-evidence",
            authority_id="owner-stop-marker-20260822",
            apply=True,
        )
    assert all((tmp_path / "state" / marker).exists() for marker in LEGACY_MIGRATABLE_MARKERS)


def test_cli_keeps_normal_clear_contract_and_requires_full_legacy_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = _write_generation(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "clear_rcc_stop_intents.py",
            "--private-root",
            str(tmp_path),
            "--expect",
            f"STOP_FARM_FULL_CYCLE.txt={expected['STOP_FARM_FULL_CYCLE.txt']}",
            "--expect",
            f"STOP_NEWS_SCANNER.txt={expected['STOP_NEWS_SCANNER.txt']}",
            "--expect",
            f"STOP_PUBLIC_NEWS.txt={expected['STOP_PUBLIC_NEWS.txt']}",
            "--json",
        ],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "CanonicalRccStopIntentClear.v1"

    legacy = _write_legacy_pair(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "clear_rcc_stop_intents.py",
            "--private-root",
            str(tmp_path),
            "--legacy-marker",
            f"STOP_NEWS_SCANNER.txt={legacy['STOP_NEWS_SCANNER.txt'][0]}:{legacy['STOP_NEWS_SCANNER.txt'][1]}",
            "--legacy-marker",
            f"STOP_PUBLIC_NEWS.txt={legacy['STOP_PUBLIC_NEWS.txt'][0]}:{legacy['STOP_PUBLIC_NEWS.txt'][1]}",
            "--archive-root",
            str(tmp_path / "private-evidence"),
            "--authority-id",
            "owner-stop-marker-20260822",
            "--json",
        ],
    )
    assert main() == 0
    assert (
        json.loads(capsys.readouterr().out)["schema"]
        == "CanonicalRccLegacyStopMarkerMigration.v1"
    )
