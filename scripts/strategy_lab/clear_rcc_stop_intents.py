"""Hash-bound offline acknowledgement for canonical contour stop markers.

The Research Control Center writes these markers during a graceful stop.  The
documented farm stop entrypoint may subsequently replace only the farm marker
with its own single-line payload.  A later operator may clear only the exact,
already-forensically-recorded marker generation after independently proving
that processes, owners, and owned ports are quiescent.  This utility
deliberately does not inspect credentials, task rows, or mutate a database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from src.research_lab.paths import resolve_private_child, resolve_private_root


RCC_STOP_MARKERS = (
    "STOP_FARM_FULL_CYCLE.txt",
    "STOP_NEWS_SCANNER.txt",
    "STOP_PUBLIC_NEWS.txt",
)
LEGACY_MIGRATABLE_MARKERS = (
    "STOP_NEWS_SCANNER.txt",
    "STOP_PUBLIC_NEWS.txt",
)
_RCC_STOP_PAYLOAD = re.compile(
    rb"control center stop requested at [0-9]+(?:\.[0-9]+)?\r?\n"
)
_DOCUMENTED_FARM_STOP_PAYLOAD = re.compile(
    rb"stop requested at (?=[^\r\n]{4,160}\r?\n)"
    rb"(?=[^\r\n]*[0-9])[^\r\n\x00-\x1f\x7f]+[ ]+"
    rb"[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:[.,][0-9]{1,2})?[ ]?\r?\n"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_AUTHORITY_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}")


class StopIntentClearError(RuntimeError):
    """The requested offline acknowledgement is not provenance-safe."""


def _parse_legacy_expected(values: Sequence[str]) -> dict[str, tuple[str, int]]:
    """Parse one explicit hash-and-size binding for each legacy marker.

    This is intentionally separate from normal marker clearance.  The caller
    must provide an external owner authorization for these exact opaque bytes;
    the public command never attempts to interpret or normalize them.
    """

    expected: dict[str, tuple[str, int]] = {}
    allowed = set(LEGACY_MIGRATABLE_MARKERS)
    for value in values:
        name, separator, remainder = value.partition("=")
        digest, size_separator, raw_size = remainder.partition(":")
        normalized = digest.strip().lower()
        try:
            size = int(raw_size)
        except ValueError:
            size = -1
        if (
            not separator
            or not size_separator
            or name not in allowed
            or not _SHA256.fullmatch(normalized)
            or size < 0
            or name in expected
        ):
            raise StopIntentClearError("invalid_legacy_stop_marker_binding")
        expected[name] = (normalized, size)
    if set(expected) != allowed:
        raise StopIntentClearError("incomplete_legacy_stop_marker_set")
    return expected


def _reject_reparse_path(path: Path) -> None:
    """Reject a link/reparse point rather than archive outside the exact root."""

    if not path.exists():
        return
    stat = path.lstat()
    reparse = bool(getattr(stat, "st_file_attributes", 0) & 0x400)
    if path.is_symlink() or reparse:
        raise StopIntentClearError("unsafe_legacy_archive_path")


def _safe_archive_directory(archive_root: Path | str, authority_id: str) -> Path:
    if not _AUTHORITY_ID.fullmatch(authority_id):
        raise StopIntentClearError("invalid_legacy_stop_authority_id")
    root = Path(archive_root)
    if not root.is_absolute():
        raise StopIntentClearError("legacy_archive_root_not_absolute")
    _reject_reparse_path(root)
    target = root / "trading-bot-v2" / "stop-marker-provenance-v1" / authority_id
    for parent in (root, root / "trading-bot-v2", root / "trading-bot-v2" / "stop-marker-provenance-v1", target):
        _reject_reparse_path(parent)
    target.mkdir(parents=True, exist_ok=True)
    _reject_reparse_path(target)
    return target.resolve()


def _verify_exact_file(path: Path, *, digest: str, size: int) -> bool:
    try:
        if not path.is_file() or path.stat().st_size != size:
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == digest
    except OSError:
        return False


def _write_once_exact(path: Path, payload: bytes, *, digest: str, size: int) -> bool:
    """Write one immutable archive object or verify the prior exact object."""

    if _verify_exact_file(path, digest=digest, size=size):
        return False
    if path.exists():
        raise StopIntentClearError("legacy_archive_collision")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if not _verify_exact_file(temporary, digest=digest, size=size):
            raise StopIntentClearError("legacy_archive_write_mismatch")
        try:
            os.link(temporary, path)
        except FileExistsError:
            if not _verify_exact_file(path, digest=digest, size=size):
                raise StopIntentClearError("legacy_archive_collision")
        if not _verify_exact_file(path, digest=digest, size=size):
            raise StopIntentClearError("legacy_archive_write_mismatch")
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return True


def _canonical_json(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_manifest_once(path: Path, payload: Mapping[str, object]) -> bool:
    encoded = _canonical_json(payload)
    return _write_once_exact(
        path,
        encoded,
        digest=hashlib.sha256(encoded).hexdigest(),
        size=len(encoded),
    )


def _payload_matches_marker(name: str, payload: bytes) -> bool:
    if _RCC_STOP_PAYLOAD.fullmatch(payload):
        return True
    return (
        name == "STOP_FARM_FULL_CYCLE.txt"
        and _DOCUMENTED_FARM_STOP_PAYLOAD.fullmatch(payload) is not None
    )


def _parse_expected(values: Sequence[str]) -> dict[str, str]:
    expected: dict[str, str] = {}
    allowed = set(RCC_STOP_MARKERS)
    for value in values:
        name, separator, digest = value.partition("=")
        normalized = digest.strip().lower()
        if (
            not separator
            or name not in allowed
            or not _SHA256.fullmatch(normalized)
            or name in expected
        ):
            raise StopIntentClearError("invalid_expected_stop_marker")
        expected[name] = normalized
    if set(expected) != allowed:
        raise StopIntentClearError("incomplete_expected_stop_marker_set")
    return expected


def clear_expected_rcc_stop_intents(
    private_root: Path,
    *,
    expected_sha256: Mapping[str, str],
    apply: bool = False,
) -> dict[str, object]:
    """Validate, then optionally remove, only one exact RCC stop generation.

    Missing expected markers are treated as already acknowledged so that a
    repeated apply is idempotent.  Every marker still present is validated
    before any marker is removed, preventing partial clearance on mismatch.
    """

    root = resolve_private_root(private_root)
    expected = {
        str(name): str(digest).strip().lower()
        for name, digest in expected_sha256.items()
    }
    if set(expected) != set(RCC_STOP_MARKERS) or any(
        not _SHA256.fullmatch(digest) for digest in expected.values()
    ):
        raise StopIntentClearError("invalid_expected_stop_marker_set")

    state = root / "state"
    eligible: list[Path] = []
    observed: dict[str, str] = {}
    for name in RCC_STOP_MARKERS:
        path = state / name
        if not path.exists():
            continue
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise StopIntentClearError("stop_marker_unreadable") from exc
        digest = hashlib.sha256(payload).hexdigest()
        if not _payload_matches_marker(name, payload):
            raise StopIntentClearError("stop_marker_provenance_mismatch")
        if digest != expected[name]:
            raise StopIntentClearError("stop_marker_hash_mismatch")
        observed[name] = digest
        eligible.append(path)

    cleared: list[str] = []
    if apply:
        for path in eligible:
            try:
                path.unlink()
            except OSError as exc:
                raise StopIntentClearError("stop_marker_clear_failed") from exc
            cleared.append(path.name)

    remaining = [name for name in RCC_STOP_MARKERS if (state / name).exists()]
    return {
        "schema": "CanonicalRccStopIntentClear.v1",
        "mode": "apply" if apply else "dry_run",
        "eligible": sorted(observed),
        "cleared": sorted(cleared),
        "eligible_count": len(observed),
        "cleared_count": len(cleared),
        "remaining_count": len(remaining),
        "idempotent": not remaining,
    }


def migrate_exact_legacy_stop_intents(
    private_root: Path,
    *,
    expected_legacy: Mapping[str, tuple[str, int]],
    archive_root: Path,
    authority_id: str,
    apply: bool = False,
) -> dict[str, object]:
    """Archive and acknowledge one exact, externally authorized legacy pair.

    This is not a broader provenance rule: normal ``clear`` still rejects any
    unknown bytes.  Migration accepts only the scanner/public-news pair bound
    by exact SHA-256 and byte length, preserving each source byte sequence in a
    private immutable archive before removing the source marker.
    """

    root = resolve_private_root(private_root)
    expected = {
        str(name): (str(binding[0]).strip().lower(), int(binding[1]))
        for name, binding in expected_legacy.items()
    }
    if (
        set(expected) != set(LEGACY_MIGRATABLE_MARKERS)
        or any(
            not _SHA256.fullmatch(digest) or size < 0
            for digest, size in expected.values()
        )
    ):
        raise StopIntentClearError("invalid_legacy_stop_marker_binding")

    source_payloads: dict[str, bytes] = {}
    source_present: dict[str, bool] = {}
    source_paths: dict[str, Path] = {}
    for name in LEGACY_MIGRATABLE_MARKERS:
        # Resolve every path component below the approved private root.  A
        # user-controlled reparse point under ``state`` must never redirect a
        # hash-authorized disposition outside that root.
        source = resolve_private_child(root, "state", name)
        source_paths[name] = source
        digest, size = expected[name]
        if not source.exists():
            source_present[name] = False
            continue
        _reject_reparse_path(source)
        try:
            payload = source.read_bytes()
        except OSError as exc:
            raise StopIntentClearError("legacy_stop_marker_unreadable") from exc
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
            raise StopIntentClearError("legacy_stop_marker_hash_mismatch")
        source_present[name] = True
        source_payloads[name] = payload

    archive = _safe_archive_directory(archive_root, authority_id)
    manifest_payload = {
        "schema": "CanonicalRccLegacyStopMarkerMigration.v1",
        "authority_id": authority_id,
        "source_root": str(root),
        "markers": [
            {
                "name": name,
                "sha256": expected[name][0],
                "bytes": expected[name][1],
                "archive_name": f"{name}.bin",
            }
            for name in LEGACY_MIGRATABLE_MARKERS
        ],
    }
    manifest = archive / "migration_manifest.json"
    archive_targets = {
        name: archive / f"{name}.bin" for name in LEGACY_MIGRATABLE_MARKERS
    }
    for name, target in archive_targets.items():
        digest, size = expected[name]
        if not source_present[name] and not _verify_exact_file(
            target, digest=digest, size=size
        ):
            raise StopIntentClearError("legacy_stop_marker_missing_unarchived")

    if not apply:
        return {
            "schema": "CanonicalRccLegacyStopMarkerMigration.v1",
            "mode": "dry_run",
            "authority_id": authority_id,
            "eligible": sorted(name for name, present in source_present.items() if present),
            "eligible_count": sum(source_present.values()),
            "archived_count": 0,
            "cleared_count": 0,
            "remaining_count": sum(source_present.values()),
            "idempotent": not any(source_present.values()),
        }

    archived_count = 0
    for name, payload in source_payloads.items():
        digest, size = expected[name]
        if _write_once_exact(archive_targets[name], payload, digest=digest, size=size):
            archived_count += 1
    _write_manifest_once(manifest, manifest_payload)

    cleared: list[str] = []
    for name in LEGACY_MIGRATABLE_MARKERS:
        source = source_paths[name]
        if not source_present[name]:
            continue
        digest, size = expected[name]
        if not _verify_exact_file(archive_targets[name], digest=digest, size=size):
            raise StopIntentClearError("legacy_archive_proof_missing")
        try:
            source.unlink()
        except OSError as exc:
            raise StopIntentClearError("legacy_stop_marker_clear_failed") from exc
        cleared.append(name)

    remaining = [
        name for name in LEGACY_MIGRATABLE_MARKERS if source_paths[name].exists()
    ]
    return {
        "schema": "CanonicalRccLegacyStopMarkerMigration.v1",
        "mode": "apply",
        "authority_id": authority_id,
        "eligible": sorted(name for name, present in source_present.items() if present),
        "eligible_count": sum(source_present.values()),
        "archived_count": archived_count,
        "cleared": sorted(cleared),
        "cleared_count": len(cleared),
        "remaining_count": len(remaining),
        "idempotent": not remaining,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="NAME=SHA256",
        help="exact forensic digest for each of the three canonical RCC markers",
    )
    parser.add_argument(
        "--legacy-marker",
        action="append",
        default=[],
        metavar="NAME=SHA256:BYTES",
        help="enable exact externally authorized legacy scanner/public-news migration",
    )
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--authority-id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    legacy_mode = bool(args.legacy_marker)
    try:
        if legacy_mode:
            if args.expect or args.archive_root is None or not args.authority_id:
                raise StopIntentClearError("invalid_legacy_migration_arguments")
            result = migrate_exact_legacy_stop_intents(
                args.private_root,
                expected_legacy=_parse_legacy_expected(args.legacy_marker),
                archive_root=Path(args.archive_root),
                authority_id=str(args.authority_id),
                apply=args.apply,
            )
        else:
            result = clear_expected_rcc_stop_intents(
                args.private_root,
                expected_sha256=_parse_expected(args.expect),
                apply=args.apply,
            )
    except (OSError, ValueError, StopIntentClearError) as exc:
        result = {
            "schema": (
                "CanonicalRccStopIntentClear.v1"
                if not legacy_mode
                else "CanonicalRccLegacyStopMarkerMigration.v1"
            ),
            "mode": "apply" if args.apply else "dry_run",
            "status": "blocked",
            "reason": str(exc),
        }
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print(f"BLOCKED: {result['reason']}")
        return 2
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            f"{result['mode']}: eligible={result['eligible_count']} "
            f"cleared={result['cleared_count']} remaining={result['remaining_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
