"""Synthetic-only storage root authority and no-follow path validation.

Package 08A intentionally exposes no production/private activation policy.  The
only trust anchor is obtained independently from the process TEMP/TMP variables.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import stat
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

POLICY_ID = "synthetic_temporary_storage.v2"
SCHEMA = "StorageRootCapability.v2"
RESERVED = ".storage-v2"
ALLOWED_SUBTREE = "cache"
ALLOWED_EXTENSIONS = (".json", ".jsonl", ".bin", ".parquet")
MAX_BUDGET_MB = 1024 * 1024
_REPO_ROOT = Path(__file__).resolve().parents[2]


class StorageCapabilityError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    attrs = int(getattr(info, "st_file_attributes", 0))
    return stat.S_ISLNK(info.st_mode) or bool(attrs & 0x400)


def _windows_temp_anchor() -> Path:
    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    folder_id = GUID()
    if ole32.CLSIDFromString(
        ctypes.c_wchar_p("{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}"),
        ctypes.byref(folder_id),
    ) != 0:
        raise StorageCapabilityError("cannot resolve the LocalAppData authority id")
    raw_pointer = ctypes.c_void_p()
    result = shell32.SHGetKnownFolderPath(
        ctypes.byref(folder_id), 0x00004000, None, ctypes.byref(raw_pointer)
    )
    if result != 0 or not raw_pointer.value:
        raise StorageCapabilityError("Windows profile temp authority is unavailable")
    try:
        local_app_data = Path(ctypes.wstring_at(raw_pointer.value))
    finally:
        ole32.CoTaskMemFree(raw_pointer)
    anchor = (local_app_data / "Temp").resolve(strict=True)
    if str(anchor).startswith("\\\\"):
        raise StorageCapabilityError("network temporary roots are unsupported")
    drive_type = int(kernel32.GetDriveTypeW(ctypes.c_wchar_p(anchor.anchor)))
    if drive_type != 3:  # DRIVE_FIXED
        raise StorageCapabilityError("synthetic storage requires a fixed local drive")
    return anchor


def fixed_temp_anchor() -> Path:
    if os.name == "nt":
        return _windows_temp_anchor()
    anchor = Path("/tmp").resolve(strict=True)
    if anchor != Path("/tmp") or is_link_or_reparse(Path("/tmp")):
        raise StorageCapabilityError("fixed POSIX temp anchor must not be redirected")
    return anchor


def _volume_serial(path: Path) -> int:
    if os.name != "nt":
        return 0
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    serial = ctypes.c_ulong()
    ok = kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(path.anchor),
        None,
        0,
        ctypes.byref(serial),
        None,
        None,
        None,
        0,
    )
    if not ok:
        raise StorageCapabilityError("cannot establish local volume identity")
    return int(serial.value)


def filesystem_identity(path: Path) -> dict[str, int]:
    info = path.stat(follow_symlinks=False)
    return {"device": int(info.st_dev), "volume_serial": _volume_serial(path)}


def _validate_existing_chain(anchor: Path, target: Path) -> None:
    if is_link_or_reparse(anchor):
        raise StorageCapabilityError("temporary anchor is a link or reparse point")
    current = anchor
    for part in target.relative_to(anchor).parts:
        current /= part
        if not os.path.lexists(current):
            raise StorageCapabilityError("managed path component is missing")
        if is_link_or_reparse(current):
            raise StorageCapabilityError("managed path contains a link or reparse point")


@dataclass(frozen=True)
class StorageRootCapability:
    schema: str
    policy_id: str
    root_id: str
    canonical_root: str
    canonical_anchor: str
    filesystem_identity: dict[str, int]
    allowed_subtree: str
    allowed_extensions: tuple[str, ...]
    reserved_subtree: str
    max_budget_mb: int
    canonicalization: str
    capability_digest: str

    def payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("capability_digest")
        payload["allowed_extensions"] = list(self.allowed_extensions)
        return payload


def parse_positive_budget(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StorageCapabilityError("budget must be a finite positive number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0 or parsed > MAX_BUDGET_MB:
        raise StorageCapabilityError("budget must be finite, positive, and bounded")
    return parsed


def _validate_synthetic_root(root: Path) -> tuple[Path, Path]:
    anchor = fixed_temp_anchor()
    requested = Path(root)
    if not requested.is_absolute() or not requested.exists() or not requested.is_dir():
        raise StorageCapabilityError("managed root must be an existing absolute directory")
    lexical = Path(os.path.abspath(requested))
    if lexical == anchor or not is_relative_to(lexical, anchor):
        raise StorageCapabilityError("managed root is outside the fixed temp policy")
    if len(lexical.relative_to(anchor).parts) < 2:
        raise StorageCapabilityError("managed root is too shallow")
    if lexical == _REPO_ROOT or is_relative_to(lexical, _REPO_ROOT):
        raise StorageCapabilityError("repository paths cannot be activated")
    _validate_existing_chain(anchor, lexical)
    canonical = lexical.resolve(strict=True)
    if os.path.normcase(str(canonical)) != os.path.normcase(str(lexical)):
        raise StorageCapabilityError("managed root canonical path changed through a link")
    filesystem_identity(canonical)
    return anchor, canonical


def _control_paths(root: Path) -> tuple[Path, ...]:
    control = root / RESERVED
    return (
        control,
        control / "capability.json",
        control / "marker.json",
        control / "locks",
        control / "locks" / "operation.lock",
        control / "staging",
        control / "quarantine",
    )


def _validate_control_tree(root: Path, expected_fs: dict[str, int]) -> None:
    for path in _control_paths(root):
        if not os.path.lexists(path):
            raise StorageCapabilityError("reserved storage metadata is incomplete")
        if is_link_or_reparse(path):
            raise StorageCapabilityError("reserved storage metadata contains a link")
        if filesystem_identity(path) != expected_fs:
            raise StorageCapabilityError("reserved storage metadata changed volume")


def activate_synthetic_root(root: Path) -> StorageRootCapability:
    anchor, canonical = _validate_synthetic_root(root)
    if any(canonical.iterdir()):
        raise StorageCapabilityError("synthetic managed root must be empty at activation")
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "policy_id": POLICY_ID,
        "root_id": f"storageroot_{uuid.uuid4().hex}",
        "canonical_root": str(canonical),
        "canonical_anchor": str(anchor),
        "filesystem_identity": filesystem_identity(canonical),
        "allowed_subtree": ALLOWED_SUBTREE,
        "allowed_extensions": list(ALLOWED_EXTENSIONS),
        "reserved_subtree": RESERVED,
        "max_budget_mb": MAX_BUDGET_MB,
        "canonicalization": "canonical-json-sha256.v1",
    }
    digest = content_digest(payload)
    control = canonical / RESERVED
    control.mkdir()
    for name in ("locks", "staging", "quarantine"):
        (control / name).mkdir()
    manifest = {**payload, "capability_digest": digest}
    marker = {
        "schema": "StorageRootMarker.v2",
        "root_id": payload["root_id"],
        "capability_digest": digest,
        "canonical_root": str(canonical),
        "filesystem_identity": payload["filesystem_identity"],
    }
    (control / "capability.json").write_text(canonical_json(manifest), encoding="utf-8")
    (control / "marker.json").write_text(canonical_json(marker), encoding="utf-8")
    (control / "locks" / "operation.lock").write_bytes(b"0")
    return load_capability(canonical)


def load_capability(root: Path) -> StorageRootCapability:
    anchor, canonical = _validate_synthetic_root(root)
    control = canonical / RESERVED
    manifest_path = control / "capability.json"
    marker_path = control / "marker.json"
    expected_filesystem = filesystem_identity(canonical)
    _validate_control_tree(canonical, expected_filesystem)
    if not manifest_path.is_file() or not marker_path.is_file():
        raise StorageCapabilityError("capability manifest and marker are required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    digest = str(manifest.pop("capability_digest", ""))
    if digest != content_digest(manifest):
        raise StorageCapabilityError("capability digest mismatch")
    expected = {
        "schema": SCHEMA,
        "policy_id": POLICY_ID,
        "canonical_root": str(canonical),
        "canonical_anchor": str(anchor),
        "filesystem_identity": expected_filesystem,
        "allowed_subtree": ALLOWED_SUBTREE,
        "allowed_extensions": list(ALLOWED_EXTENSIONS),
        "reserved_subtree": RESERVED,
        "max_budget_mb": MAX_BUDGET_MB,
        "canonicalization": "canonical-json-sha256.v1",
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise StorageCapabilityError("capability violates the fixed synthetic policy")
    expected_marker = {
        "schema": "StorageRootMarker.v2",
        "root_id": manifest.get("root_id"),
        "capability_digest": digest,
        "canonical_root": str(canonical),
        "filesystem_identity": expected["filesystem_identity"],
    }
    if marker != expected_marker:
        raise StorageCapabilityError("root marker mismatch")
    return StorageRootCapability(
        **{**manifest, "allowed_extensions": tuple(manifest["allowed_extensions"])},
        capability_digest=digest,
    )


def validate_relative_path_text(capability: StorageRootCapability, value: str) -> PurePosixPath:
    if not value or "\\" in value:
        raise StorageCapabilityError("relative path must use canonical POSIX separators")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise StorageCapabilityError("relative path is not canonical")
    if not relative.parts or relative.parts[0] != capability.allowed_subtree:
        raise StorageCapabilityError("relative path is outside the fixed cache subtree")
    if Path(relative.name).suffix.lower() not in capability.allowed_extensions:
        raise StorageCapabilityError("candidate extension is not allowed")
    return relative


def safe_relative_file(capability: StorageRootCapability, path: Path) -> str:
    root = Path(capability.canonical_root)
    requested = Path(path)
    lexical = Path(os.path.abspath(root / requested if not requested.is_absolute() else requested))
    try:
        relative_native = lexical.relative_to(root)
    except ValueError as exc:
        raise StorageCapabilityError("candidate escapes the managed root") from exc
    relative = validate_relative_path_text(capability, PurePosixPath(*relative_native.parts).as_posix())
    current = root
    for part in relative.parts:
        current /= part
        if not os.path.lexists(current):
            raise StorageCapabilityError("candidate path component is missing")
        if is_link_or_reparse(current):
            raise StorageCapabilityError("candidate path contains a link or reparse point")
    info = lexical.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise StorageCapabilityError("candidate must be a regular file")
    if lexical.resolve(strict=True) != lexical:
        raise StorageCapabilityError("candidate canonical path changed")
    if filesystem_identity(lexical) != capability.filesystem_identity:
        raise StorageCapabilityError("candidate changed filesystem")
    return relative.as_posix()


def ensure_safe_parent(capability: StorageRootCapability, relative_path: str) -> Path:
    relative = validate_relative_path_text(capability, relative_path)
    root = Path(capability.canonical_root)
    parent = root
    for part in relative.parts[:-1]:
        candidate = parent / part
        if os.path.lexists(candidate):
            if is_link_or_reparse(candidate) or not candidate.is_dir():
                raise StorageCapabilityError("destination ancestor is unsafe")
            if filesystem_identity(candidate) != capability.filesystem_identity:
                raise StorageCapabilityError("destination ancestor changed filesystem")
        else:
            candidate.mkdir()
            if is_link_or_reparse(candidate):
                raise StorageCapabilityError("created destination ancestor is unsafe")
        if filesystem_identity(candidate) != capability.filesystem_identity:
            raise StorageCapabilityError("destination ancestor changed filesystem")
        parent = candidate
    return parent
