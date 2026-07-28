"""Synthetic legacy-JSONL migration and reversible read-cutover evidence.

This module never discovers private paths and never changes a producer.  It
proves that an exact legacy source can be archived, verified, selected for
bounded reads, and rolled back without deleting either copy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from scripts.ci.check_supply_chain_policy import reject_sensitive_data
from src.research_lab.archive_catalog import (
    ArchiveArtifactManifest,
    ArchiveCatalog,
)
from src.research_lab.private_archive_capability import CONTROL_DIR
from src.research_lab.storage_capability import canonical_json, is_link_or_reparse
from src.research_lab.storage_os_lock import StorageLockConflict, storage_root_lock


PLAN_SCHEMA = "ArchiveMigrationPlan.v1"
CUTOVER_SCHEMA = "ArchiveReadCutoverEvent.v1"
_STREAM_ID = re.compile(r"[a-z][a-z0-9_.]{2,95}")
_REQUEST_ID = re.compile(r"req_[a-f0-9]{32}")
_PLAN_ID = re.compile(r"plan_[a-f0-9]{32}")
_SHA256 = re.compile(r"[a-f0-9]{64}")


class ArchiveMigrationError(RuntimeError):
    """The migration plan, parity proof, or reversible cutover is invalid."""


@dataclass(frozen=True)
class ArchiveMigrationPlan:
    schema: str
    plan_id: str
    project_id: str
    stream_id: str
    source_ref: str
    source_sha256: str
    source_bytes: int
    record_count: int
    kind: str
    contour: str
    payload_schema: str
    source_revision: str
    sensitivity: str
    first_observed_at: str
    last_observed_at: str
    created_at: str
    plan_sha256: str

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("plan_sha256")
        return value


def _hash_source(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    size = 0
    records = 0
    with path.open("rb") as stream:
        for line in stream:
            size += len(line)
            digest.update(line)
            if line.strip():
                records += 1
    return digest.hexdigest(), size, records


def _source_path(catalog: ArchiveCatalog, source_ref: str) -> Path:
    if "\\" in source_ref or source_ref.startswith("/") or ".." in source_ref.split("/"):
        raise ArchiveMigrationError("migration source reference is invalid")
    source_root = Path(catalog.capability.source_root)
    path = Path(os.path.abspath(source_root / Path(*source_ref.split("/"))))
    try:
        path.relative_to(source_root)
    except ValueError as exc:
        raise ArchiveMigrationError("migration source escapes bound root") from exc
    if not path.exists() or not path.is_file() or is_link_or_reparse(path):
        raise ArchiveMigrationError("migration source is absent or unsafe")
    if path.resolve(strict=True) != path:
        raise ArchiveMigrationError("migration source canonical path changed")
    return path


def build_migration_plan(
    catalog: ArchiveCatalog,
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
) -> ArchiveMigrationPlan:
    if not catalog.capability.synthetic:
        raise ArchiveMigrationError(
            "private migration requires a separately reviewed operational package"
        )
    if not _STREAM_ID.fullmatch(stream_id):
        raise ArchiveMigrationError("migration stream id is invalid")
    source_root = Path(catalog.capability.source_root)
    requested = Path(os.path.abspath(source))
    try:
        source_ref = requested.relative_to(source_root).as_posix()
    except ValueError as exc:
        raise ArchiveMigrationError("migration source is outside bound root") from exc
    reject_sensitive_data({"source_ref": source_ref})
    path = _source_path(catalog, source_ref)
    source_sha, source_bytes, record_count = _hash_source(path)
    payload = {
        "schema": PLAN_SCHEMA,
        "plan_id": "",
        "project_id": catalog.capability.project_id,
        "stream_id": stream_id,
        "source_ref": source_ref,
        "source_sha256": source_sha,
        "source_bytes": source_bytes,
        "record_count": record_count,
        "kind": kind,
        "contour": contour,
        "payload_schema": payload_schema,
        "source_revision": source_revision,
        "sensitivity": sensitivity,
        "first_observed_at": first_observed_at,
        "last_observed_at": last_observed_at,
        "created_at": created_at,
    }
    plan_id = "plan_" + hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()[:32]
    payload["plan_id"] = plan_id
    reject_sensitive_data(payload)
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return ArchiveMigrationPlan(
        schema=PLAN_SCHEMA,
        plan_id=plan_id,
        project_id=catalog.capability.project_id,
        stream_id=stream_id,
        source_ref=source_ref,
        source_sha256=source_sha,
        source_bytes=source_bytes,
        record_count=record_count,
        kind=kind,
        contour=contour,
        payload_schema=payload_schema,
        source_revision=source_revision,
        sensitivity=sensitivity,
        first_observed_at=first_observed_at,
        last_observed_at=last_observed_at,
        created_at=created_at,
        plan_sha256=digest,
    )


def validate_migration_plan(
    catalog: ArchiveCatalog, plan: ArchiveMigrationPlan
) -> Path:
    if plan.schema != PLAN_SCHEMA or plan.project_id != catalog.capability.project_id:
        raise ArchiveMigrationError("migration plan binding mismatch")
    if not catalog.capability.synthetic:
        raise ArchiveMigrationError(
            "private migration requires a separately reviewed operational package"
        )
    if not _PLAN_ID.fullmatch(plan.plan_id):
        raise ArchiveMigrationError("migration plan id is invalid")
    if not _STREAM_ID.fullmatch(plan.stream_id):
        raise ArchiveMigrationError("migration stream id is invalid")
    if not _SHA256.fullmatch(plan.source_sha256):
        raise ArchiveMigrationError("migration source digest is invalid")
    digest = hashlib.sha256(
        canonical_json(plan.payload()).encode("utf-8")
    ).hexdigest()
    if digest != plan.plan_sha256:
        raise ArchiveMigrationError("migration plan digest mismatch")
    expected_plan_id_payload = plan.payload()
    expected_plan_id_payload["plan_id"] = ""
    expected_plan_id = "plan_" + hashlib.sha256(
        canonical_json(expected_plan_id_payload).encode("utf-8")
    ).hexdigest()[:32]
    if plan.plan_id != expected_plan_id:
        raise ArchiveMigrationError("migration plan id mismatch")
    reject_sensitive_data(plan.payload())
    path = _source_path(catalog, plan.source_ref)
    source_sha, source_bytes, record_count = _hash_source(path)
    if (
        source_sha != plan.source_sha256
        or source_bytes != plan.source_bytes
        or record_count != plan.record_count
    ):
        raise ArchiveMigrationError("migration source changed after planning")
    return path


def apply_migration_plan(
    catalog: ArchiveCatalog,
    plan: ArchiveMigrationPlan,
    *,
    restore_verified: bool,
) -> ArchiveArtifactManifest:
    path = validate_migration_plan(catalog, plan)
    manifest = catalog.register_jsonl(
        path,
        stream_id=plan.stream_id,
        kind=plan.kind,
        contour=plan.contour,
        payload_schema=plan.payload_schema,
        source_revision=plan.source_revision,
        sensitivity=plan.sensitivity,
        first_observed_at=plan.first_observed_at,
        last_observed_at=plan.last_observed_at,
        created_at=plan.created_at,
        restore_verified=restore_verified,
    )
    if (
        manifest.source_sha256 != plan.source_sha256
        or manifest.logical_bytes != plan.source_bytes
        or manifest.record_count != plan.record_count
    ):
        raise ArchiveMigrationError("archived object failed source parity")
    return manifest


class ArchiveReadCutover:
    """Append-only read-selection events; neither source is ever removed."""

    def __init__(self, catalog: ArchiveCatalog) -> None:
        if not catalog.capability.synthetic:
            raise ArchiveMigrationError(
                "private cutover requires a separately reviewed operational package"
            )
        self.catalog = catalog
        self.root = catalog.root
        self.events_root = self.root / CONTROL_DIR / "cutovers"
        self.lock_path = self.root / CONTROL_DIR / "locks" / "catalog.lock"

    def _path(self, stream_id: str) -> Path:
        if not _STREAM_ID.fullmatch(stream_id):
            raise ArchiveMigrationError("cutover stream id is invalid")
        return self.events_root / f"{stream_id}.jsonl"

    def _events(self, stream_id: str) -> list[dict[str, Any]]:
        path = self._path(stream_id)
        if not path.exists():
            return []
        if is_link_or_reparse(path) or not path.is_file():
            raise ArchiveMigrationError("cutover event path is unsafe")
        events: list[dict[str, Any]] = []
        prior = ""
        with path.open("r", encoding="utf-8") as stream:
            for sequence, line in enumerate(stream, 1):
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError) as exc:
                    raise ArchiveMigrationError("cutover event is invalid") from exc
                if event.get("schema") != CUTOVER_SCHEMA:
                    raise ArchiveMigrationError("cutover event schema mismatch")
                if (
                    event.get("project_id") != self.catalog.capability.project_id
                    or event.get("capability_digest")
                    != self.catalog.capability.capability_digest
                    or event.get("stream_id") != stream_id
                ):
                    raise ArchiveMigrationError("cutover event binding mismatch")
                expected = dict(event)
                event_hash = str(expected.pop("event_sha256", ""))
                if (
                    int(event.get("sequence") or 0) != sequence
                    or event.get("prior_event_sha256") != prior
                    or hashlib.sha256(
                        canonical_json(expected).encode("utf-8")
                    ).hexdigest()
                    != event_hash
                ):
                    raise ArchiveMigrationError("cutover event chain mismatch")
                prior = event_hash
                events.append(event)
        return events

    def status(self, stream_id: str) -> dict[str, Any]:
        events = self._events(stream_id)
        if not events:
            return {
                "stream_id": stream_id,
                "read_source": "legacy",
                "artifact_id": "",
                "sequence": 0,
            }
        latest = events[-1]
        return {
            "stream_id": stream_id,
            "read_source": latest["read_source"],
            "artifact_id": latest["artifact_id"],
            "sequence": latest["sequence"],
        }

    def _append(
        self,
        *,
        stream_id: str,
        request_id: str,
        read_source: str,
        artifact_id: str,
        reason: str,
        created_at: str,
        expected_read_source: str,
    ) -> dict[str, Any]:
        if not _REQUEST_ID.fullmatch(request_id):
            raise ArchiveMigrationError("cutover request id is invalid")
        if read_source not in {"legacy", "archive"}:
            raise ArchiveMigrationError("cutover read source is invalid")
        try:
            with storage_root_lock(self.lock_path, wait_seconds=5.0):
                self.catalog._revalidate()
                events = self._events(stream_id)
                matching = [event for event in events if event["request_id"] == request_id]
                if matching:
                    if (
                        len(matching) == 1
                        and matching[0]["read_source"] == read_source
                        and matching[0]["artifact_id"] == artifact_id
                        and matching[0]["reason"] == reason
                        and matching[0]["created_at"] == created_at
                    ):
                        return matching[0]
                    raise ArchiveMigrationError("cutover request id was reused")
                current_read_source = (
                    str(events[-1]["read_source"]) if events else "legacy"
                )
                if current_read_source != expected_read_source:
                    raise ArchiveMigrationError(
                        "cutover state changed before the requested transition"
                    )
                payload = {
                    "schema": CUTOVER_SCHEMA,
                    "project_id": self.catalog.capability.project_id,
                    "capability_digest": self.catalog.capability.capability_digest,
                    "stream_id": stream_id,
                    "request_id": request_id,
                    "sequence": len(events) + 1,
                    "read_source": read_source,
                    "artifact_id": artifact_id,
                    "reason": reason,
                    "created_at": created_at,
                    "prior_event_sha256": (
                        events[-1]["event_sha256"] if events else ""
                    ),
                }
                reject_sensitive_data(payload)
                event_hash = hashlib.sha256(
                    canonical_json(payload).encode("utf-8")
                ).hexdigest()
                event = {**payload, "event_sha256": event_hash}
                path = self._path(stream_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(canonical_json(event) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                return event
        except StorageLockConflict as exc:
            raise ArchiveMigrationError("cutover lock is unavailable") from exc

    def promote(
        self,
        *,
        stream_id: str,
        artifact_id: str,
        request_id: str,
        created_at: str,
    ) -> dict[str, Any]:
        matches = [
            item
            for item in self.catalog.manifests()
            if item.artifact_id == artifact_id
        ]
        if len(matches) != 1:
            raise ArchiveMigrationError("cutover artifact is absent or ambiguous")
        manifest = matches[0]
        if manifest.stream_id != stream_id:
            raise ArchiveMigrationError("cutover artifact stream mismatch")
        if not manifest.copy_verified or not manifest.restore_verified:
            raise ArchiveMigrationError("cutover requires copy and restore verification")
        return self._append(
            stream_id=stream_id,
            request_id=request_id,
            read_source="archive",
            artifact_id=artifact_id,
            reason="verified_archive_promoted",
            created_at=created_at,
            expected_read_source="legacy",
        )

    def rollback(
        self,
        *,
        stream_id: str,
        request_id: str,
        created_at: str,
    ) -> dict[str, Any]:
        return self._append(
            stream_id=stream_id,
            request_id=request_id,
            read_source="legacy",
            artifact_id="",
            reason="owner_requested_non_destructive_rollback",
            created_at=created_at,
            expected_read_source="archive",
        )
