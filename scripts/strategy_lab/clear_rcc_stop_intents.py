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
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from src.research_lab.paths import resolve_private_root


RCC_STOP_MARKERS = (
    "STOP_FARM_FULL_CYCLE.txt",
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


class StopIntentClearError(RuntimeError):
    """The requested offline acknowledgement is not provenance-safe."""


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
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = clear_expected_rcc_stop_intents(
            args.private_root,
            expected_sha256=_parse_expected(args.expect),
            apply=args.apply,
        )
    except (OSError, ValueError, StopIntentClearError) as exc:
        result = {
            "schema": "CanonicalRccStopIntentClear.v1",
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
