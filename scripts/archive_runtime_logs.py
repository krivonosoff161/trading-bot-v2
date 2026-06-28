"""Archive current runtime logs without deleting subscriptions or code.

This is for revival cycles where the product starts a fresh logging window.
It moves selected files/directories under logs/ into logs_archive/<label>/ and
writes a manifest with sizes and hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOGS = ROOT / "logs"
DEFAULT_ARCHIVE = ROOT / "logs_archive"

SKIP_NAMES = {".gitkeep"}


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_entries(logs_root: Path) -> list[Path]:
    if not logs_root.exists():
        return []
    return sorted(p for p in logs_root.iterdir() if p.name not in SKIP_NAMES)


def build_manifest(logs_root: Path) -> list[dict]:
    rows = []
    for entry in _iter_entries(logs_root):
        if entry.is_file():
            rows.append({
                "path": str(entry.relative_to(logs_root)),
                "kind": "file",
                "bytes": entry.stat().st_size,
                "sha256": _hash_file(entry),
            })
            continue
        file_count = 0
        total_bytes = 0
        for child in entry.rglob("*"):
            if child.is_file():
                file_count += 1
                total_bytes += child.stat().st_size
        rows.append({
            "path": str(entry.relative_to(logs_root)),
            "kind": "dir",
            "files": file_count,
            "bytes": total_bytes,
        })
    return rows


def archive_runtime_logs(
    *,
    logs_root: Path = DEFAULT_LOGS,
    archive_root: Path = DEFAULT_ARCHIVE,
    label: str | None = None,
    apply: bool = False,
) -> dict:
    label = label or datetime.now(tz=timezone.utc).strftime("revival_%Y-%m-%d_%H-%M-%S")
    target = archive_root / label
    rows = build_manifest(logs_root)
    report = {
        "schema": "runtime_log_archive.v1",
        "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "logs_root": str(logs_root),
        "archive_root": str(target),
        "apply": apply,
        "entries": rows,
        "moved": [],
    }
    if not apply:
        return report
    target.mkdir(parents=True, exist_ok=False)
    for entry in _iter_entries(logs_root):
        dest = target / entry.name
        shutil.move(str(entry), str(dest))
        report["moved"].append(entry.name)
    logs_root.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs-root", default=str(DEFAULT_LOGS))
    ap.add_argument("--archive-root", default=str(DEFAULT_ARCHIVE))
    ap.add_argument("--label", default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    report = archive_runtime_logs(
        logs_root=Path(args.logs_root),
        archive_root=Path(args.archive_root),
        label=args.label,
        apply=args.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

