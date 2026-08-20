"""Crash-safe sealing for sanitized canary final evidence.

The evidence directory is outside public Git.  This module owns only the
publication protocol: it never starts a process, opens a runtime database or
serializes private payloads.  A seal is valid only when the immutable final
report, manifest and handoff prove the same hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from src.research_lab.storage_capability import is_link_or_reparse
from src.research_lab.storage_os_lock import storage_root_lock

FINAL_REPORT_NAME = "FINAL_REPORT.json"
MANIFEST_NAME = "EVIDENCE_MANIFEST.json"
HANDOFF_NAME = "FINAL_HANDOFF.json"
LOCK_NAME = ".canary_evidence_finalization.lock"
SCHEMA = "CanaryEvidenceFinalization.v1"


class CanaryEvidenceFinalizationError(RuntimeError):
    """A final-evidence seal is absent, inconsistent or tampered with."""


def _canonical_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _ensure_root_and_lock(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if is_link_or_reparse(root):
        raise CanaryEvidenceFinalizationError("evidence root is unsafe")
    lock = root / LOCK_NAME
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        pass
    else:
        os.write(descriptor, b"0")
        os.fsync(descriptor)
        os.close(descriptor)
    if is_link_or_reparse(lock) or not lock.is_file():
        raise CanaryEvidenceFinalizationError("evidence finalization lock is unsafe")
    if lock.stat().st_size < 1:
        with lock.open("r+b") as handle:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
    return lock


def _safe_declared_artifact(root: Path, raw_path: str) -> tuple[str, Path]:
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise CanaryEvidenceFinalizationError("declared evidence artifact path is unsafe")
    candidate = root / relative
    try:
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CanaryEvidenceFinalizationError(
            "declared evidence artifact is unavailable"
        ) from exc
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise CanaryEvidenceFinalizationError("declared evidence artifact is unsafe")
    return relative.as_posix(), resolved


def _write_or_verify_exact(path: Path, expected: bytes) -> bool:
    """Atomically create one immutable protocol file or verify its exact bytes."""

    if path.exists():
        if is_link_or_reparse(path) or not path.is_file():
            raise CanaryEvidenceFinalizationError("sealed evidence file is unsafe")
        try:
            actual = path.read_bytes()
        except OSError as exc:
            raise CanaryEvidenceFinalizationError(
                "sealed evidence file cannot be read"
            ) from exc
        if actual != expected:
            raise CanaryEvidenceFinalizationError("sealed evidence file differs")
        return False
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(expected)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    try:
        actual = path.read_bytes()
    except OSError as exc:
        raise CanaryEvidenceFinalizationError(
            "published evidence file cannot be verified"
        ) from exc
    if actual != expected:
        raise CanaryEvidenceFinalizationError("published evidence bytes changed")
    return True


def _manifest_payload(root: Path, artifact_paths: Iterable[str]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for raw_path in sorted(set(artifact_paths)):
        relative, path = _safe_declared_artifact(root, raw_path)
        entries.append(
            {
                "path": relative,
                "sha256": _sha256_file(path),
                "size": int(path.stat().st_size),
            }
        )
    return {"schema": SCHEMA, "artifacts": entries}


def _load_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanaryEvidenceFinalizationError(
            f"sealed {label} is unreadable"
        ) from exc
    if not isinstance(payload, dict):
        raise CanaryEvidenceFinalizationError(f"sealed {label} is invalid")
    return payload


def _verify_seal(root: Path) -> dict[str, Any]:
    report = root / FINAL_REPORT_NAME
    manifest = root / MANIFEST_NAME
    handoff = root / HANDOFF_NAME
    if not handoff.exists():
        return {}
    if not report.exists() or not manifest.exists():
        raise CanaryEvidenceFinalizationError("sealed handoff is missing its evidence")
    if any(
        is_link_or_reparse(path) or not path.is_file()
        for path in (report, manifest, handoff)
    ):
        raise CanaryEvidenceFinalizationError("sealed evidence path is unsafe")
    handoff_payload = _load_mapping(handoff, label="handoff")
    manifest_payload = _load_mapping(manifest, label="manifest")
    report_sha256 = _sha256_file(report)
    manifest_sha256 = _sha256_file(manifest)
    if (
        handoff_payload.get("schema") != SCHEMA
        or handoff_payload.get("state") != "sealed"
        or handoff_payload.get("final_report_sha256") != report_sha256
        or handoff_payload.get("manifest_sha256") != manifest_sha256
    ):
        raise CanaryEvidenceFinalizationError("sealed evidence hash mismatch")
    entries = manifest_payload.get("artifacts")
    report_entry = (
        next(
            (
                item
                for item in entries
                if isinstance(item, dict) and item.get("path") == FINAL_REPORT_NAME
            ),
            None,
        )
        if isinstance(entries, list)
        else None
    )
    if (
        manifest_payload.get("schema") != SCHEMA
        or not isinstance(report_entry, dict)
        or report_entry.get("sha256") != report_sha256
    ):
        raise CanaryEvidenceFinalizationError("sealed manifest does not bind final report")
    return {
        "state": "sealed",
        "final_report_sha256": report_sha256,
        "manifest_sha256": manifest_sha256,
        "idempotent": True,
    }


def finalize_canary_evidence(
    root: Path | str,
    final_report: dict[str, Any],
    *,
    artifact_paths: Iterable[str] = (),
    after_step: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Seal an already-sanitized final report with crash-safe idempotency.

    ``after_step`` exists solely for deterministic fault-injection tests.  A
    crash after the report or manifest leaves no handoff, so a later call can
    finish only if it proves the exact prior bytes.  Once the handoff exists,
    every later call verifies it before accepting any requested content.
    """

    root = Path(root)
    report_bytes = _canonical_json_bytes(final_report)
    lock = _ensure_root_and_lock(root)
    with storage_root_lock(lock, wait_seconds=1.0):
        sealed = _verify_seal(root)
        if sealed:
            if (root / FINAL_REPORT_NAME).read_bytes() != report_bytes:
                raise CanaryEvidenceFinalizationError(
                    "requested final report conflicts with sealed evidence"
                )
            return sealed

        report_path = root / FINAL_REPORT_NAME
        _write_or_verify_exact(report_path, report_bytes)
        if after_step is not None:
            after_step("report_published")

        requested = tuple(artifact_paths)
        manifest_payload = _manifest_payload(
            root,
            (FINAL_REPORT_NAME, *requested),
        )
        manifest_path = root / MANIFEST_NAME
        _write_or_verify_exact(manifest_path, _canonical_json_bytes(manifest_payload))
        if after_step is not None:
            after_step("manifest_published")

        report_sha256 = _sha256_file(report_path)
        manifest_sha256 = _sha256_file(manifest_path)
        verified_manifest = _load_mapping(manifest_path, label="manifest")
        report_entry = next(
            (
                item
                for item in verified_manifest.get("artifacts") or ()
                if isinstance(item, dict) and item.get("path") == FINAL_REPORT_NAME
            ),
            None,
        )
        if not isinstance(report_entry, dict) or report_entry.get("sha256") != report_sha256:
            raise CanaryEvidenceFinalizationError(
                "manifest does not bind the final report bytes"
            )
        handoff_payload = {
            "schema": SCHEMA,
            "state": "sealed",
            "final_report": FINAL_REPORT_NAME,
            "final_report_sha256": report_sha256,
            "manifest": MANIFEST_NAME,
            "manifest_sha256": manifest_sha256,
        }
        _write_or_verify_exact(
            root / HANDOFF_NAME,
            _canonical_json_bytes(handoff_payload),
        )
        if after_step is not None:
            after_step("handoff_published")
        return _verify_seal(root)
