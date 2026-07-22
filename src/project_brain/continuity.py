"""Small continuity checks for SESSION/TASK without treating them as truth."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Iterable


FULL_SHA = re.compile(r"(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])", re.IGNORECASE)
SHORT_SHA = re.compile(r"(?<![0-9a-f])([0-9a-f]{7,39})(?![0-9a-f])", re.IGNORECASE)


@dataclass(frozen=True)
class ContinuityDocument:
    path: str
    exists: bool
    declared_sha: str
    freshness: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def inspect_continuity_documents(
    root: Path,
    current_sha: str,
    *,
    names: Iterable[str] = ("SESSION.md", "TASK.md"),
) -> tuple[ContinuityDocument, ...]:
    rows: list[ContinuityDocument] = []
    for name in names:
        path = root / name
        if not path.is_file():
            rows.append(
                ContinuityDocument(name, False, "", "missing", "document_missing")
            )
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        matches = FULL_SHA.findall(text) or SHORT_SHA.findall(text)
        declared = matches[-1].lower() if matches else ""
        if not declared:
            freshness, reason = "unknown", "no_commit_identity"
        elif current_sha.lower().startswith(declared):
            freshness, reason = "current", "commit_matches"
        else:
            freshness, reason = "stale", "commit_mismatch"
        rows.append(ContinuityDocument(name, True, declared, freshness, reason))
    return tuple(rows)
