"""Exact-root operator CLI for the off-by-default runtime storage capability."""

from __future__ import annotations

import argparse
from dataclasses import fields
import json
from pathlib import Path
import time
from typing import Any

from src.research_lab.archive_catalog import ArchiveCatalog, ArchiveCatalogError
from src.research_lab.private_archive_capability import (
    PrivateArchiveAuthority,
    PrivateArchiveCapabilityError,
    activate_private_archive_root,
    load_private_archive_capability,
)
from src.research_lab.runtime_storage_rotation import (
    RuntimeStorageAuthority,
    RuntimeStorageError,
    activate_runtime_storage,
    archive_pending_segments,
    load_runtime_storage_capability,
    storage_budget_status,
)


def _authority(path: Path) -> RuntimeStorageAuthority:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeStorageError("runtime storage authority file is unreadable") from exc
    expected = {item.name for item in fields(RuntimeStorageAuthority)}
    if not isinstance(value, dict) or set(value) != expected:
        raise RuntimeStorageError("runtime storage authority shape is invalid")
    try:
        return RuntimeStorageAuthority(**value)
    except (TypeError, ValueError) as exc:
        raise RuntimeStorageError("runtime storage authority values are invalid") from exc


def _archive_authority(path: Path) -> PrivateArchiveAuthority:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeStorageError("archive authority file is unreadable") from exc
    expected = {item.name for item in fields(PrivateArchiveAuthority)}
    if not isinstance(value, dict) or set(value) != expected:
        raise RuntimeStorageError("archive authority shape is invalid")
    value["allowed_kinds"] = tuple(value["allowed_kinds"])
    try:
        return PrivateArchiveAuthority(**value)
    except (TypeError, ValueError) as exc:
        raise RuntimeStorageError("archive authority values are invalid") from exc


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    activate = commands.add_parser("activate")
    activate.add_argument("--archive-root", type=Path, required=True)
    activate.add_argument("--authority-file", type=Path, required=True)
    activate.add_argument(
        "--archive-authority-file",
        type=Path,
        help="required only when the exact archive root is still empty/unactivated",
    )
    commands.add_parser("status")
    commands.add_parser("maintain")
    commands.add_parser("verify")
    args = parser.parse_args(argv)

    try:
        if args.command == "activate":
            try:
                load_private_archive_capability(args.archive_root)
            except PrivateArchiveCapabilityError:
                if args.archive_authority_file is None or any(args.archive_root.iterdir()):
                    raise RuntimeStorageError(
                        "archive root is not an activated compatible capability"
                    )
                activate_private_archive_root(
                    args.archive_root,
                    source_root=args.source_root,
                    authority=_archive_authority(args.archive_authority_file),
                    now=time.time(),
                )
            capability = activate_runtime_storage(
                args.source_root,
                archive_root=args.archive_root,
                authority=_authority(args.authority_file),
                now=time.time(),
            )
            _emit(
                {
                    "schema": "RuntimeStorageActivationResult.v1",
                    "state": "active",
                    "source_revision": capability.source_revision,
                    "stream_count": len(capability.streams),
                    "capability_digest": capability.capability_digest,
                }
            )
            return 0
        capability = load_runtime_storage_capability(args.source_root)
        if args.command == "status":
            status = storage_budget_status(capability)
            _emit(status)
            return 0 if status["state"] == "ready" else 2
        if args.command == "maintain":
            result = archive_pending_segments(capability)
            budget = storage_budget_status(capability)
            _emit({**result, "budget": budget})
            return 0 if result["state"] == budget["state"] == "ready" else 2
        manifests = ArchiveCatalog(Path(capability.archive_root)).manifests()
        relevant = [item for item in manifests if item.kind in {policy.kind for policy in capability.streams}]
        problems = [item.artifact_id for item in relevant if not item.copy_verified or not item.restore_verified]
        _emit(
            {
                "schema": "RuntimeStorageArchiveVerification.v1",
                "state": "ready" if not problems else "failed",
                "manifest_count": len(relevant),
                "restore_verified_count": len(relevant) - len(problems),
                "problems": ["archive_restore_unverified"] if problems else [],
            }
        )
        return 0 if not problems else 2
    except (RuntimeStorageError, ArchiveCatalogError, PrivateArchiveCapabilityError) as exc:
        _emit(
            {
                "schema": "RuntimeStorageCommandError.v1",
                "state": "failed",
                "error_type": type(exc).__name__,
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
