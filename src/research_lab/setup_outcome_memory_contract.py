"""Pure, shared integrity contract for setup-outcome known-bad snapshots.

The durable snapshot can contain several historical rows for one exact setup.
Those rows are semantically one censorship identity, but the integrity digest
must cover the complete sorted row sequence so that duplicate provenance is not
silently discarded before verification.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping


KnownBadSnapshotIdentity = tuple[str, str, str, str]


def known_bad_snapshot_rows(
    records: Iterable[object],
) -> list[KnownBadSnapshotIdentity]:
    """Return the complete sorted known-bad row sequence for snapshot integrity."""
    rows: list[KnownBadSnapshotIdentity] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        if (
            record.get("outcome_class") != "CONFIRMED_BAD"
            and record.get("tactical_status") != "REJECTED_CONFIRMED_BAD"
            and record.get("tactical_class") != "REJECTED_CONFIRMED_BAD"
        ):
            continue
        identity = (
            str(record.get("symbol") or ""),
            str(record.get("timeframe") or ""),
            str(record.get("family") or ""),
            str(record.get("params_hash") or ""),
        )
        if all(identity[:3]):
            rows.append(identity)
    return sorted(rows)


def known_bad_snapshot_digest(items: Iterable[KnownBadSnapshotIdentity]) -> str:
    """Digest the complete sorted row sequence without deduplicating it."""
    payload = [list(item) for item in sorted(items)]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
