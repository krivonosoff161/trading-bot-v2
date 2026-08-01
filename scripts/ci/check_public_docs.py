from __future__ import annotations

import re
import shutil
# Fixed, non-shell Git inventory commands only.
import subprocess  # nosec B404
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GIT = shutil.which("git")
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
BATCH_REFERENCE = re.compile(r"`([^`]+\.bat)`")


def tracked_markdown_files() -> list[Path]:
    result = subprocess.run(
        [GIT or "git", "ls-files", "*.md"],  # nosec B603
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line]


def tracked_paths() -> set[Path]:
    result = subprocess.run(
        [GIT or "git", "ls-files"],  # nosec B603
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = {(ROOT / line).resolve() for line in result.stdout.splitlines() if line}
    untracked = subprocess.run(
        [GIT or "git", "ls-files", "--others", "--exclude-standard"],  # nosec B603
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths.update((ROOT / line).resolve() for line in untracked.stdout.splitlines() if line)
    return paths


def local_destination(source: Path, raw_destination: str) -> Path | None:
    destination = raw_destination.strip().split(maxsplit=1)[0].strip("<>")
    if not destination or "://" in destination or destination.startswith(("#", "mailto:")):
        return None
    return (source.parent / destination.split("#", maxsplit=1)[0]).resolve()


def check_markdown_links() -> list[str]:
    failures: list[str] = []
    public_paths = tracked_paths()
    for source in tracked_markdown_files():
        text = source.read_text(encoding="utf-8", errors="replace")
        for raw_destination in MARKDOWN_LINK.findall(text):
            destination = local_destination(source, raw_destination)
            if destination is not None and destination not in public_paths:
                failures.append(
                    f"broken public doc link: {source.relative_to(ROOT)} -> {raw_destination}"
                )
    return failures


def resolve_entrypoint(name: str) -> Path | None:
    direct = ROOT / name.replace("\\", "/")
    if direct.is_file():
        return direct
    in_bat = ROOT / "bat" / name
    return in_bat if in_bat.is_file() else None


def check_entrypoint_catalog() -> list[str]:
    catalogs = (
        ROOT / "docs" / "entrypoints.md",
        ROOT / "docs" / "entrypoint-inventory.md",
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in catalogs)
    documented: set[Path] = set()
    failures: list[str] = []
    for name in sorted(set(BATCH_REFERENCE.findall(text))):
        entrypoint = resolve_entrypoint(name)
        if entrypoint is None:
            failures.append(f"missing entrypoint referenced by catalogs: {name}")
        else:
            documented.add(entrypoint.resolve())

    actual = {
        path.resolve()
        for pattern in ("*.bat", "bat/*.bat")
        for path in ROOT.glob(pattern)
    }
    for path in sorted(actual - documented):
        failures.append(f"entrypoint missing from catalogs: {path.relative_to(ROOT)}")
    return failures


def main() -> int:
    failures = check_markdown_links() + check_entrypoint_catalog()
    if failures:
        print("public documentation guard: failed", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1
    print("public documentation guard: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
