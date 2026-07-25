"""Content-bound current-generation authority for hard validation and SetupCards.

Artifact directories retain history.  Only an atomically published manifest may name
the current completed request/report/verdict/card chain.  Readers independently verify
the manifest identity, current code bytes, every linked artifact, and cross-artifact
status before accepting any candidate.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.research_lab.honest_backtest_bridge import _artifact_stem

SCHEMA = "HardValidationGeneration.v1"
MANIFEST_NAME = "current_generation.json"
_IDENTITY_FIELDS = (
    "task_ids",
    "task_inputs",
    "producer_code",
    "exported_ids",
    "requests",
    "active",
    "incomplete_ids",
)


def manifest_path(private_root: Path) -> Path:
    return Path(private_root) / "hard_validation" / MANIFEST_NAME


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(path: Path, private_root: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(Path(private_root)).as_posix(),
        "sha256": _sha256(path),
    }


def _producer_code_paths() -> tuple[Path, ...]:
    root = Path(__file__).resolve().parents[2]
    modules = (
        "validation_generation.py",
        "validation_orchestrator.py",
        "validation_handoff.py",
        "hard_validation_export.py",
        "honest_backtest_bridge.py",
        "hard_validation_contract.py",
        "setup_library.py",
        "paper_runtime.py",
        "paper_readiness.py",
        "setup_lifecycle.py",
        "farm_coordinator.py",
        "paper_signals/pfr_bridge.py",
        "paper_signals/cycle.py",
    )
    paths = [root / "src" / "research_lab" / name for name in modules]
    vendor = root / "vendor" / "honest-backtest"
    vendor_modules = (
        "__init__.py",
        "_synth.py",
        "adversarial.py",
        "costs.py",
        "data_checks.py",
        "forward.py",
        "overfit.py",
        "robustness.py",
        "significance.py",
        "splits.py",
    )
    paths.extend(vendor / "src" / "backtest_sanity" / name for name in vendor_modules)
    paths.extend([vendor / "VENDOR.md", vendor / "LICENSE"])
    return tuple(paths)


def _producer_code_manifest() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    paths = _producer_code_paths()
    missing = [path for path in paths if not path.is_file()]
    if missing:
        labels = ", ".join(path.relative_to(root).as_posix() for path in missing)
        raise FileNotFoundError(f"required validation code missing: {labels}")
    return {path.relative_to(root).as_posix(): _sha256(path) for path in paths}


def _task_inputs(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in tasks:
        raw_payload = str(task.get("payload_json") or "{}")
        rows.append(
            {
                "task_id": int(task["task_id"]),
                "task_type": str(task.get("task_type") or ""),
                "task_key": str(task.get("task_key") or ""),
                "candidate_id": str(task.get("candidate_id") or ""),
                "payload_sha256": hashlib.sha256(
                    raw_payload.encode("utf-8")
                ).hexdigest(),
            }
        )
    return sorted(rows, key=lambda row: row["task_id"])


def _load_dict(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _current_candidate_path(
    private_root: Path, subdir: str, candidate_id: str
) -> Path | None:
    path = (
        Path(private_root)
        / "hard_validation"
        / subdir
        / f"{_artifact_stem(candidate_id)}.json"
    )
    payload = _load_dict(path)
    if payload is None or str(payload.get("candidate_id") or "") != candidate_id:
        return None
    return path


def _legacy_candidate_path(
    private_root: Path, subdir: str, candidate_id: str
) -> Path | None:
    """Preserve explicit pre-manifest filename compatibility, then content lookup."""
    directory = (Path(private_root) / "hard_validation" / subdir).resolve()
    try:
        raw = (directory / f"{candidate_id}.json").resolve()
        raw.relative_to(directory)
    except ValueError:
        raw = directory / "__outside__"
    if raw.is_file():
        return raw
    current = _current_candidate_path(private_root, subdir, candidate_id)
    if current is not None:
        return current
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.json")):
        payload = _load_dict(path)
        if (
            payload is not None
            and str(payload.get("candidate_id") or "") == candidate_id
        ):
            return path
    return None


def _current_setup_path(private_root: Path, candidate_id: str) -> Path | None:
    directory = (Path(private_root) / "setup_library" / "cards").resolve()
    try:
        path = (directory / f"setup-{candidate_id}.json").resolve()
        path.relative_to(directory)
    except ValueError:
        return None
    payload = _load_dict(path)
    if payload is None or str(payload.get("candidate_id") or "") != candidate_id:
        return None
    return path


def _identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {field: payload.get(field) for field in _IDENTITY_FIELDS}


def _generation_id(identity: dict[str, Any]) -> str:
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "hvg_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _publish(private_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path = manifest_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return payload


def write_pending_generation(
    private_root: Path,
    *,
    tasks: list[dict[str, Any]],
    producer_time: float | None,
) -> dict[str, Any]:
    """Invalidate prior authority before export/bridge/card side effects begin."""
    identity = {
        "task_ids": sorted(set(int(task["task_id"]) for task in tasks)),
        "task_inputs": _task_inputs(tasks),
        "producer_code": _producer_code_manifest(),
        "exported_ids": [],
        "requests": {},
        "active": {},
        "incomplete_ids": [],
    }
    return _publish(
        Path(private_root),
        {
            "schema": SCHEMA,
            "generation_id": _generation_id(identity),
            "producer_time": producer_time,
            "producer_complete": False,
            **identity,
            "paper_only": True,
            "execution_allowed": False,
        },
    )


def write_current_generation(
    private_root: Path,
    *,
    tasks: list[dict[str, Any]],
    exported_ids: list[str],
    completed_ids: list[str],
    producer_time: float | None,
) -> dict[str, Any]:
    """Atomically publish only complete canonical vertical artifact chains."""
    private_root = Path(private_root)
    exported = list(dict.fromkeys(str(cid) for cid in exported_ids if str(cid)))
    completed = {str(cid) for cid in completed_ids if str(cid)}
    requests: dict[str, dict[str, str]] = {}
    active: dict[str, dict[str, Any]] = {}
    incomplete: list[str] = []
    for candidate_id in exported:
        request_path = _current_candidate_path(private_root, "requests", candidate_id)
        if request_path is not None:
            requests[candidate_id] = _record(request_path, private_root)
        if candidate_id not in completed:
            incomplete.append(candidate_id)
            continue
        report_path = _current_candidate_path(private_root, "reports", candidate_id)
        verdict_path = _current_candidate_path(private_root, "verdicts", candidate_id)
        card_path = _current_setup_path(private_root, candidate_id)
        if (
            request_path is None
            or report_path is None
            or verdict_path is None
            or card_path is None
        ):
            incomplete.append(candidate_id)
            continue
        verdict = _load_dict(verdict_path)
        if verdict is None:
            incomplete.append(candidate_id)
            continue
        active[candidate_id] = {
            "candidate_id": candidate_id,
            "hard_status": str(verdict.get("hard_status") or ""),
            "request": _record(request_path, private_root),
            "report": _record(report_path, private_root),
            "verdict": _record(verdict_path, private_root),
            "setup_card": _record(card_path, private_root),
        }
    identity = {
        "task_ids": sorted(set(int(task["task_id"]) for task in tasks)),
        "task_inputs": _task_inputs(tasks),
        "producer_code": _producer_code_manifest(),
        "exported_ids": exported,
        "requests": requests,
        "active": active,
        "incomplete_ids": sorted(incomplete),
    }
    return _publish(
        private_root,
        {
            "schema": SCHEMA,
            "generation_id": _generation_id(identity),
            "producer_time": producer_time,
            "producer_complete": True,
            **identity,
            "paper_only": True,
            "execution_allowed": False,
        },
    )


def load_current_generation(private_root: Path) -> dict[str, Any] | None:
    """Return ``None`` only for the explicit pre-manifest legacy state."""
    path = manifest_path(private_root)
    if not path.exists():
        return None
    return _load_dict(path) or {}


def _base_manifest_valid(manifest: dict[str, Any]) -> bool:
    if (
        manifest.get("schema") != SCHEMA
        or manifest.get("producer_complete") is not True
        or manifest.get("paper_only") is not True
        or manifest.get("execution_allowed") is not False
        or not isinstance(manifest.get("active"), dict)
    ):
        return False
    identity = _identity(manifest)
    if manifest.get("generation_id") != _generation_id(identity):
        return False
    try:
        current_code = _producer_code_manifest()
    except OSError:
        return False
    return identity.get("producer_code") == current_code


def _artifact_payload(
    private_root: Path,
    record: dict[str, Any],
    candidate_id: str,
    kind: str,
) -> tuple[Path, dict[str, Any]] | None:
    artifact = record.get(kind)
    if not isinstance(artifact, dict):
        return None
    try:
        root = Path(private_root).resolve()
        path = (root / str(artifact.get("path") or "")).resolve()
        path.relative_to(root)
        if _sha256(path) != str(artifact.get("sha256") or ""):
            return None
    except (OSError, ValueError):
        return None
    payload = _load_dict(path)
    if payload is None or str(payload.get("candidate_id") or "") != candidate_id:
        return None
    return path, payload


def _active_payloads(
    private_root: Path,
    manifest: dict[str, Any],
    candidate_id: str,
) -> dict[str, tuple[Path, dict[str, Any]]] | None:
    if not _base_manifest_valid(manifest):
        return None
    if candidate_id not in (manifest.get("exported_ids") or []):
        return None
    if candidate_id in (manifest.get("incomplete_ids") or []):
        return None
    active = manifest.get("active")
    record = active.get(candidate_id) if isinstance(active, dict) else None
    if (
        not isinstance(record, dict)
        or str(record.get("candidate_id") or "") != candidate_id
    ):
        return None
    requests = (
        raw_requests
        if isinstance(raw_requests := manifest.get("requests"), dict)
        else {}
    )
    if requests.get(candidate_id) != record.get("request"):
        return None
    payloads: dict[str, tuple[Path, dict[str, Any]]] = {}
    for kind in ("request", "report", "verdict", "setup_card"):
        artifact = _artifact_payload(private_root, record, candidate_id, kind)
        if artifact is None:
            return None
        payloads[kind] = artifact
    hard_status = str(record.get("hard_status") or "")
    report_verdict = payloads["report"][1].get("verdict")
    report_status = (
        str(report_verdict.get("hard_status") or "")
        if isinstance(report_verdict, dict)
        else ""
    )
    if (
        not isinstance(report_verdict, dict)
        or str(report_verdict.get("candidate_id") or "") != candidate_id
    ):
        return None
    if not hard_status or any(
        status != hard_status
        for status in (
            str(payloads["verdict"][1].get("hard_status") or ""),
            str(payloads["setup_card"][1].get("hard_status") or ""),
            report_status,
        )
    ):
        return None
    request = payloads["request"][1]
    report = payloads["report"][1]
    card = payloads["setup_card"][1]
    for field in ("symbol", "timeframe", "strategy_id"):
        values = [str(payload.get(field) or "") for payload in (request, report, card)]
        if len(set(values)) != 1:
            return None
    if (request.get("params") or {}) != (card.get("params") or {}):
        return None
    if str(card.get("setup_id") or "") != f"setup-{candidate_id}":
        return None
    if card.get("paper_forward_ready") is True and hard_status != "PAPER_FORWARD_READY":
        return None
    if card.get("main_engine_ready", False) is not False:
        return None
    return payloads


def current_candidate_ids(private_root: Path) -> set[str] | None:
    """Return fully verified active IDs, ``None`` only for legacy absence."""
    manifest = load_current_generation(private_root)
    if manifest is None:
        return None
    if not _base_manifest_valid(manifest):
        return set()
    return {
        str(candidate_id)
        for candidate_id in manifest.get("active", {})
        if str(candidate_id)
        and _active_payloads(private_root, manifest, str(candidate_id)) is not None
    }


def read_current_validation_artifact(
    private_root: Path,
    candidate_id: str,
    kind: str,
) -> dict[str, Any] | None:
    if kind not in {"request", "report", "verdict"}:
        raise ValueError("kind must be request, report, or verdict")
    manifest = load_current_generation(private_root)
    if manifest is None:
        path = _legacy_candidate_path(private_root, f"{kind}s", candidate_id)
        return _load_dict(path) if path is not None else None
    payloads = _active_payloads(Path(private_root), manifest, candidate_id)
    return payloads[kind][1] if payloads is not None else None


def read_current_setup_card(private_root: Path, path: Path) -> dict[str, Any] | None:
    payload = _load_dict(Path(path))
    if payload is None:
        return None
    manifest = load_current_generation(private_root)
    if manifest is None:
        return payload
    candidate_id = str(payload.get("candidate_id") or "")
    payloads = _active_payloads(Path(private_root), manifest, candidate_id)
    if payloads is None:
        return None
    expected_path, expected_payload = payloads["setup_card"]
    try:
        if Path(path).resolve() != expected_path.resolve():
            return None
    except OSError:
        return None
    return expected_payload


def read_current_setup_card_for_candidate(
    private_root: Path,
    candidate_id: str,
) -> dict[str, Any] | None:
    manifest = load_current_generation(private_root)
    if manifest is None:
        return None
    payloads = _active_payloads(Path(private_root), manifest, candidate_id)
    return payloads["setup_card"][1] if payloads is not None else None
