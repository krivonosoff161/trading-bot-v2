from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
FULL_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
USES_DIRECTIVE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^#\s]+)", re.MULTILINE)
EXACT_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9][A-Za-z0-9_.!+*-]*"
    r"(?:\s*;\s*.+)?$"
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?P<name>"
    r"(?:[a-z0-9_]*_)?(?:api[_-]?key|access[_-]?token|auth[_-]?token|bot[_-]?token|"
    r"telegram[_-]?bot[_-]?token|secret(?:[_-]?key)?|password|passwd|sessionid|csrftoken)"
    r")\b\s*[:=]\s*['\"]?(?P<value>[A-Za-z0-9_./:+=@~-]{16,})"
)
PRIVATE_KEY_MARKER = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

TEXT_EXTENSIONS = {
    ".bat",
    ".cfg",
    ".css",
    ".csv",
    ".env.example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
SKIP_PATH_PREFIXES = {
    ".git/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "venv/",
}
SAFE_PLACEHOLDER_VALUES = {
    "change_me",
    "changeme",
    "example",
    "example_token",
    "placeholder",
    "redacted",
    "replace_me",
    "todo",
    "your_token_here",
}


@dataclass(frozen=True)
class PolicyPaths:
    root: Path
    workflows: tuple[Path, ...]
    ci_requirements: Path
    requirements_digest: Path


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _is_external_action(action: str) -> bool:
    name = action.split("@", maxsplit=1)[0]
    return "/" in name and not name.startswith(("./", "../"))


def check_workflow_action_refs(workflows: Iterable[Path], *, root: Path | None = None) -> list[str]:
    root = (root or ROOT).resolve()
    failures: list[str] = []
    for workflow in workflows:
        text = _read_text(workflow)
        for raw_action in USES_DIRECTIVE.findall(text):
            action = raw_action.strip().strip("'\"")
            if not _is_external_action(action):
                continue
            if "@" not in action:
                failures.append(
                    f"{_relative(workflow, root)}: external action {action} has no immutable ref"
                )
                continue
            name, ref = action.rsplit("@", maxsplit=1)
            if not FULL_COMMIT_SHA.fullmatch(ref):
                failures.append(
                    f"{_relative(workflow, root)}: external action {name} must use a full 40-char commit SHA"
                )
    return failures


def _strip_inline_comment(line: str) -> str:
    in_quote: str | None = None
    for index, char in enumerate(line):
        if char in {"'", '"'}:
            in_quote = None if in_quote == char else char
        elif char == "#" and in_quote is None:
            return line[:index].rstrip()
    return line.strip()


def check_ci_requirements(path: Path, *, root: Path | None = None) -> list[str]:
    root = (root or ROOT).resolve()
    if not path.is_file():
        return [f"{_relative(path, root)}: missing locked CI requirements file"]

    failures: list[str] = []
    for line_number, raw_line in enumerate(_read_text(path).splitlines(), start=1):
        line = _strip_inline_comment(raw_line)
        if not line:
            continue
        if line.startswith(("-r ", "--requirement ", "-c ", "--constraint ", "--index-url", "--extra-index-url")):
            failures.append(
                f"{_relative(path, root)}:{line_number}: CI requirements must be self-contained exact == pins"
            )
            continue
        if not EXACT_REQUIREMENT.fullmatch(line):
            package = re.split(r"[<>=!~\s\[]", line, maxsplit=1)[0] or "<unknown>"
            failures.append(
                f"{_relative(path, root)}:{line_number}: {package} must use an exact == CI pin"
            )
            continue
        version = line.split("==", maxsplit=1)[1].split(";", maxsplit=1)[0].strip()
        if "*" in version:
            package = line.split("==", maxsplit=1)[0]
            failures.append(
                f"{_relative(path, root)}:{line_number}: {package} must not use wildcard CI pins"
            )
    return failures


def check_requirements_digest(
    requirements_path: Path,
    digest_path: Path,
    *,
    root: Path | None = None,
) -> list[str]:
    root = (root or ROOT).resolve()
    if not digest_path.is_file():
        return [f"{_relative(digest_path, root)}: missing requirements digest manifest"]

    digest_text = _read_text(digest_path).strip()
    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", digest_text)
    if not match:
        return [f"{_relative(digest_path, root)}: expected '<sha256>  requirements-ci.txt'"]

    expected_sha, expected_name = match.groups()
    if Path(expected_name).name != requirements_path.name:
        return [
            f"{_relative(digest_path, root)}: digest target must be {requirements_path.name}"
        ]

    actual_sha = hashlib.sha256(requirements_path.read_bytes()).hexdigest()
    if actual_sha.lower() != expected_sha.lower():
        return [
            f"{_relative(digest_path, root)}: digest mismatch for {requirements_path.name}"
        ]
    return []


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [root / line for line in result.stdout.splitlines() if line]


def _looks_text_path(path: Path, root: Path) -> bool:
    rel = _relative(path, root)
    if any(rel.startswith(prefix) for prefix in SKIP_PATH_PREFIXES):
        return False
    suffix = "".join(path.suffixes[-2:]).lower()
    return suffix in TEXT_EXTENSIONS or path.suffix.lower() in TEXT_EXTENSIONS


def _safe_placeholder(value: str) -> bool:
    normalized = value.strip("'\"").strip().lower()
    return (
        normalized in SAFE_PLACEHOLDER_VALUES
        or normalized.startswith(("example_", "your_", "replace_", "redacted_"))
        or set(normalized) <= {"x", "0", "*", "-"}
    )


def scan_tracked_text_files(paths: Iterable[Path], *, root: Path | None = None) -> list[str]:
    root = (root or ROOT).resolve()
    failures: list[str] = []
    for path in paths:
        if not path.is_file() or not _looks_text_path(path, root):
            continue
        try:
            text = _read_text(path)
        except OSError as exc:
            failures.append(f"{_relative(path, root)}: could not scan text file: {exc.__class__.__name__}")
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if PRIVATE_KEY_MARKER.search(line):
                failures.append(
                    f"{_relative(path, root)}:{line_number}: private_key_marker"
                )
            for match in SECRET_ASSIGNMENT.finditer(line):
                name = match.group("name").lower()
                value = match.group("value")
                if _safe_placeholder(value):
                    continue
                failures.append(
                    f"{_relative(path, root)}:{line_number}: secret_like_assignment:{name}"
                )
    return failures


def check_ci_uses_locked_requirements(workflows: Iterable[Path], *, root: Path | None = None) -> list[str]:
    root = (root or ROOT).resolve()
    failures: list[str] = []
    for workflow in workflows:
        text = _read_text(workflow)
        rel = _relative(workflow, root)
        if "python scripts/ci/check_supply_chain_policy.py" not in text:
            failures.append(f"{rel}: CI must run supply-chain policy before tests")
        if "pip install -r requirements.txt" in text:
            failures.append(f"{rel}: CI must not install mutable developer requirements.txt")
        if "requirements-ci.txt" not in text:
            failures.append(f"{rel}: CI must install requirements-ci.txt")
    return failures


def resolve_policy_paths(root: Path) -> PolicyPaths:
    workflow_dir = root / ".github" / "workflows"
    workflows = tuple(sorted(workflow_dir.glob("*.yml"))) + tuple(sorted(workflow_dir.glob("*.yaml")))
    return PolicyPaths(
        root=root,
        workflows=workflows,
        ci_requirements=root / "requirements-ci.txt",
        requirements_digest=root / "requirements-ci.sha256",
    )


def evaluate_policy(root: Path = ROOT) -> list[str]:
    root = root.resolve()
    paths = resolve_policy_paths(root)
    failures: list[str] = []
    if not paths.workflows:
        failures.append(".github/workflows: missing workflow files")
    failures.extend(check_workflow_action_refs(paths.workflows, root=root))
    failures.extend(check_ci_uses_locked_requirements(paths.workflows, root=root))
    failures.extend(check_ci_requirements(paths.ci_requirements, root=root))
    if paths.ci_requirements.is_file():
        failures.extend(
            check_requirements_digest(paths.ci_requirements, paths.requirements_digest, root=root)
        )
    failures.extend(scan_tracked_text_files(_tracked_files(root), root=root))
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline public supply-chain and content hygiene policy guard."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    failures = evaluate_policy(args.root)
    if failures:
        print("supply-chain policy guard: failed", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1
    print("supply-chain policy guard: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
