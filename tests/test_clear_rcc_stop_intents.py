from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.strategy_lab.clear_rcc_stop_intents import (
    RCC_STOP_MARKERS,
    StopIntentClearError,
    clear_expected_rcc_stop_intents,
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
