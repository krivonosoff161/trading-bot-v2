"""Plan-bound retention for quiescent RCC backup generations.

One explicitly named, separately evidence-bound complete generation stays
unpacked. Older generations are removed only after every exact source file has
a content-addressed gzip object, an immutable manifest, and a successful
decompression/hash proof. This is a same-volume space-reclamation archive, not
a disaster-recovery copy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import time
from typing import Any, Iterable, Mapping
import uuid

from scripts.ci.check_supply_chain_policy import reject_sensitive_data
from src.research_lab.storage_capability import (
    canonical_json,
    content_digest,
    filesystem_identity,
    is_link_or_reparse,
)
from src.research_lab.storage_os_lock import StorageLockConflict, storage_root_lock


PLAN_SCHEMA = "BackupRetentionPlan.v1"
AUTHORITY_SCHEMA = "BackupRetentionAuthority.v1"
FILE_MANIFEST_SCHEMA = "BackupRetentionFileManifest.v1"
APPLY_REPORT_SCHEMA = "BackupRetentionApplyReport.v1"
POLICY_ID = "backup_retention_lifecycle.v1"
PROJECT_ID = "trading-bot-v2"
CONTROL_DIR = ".backup-retention-v1"
DEFAULT_MAX_BACKUP_BYTES = 20 * 1024**3
DEFAULT_MIN_FREE_BYTES = 32 * 1024**3
_READ_SIZE = 4 * 1024 * 1024
_AUTHORITY_ID = re.compile(r"owner_[a-f0-9]{32}")


class BackupRetentionError(RuntimeError):
    """The backup inventory, plan, archive, or apply proof is unsafe."""


@dataclass(frozen=True)
class BackupFile:
    source_ref: str
    generation: str
    logical_bytes: int
    mtime_ns: int
    source_sha256: str


@dataclass(frozen=True)
class BackupGeneration:
    generation: str
    evidence_class: str
    complete_generation: bool
    newest_mtime_ns: int
    logical_bytes: int
    file_count: int
    disposition: str


@dataclass(frozen=True)
class BackupRetentionPlan:
    schema: str
    policy_id: str
    project_id: str
    backup_root: str
    archive_root: str
    backup_filesystem: dict[str, int]
    archive_filesystem: dict[str, int]
    created_at: str
    retain_unpacked_generations: tuple[str, ...]
    retained_generation_evidence_sha256: str
    archive_remove_generations: tuple[str, ...]
    generations: tuple[BackupGeneration, ...]
    files: tuple[BackupFile, ...]
    source_logical_bytes: int
    retained_logical_bytes: int
    reclaim_candidate_bytes: int
    unique_archive_logical_bytes: int
    max_backup_bytes: int
    min_free_bytes: int
    plan_digest: str

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("plan_digest")
        value["retain_unpacked_generations"] = list(self.retain_unpacked_generations)
        value["archive_remove_generations"] = list(self.archive_remove_generations)
        return value


@dataclass(frozen=True)
class BackupRetentionAuthority:
    schema: str
    project_id: str
    action: str
    backup_root: str
    archive_root: str
    expected_plan_digest: str
    issued_at: float
    expires_at: float
    turn_id: str
    authority_id: str


@dataclass(frozen=True)
class BackupRetentionApplyReport:
    schema: str
    policy_id: str
    project_id: str
    plan_digest: str
    changed_files: int
    already_applied_files: int
    removed_directories: int
    logical_bytes_reclaimed: int
    archive_objects_created: int
    archive_objects_reused: int
    archive_stored_bytes: int
    completed_at: str
    report_digest: str

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("report_digest")
        return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_READ_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_file_identity(path: Path) -> tuple[os.stat_result, str]:
    before = path.stat(follow_symlinks=False)
    digest = _sha256_file(path)
    after = path.stat(follow_symlinks=False)
    if (
        int(before.st_size) != int(after.st_size)
        or int(before.st_mtime_ns) != int(after.st_mtime_ns)
    ):
        raise BackupRetentionError("backup source changed during hashing")
    return after, digest


def _exact_directory(path: Path, *, label: str) -> Path:
    requested = Path(path)
    if not requested.is_absolute() or not requested.exists() or not requested.is_dir():
        raise BackupRetentionError(f"{label} must be an existing absolute directory")
    lexical = Path(os.path.abspath(requested))
    if str(lexical).startswith("\\\\"):
        raise BackupRetentionError(f"{label} must be on a local filesystem")
    if is_link_or_reparse(lexical):
        raise BackupRetentionError(f"{label} cannot be a link or reparse point")
    canonical = lexical.resolve(strict=True)
    if os.path.normcase(str(canonical)) != os.path.normcase(str(lexical)):
        raise BackupRetentionError(f"{label} changed through path resolution")
    return canonical


def _validate_roots(backup_root: Path, archive_root: Path) -> tuple[Path, Path]:
    backup = _exact_directory(backup_root, label="backup root")
    archive = _exact_directory(archive_root, label="archive root")
    if backup == archive:
        raise BackupRetentionError("backup and archive roots must differ")
    for child, parent, message in (
        (archive, backup, "archive root cannot be inside backup root"),
        (backup, archive, "backup root cannot be inside archive root"),
    ):
        try:
            child.relative_to(parent)
        except ValueError:
            continue
        raise BackupRetentionError(message)
    return backup, archive


def _safe_archive_directory(root: Path, target: Path, *, create: bool) -> Path:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise BackupRetentionError("archive control path escapes its root") from exc
    current = root
    for part in relative.parts:
        current /= part
        if os.path.lexists(current):
            if not current.is_dir() or is_link_or_reparse(current):
                raise BackupRetentionError("archive control path is unsafe")
        elif create:
            current.mkdir()
        else:
            raise BackupRetentionError("archive control path is missing")
        if (
            current.resolve(strict=True) != current
            or filesystem_identity(current) != filesystem_identity(root)
        ):
            raise BackupRetentionError("archive control path identity changed")
    return current


def _safe_archive_file(root: Path, target: Path, *, label: str) -> Path:
    _safe_archive_directory(root, target.parent, create=False)
    if not target.is_file() or is_link_or_reparse(target):
        raise BackupRetentionError(f"{label} is unsafe")
    if (
        target.resolve(strict=True) != target
        or filesystem_identity(target) != filesystem_identity(root)
    ):
        raise BackupRetentionError(f"{label} identity changed")
    return target


def _safe_relative(value: str) -> PurePosixPath:
    if not value or "\\" in value:
        raise BackupRetentionError("backup source reference is not canonical")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise BackupRetentionError("backup source reference is unsafe")
    if len(relative.parts) < 2:
        raise BackupRetentionError("backup source reference lacks a generation")
    try:
        reject_sensitive_data({"source_ref": value})
    except Exception as exc:  # noqa: BLE001 - fail closed without echoing the value
        raise BackupRetentionError("backup source reference is sensitive") from exc
    return relative


def _safe_source_path(root: Path, source_ref: str, *, allow_missing: bool) -> Path:
    relative = _safe_relative(source_ref)
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current /= part
        if not os.path.lexists(current):
            if allow_missing:
                return candidate
            raise BackupRetentionError("planned backup source is missing")
        if is_link_or_reparse(current):
            raise BackupRetentionError("backup source path contains a link or reparse point")
    info = candidate.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise BackupRetentionError("backup source is not a regular file")
    if candidate.resolve(strict=True) != candidate:
        raise BackupRetentionError("backup source canonical path changed")
    if filesystem_identity(candidate) != filesystem_identity(root):
        raise BackupRetentionError("backup source changed filesystem")
    return candidate


def _generation_class(name: str, relative_refs: Iterable[str]) -> str:
    lowered = name.lower()
    refs = tuple(value.lower() for value in relative_refs)
    if lowered.startswith(("phase0-pre", "phase0-post")):
        return "pre_cutover_baseline"
    if "hardfail" in lowered or any("post-hard-fail" in value for value in refs):
        return "incident_evidence"
    if lowered.startswith("canary-"):
        return "canary_evidence"
    return "other_backup_evidence"


def _is_complete_generation(relative_refs: Iterable[str]) -> bool:
    roots = {PurePosixPath(value).parts[1] for value in relative_refs}
    return {"raw", "logical", "restore"}.issubset(roots)


def _iter_generation_files(root: Path) -> dict[str, list[Path]]:
    generations: dict[str, list[Path]] = {}
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if is_link_or_reparse(child):
            raise BackupRetentionError("backup root contains a link or reparse point")
        if not child.is_dir():
            raise BackupRetentionError("backup root contains an unmanaged root file")
        files: list[Path] = []
        for path in child.rglob("*"):
            if is_link_or_reparse(path):
                raise BackupRetentionError("backup generation contains a link or reparse point")
            if path.is_file():
                files.append(path)
            elif not path.is_dir():
                raise BackupRetentionError("backup generation contains an unsupported entry")
        generations[child.name] = sorted(files, key=lambda item: item.as_posix())
    if not generations:
        raise BackupRetentionError("backup root has no generations")
    return generations


def build_retention_plan(
    backup_root: Path,
    archive_root: Path,
    *,
    retain_generation: str,
    retained_generation_evidence_sha256: str,
    max_backup_bytes: int = DEFAULT_MAX_BACKUP_BYTES,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    created_at: str | None = None,
) -> BackupRetentionPlan:
    """Hash every backup file and build a deterministic, non-mutating plan."""
    if not re.fullmatch(r"[a-f0-9]{64}", retained_generation_evidence_sha256):
        raise BackupRetentionError("retained generation evidence digest is invalid")
    if max_backup_bytes <= 0 or min_free_bytes <= 0:
        raise BackupRetentionError("storage budgets must be positive")
    backup, archive = _validate_roots(backup_root, archive_root)
    generation_paths = _iter_generation_files(backup)
    files: list[BackupFile] = []
    rows: list[dict[str, Any]] = []
    for generation, paths in generation_paths.items():
        refs: list[str] = []
        logical_bytes = 0
        newest_mtime_ns = 0
        for path in paths:
            relative = path.relative_to(backup).as_posix()
            _safe_relative(relative)
            info, source_sha256 = _stable_file_identity(path)
            refs.append(relative)
            logical_bytes += int(info.st_size)
            newest_mtime_ns = max(newest_mtime_ns, int(info.st_mtime_ns))
            files.append(
                BackupFile(
                    source_ref=relative,
                    generation=generation,
                    logical_bytes=int(info.st_size),
                    mtime_ns=int(info.st_mtime_ns),
                    source_sha256=source_sha256,
                )
            )
        rows.append(
            {
                "generation": generation,
                "evidence_class": _generation_class(generation, refs),
                "complete_generation": _is_complete_generation(refs),
                "newest_mtime_ns": newest_mtime_ns,
                "logical_bytes": logical_bytes,
                "file_count": len(paths),
            }
        )
    retained_rows = [row for row in rows if row["generation"] == retain_generation]
    if len(retained_rows) != 1 or not retained_rows[0]["complete_generation"]:
        raise BackupRetentionError("retained generation is absent or incomplete")
    retained = retain_generation
    generations = tuple(
        BackupGeneration(
            **row,
            disposition="retain_unpacked" if row["generation"] == retained else "archive_then_remove",
        )
        for row in sorted(rows, key=lambda item: str(item["generation"]))
    )
    sorted_files = tuple(sorted(files, key=lambda item: item.source_ref))
    archive_files = [item for item in sorted_files if item.generation != retained]
    first_by_digest: dict[str, BackupFile] = {}
    for item in archive_files:
        first_by_digest.setdefault(item.source_sha256, item)
    payload: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "policy_id": POLICY_ID,
        "project_id": PROJECT_ID,
        "backup_root": str(backup),
        "archive_root": str(archive),
        "backup_filesystem": filesystem_identity(backup),
        "archive_filesystem": filesystem_identity(archive),
        "created_at": created_at or _utc_now(),
        "retain_unpacked_generations": [retained],
        "retained_generation_evidence_sha256": retained_generation_evidence_sha256,
        "archive_remove_generations": sorted(row["generation"] for row in rows if row["generation"] != retained),
        "generations": [asdict(item) for item in generations],
        "files": [asdict(item) for item in sorted_files],
        "source_logical_bytes": sum(item.logical_bytes for item in sorted_files),
        "retained_logical_bytes": sum(
            item.logical_bytes for item in sorted_files if item.generation == retained
        ),
        "reclaim_candidate_bytes": sum(item.logical_bytes for item in archive_files),
        "unique_archive_logical_bytes": sum(item.logical_bytes for item in first_by_digest.values()),
        "max_backup_bytes": int(max_backup_bytes),
        "min_free_bytes": int(min_free_bytes),
    }
    return BackupRetentionPlan(
        **{
            **payload,
            "retain_unpacked_generations": tuple(payload["retain_unpacked_generations"]),
            "archive_remove_generations": tuple(payload["archive_remove_generations"]),
            "generations": generations,
            "files": sorted_files,
        },
        plan_digest=content_digest(payload),
    )


def _atomic_json(value: Mapping[str, Any], path: Path, *, replace: bool) -> None:
    payload = (canonical_json(dict(value)) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError:
                if path.read_bytes() != payload:
                    raise BackupRetentionError("immutable archive metadata differs")
    finally:
        temporary.unlink(missing_ok=True)


def write_plan(plan: BackupRetentionPlan, path: Path) -> None:
    _atomic_json({**plan.payload(), "plan_digest": plan.plan_digest}, Path(path), replace=True)


def _plan_from_mapping(value: Mapping[str, Any]) -> BackupRetentionPlan:
    if set(value) != set(BackupRetentionPlan.__dataclass_fields__):
        raise BackupRetentionError("cleanup plan key set is invalid")
    payload = dict(value)
    digest = str(payload.pop("plan_digest", ""))
    if digest != content_digest(payload):
        raise BackupRetentionError("cleanup plan digest mismatch")
    try:
        generations = tuple(BackupGeneration(**item) for item in payload["generations"])
        files = tuple(BackupFile(**item) for item in payload["files"])
        plan = BackupRetentionPlan(
            **{
                **payload,
                "retain_unpacked_generations": tuple(payload["retain_unpacked_generations"]),
                "archive_remove_generations": tuple(payload["archive_remove_generations"]),
                "generations": generations,
                "files": files,
            },
            plan_digest=digest,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupRetentionError("cleanup plan shape is invalid") from exc
    if (
        plan.schema != PLAN_SCHEMA
        or plan.policy_id != POLICY_ID
        or plan.project_id != PROJECT_ID
        or not isinstance(plan.backup_root, str)
        or not isinstance(plan.archive_root, str)
        or not isinstance(plan.backup_filesystem, dict)
        or not isinstance(plan.archive_filesystem, dict)
        or not isinstance(plan.created_at, str)
        or not all(isinstance(item, str) and item for item in plan.retain_unpacked_generations)
        or not all(isinstance(item, str) and item for item in plan.archive_remove_generations)
        or len(plan.retain_unpacked_generations) != 1
        or not isinstance(plan.retained_generation_evidence_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", plan.retained_generation_evidence_sha256)
        or set(plan.retain_unpacked_generations) & set(plan.archive_remove_generations)
    ):
        raise BackupRetentionError("cleanup plan policy is invalid")
    if not all(
        isinstance(item.generation, str)
        and item.generation
        and isinstance(item.evidence_class, str)
        and isinstance(item.complete_generation, bool)
        and isinstance(item.newest_mtime_ns, int)
        and not isinstance(item.newest_mtime_ns, bool)
        and item.newest_mtime_ns >= 0
        and isinstance(item.logical_bytes, int)
        and not isinstance(item.logical_bytes, bool)
        and item.logical_bytes >= 0
        and isinstance(item.file_count, int)
        and not isinstance(item.file_count, bool)
        and item.file_count >= 0
        and isinstance(item.disposition, str)
        for item in plan.generations
    ):
        raise BackupRetentionError("cleanup plan generation entry is invalid")
    generation_names = [item.generation for item in plan.generations]
    if (
        len(generation_names) != len(set(generation_names))
        or set(generation_names)
        != set(plan.retain_unpacked_generations) | set(plan.archive_remove_generations)
    ):
        raise BackupRetentionError("cleanup plan generation partition is invalid")
    if tuple(sorted(plan.files, key=lambda item: item.source_ref)) != plan.files:
        raise BackupRetentionError("cleanup plan files are not canonical")
    if len({item.source_ref for item in plan.files}) != len(plan.files):
        raise BackupRetentionError("cleanup plan source references are duplicated")
    for item in plan.files:
        if not isinstance(item.source_ref, str) or not isinstance(item.generation, str):
            raise BackupRetentionError("cleanup plan file entry is invalid")
        relative = _safe_relative(item.source_ref)
        if (
            relative.parts[0] != item.generation
            or not re.fullmatch(r"[a-f0-9]{64}", item.source_sha256)
            or isinstance(item.logical_bytes, bool)
            or not isinstance(item.logical_bytes, int)
            or item.logical_bytes < 0
            or isinstance(item.mtime_ns, bool)
            or not isinstance(item.mtime_ns, int)
            or item.mtime_ns < 0
        ):
            raise BackupRetentionError("cleanup plan file entry is invalid")
    by_generation = {
        generation: tuple(item for item in plan.files if item.generation == generation)
        for generation in generation_names
    }
    for generation in plan.generations:
        items = by_generation[generation.generation]
        expected_disposition = (
            "retain_unpacked"
            if generation.generation in plan.retain_unpacked_generations
            else "archive_then_remove"
        )
        if (
            generation.disposition != expected_disposition
            or generation.file_count != len(items)
            or generation.logical_bytes != sum(item.logical_bytes for item in items)
            or generation.newest_mtime_ns
            != max((item.mtime_ns for item in items), default=0)
            or generation.complete_generation
            != _is_complete_generation(item.source_ref for item in items)
        ):
            raise BackupRetentionError("cleanup plan generation aggregate is invalid")
    archive_files = tuple(
        item for item in plan.files if item.generation in plan.archive_remove_generations
    )
    unique_archive: dict[str, BackupFile] = {}
    for item in archive_files:
        unique_archive.setdefault(item.source_sha256, item)
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in (plan.max_backup_bytes, plan.min_free_bytes)
        )
        or plan.source_logical_bytes != sum(item.logical_bytes for item in plan.files)
        or plan.retained_logical_bytes
        != sum(
            item.logical_bytes
            for item in plan.files
            if item.generation in plan.retain_unpacked_generations
        )
        or plan.reclaim_candidate_bytes != sum(item.logical_bytes for item in archive_files)
        or plan.unique_archive_logical_bytes
        != sum(item.logical_bytes for item in unique_archive.values())
    ):
        raise BackupRetentionError("cleanup plan byte aggregate is invalid")
    return plan


def load_plan(path: Path) -> BackupRetentionPlan:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise BackupRetentionError("cleanup plan is unreadable") from exc
    if not isinstance(value, Mapping):
        raise BackupRetentionError("cleanup plan must be an object")
    return _plan_from_mapping(value)


def _validate_authority(
    authority: BackupRetentionAuthority,
    plan: BackupRetentionPlan,
    *,
    now: float,
) -> None:
    expected = {
        "schema": AUTHORITY_SCHEMA,
        "project_id": PROJECT_ID,
        "action": "repair_and_apply_e_drive_storage_lifecycle",
        "backup_root": plan.backup_root,
        "archive_root": plan.archive_root,
        "expected_plan_digest": plan.plan_digest,
    }
    if any(getattr(authority, field) != expected_value for field, expected_value in expected.items()):
        raise BackupRetentionError("retention authority scope mismatch")
    if (
        not isinstance(authority.turn_id, str)
        or not authority.turn_id
        or not isinstance(authority.authority_id, str)
        or not _AUTHORITY_ID.fullmatch(authority.authority_id)
        or isinstance(authority.issued_at, bool)
        or not isinstance(authority.issued_at, (int, float))
        or isinstance(authority.expires_at, bool)
        or not isinstance(authority.expires_at, (int, float))
        or not math.isfinite(authority.issued_at)
        or not math.isfinite(authority.expires_at)
    ):
        raise BackupRetentionError("retention authority identity is invalid")
    if not authority.issued_at <= now < authority.expires_at:
        raise BackupRetentionError("retention authority is not currently valid")


def load_authority(
    path: Path,
    plan: BackupRetentionPlan,
    *,
    now: float | None = None,
) -> BackupRetentionAuthority:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise BackupRetentionError("retention authority is unreadable") from exc
    if not isinstance(value, dict) or set(value) != set(BackupRetentionAuthority.__dataclass_fields__):
        raise BackupRetentionError("retention authority shape is invalid")
    try:
        authority = BackupRetentionAuthority(**value)
    except (TypeError, ValueError) as exc:
        raise BackupRetentionError("retention authority shape is invalid") from exc
    _validate_authority(authority, plan, now=time.time() if now is None else float(now))
    return authority


def _control_paths(archive_root: Path) -> dict[str, Path]:
    control = archive_root / CONTROL_DIR
    return {
        "control": control,
        "objects": control / "objects" / "sha256",
        "manifests": control / "manifests" / "files",
        "reports": control / "reports",
        "staging": control / "staging",
        "locks": control / "locks",
        "lock": control / "locks" / "operation.lock",
    }


def _ensure_control(archive_root: Path) -> dict[str, Path]:
    paths = _control_paths(archive_root)
    for key in ("control", "objects", "manifests", "reports", "staging", "locks"):
        _safe_archive_directory(archive_root, paths[key], create=True)
    lock = paths["lock"]
    if lock.exists():
        if not lock.is_file() or is_link_or_reparse(lock):
            raise BackupRetentionError("archive operation lock is unsafe")
    else:
        lock.write_bytes(b"0")
    return paths


def _object_ref(source_sha256: str) -> str:
    return f"{CONTROL_DIR}/objects/sha256/{source_sha256[:2]}/{source_sha256}.gz"


def _file_manifest_ref(source_ref: str, source_sha256: str) -> str:
    leaf = hashlib.sha256(f"{source_ref}\0{source_sha256}".encode()).hexdigest()
    return f"{CONTROL_DIR}/manifests/files/{leaf[:2]}/{leaf}.json"


def _verify_gzip_object(
    path: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    archive_root: Path | None = None,
) -> int:
    if archive_root is not None:
        _safe_archive_file(archive_root, path, label="archive object")
    digest = hashlib.sha256()
    restored_bytes = 0
    try:
        with gzip.open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(_READ_SIZE), b""):
                restored_bytes += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise BackupRetentionError("archive restore verification failed") from exc
    if restored_bytes != expected_bytes or digest.hexdigest() != expected_sha256:
        raise BackupRetentionError("archive restore verification failed")
    return int(path.stat().st_size)


def _ensure_object(
    source: Path,
    item: BackupFile,
    archive_root: Path,
    staging_root: Path,
) -> tuple[Path, bool, int]:
    target = archive_root / _object_ref(item.source_sha256)
    if target.exists():
        _safe_archive_file(archive_root, target, label="archive object")
        return (
            target,
            False,
            _verify_gzip_object(
                target,
                expected_sha256=item.source_sha256,
                expected_bytes=item.logical_bytes,
                archive_root=archive_root,
            ),
        )
    _safe_archive_directory(archive_root, target.parent, create=True)
    temporary = staging_root / f"object-{uuid.uuid4().hex}.tmp"
    try:
        with source.open("rb") as source_stream, temporary.open("xb") as raw_output:
            with gzip.GzipFile(fileobj=raw_output, mode="wb", filename="", mtime=0) as output:
                shutil.copyfileobj(source_stream, output, length=_READ_SIZE)
            raw_output.flush()
            os.fsync(raw_output.fileno())
        stored = _verify_gzip_object(
            temporary,
            expected_sha256=item.source_sha256,
            expected_bytes=item.logical_bytes,
        )
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            return (
                target,
                False,
                _verify_gzip_object(
                    target,
                    expected_sha256=item.source_sha256,
                    expected_bytes=item.logical_bytes,
                    archive_root=archive_root,
                ),
            )
        return target, True, stored
    finally:
        temporary.unlink(missing_ok=True)


def _file_manifest_payload(
    item: BackupFile,
    *,
    object_ref: str,
    stored_bytes: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": FILE_MANIFEST_SCHEMA,
        "policy_id": POLICY_ID,
        "project_id": PROJECT_ID,
        "generation": item.generation,
        "source_ref": item.source_ref,
        "source_sha256": item.source_sha256,
        "logical_bytes": item.logical_bytes,
        "object_ref": object_ref,
        "stored_bytes": stored_bytes,
        "copy_verified": True,
        "restore_verified": True,
    }
    return {**payload, "manifest_digest": content_digest(payload)}


def _ensure_file_manifest(
    item: BackupFile,
    object_path: Path,
    archive_root: Path,
) -> Path:
    target = archive_root / _file_manifest_ref(item.source_ref, item.source_sha256)
    value = _file_manifest_payload(
        item,
        object_ref=object_path.relative_to(archive_root).as_posix(),
        stored_bytes=int(object_path.stat().st_size),
    )
    _safe_archive_directory(archive_root, target.parent, create=True)
    _atomic_json(value, target, replace=False)
    return target


def _load_file_manifest(item: BackupFile, archive_root: Path) -> Mapping[str, Any] | None:
    path = archive_root / _file_manifest_ref(item.source_ref, item.source_sha256)
    if not path.exists():
        return None
    _safe_archive_file(archive_root, path, label="archive file manifest")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise BackupRetentionError("archive file manifest is unreadable") from exc
    if not isinstance(value, dict):
        raise BackupRetentionError("archive file manifest shape is invalid")
    digest = str(value.get("manifest_digest", ""))
    payload = {key: nested for key, nested in value.items() if key != "manifest_digest"}
    expected_fixed = {
        "schema": FILE_MANIFEST_SCHEMA,
        "policy_id": POLICY_ID,
        "project_id": PROJECT_ID,
        "generation": item.generation,
        "source_ref": item.source_ref,
        "source_sha256": item.source_sha256,
        "logical_bytes": item.logical_bytes,
        "object_ref": _object_ref(item.source_sha256),
        "copy_verified": True,
        "restore_verified": True,
    }
    if (
        any(payload.get(key) != expected for key, expected in expected_fixed.items())
        or not isinstance(payload.get("stored_bytes"), int)
        or int(payload["stored_bytes"]) < 0
        or digest != content_digest(payload)
    ):
        raise BackupRetentionError("archive file manifest verification failed")
    return value


def _verify_plan_roots(plan: BackupRetentionPlan) -> tuple[Path, Path]:
    backup, archive = _validate_roots(Path(plan.backup_root), Path(plan.archive_root))
    if (
        filesystem_identity(backup) != plan.backup_filesystem
        or filesystem_identity(archive) != plan.archive_filesystem
    ):
        raise BackupRetentionError("cleanup plan root identity changed")
    return backup, archive


def _verify_plan_inventory(
    plan: BackupRetentionPlan,
    backup_root: Path,
    archive_root: Path,
) -> None:
    planned = {item.source_ref: item for item in plan.files}
    current = {
        path.relative_to(backup_root).as_posix()
        for paths in _iter_generation_files(backup_root).values()
        for path in paths
    }
    if current - set(planned):
        raise BackupRetentionError("backup root gained an unplanned file")
    for source_ref, item in planned.items():
        path = _safe_source_path(backup_root, source_ref, allow_missing=True)
        if path.exists():
            info = path.stat(follow_symlinks=False)
            if int(info.st_size) != item.logical_bytes or int(info.st_mtime_ns) != item.mtime_ns:
                raise BackupRetentionError("planned backup source metadata changed")
            stable_info, source_sha256 = _stable_file_identity(path)
            if (
                int(stable_info.st_size) != item.logical_bytes
                or int(stable_info.st_mtime_ns) != item.mtime_ns
            ):
                raise BackupRetentionError("planned backup source metadata changed")
            if source_sha256 != item.source_sha256:
                raise BackupRetentionError("planned backup source content changed")
        elif item.generation in plan.retain_unpacked_generations:
            raise BackupRetentionError("retained generation is incomplete")
        elif _load_file_manifest(item, archive_root) is None:
            raise BackupRetentionError("planned source vanished without archive proof")


def _verify_capacity(
    plan: BackupRetentionPlan,
    backup_root: Path,
    archive_root: Path,
) -> None:
    # A gzip member is written and restore-verified before its exact source is
    # unlinked. Model that sequential peak rather than reserving the sum of all
    # objects, which would incorrectly reject a same-volume reclamation run.
    # This zlib-style upper bound includes wrapper/trailer and small-file slack.
    def gzip_upper_bound(logical_bytes: int) -> int:
        return (
            logical_bytes
            + (logical_bytes >> 12)
            + (logical_bytes >> 14)
            + (logical_bytes >> 25)
            + 64
        )

    available_delta = 0
    peak_new_bytes = 0
    available_objects = {
        item.source_sha256
        for item in plan.files
        if (archive_root / _object_ref(item.source_sha256)).exists()
    }
    for item in plan.files:
        if item.generation not in plan.archive_remove_generations:
            continue
        source_exists = _safe_source_path(
            backup_root,
            item.source_ref,
            allow_missing=True,
        ).exists()
        if item.source_sha256 in available_objects:
            if source_exists:
                available_delta += item.logical_bytes
            continue
        if not source_exists:
            raise BackupRetentionError("missing source lacks an archive object")
        object_bound = gzip_upper_bound(item.logical_bytes)
        peak_new_bytes = max(peak_new_bytes, object_bound - available_delta)
        available_delta += item.logical_bytes - object_bound
        available_objects.add(item.source_sha256)
    if shutil.disk_usage(archive_root).free - peak_new_bytes < plan.min_free_bytes:
        raise BackupRetentionError("archive filesystem lacks conservative free-space budget")


def _remove_empty_generation_directories(root: Path, generations: Iterable[str]) -> int:
    removed = 0
    for generation in sorted(set(generations)):
        base = root / generation
        if not base.exists():
            continue
        directories = [candidate for candidate in base.rglob("*") if candidate.is_dir()]
        for path in sorted(directories, key=lambda candidate: len(candidate.parts), reverse=True):
            if not any(path.iterdir()):
                path.rmdir()
                removed += 1
        if base.exists() and not any(base.iterdir()):
            base.rmdir()
            removed += 1
    return removed


def apply_retention_plan(
    plan: BackupRetentionPlan,
    authority: BackupRetentionAuthority,
    *,
    expected_plan_digest: str,
    fail_after_files: int | None = None,
) -> BackupRetentionApplyReport:
    """Archive and remove exact plan files; safe to resume after interruption."""
    if expected_plan_digest != plan.plan_digest:
        raise BackupRetentionError("expected cleanup plan digest mismatch")
    _validate_authority(authority, plan, now=time.time())
    backup, archive = _verify_plan_roots(plan)
    _verify_plan_inventory(plan, backup, archive)
    _verify_capacity(plan, backup, archive)
    paths = _ensure_control(archive)
    try:
        with storage_root_lock(paths["lock"]):
            _verify_plan_inventory(plan, backup, archive)
            _verify_capacity(plan, backup, archive)
            changed = already = created = reused = reclaimed = 0
            stored_by_digest: dict[str, int] = {}
            candidates = [
                item for item in plan.files if item.generation in plan.archive_remove_generations
            ]
            for item in candidates:
                source = _safe_source_path(backup, item.source_ref, allow_missing=True)
                if not source.exists():
                    if _load_file_manifest(item, archive) is None:
                        raise BackupRetentionError("missing source lacks archive proof")
                    stored_by_digest[item.source_sha256] = _verify_gzip_object(
                        archive / _object_ref(item.source_sha256),
                        expected_sha256=item.source_sha256,
                        expected_bytes=item.logical_bytes,
                        archive_root=archive,
                    )
                    already += 1
                    continue
                object_path, was_created, stored = _ensure_object(
                    source, item, archive, paths["staging"]
                )
                stored_by_digest[item.source_sha256] = stored
                created += int(was_created)
                reused += int(not was_created)
                _ensure_file_manifest(item, object_path, archive)
                source.unlink()
                changed += 1
                reclaimed += item.logical_bytes
                if fail_after_files is not None and changed >= fail_after_files:
                    raise BackupRetentionError("synthetic interruption after verified removal")
            removed_directories = _remove_empty_generation_directories(
                backup, plan.archive_remove_generations
            )
            report_payload: dict[str, Any] = {
                "schema": APPLY_REPORT_SCHEMA,
                "policy_id": POLICY_ID,
                "project_id": PROJECT_ID,
                "plan_digest": plan.plan_digest,
                "changed_files": changed,
                "already_applied_files": already,
                "removed_directories": removed_directories,
                "logical_bytes_reclaimed": reclaimed,
                "archive_objects_created": created,
                "archive_objects_reused": reused,
                "archive_stored_bytes": sum(stored_by_digest.values()),
                "completed_at": _utc_now(),
            }
            report = BackupRetentionApplyReport(
                **report_payload,
                report_digest=content_digest(report_payload),
            )
            if changed or removed_directories:
                report_path = paths["reports"] / f"{plan.plan_digest.split(':', 1)[1]}.json"
                _atomic_json(
                    {**report.payload(), "report_digest": report.report_digest},
                    report_path,
                    replace=True,
                )
            return report
    except StorageLockConflict as exc:
        raise BackupRetentionError("backup retention lock is unavailable") from exc


def verify_archive(plan: BackupRetentionPlan) -> dict[str, int | str]:
    """Restore-verify every archived object and immutable source manifest."""
    _, archive = _verify_plan_roots(plan)
    verified_files = 0
    verified_objects: set[str] = set()
    logical_bytes = 0
    for item in plan.files:
        if item.generation not in plan.archive_remove_generations:
            continue
        if _load_file_manifest(item, archive) is None:
            raise BackupRetentionError("archive file manifest is missing")
        if item.source_sha256 not in verified_objects:
            _verify_gzip_object(
                archive / _object_ref(item.source_sha256),
                expected_sha256=item.source_sha256,
                expected_bytes=item.logical_bytes,
                archive_root=archive,
            )
            verified_objects.add(item.source_sha256)
            logical_bytes += item.logical_bytes
        verified_files += 1
    return {
        "status": "ok",
        "verified_files": verified_files,
        "verified_objects": len(verified_objects),
        "unique_logical_bytes": logical_bytes,
    }


def storage_budget_status(
    backup_root: Path,
    *,
    max_backup_bytes: int = DEFAULT_MAX_BACKUP_BYTES,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
) -> dict[str, int | str | bool]:
    root = _exact_directory(backup_root, label="backup root")
    total = files = 0
    for path in root.rglob("*"):
        if is_link_or_reparse(path):
            raise BackupRetentionError("backup root contains a link or reparse point")
        if path.is_file():
            total += int(path.stat().st_size)
            files += 1
    free = int(shutil.disk_usage(root).free)
    within_budget = total <= max_backup_bytes and free >= min_free_bytes
    return {
        "schema": "BackupStorageBudgetStatus.v1",
        "status": "ok" if within_budget else "blocked",
        "within_budget": within_budget,
        "backup_bytes": total,
        "backup_files": files,
        "max_backup_bytes": int(max_backup_bytes),
        "free_bytes": free,
        "min_free_bytes": int(min_free_bytes),
    }
