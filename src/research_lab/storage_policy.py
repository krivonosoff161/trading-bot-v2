# -*- coding: utf-8 -*-
"""Hot/cold storage policy — keep a constrained desktop honest about disk.

This machine has a small SSD (``C:``) and a larger HDD (``E:``). The rule is NOT
"put everything on SSD"; it is:

    * cold/private (HDD ok): master candles, completed experiments, archives, old
      logs -> ``TRADING_BOT_RESEARCH_ROOT`` (defaults to the private research root);
    * hot (SSD if available): tiny working cache (e.g. the OKX instruments snapshot)
      -> ``STRATEGY_LAB_HOT_ROOT``, bounded by ``STRATEGY_LAB_HOT_CACHE_MAX_MB`` (LRU);
    * logs rotate/compress to ``SCANNER_LOG_ARCHIVE_ROOT`` past ``SCANNER_LOG_ROTATE_MB``.

Legacy maintenance is report-only.  Destructive v1 eviction, truncating rotation,
and count-only spec pruning are deliberately disabled; reversible v2 operations use
an explicitly activated temporary-root capability instead.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MB = 1024 * 1024


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    return Path(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def research_root() -> Path:
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    return _env_path("TRADING_BOT_RESEARCH_ROOT", DEFAULT_PRIVATE_ROOT)


def hot_root() -> Path:
    return _env_path("STRATEGY_LAB_HOT_ROOT", _ROOT / "logs" / "scout" / "cache")


def hot_cache_max_mb() -> float:
    return _env_float("STRATEGY_LAB_HOT_CACHE_MAX_MB", 256.0)


def log_rotate_mb() -> float:
    return _env_float("SCANNER_LOG_ROTATE_MB", 50.0)


def log_archive_root() -> Path:
    return _env_path("SCANNER_LOG_ARCHIVE_ROOT", _ROOT / "logs_archive" / "scout")


def storage_policy() -> dict:
    """Resolved policy snapshot (for status output / notes)."""
    return {
        "research_root": str(research_root()),
        "hot_root": str(hot_root()),
        "hot_cache_max_mb": hot_cache_max_mb(),
        "log_rotate_mb": log_rotate_mb(),
        "log_archive_root": str(log_archive_root()),
    }


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def dir_size_mb(root: Path) -> float:
    return sum(p.stat().st_size for p in _files(root)) / _MB


def enforce_lru_budget(root: Path | None = None, max_mb: float | None = None,
                       *, apply: bool = False) -> dict:
    """Report oldest hot-cache candidates; legacy apply has no mutation authority."""
    root = root or hot_root()
    max_mb = hot_cache_max_mb() if max_mb is None else max_mb
    files = sorted(_files(root), key=lambda p: p.stat().st_mtime)  # oldest first
    total = sum(p.stat().st_size for p in files)
    budget = max_mb * _MB
    removed: list[str] = []
    for path in files:
        if total <= budget:
            break
        size = path.stat().st_size
        removed.append(path.relative_to(root).as_posix())
        total -= size
    return {
        "root": str(root),
        "max_mb": max_mb,
        "removed": removed,
        "after_mb": round(total / _MB, 3),
        "applied": False,
        "apply_requested": bool(apply),
        "reason": "report_only_protected_root" if apply else "report_only",
    }


def rotate_if_large(path: Path, *, max_mb: float | None = None, archive_root: Path | None = None,
                    apply: bool = False) -> dict:
    """Report oversized legacy logs; copy/truncate rotation is unsupported."""
    max_mb = log_rotate_mb() if max_mb is None else max_mb
    archive_root = archive_root or log_archive_root()
    if not path.exists():
        return {
            "path": str(path),
            "rotated": False,
            "would_rotate": False,
            "applied": False,
            "apply_requested": bool(apply),
            "reason": "absent",
            "storage_class": "legacy_uncoordinated_storage",
        }
    size_mb = path.stat().st_size / _MB
    if size_mb < max_mb:
        return {
            "path": str(path),
            "rotated": False,
            "would_rotate": False,
            "applied": False,
            "apply_requested": bool(apply),
            "reason": "under_cap",
            "storage_class": "legacy_uncoordinated_storage",
            "size_mb": round(size_mb, 3),
        }
    stamp = int(time.time())
    dest = archive_root / f"{path.name}.{stamp}.gz"
    return {
        "path": str(path),
        "rotated": False,
        "would_rotate": True,
        "archive": str(dest),
        "size_mb": round(size_mb, 3),
        "applied": False,
        "apply_requested": bool(apply),
        "reason": "legacy_rotation_report_only",
        "storage_class": "legacy_uncoordinated_storage",
    }


def prune_event_specs(private_root: Path, *, keep: int = 500, apply: bool = False) -> dict:
    """Bound plans/event_specs/*.json — keep the newest ``keep`` files, drop older.

    One spec file is written per materialized sweep; on a continuous loop this would
    grow unbounded, so we cap it. Returns how many were (or would be) removed.
    """
    spec_dir = Path(private_root) / "plans" / "event_specs"
    if not spec_dir.exists():
        return {
            "present": 0,
            "removed": 0,
            "candidates": [],
            "applied": False,
            "apply_requested": bool(apply),
            "reason": "event_spec_apply_unsupported" if apply else "report_only",
        }
    files = sorted(spec_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    stale = files[keep:]
    return {
        "present": len(files),
        "removed": len(stale),
        "candidates": [path.relative_to(spec_dir).as_posix() for path in stale],
        "applied": False,
        "apply_requested": bool(apply),
        "reason": "event_spec_apply_unsupported" if apply else "report_only",
    }


def bound_farm_artifacts(private_root: Path, *, keep_specs: int = 500,
                         keep_terminal: int = 5000, apply: bool = False) -> dict:
    """Read-only growth report for event specs and terminal task history."""
    specs = prune_event_specs(private_root, keep=keep_specs, apply=False)
    tasks_pruned = 0
    try:
        from src.research_lab.farm_tasks_db import tasks_db_path
        db_path = tasks_db_path(private_root)
        if db_path.exists():
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            try:
                terminal_total = int(conn.execute(
                    "SELECT COUNT(*) FROM tasks "
                    "WHERE state IN ('completed','skipped','failed')"
                ).fetchone()[0])
                unique_total = int(
                    conn.execute("SELECT COUNT(*) FROM unique_candidates").fetchone()[0]
                )
            finally:
                conn.close()
            tasks_pruned = max(0, terminal_total - int(keep_terminal))
            uc_pruned = max(0, unique_total - int(keep_terminal))
            return {
                "event_specs": specs,
                "terminal_tasks_pruned": tasks_pruned,
                "unique_candidates_pruned": uc_pruned,
                "applied": False,
                "apply_requested": bool(apply),
                "reason": "database_prune_report_only",
            }
    except Exception as exc:  # noqa: BLE001 - hygiene must never break a cycle
        return {
            "event_specs": specs,
            "terminal_tasks_pruned": 0,
            "unique_candidates_pruned": 0,
            "applied": False,
            "apply_requested": bool(apply),
            "reason": "database_report_incomplete",
            "error": type(exc).__name__,
        }
    return {
        "event_specs": specs,
        "terminal_tasks_pruned": tasks_pruned,
        "unique_candidates_pruned": 0,
        "applied": False,
        "apply_requested": bool(apply),
        "reason": "database_prune_report_only",
    }


def maintain(
    log_paths: list[Path] | None = None,
    *,
    hot_cache_root: Path | None = None,
    hot_cache_budget_mb: float | None = None,
    apply: bool = False,
) -> dict:
    """One maintenance pass: rotate oversized append-only logs + LRU-bound the hot cache.

    Safe to call at the tail of every scan pass: it never grants mutation authority.
    Errors are reduced to report fields so storage reporting cannot break a scan.
    """
    rotated: list[dict] = []
    for path in log_paths or []:
        try:
            rotated.append(rotate_if_large(Path(path), apply=False))
        except OSError as exc:
            rotated.append({"path": str(path), "rotated": False, "reason": f"error:{type(exc).__name__}"})
    if hot_cache_root is None:
        hot = {
            "status": "not_inventoried",
            "applied": False,
            "reason": "explicit_root_required",
        }
    else:
        try:
            hot = enforce_lru_budget(
                root=Path(hot_cache_root),
                max_mb=hot_cache_budget_mb,
                apply=False,
            )
        except OSError as exc:
            hot = {"error": type(exc).__name__, "applied": False}
    return {
        "rotated": rotated,
        "hot_cache": hot,
        "applied": False,
        "apply_requested": bool(apply),
        "reason": "legacy_maintenance_report_only",
    }
