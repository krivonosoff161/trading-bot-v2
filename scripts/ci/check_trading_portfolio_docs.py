"""Validate the public-safe Trading Portfolio documentation contract."""

from __future__ import annotations

import re
import shutil
# Fixed, non-shell Git inventory commands only.
import subprocess  # nosec B404
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
GIT = shutil.which("git")
ROADMAP = ROOT / "docs" / "trading-portfolio-roadmap.yaml"
SHA256 = re.compile(r"[0-9a-f]{40}")
CURRENT_STATUS = re.compile(r"(?m)^Status:\s*\*\*CURRENT\*\*\s*$")
CONTROL_FIELDS = (
    re.compile(r"(?m)^- Verified:\s*\d{4}-\d{2}-\d{2}\s*$"),
    re.compile(r"(?m)^- Verified against:\s*`[0-9a-f]{40}`\s*$"),
    re.compile(r"(?m)^- Scope:\s*\S"),
    re.compile(r"(?m)^- Evidence:\s*\S"),
    re.compile(r"(?m)^- Residual risks:\s*\S"),
    re.compile(r"(?m)^- Next gate:\s*\S"),
)
PRIVATE_POINTERS = (
    re.compile(r"(?i)(?:^|[\s'\"])[a-z]:[\\/]"),
    re.compile(r"(?i)(?:^|[/\\])\.env(?:$|[.\s/\\])"),
    re.compile(r"(?i)(?:credential|recipient|token|password|private[_ -]?key)[_-]?id\s*[:=]"),
    re.compile(r"(?i)\.(?:sqlite3?|db|wal|shm|log|jsonl)(?:$|[\s'\"])")
)
REQUIRED_MODULE_FIELDS = {
    "module_id",
    "owner_repository",
    "purpose",
    "status",
    "dependencies",
    "implemented_evidence",
    "missing_evidence",
    "next_gate",
    "authority",
    "public_private_boundary",
}
REQUIRED_DOCUMENT_FIELDS = {
    "path",
    "scope",
    "evidence",
    "residual_risk",
    "next_gate",
}
REQUIRED_GOVERNANCE = {
    "documentation_owner": "trading-bot-v2",
    "portfolio_integrator": "krivonosoff161",
    "verified_against_kind": "implementation_baseline",
    "public_projection": "sanitized_manifest_only",
    "hash_canonicalization": "utf8_lf",
}


def tracked_markdown(root: Path) -> list[Path]:
    try:
        result = subprocess.run(
            [GIT or "git", "ls-files", "*.md"],  # nosec B603
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return sorted(root.rglob("*.md"))
    return [root / line for line in result.stdout.splitlines() if line]


def iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_strings(item)


def load_contract(path: Path = ROADMAP) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("roadmap_root_must_be_mapping")
    return value


def _local_evidence_path(root: Path, value: str) -> Path | None:
    if value.startswith("honest-backtest:") or "://" in value:
        return None
    return root / value


def validate_contract(contract: Mapping[str, Any], root: Path = ROOT) -> list[str]:
    failures: list[str] = []
    allowed_statuses = set(contract.get("status_values") or [])
    allowed_authorities = set(contract.get("authority_values") or [])
    if contract.get("schema") != "TradingPortfolioRoadmap.v1":
        failures.append("invalid roadmap schema")
    for field, expected in REQUIRED_GOVERNANCE.items():
        if contract.get(field) != expected:
            failures.append(f"invalid documentation governance field: {field}")
    verified = contract.get("verified_against")
    if not isinstance(verified, Mapping) or any(
        not SHA256.fullmatch(str(verified.get(repo, "")))
        for repo in ("trading-bot-v2", "honest-backtest")
    ):
        failures.append("missing verified repository SHA")

    documents = contract.get("current_documents")
    if not isinstance(documents, list):
        failures.append("current_documents must be a list")
        documents = []
    registered_documents: set[str] = set()
    for index, document in enumerate(documents):
        if not isinstance(document, Mapping):
            failures.append(f"current document {index} must be a mapping")
            continue
        missing = REQUIRED_DOCUMENT_FIELDS - set(document)
        path_value = str(document.get("path", ""))
        if missing:
            failures.append(f"current document {path_value or index} missing fields")
        if path_value in registered_documents:
            failures.append(f"duplicate current document: {path_value}")
        registered_documents.add(path_value)
        path = root / path_value
        if not path.is_file():
            failures.append(f"missing current document: {path_value}")
            continue
        text = path.read_text(encoding="utf-8")
        if not CURRENT_STATUS.search(text):
            failures.append(f"current document lacks CURRENT status: {path_value}")
        for pattern in CONTROL_FIELDS:
            if not pattern.search(text):
                failures.append(f"current document lacks control field: {path_value}")
                break

    for path in tracked_markdown(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(root).as_posix()
        if CURRENT_STATUS.search(text) and relative not in registered_documents:
            failures.append(f"unregistered current document: {relative}")

    modules = contract.get("modules")
    if not isinstance(modules, list):
        failures.append("modules must be a list")
        modules = []
    module_ids: set[str] = set()
    dependencies: list[tuple[str, str]] = []
    for index, module in enumerate(modules):
        if not isinstance(module, Mapping):
            failures.append(f"module {index} must be a mapping")
            continue
        module_id = str(module.get("module_id", ""))
        missing = REQUIRED_MODULE_FIELDS - set(module)
        if missing:
            failures.append(f"module {module_id or index} missing fields")
        if not module_id or module_id in module_ids:
            failures.append(f"duplicate or empty module_id: {module_id or index}")
        module_ids.add(module_id)
        if module.get("status") not in allowed_statuses:
            failures.append(f"module {module_id} has invalid status")
        if module.get("authority") not in allowed_authorities:
            failures.append(f"module {module_id} has invalid authority")
        owner = module.get("owner_repository")
        if owner not in {"trading-bot-v2", "honest-backtest"}:
            failures.append(f"module {module_id} has invalid owner")
        evidence = module.get("implemented_evidence")
        if module.get("status") in {"implemented", "implemented_bounded"} and not evidence:
            failures.append(f"implemented module {module_id} lacks evidence")
        if isinstance(evidence, list):
            for item in evidence:
                value = str(item)
                path = _local_evidence_path(root, value)
                if path is not None and not path.exists():
                    failures.append(f"module {module_id} evidence path missing: {value}")
        if isinstance(module.get("dependencies"), list):
            dependencies.extend(
                (module_id, str(dependency))
                for dependency in module["dependencies"]
            )
        else:
            failures.append(f"module {module_id} dependencies must be a list")
    for module_id, dependency in dependencies:
        if dependency not in module_ids:
            failures.append(f"module {module_id} has unknown dependency: {dependency}")

    if any(pattern.search(value) for value in iter_strings(contract) for pattern in PRIVATE_POINTERS):
        failures.append("roadmap contains a private or runtime pointer")
    return failures


def main() -> int:
    try:
        failures = validate_contract(load_contract())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        failures = [f"roadmap could not be validated: {type(exc).__name__}"]
    if failures:
        print("trading portfolio documentation guard: failed", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1
    print("trading portfolio documentation guard: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
