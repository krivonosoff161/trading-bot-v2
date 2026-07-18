# -*- coding: utf-8 -*-
"""Tests for the hot/cold storage policy (LRU hot-cache budget + log rotation)."""
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import storage_policy as SP  # noqa: E402


def _write(path: Path, size_bytes: int, mtime: float | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size_bytes)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_storage_policy_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEGY_LAB_HOT_ROOT", str(tmp_path / "hot"))
    monkeypatch.setenv("STRATEGY_LAB_HOT_CACHE_MAX_MB", "12")
    pol = SP.storage_policy()
    assert pol["hot_root"].endswith("hot")
    assert pol["hot_cache_max_mb"] == 12.0


def test_lru_budget_dry_run_lists_without_deleting(tmp_path):
    root = tmp_path / "hot"
    _write(root / "old.json", 2 * 1024 * 1024, mtime=time.time() - 1000)
    _write(root / "new.json", 2 * 1024 * 1024, mtime=time.time())
    res = SP.enforce_lru_budget(root, max_mb=3, apply=False)
    assert res["applied"] is False
    assert "old.json" in res["removed"]          # oldest scheduled for eviction
    assert (root / "old.json").exists()           # but not actually removed in dry-run


def test_lru_budget_apply_request_remains_report_only(tmp_path):
    root = tmp_path / "hot"
    _write(root / "old.json", 2 * 1024 * 1024, mtime=time.time() - 1000)
    _write(root / "new.json", 2 * 1024 * 1024, mtime=time.time())
    res = SP.enforce_lru_budget(root, max_mb=3, apply=True)
    assert res["applied"] is False
    assert res["reason"] == "report_only_protected_root"
    assert (root / "old.json").exists()
    assert (root / "new.json").exists()
    assert res["after_mb"] <= 3


def test_lru_budget_under_budget_removes_nothing(tmp_path):
    root = tmp_path / "hot"
    _write(root / "a.json", 1024)
    res = SP.enforce_lru_budget(root, max_mb=100, apply=True)
    assert res["removed"] == []


def test_rotate_under_cap_is_noop(tmp_path):
    log = tmp_path / "scanner.log"
    _write(log, 1024)
    res = SP.rotate_if_large(log, max_mb=50, archive_root=tmp_path / "arch", apply=True)
    assert res["rotated"] is False


def test_rotate_large_log_is_report_only(tmp_path):
    log = tmp_path / "scanner.log"
    _write(log, 3 * 1024 * 1024)
    arch = tmp_path / "arch"
    res = SP.rotate_if_large(log, max_mb=1, archive_root=arch, apply=True)
    assert res["rotated"] is False
    assert res["would_rotate"] is True
    assert log.stat().st_size == 3 * 1024 * 1024
    assert not arch.exists()
    assert res["applied"] is False
    assert res["reason"] == "legacy_rotation_report_only"


def test_rotate_absent_log(tmp_path):
    res = SP.rotate_if_large(tmp_path / "nope.log", apply=True)
    assert res["rotated"] is False


def test_maintain_reports_logs_and_hot_cache_without_mutation(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEGY_LAB_HOT_ROOT", str(tmp_path / "hot"))
    monkeypatch.setenv("STRATEGY_LAB_HOT_CACHE_MAX_MB", "3")
    monkeypatch.setenv("SCANNER_LOG_ROTATE_MB", "1")
    monkeypatch.setenv("SCANNER_LOG_ARCHIVE_ROOT", str(tmp_path / "arch"))
    big_log = tmp_path / "scanner_journal.jsonl"
    _write(big_log, 2 * 1024 * 1024)
    _write(tmp_path / "hot" / "old.json", 2 * 1024 * 1024, mtime=time.time() - 1000)
    _write(tmp_path / "hot" / "new.json", 2 * 1024 * 1024, mtime=time.time())

    report = SP.maintain(
        [big_log],
        hot_cache_root=tmp_path / "hot",
        hot_cache_budget_mb=3,
        apply=True,
    )
    assert report["rotated"][0]["rotated"] is False
    assert report["rotated"][0]["would_rotate"] is True
    assert big_log.stat().st_size == 2 * 1024 * 1024
    assert report["hot_cache"]["applied"] is False
    assert (tmp_path / "hot" / "old.json").exists()
    assert report["reason"] == "legacy_maintenance_report_only"


def test_maintain_does_not_inventory_ambient_hot_root(monkeypatch, tmp_path):
    ambient = tmp_path / "ambient"
    _write(ambient / "evidence.json", 1024)
    monkeypatch.setenv("STRATEGY_LAB_HOT_ROOT", str(ambient))

    report = SP.maintain([], apply=True)

    assert report["hot_cache"] == {
        "status": "not_inventoried",
        "applied": False,
        "reason": "explicit_root_required",
    }


def test_maintain_never_raises_on_bad_path(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_HOT_ROOT", str(tmp_path / "hot"))
    # Absent log + empty hot cache: maintain is a clean no-op, returns a report.
    report = SP.maintain([tmp_path / "absent.jsonl"], apply=True)
    assert report["rotated"][0]["rotated"] is False
    assert "hot_cache" in report
