"""Canonical, manifest-bound database targets for post-stop integrity checks.

External canary monitors must not guess runtime SQLite locations.  A stale
hard-coded path can turn a clean final stop into an ``unable_to_open`` alert,
which is neither a database result nor a useful safety verdict.  This resolver
has no database access: it only derives canonical paths from the same private
root and Paper Evidence V2 manifest used by production.
"""

from __future__ import annotations

from pathlib import Path
from src.research_lab.candle_store import candle_store_path
from src.research_lab.paper_generation_cutover import (
    DEFAULT_DATABASE_RELATIVE,
    load_cutover_manifest,
)
from src.research_lab.storage_capability import is_link_or_reparse


class PostStopIntegrityTargetError(RuntimeError):
    """A requested post-stop integrity target is not canonical and safe."""


def _is_link_or_reparse_if_present(path: Path) -> bool:
    """Keep a missing canonical target observable for the later integrity probe."""

    try:
        return is_link_or_reparse(path)
    except FileNotFoundError:
        return False


def _canonical_root(private_root: Path | str) -> Path:
    root = Path(private_root)
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise PostStopIntegrityTargetError("private root is unavailable") from exc
    if not resolved.is_dir() or _is_link_or_reparse_if_present(root):
        raise PostStopIntegrityTargetError("private root is unsafe")
    return resolved


def _resolved_exact_child(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise PostStopIntegrityTargetError("integrity target escapes private root")
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise PostStopIntegrityTargetError("integrity target cannot be resolved") from exc
    if not resolved.is_relative_to(root) or _is_link_or_reparse_if_present(candidate):
        raise PostStopIntegrityTargetError("integrity target is unsafe")
    return resolved


def resolve_post_stop_integrity_targets(
    private_root: Path | str,
) -> dict[str, Path]:
    """Return only canonical candle and active Paper Evidence V2 DB targets.

    The active cutover manifest is mandatory for the Paper Evidence target and
    is always loaded through its canonical fail-closed validator. Existence and
    SQLite integrity are intentionally separate read-only operations performed
    only after an owner has proved quiescence.
    """

    root = _canonical_root(private_root)
    manifest = load_cutover_manifest(root, require_active=True)
    declared = str(manifest.get("authority_database_relative_path") or "")
    if declared != DEFAULT_DATABASE_RELATIVE.as_posix():
        raise PostStopIntegrityTargetError("paper evidence manifest target is not canonical")
    paper_evidence = _resolved_exact_child(root, DEFAULT_DATABASE_RELATIVE)
    candles = candle_store_path(root)
    try:
        candles = candles.resolve(strict=False)
    except OSError as exc:
        raise PostStopIntegrityTargetError("candle target cannot be resolved") from exc
    if not candles.is_relative_to(root) or _is_link_or_reparse_if_present(candles):
        raise PostStopIntegrityTargetError("candle target is unsafe")
    return {"candles": candles, "paper_evidence_v2": paper_evidence}
