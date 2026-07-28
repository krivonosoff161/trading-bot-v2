"""Immutable archive objects plus a rebuildable metadata/query catalog.

The canonical bytes are content-addressed objects and immutable JSON manifests.
SQLite is only a disposable index.  Payload retrieval is bounded and allowed
only for ``public_safe_derived`` artifacts; private archives remain metadata and
evidence pointers until a separate authority-bearing consumer is reviewed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Mapping
import uuid

from scripts.ci.check_supply_chain_policy import reject_sensitive_data
from src.research_lab.private_archive_capability import (
    CONTROL_DIR,
    PrivateArchiveCapability,
    PrivateArchiveCapabilityError,
    load_private_archive_capability,
)
from src.research_lab.storage_capability import canonical_json, is_link_or_reparse
from src.research_lab.storage_os_lock import StorageLockConflict, storage_root_lock


MANIFEST_SCHEMA = "ArchiveArtifactManifest.v1"
CATALOG_SCHEMA = "ArchiveCatalog.v1"
_SHA256 = re.compile(r"[a-f0-9]{64}")
_COMMIT_SHA = re.compile(r"[a-f0-9]{40}")
_ARTIFACT_ID = re.compile(r"artifact_[a-f0-9]{32}")
_SAFE_CONTOUR = re.compile(r"[a-z][a-z0-9_]{1,63}")
_SAFE_SCHEMA = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{1,95}")
_STREAM_ID = re.compile(r"[a-z][a-z0-9_.]{2,95}")
_MAX_RECORDS = 1_000
_MAX_BYTES = 16 * 1024 * 1024


class ArchiveCatalogError(RuntimeError):
    """Archive metadata, immutable bytes, or bounded retrieval are invalid."""


@dataclass(frozen=True)
class ArchiveArtifactManifest:
    schema: str
    artifact_id: str
    project_id: str
    stream_id: str
    kind: str
    contour: str
    payload_schema: str
    source_revision: str
    source_ref: str
    source_sha256: str
    object_ref: str
    object_sha256: str
    logical_bytes: int
    stored_bytes: int
    record_count: int
    first_observed_at: str
    last_observed_at: str
    sensitivity: str
    load_policy: str
    created_at: str
    copy_verified: bool
    restore_verified: bool
    manifest_sha256: str

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("manifest_sha256")
        return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_gzip_restore(
    path: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    expected_records: int,
) -> None:
    digest = hashlib.sha256()
    logical_bytes = 0
    record_count = 0
    try:
        with gzip.open(path, "rb") as stream:
            for line in stream:
                logical_bytes += len(line)
                digest.update(line)
                if line.strip():
                    record_count += 1
    except OSError as exc:
        raise ArchiveCatalogError("archive restore verification failed") from exc
    if (
        digest.hexdigest() != expected_sha256
        or logical_bytes != expected_bytes
        or record_count != expected_records
    ):
        raise ArchiveCatalogError("archive restore verification failed")


def _safe_relative(root: Path, path: Path, *, label: str) -> str:
    requested = Path(os.path.abspath(path))
    if not requested.exists() or not requested.is_file():
        raise ArchiveCatalogError(f"{label} must be an existing regular file")
    if is_link_or_reparse(requested):
        raise ArchiveCatalogError(f"{label} cannot be a link or reparse point")
    canonical = requested.resolve(strict=True)
    if os.path.normcase(str(canonical)) != os.path.normcase(str(requested)):
        raise ArchiveCatalogError(f"{label} changed through path resolution")
    try:
        relative = canonical.relative_to(root).as_posix()
    except ValueError as exc:
        raise ArchiveCatalogError(f"{label} is outside its bound root") from exc
    reject_sensitive_data({"source_ref": relative})
    return relative


def _atomic_no_replace(path: Path, payload: bytes, staging_root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the staging leaf short enough for default Windows path limits even
    # when the immutable target name carries both artifact and manifest hashes.
    staging = staging_root / f"manifest-{uuid.uuid4().hex}.tmp"
    with staging.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(staging, path, follow_symlinks=False)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ArchiveCatalogError("immutable archive target already differs")
    finally:
        staging.unlink(missing_ok=True)


def _manifest_from_dict(value: Mapping[str, Any]) -> ArchiveArtifactManifest:
    expected = set(ArchiveArtifactManifest.__dataclass_fields__)
    if set(value) != expected:
        raise ArchiveCatalogError("archive manifest key set is invalid")
    try:
        manifest = ArchiveArtifactManifest(**dict(value))
    except (TypeError, ValueError) as exc:
        raise ArchiveCatalogError("archive manifest shape is invalid") from exc
    text_fields = (
        manifest.schema,
        manifest.artifact_id,
        manifest.project_id,
        manifest.stream_id,
        manifest.kind,
        manifest.contour,
        manifest.payload_schema,
        manifest.source_revision,
        manifest.source_ref,
        manifest.source_sha256,
        manifest.object_ref,
        manifest.object_sha256,
        manifest.first_observed_at,
        manifest.last_observed_at,
        manifest.sensitivity,
        manifest.load_policy,
        manifest.created_at,
        manifest.manifest_sha256,
    )
    if not all(isinstance(item, str) for item in text_fields):
        raise ArchiveCatalogError("archive manifest text fields are invalid")
    if (
        not isinstance(manifest.logical_bytes, int)
        or isinstance(manifest.logical_bytes, bool)
        or not isinstance(manifest.stored_bytes, int)
        or isinstance(manifest.stored_bytes, bool)
        or not isinstance(manifest.record_count, int)
        or isinstance(manifest.record_count, bool)
        or not isinstance(manifest.copy_verified, bool)
        or not isinstance(manifest.restore_verified, bool)
    ):
        raise ArchiveCatalogError("archive manifest typed fields are invalid")
    if manifest.schema != MANIFEST_SCHEMA:
        raise ArchiveCatalogError("archive manifest schema mismatch")
    if not _ARTIFACT_ID.fullmatch(manifest.artifact_id):
        raise ArchiveCatalogError("archive artifact id is invalid")
    if manifest.project_id != "trading-bot-v2":
        raise ArchiveCatalogError("archive manifest project mismatch")
    if not _STREAM_ID.fullmatch(manifest.stream_id):
        raise ArchiveCatalogError("archive stream id is invalid")
    if not _SAFE_CONTOUR.fullmatch(manifest.contour):
        raise ArchiveCatalogError("archive contour is invalid")
    if not _SAFE_SCHEMA.fullmatch(manifest.payload_schema):
        raise ArchiveCatalogError("archive payload schema is invalid")
    if not _COMMIT_SHA.fullmatch(manifest.source_revision):
        raise ArchiveCatalogError("archive source revision is invalid")
    if not _SHA256.fullmatch(manifest.source_sha256) or not _SHA256.fullmatch(
        manifest.object_sha256
    ):
        raise ArchiveCatalogError("archive content digest is invalid")
    if manifest.logical_bytes < 0 or manifest.stored_bytes < 0 or manifest.record_count < 0:
        raise ArchiveCatalogError("archive counters cannot be negative")
    digest = hashlib.sha256(
        canonical_json(manifest.payload()).encode("utf-8")
    ).hexdigest()
    if digest != manifest.manifest_sha256:
        raise ArchiveCatalogError("archive manifest digest mismatch")
    reject_sensitive_data(manifest.payload())
    return manifest


class ArchiveCatalog:
    def __init__(self, root: Path) -> None:
        try:
            self.capability: PrivateArchiveCapability = (
                load_private_archive_capability(root)
            )
        except PrivateArchiveCapabilityError as exc:
            raise ArchiveCatalogError("private archive capability is invalid") from exc
        self.root = Path(self.capability.canonical_root)
        self.control = self.root / CONTROL_DIR
        self.manifest_root = self.control / "manifests"
        self.staging_root = self.control / "staging"
        self.object_root = self.root / "objects"
        self.lock_path = self.control / "locks" / "catalog.lock"
        self.index_path = self.control / "catalog.sqlite3"

    def _revalidate(self) -> None:
        current = load_private_archive_capability(self.root)
        if current != self.capability:
            raise ArchiveCatalogError("private archive capability changed")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.index_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS catalog_meta(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts(
              artifact_id TEXT PRIMARY KEY,
              stream_id TEXT NOT NULL,
              contour TEXT NOT NULL,
              kind TEXT NOT NULL,
              first_observed_at TEXT NOT NULL,
              last_observed_at TEXT NOT NULL,
              sensitivity TEXT NOT NULL,
              load_policy TEXT NOT NULL,
              manifest_sha256 TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_artifact_contour_time
              ON artifacts(contour,last_observed_at);
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO catalog_meta(key,value) VALUES('schema',?)",
            (CATALOG_SCHEMA,),
        )
        connection.execute(
            "INSERT OR REPLACE INTO catalog_meta(key,value) VALUES('capability_digest',?)",
            (self.capability.capability_digest,),
        )
        return connection

    def _manifest_path(self, manifest: ArchiveArtifactManifest) -> Path:
        return self.manifest_root / (
            f"{manifest.artifact_id}.{manifest.manifest_sha256}.json"
        )

    def _object_path(self, object_ref: str) -> Path:
        if "\\" in object_ref or not object_ref.startswith("objects/"):
            raise ArchiveCatalogError("archive object reference is invalid")
        relative = Path(*object_ref.split("/"))
        target = Path(os.path.abspath(self.root / relative))
        try:
            target.relative_to(self.object_root)
        except ValueError as exc:
            raise ArchiveCatalogError("archive object escapes object root") from exc
        if not target.exists() or not target.is_file() or is_link_or_reparse(target):
            raise ArchiveCatalogError("archive object is absent or unsafe")
        if os.path.normcase(str(target.resolve(strict=True))) != os.path.normcase(
            str(target)
        ):
            raise ArchiveCatalogError("archive object canonical path changed")
        return target

    def _validate_object(self, manifest: ArchiveArtifactManifest) -> Path:
        target = self._object_path(manifest.object_ref)
        if target.stat().st_size != manifest.stored_bytes:
            raise ArchiveCatalogError("archive object size mismatch")
        if _sha256_file(target) != manifest.object_sha256:
            raise ArchiveCatalogError("archive object digest mismatch")
        return target

    def register_jsonl(
        self,
        source: Path,
        *,
        stream_id: str,
        kind: str,
        contour: str,
        payload_schema: str,
        source_revision: str,
        sensitivity: str,
        first_observed_at: str,
        last_observed_at: str,
        created_at: str,
        restore_verified: bool = False,
    ) -> ArchiveArtifactManifest:
        """Copy one stable legacy JSONL file into a deterministic gzip object."""

        if kind not in self.capability.allowed_kinds:
            raise ArchiveCatalogError("archive kind is outside capability scope")
        if not _STREAM_ID.fullmatch(stream_id):
            raise ArchiveCatalogError("archive stream id is invalid")
        if sensitivity not in self.capability.allowed_sensitivity:
            raise ArchiveCatalogError("archive sensitivity is invalid")
        if kind == "project_brain_events" and sensitivity != "public_safe_derived":
            raise ArchiveCatalogError("Project Brain event archives must be public-safe")
        if not _SAFE_CONTOUR.fullmatch(contour):
            raise ArchiveCatalogError("archive contour is invalid")
        if not _SAFE_SCHEMA.fullmatch(payload_schema):
            raise ArchiveCatalogError("archive payload schema is invalid")
        if not _COMMIT_SHA.fullmatch(source_revision):
            raise ArchiveCatalogError("archive source revision is invalid")
        source_root = Path(self.capability.source_root)
        source_ref = _safe_relative(source_root, source, label="archive source")
        before = source.stat()
        raw_digest = hashlib.sha256()
        logical_bytes = 0
        record_count = 0
        staging = self.staging_root / f"archive-object-{uuid.uuid4().hex}.tmp"
        try:
            with source.open("rb") as incoming, staging.open("xb") as raw_output:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    fileobj=raw_output,
                    compresslevel=6,
                    mtime=0,
                ) as compressed:
                    for line in incoming:
                        logical_bytes += len(line)
                        raw_digest.update(line)
                        if line.strip():
                            record_count += 1
                            if sensitivity == "public_safe_derived":
                                try:
                                    value = json.loads(line)
                                    if not isinstance(value, dict):
                                        raise ValueError
                                    row_schema = str(
                                        value.get("event_schema")
                                        or value.get("schema")
                                        or ""
                                    )
                                    if row_schema != payload_schema:
                                        raise ValueError
                                    reject_sensitive_data(value)
                                except (ValueError, UnicodeError) as exc:
                                    raise ArchiveCatalogError(
                                        "public-safe archive source failed content validation"
                                    ) from exc
                        compressed.write(line)
                raw_output.flush()
                os.fsync(raw_output.fileno())
        except Exception:
            staging.unlink(missing_ok=True)
            raise
        after = source.stat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            staging.unlink(missing_ok=True)
            raise ArchiveCatalogError("archive source changed during copy")
        source_sha = raw_digest.hexdigest()
        object_sha = _sha256_file(staging)
        if restore_verified:
            try:
                _verify_gzip_restore(
                    staging,
                    expected_sha256=source_sha,
                    expected_bytes=logical_bytes,
                    expected_records=record_count,
                )
            except ArchiveCatalogError:
                staging.unlink(missing_ok=True)
                raise
        object_ref = f"objects/{object_sha[:2]}/{object_sha}.jsonl.gz"
        target = self.root / Path(*object_ref.split("/"))
        load_policy = (
            "bounded_verified_payload"
            if sensitivity == "public_safe_derived"
            else "metadata_only"
        )
        artifact_id = "artifact_" + hashlib.sha256(
            canonical_json(
                {
                    "project_id": self.capability.project_id,
                    "stream_id": stream_id,
                    "kind": kind,
                    "contour": contour,
                    "source_ref": source_ref,
                    "source_sha256": source_sha,
                    "payload_schema": payload_schema,
                }
            ).encode("utf-8")
        ).hexdigest()[:32]
        payload = {
            "schema": MANIFEST_SCHEMA,
            "artifact_id": artifact_id,
            "project_id": self.capability.project_id,
            "stream_id": stream_id,
            "kind": kind,
            "contour": contour,
            "payload_schema": payload_schema,
            "source_revision": source_revision,
            "source_ref": source_ref,
            "source_sha256": source_sha,
            "object_ref": object_ref,
            "object_sha256": object_sha,
            "logical_bytes": logical_bytes,
            "stored_bytes": staging.stat().st_size,
            "record_count": record_count,
            "first_observed_at": first_observed_at,
            "last_observed_at": last_observed_at,
            "sensitivity": sensitivity,
            "load_policy": load_policy,
            "created_at": created_at,
            "copy_verified": True,
            "restore_verified": bool(restore_verified),
        }
        reject_sensitive_data(payload)
        manifest_sha = hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()
        manifest = _manifest_from_dict(
            {**payload, "manifest_sha256": manifest_sha}
        )
        manifest_bytes = canonical_json(asdict(manifest)).encode("utf-8")
        try:
            with storage_root_lock(self.lock_path, wait_seconds=5.0):
                self._revalidate()
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(staging, target, follow_symlinks=False)
                except FileExistsError:
                    if _sha256_file(target) != object_sha:
                        raise ArchiveCatalogError(
                            "content-addressed archive object differs"
                        )
                finally:
                    staging.unlink(missing_ok=True)
                _atomic_no_replace(
                    self._manifest_path(manifest), manifest_bytes, self.staging_root
                )
                self._validate_object(manifest)
                self._index_manifest(manifest)
        except StorageLockConflict as exc:
            staging.unlink(missing_ok=True)
            raise ArchiveCatalogError("archive catalog lock is unavailable") from exc
        return manifest

    def _index_manifest(self, manifest: ArchiveArtifactManifest) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        manifest.artifact_id,
                        manifest.stream_id,
                        manifest.contour,
                        manifest.kind,
                        manifest.first_observed_at,
                        manifest.last_observed_at,
                        manifest.sensitivity,
                        manifest.load_policy,
                        manifest.manifest_sha256,
                        canonical_json(asdict(manifest)),
                    ),
                )
        finally:
            connection.close()

    def manifests(self) -> list[ArchiveArtifactManifest]:
        result: list[ArchiveArtifactManifest] = []
        for path in sorted(self.manifest_root.glob("*.json")):
            if is_link_or_reparse(path) or not path.is_file():
                raise ArchiveCatalogError("archive manifest path is unsafe")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError) as exc:
                raise ArchiveCatalogError("archive manifest is unreadable") from exc
            manifest = _manifest_from_dict(value)
            expected_load_policy = (
                "bounded_verified_payload"
                if manifest.sensitivity == "public_safe_derived"
                else "metadata_only"
            )
            if (
                manifest.kind not in self.capability.allowed_kinds
                or manifest.sensitivity not in self.capability.allowed_sensitivity
                or manifest.load_policy != expected_load_policy
                or not manifest.copy_verified
                or "\\" in manifest.source_ref
                or manifest.source_ref.startswith("/")
                or ".." in manifest.source_ref.split("/")
            ):
                raise ArchiveCatalogError("archive manifest exceeds capability policy")
            if path != self._manifest_path(manifest):
                raise ArchiveCatalogError("archive manifest filename mismatch")
            self._validate_object(manifest)
            result.append(manifest)
        return result

    def rebuild_index(self) -> int:
        manifests = self.manifests()
        try:
            with storage_root_lock(self.lock_path, wait_seconds=5.0):
                self._revalidate()
                for suffix in ("", "-wal", "-shm"):
                    Path(f"{self.index_path}{suffix}").unlink(missing_ok=True)
                for manifest in manifests:
                    self._index_manifest(manifest)
        except StorageLockConflict as exc:
            raise ArchiveCatalogError("archive catalog lock is unavailable") from exc
        return len(manifests)

    def query(
        self,
        *,
        contours: Iterable[str] = (),
        kinds: Iterable[str] = (),
        limit: int = 100,
    ) -> list[ArchiveArtifactManifest]:
        if limit <= 0 or limit > 1_000:
            raise ArchiveCatalogError("archive query limit is invalid")
        contour_set = set(contours)
        kind_set = set(kinds)
        rows = [
            manifest
            for manifest in self.manifests()
            if (not contour_set or manifest.contour in contour_set)
            and (not kind_set or manifest.kind in kind_set)
        ]
        return sorted(
            rows,
            key=lambda item: (item.last_observed_at, item.artifact_id),
            reverse=True,
        )[:limit]

    def read_bounded_jsonl(
        self,
        artifact_id: str,
        *,
        max_records: int,
        max_uncompressed_bytes: int,
    ) -> list[dict[str, Any]]:
        if not 0 < max_records <= _MAX_RECORDS:
            raise ArchiveCatalogError("archive record budget is invalid")
        if not 0 < max_uncompressed_bytes <= _MAX_BYTES:
            raise ArchiveCatalogError("archive byte budget is invalid")
        matches = [
            item for item in self.manifests() if item.artifact_id == artifact_id
        ]
        if len(matches) != 1:
            raise ArchiveCatalogError("archive artifact is absent or ambiguous")
        manifest = matches[0]
        if (
            manifest.sensitivity != "public_safe_derived"
            or manifest.load_policy != "bounded_verified_payload"
        ):
            raise ArchiveCatalogError("archive payload retrieval is not permitted")
        target = self._validate_object(manifest)
        records: list[dict[str, Any]] = []
        consumed = 0
        with gzip.open(target, "rb") as stream:
            for line in stream:
                consumed += len(line)
                if consumed > max_uncompressed_bytes:
                    raise ArchiveCatalogError("archive byte budget exceeded")
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeError) as exc:
                    raise ArchiveCatalogError("archive JSONL record is invalid") from exc
                if not isinstance(value, dict):
                    raise ArchiveCatalogError("archive JSONL record must be an object")
                row_schema = str(value.get("event_schema") or value.get("schema") or "")
                if row_schema != manifest.payload_schema:
                    raise ArchiveCatalogError("archive payload schema mismatch")
                try:
                    reject_sensitive_data(value)
                except ValueError as exc:
                    raise ArchiveCatalogError(
                        "archive payload failed the public-safe content gate"
                    ) from exc
                records.append(value)
                if len(records) >= max_records:
                    break
        return records
