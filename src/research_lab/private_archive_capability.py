"""Exact-root capability for an off-by-default private archive store.

This module deliberately exposes no environment discovery, default path, CLI, or
launcher integration.  A caller must translate a current external owner manifest
into :class:`PrivateArchiveAuthority` and pass the exact source/archive roots.
The capability is a local binding and safety check, not proof that the caller
actually received owner authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
from typing import Any

from src.research_lab.storage_capability import (
    canonical_json,
    content_digest,
    filesystem_identity,
    is_link_or_reparse,
)


AUTHORITY_SCHEMA = "PrivateArchiveOwnerAuthority.v1"
CAPABILITY_SCHEMA = "PrivateArchiveRootCapability.v1"
POLICY_ID = "private_archive_storage.v1"
CONTROL_DIR = ".archive-v1"
ALLOWED_KINDS = (
    "derived_artifact",
    "project_brain_events",
    "farm_journal",
    "lineage",
    "llm_invocation",
    "runtime_stdout",
    "scout_journal",
)
ALLOWED_SENSITIVITY = ("public_safe_derived", "private_metadata", "private_payload")
_AUTHORITY_ID = re.compile(r"owner_[a-f0-9]{32}")
_ROOT_ID = re.compile(r"archive_[a-f0-9]{32}")
_CONTENT_DIGEST = re.compile(r"sha256:[a-f0-9]{64}")


class PrivateArchiveCapabilityError(ValueError):
    """The exact-root archive capability or its owner binding is invalid."""


@dataclass(frozen=True)
class PrivateArchiveAuthority:
    schema: str
    project_id: str
    action: str
    source_root: str
    archive_root: str
    allowed_kinds: tuple[str, ...]
    issued_at: float
    expires_at: float
    turn_id: str
    authority_id: str
    synthetic: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "allowed_kinds": list(self.allowed_kinds),
        }

    @property
    def digest(self) -> str:
        return content_digest(self.payload())


@dataclass(frozen=True)
class PrivateArchiveCapability:
    schema: str
    policy_id: str
    project_id: str
    root_id: str
    canonical_root: str
    source_root: str
    filesystem_identity: dict[str, int]
    source_filesystem_identity: dict[str, int]
    allowed_kinds: tuple[str, ...]
    allowed_sensitivity: tuple[str, ...]
    authority_id: str
    authority_digest: str
    synthetic: bool
    capability_digest: str

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("capability_digest")
        value["allowed_kinds"] = list(self.allowed_kinds)
        value["allowed_sensitivity"] = list(self.allowed_sensitivity)
        return value


def _exact_directory(path: Path, *, label: str) -> Path:
    requested = Path(path)
    if not requested.is_absolute() or not requested.exists() or not requested.is_dir():
        raise PrivateArchiveCapabilityError(f"{label} must be an existing absolute directory")
    lexical = Path(os.path.abspath(requested))
    if str(lexical).startswith("\\\\"):
        raise PrivateArchiveCapabilityError(f"{label} must be on a local filesystem")
    if is_link_or_reparse(lexical):
        raise PrivateArchiveCapabilityError(f"{label} cannot be a link or reparse point")
    canonical = lexical.resolve(strict=True)
    if os.path.normcase(str(canonical)) != os.path.normcase(str(lexical)):
        raise PrivateArchiveCapabilityError(f"{label} changed through path resolution")
    return canonical


def _validate_authority(
    authority: PrivateArchiveAuthority,
    *,
    source_root: Path,
    archive_root: Path,
    now: float,
) -> None:
    if authority.schema != AUTHORITY_SCHEMA:
        raise PrivateArchiveCapabilityError("archive authority schema mismatch")
    if authority.project_id != "trading-bot-v2":
        raise PrivateArchiveCapabilityError("archive authority project mismatch")
    if authority.action != "activate_private_archive_storage":
        raise PrivateArchiveCapabilityError("archive authority action mismatch")
    if not authority.turn_id:
        raise PrivateArchiveCapabilityError("archive authority turn identity is required")
    if not _AUTHORITY_ID.fullmatch(authority.authority_id):
        raise PrivateArchiveCapabilityError("archive authority id is invalid")
    if not authority.issued_at <= now < authority.expires_at:
        raise PrivateArchiveCapabilityError("archive authority is not currently valid")
    if tuple(sorted(set(authority.allowed_kinds))) != tuple(sorted(authority.allowed_kinds)):
        raise PrivateArchiveCapabilityError("archive authority kinds must be sorted and unique")
    if not authority.allowed_kinds or any(
        item not in ALLOWED_KINDS for item in authority.allowed_kinds
    ):
        raise PrivateArchiveCapabilityError("archive authority kind scope is invalid")
    if os.path.normcase(authority.source_root) != os.path.normcase(str(source_root)):
        raise PrivateArchiveCapabilityError("archive authority source root mismatch")
    if os.path.normcase(authority.archive_root) != os.path.normcase(str(archive_root)):
        raise PrivateArchiveCapabilityError("archive authority target root mismatch")


def _control_paths(root: Path) -> tuple[Path, ...]:
    control = root / CONTROL_DIR
    return (
        control,
        control / "locks",
        control / "staging",
        control / "manifests",
        control / "cutovers",
        root / "objects",
    )


def activate_private_archive_root(
    archive_root: Path,
    *,
    source_root: Path,
    authority: PrivateArchiveAuthority,
    now: float,
) -> PrivateArchiveCapability:
    """Bind one empty dedicated archive root to one exact owner manifest.

    Non-synthetic activation requires a distinct filesystem from the source.
    The current repository exposes no command that calls this function.
    """

    source = _exact_directory(source_root, label="source root")
    archive = _exact_directory(archive_root, label="archive root")
    if source == archive:
        raise PrivateArchiveCapabilityError("archive root cannot equal source root")
    if any(archive.iterdir()):
        raise PrivateArchiveCapabilityError("archive root must be empty at activation")
    _validate_authority(authority, source_root=source, archive_root=archive, now=now)
    source_fs = filesystem_identity(source)
    archive_fs = filesystem_identity(archive)
    if not authority.synthetic and source_fs == archive_fs:
        raise PrivateArchiveCapabilityError(
            "non-synthetic archive root must use a distinct filesystem"
        )

    root_id = "archive_" + content_digest(
        {
            "project_id": authority.project_id,
            "archive_root": str(archive),
            "source_root": str(source),
            "authority_digest": authority.digest,
        }
    ).split(":", 1)[1][:32]
    payload: dict[str, Any] = {
        "schema": CAPABILITY_SCHEMA,
        "policy_id": POLICY_ID,
        "project_id": authority.project_id,
        "root_id": root_id,
        "canonical_root": str(archive),
        "source_root": str(source),
        "filesystem_identity": archive_fs,
        "source_filesystem_identity": source_fs,
        "allowed_kinds": list(authority.allowed_kinds),
        "allowed_sensitivity": list(ALLOWED_SENSITIVITY),
        "authority_id": authority.authority_id,
        "authority_digest": authority.digest,
        "synthetic": bool(authority.synthetic),
    }
    capability_digest = content_digest(payload)
    control = archive / CONTROL_DIR
    control.mkdir()
    for path in _control_paths(archive)[1:]:
        path.mkdir()
    manifest = {**payload, "capability_digest": capability_digest}
    marker = {
        "schema": "PrivateArchiveRootMarker.v1",
        "root_id": root_id,
        "capability_digest": capability_digest,
        "canonical_root": str(archive),
        "filesystem_identity": archive_fs,
    }
    (control / "capability.json").write_text(
        canonical_json(manifest), encoding="utf-8"
    )
    (control / "marker.json").write_text(canonical_json(marker), encoding="utf-8")
    (control / "locks" / "catalog.lock").write_bytes(b"0")
    return load_private_archive_capability(archive)


def load_private_archive_capability(root: Path) -> PrivateArchiveCapability:
    archive = _exact_directory(root, label="archive root")
    control = archive / CONTROL_DIR
    manifest_path = control / "capability.json"
    marker_path = control / "marker.json"
    required = _control_paths(archive)
    if any(not path.exists() or not path.is_dir() for path in required):
        raise PrivateArchiveCapabilityError("archive control tree is incomplete")
    if any(is_link_or_reparse(path) for path in required):
        raise PrivateArchiveCapabilityError("archive control tree contains a link")
    if (
        not manifest_path.is_file()
        or not marker_path.is_file()
        or is_link_or_reparse(manifest_path)
        or is_link_or_reparse(marker_path)
    ):
        raise PrivateArchiveCapabilityError("archive capability files are unsafe")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise PrivateArchiveCapabilityError("archive capability is unreadable") from exc
    if not isinstance(manifest, dict) or not isinstance(marker, dict):
        raise PrivateArchiveCapabilityError("archive capability shape is invalid")
    expected_manifest_keys = set(PrivateArchiveCapability.__dataclass_fields__)
    if set(manifest) != expected_manifest_keys:
        raise PrivateArchiveCapabilityError("archive capability key set is invalid")
    digest = str(manifest.pop("capability_digest", ""))
    if digest != content_digest(manifest):
        raise PrivateArchiveCapabilityError("archive capability digest mismatch")
    expected_root = str(archive)
    expected_fs = filesystem_identity(archive)
    if (
        manifest.get("schema") != CAPABILITY_SCHEMA
        or manifest.get("policy_id") != POLICY_ID
        or manifest.get("project_id") != "trading-bot-v2"
        or manifest.get("canonical_root") != expected_root
        or manifest.get("filesystem_identity") != expected_fs
    ):
        raise PrivateArchiveCapabilityError("archive capability binding mismatch")
    if (
        not _ROOT_ID.fullmatch(str(manifest.get("root_id") or ""))
        or not _AUTHORITY_ID.fullmatch(str(manifest.get("authority_id") or ""))
        or not _CONTENT_DIGEST.fullmatch(
            str(manifest.get("authority_digest") or "")
        )
        or not isinstance(manifest.get("synthetic"), bool)
    ):
        raise PrivateArchiveCapabilityError("archive capability identity is invalid")
    expected_marker = {
        "schema": "PrivateArchiveRootMarker.v1",
        "root_id": manifest.get("root_id"),
        "capability_digest": digest,
        "canonical_root": expected_root,
        "filesystem_identity": expected_fs,
    }
    if marker != expected_marker:
        raise PrivateArchiveCapabilityError("archive root marker mismatch")
    kinds = tuple(manifest.get("allowed_kinds") or ())
    sensitivity = tuple(manifest.get("allowed_sensitivity") or ())
    if (
        not kinds
        or tuple(sorted(set(kinds))) != kinds
        or any(item not in ALLOWED_KINDS for item in kinds)
    ):
        raise PrivateArchiveCapabilityError("archive capability kind scope is invalid")
    if sensitivity != ALLOWED_SENSITIVITY:
        raise PrivateArchiveCapabilityError("archive sensitivity policy mismatch")
    source = _exact_directory(Path(str(manifest.get("source_root"))), label="source root")
    if manifest.get("source_filesystem_identity") != filesystem_identity(source):
        raise PrivateArchiveCapabilityError("archive source filesystem identity drift")
    if not bool(manifest.get("synthetic")) and filesystem_identity(source) == expected_fs:
        raise PrivateArchiveCapabilityError(
            "non-synthetic archive root lost filesystem separation"
        )
    return PrivateArchiveCapability(
        **{
            **manifest,
            "allowed_kinds": kinds,
            "allowed_sensitivity": sensitivity,
        },
        capability_digest=digest,
    )
