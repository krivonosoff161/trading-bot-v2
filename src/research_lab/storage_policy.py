# -*- coding: utf-8 -*-
"""Hot/cold storage policy — keep a constrained desktop honest about disk.

This machine has a small SSD (``C:``) and a larger HDD (``E:``). The rule is NOT
"put everything on SSD"; it is:

    * cold/private (HDD ok): master candles, completed experiments, archives, old
      logs -> ``TRADING_BOT_RESEARCH_ROOT`` (defaults to the private research root);
    * hot (SSD if available): tiny working cache (e.g. the OKX instruments snapshot)
      -> ``STRATEGY_LAB_HOT_ROOT``, bounded by ``STRATEGY_LAB_HOT_CACHE_MAX_MB`` (LRU);
    * logs rotate/compress to ``SCANNER_LOG_ARCHIVE_ROOT`` past ``SCANNER_LOG_ROTATE_MB``.

This module is config + small, safe, idempotent hooks (LRU budget + log rotate),
dry-run by default. It does NOT migrate data and never touches order/.env paths.
"""
from __future__ import annotations

import gzip
import os
import shutil
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
    """Evict oldest (LRU by mtime) hot-cache files until under budget. Dry-run by default."""
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
        if apply:
            try:
                path.unlink()
            except OSError:
                continue
        removed.append(path.name)
        total -= size
    return {"root": str(root), "max_mb": max_mb, "removed": removed,
            "after_mb": round(total / _MB, 3), "applied": apply}


def rotate_if_large(path: Path, *, max_mb: float | None = None, archive_root: Path | None = None,
                    apply: bool = False) -> dict:
    """Gzip-archive a log past the size cap and reset it. Dry-run by default."""
    max_mb = log_rotate_mb() if max_mb is None else max_mb
    archive_root = archive_root or log_archive_root()
    if not path.exists():
        return {"path": str(path), "rotated": False, "reason": "absent"}
    size_mb = path.stat().st_size / _MB
    if size_mb < max_mb:
        return {"path": str(path), "rotated": False, "reason": "under_cap", "size_mb": round(size_mb, 3)}
    stamp = int(time.time())
    dest = archive_root / f"{path.name}.{stamp}.gz"
    if apply:
        archive_root.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as src, gzip.open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
        path.write_text("", encoding="utf-8")  # reset, keep the live path
    return {"path": str(path), "rotated": True, "archive": str(dest),
            "size_mb": round(size_mb, 3), "applied": apply}


def maintain(log_paths: list[Path] | None = None, *, apply: bool = True) -> dict:
    """One maintenance pass: rotate oversized append-only logs + LRU-bound the hot cache.

    Safe to call at the tail of every scan pass — each hook is a no-op until a cap is
    exceeded. Never raises into the caller (storage hygiene must not break a scan).
    """
    rotated: list[dict] = []
    for path in log_paths or []:
        try:
            rotated.append(rotate_if_large(Path(path), apply=apply))
        except OSError as exc:
            rotated.append({"path": str(path), "rotated": False, "reason": f"error:{type(exc).__name__}"})
    try:
        hot = enforce_lru_budget(apply=apply)
    except OSError as exc:
        hot = {"error": type(exc).__name__}
    return {"rotated": rotated, "hot_cache": hot}
