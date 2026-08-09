"""Explicit cutover and canonical runtime for Paper Evidence v2.

The module deliberately separates three things that legacy launchers mixed:

* shadow/parity evidence, which grants no runtime authority;
* an explicit, digest-bound cutover marker written only by an operator command;
* the canonical runtime owner, which opens an existing store and binds its writer
  fence to the already acquired canonical farm process identity.

Nothing here discovers credentials, starts a process, sends Telegram messages, or
enables execution.  A missing/stale marker is a hard refusal, not a request to create
or migrate canonical state implicitly.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.research_lab.paper_evidence_store import (
    PaperEvidenceConflict,
    PaperEvidenceStore,
    PaperWriterLease,
)
from src.research_lab.paper_generation_contract import canonical_digest
from src.research_lab.paper_generation_run import run_paper_generation_v2
from src.research_lab.paper_money_model import default_paper_money_model
from src.research_lab.ownership import ProcessIdentity

CUTOVER_SCHEMA = "PaperEvidenceCutoverManifest.v2"
SHADOW_PARITY_SCHEMA = "PaperEvidenceShadowParity.v2"
DEFAULT_DATABASE_RELATIVE = Path("state") / "derived" / "paper_evidence.sqlite3"
DEFAULT_MANIFEST_RELATIVE = Path("state") / "paper_evidence_cutover.v2.json"
DEFAULT_PRODUCER_ID = "canonical-paper-signals"
DEFAULT_PRODUCER_METHOD = "paper-signals-current-generation.v2"
DEFAULT_SIMULATOR_MANIFEST = "paper-runtime-no-lookahead.v2"
DEFAULT_LIFECYCLE_METHOD = "paper-lifecycle-account.v2"


def current_checkout_revision(repository_root: Path | str | None = None) -> str:
    """Return the exact local commit without consulting a remote or the network."""
    root = (
        Path(repository_root).resolve()
        if repository_root is not None
        else Path(__file__).resolve().parents[2]
    )
    try:
        result = subprocess.run(  # noqa: S603 - fixed local Git command
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PaperEvidenceConflict(
            "paper evidence checkout revision is unavailable"
        ) from exc
    revision = result.stdout.strip().lower()
    if (
        result.returncode != 0
        or len(revision) != 40
        or any(character not in "0123456789abcdef" for character in revision)
    ):
        raise PaperEvidenceConflict("paper evidence checkout revision is invalid")
    return revision


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _manifest_digest(payload: dict[str, Any]) -> str:
    content = {key: value for key, value in payload.items() if key != "manifest_digest"}
    return "sha256:" + hashlib.sha256(_canonical(content).encode("utf-8")).hexdigest()


def _resolved_within(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise PaperEvidenceConflict("paper evidence path escapes private root") from exc
    return resolved


def default_account_config() -> dict[str, Any]:
    model = default_paper_money_model()
    return {
        "currency": "USDT",
        "deposit": model.deposit_usdt,
        "leverage": model.leverage,
        "position_margin": model.position_margin_usdt,
        "allocation_policy": "one-primary-per-scenario.v1",
        "cost_policy": "net-pct-cost-inclusive.v1",
        "rounding_policy": "integer-microunits-half-even.v1",
        "method": "paper-account.v2",
        "paper_only": True,
        "execution_allowed": False,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".pending")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def activate_cutover(
    private_root: Path | str,
    *,
    owner_id: str,
    identity: ProcessIdentity,
    code_identity: str,
    account_config: dict[str, Any] | None = None,
    database_path: Path | str | None = None,
    manifest_path: Path | str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Activate one preflighted v2 authority and publish its marker last.

    This function is an operational primitive.  Callers must independently prove
    quiescence, backup/restore integrity and revision equality before invoking it.
    """
    if not owner_id or not code_identity:
        raise ValueError("cutover owner and code identity are required")
    if code_identity.strip().lower() != current_checkout_revision():
        raise PaperEvidenceConflict(
            "paper evidence cutover revision does not match current checkout"
        )
    root = Path(private_root)
    database = _resolved_within(
        root,
        Path(database_path)
        if database_path is not None
        else root / DEFAULT_DATABASE_RELATIVE,
    )
    marker = _resolved_within(
        root,
        Path(manifest_path)
        if manifest_path is not None
        else root / DEFAULT_MANIFEST_RELATIVE,
    )
    store = (
        PaperEvidenceStore.open_existing(database)
        if database.is_file()
        else PaperEvidenceStore(database)
    )
    if not database.is_file():
        store.activate()
    lease: PaperWriterLease | None = None
    try:
        lease = store.acquire_writer(
            owner_id=owner_id,
            identity=identity,
            lease_seconds=90.0,
        )
        account_generation_id = store.create_account_genesis(
            lease,
            account_config or default_account_config(),
        )
        account_model = store.account_model(account_generation_id)
        store.release_writer(lease)
        lease = None
        integrity = str(
            store.connection.execute("PRAGMA integrity_check").fetchone()[0]
        )
        if integrity != "ok":
            raise PaperEvidenceConflict("paper evidence cutover integrity check failed")
    finally:
        if lease is not None:
            try:
                store.release_writer(lease)
            except PaperEvidenceConflict:
                pass
        store.close()
    payload = {
        "schema": CUTOVER_SCHEMA,
        "status": "active",
        "authority_database_relative_path": DEFAULT_DATABASE_RELATIVE.as_posix(),
        "account_generation_id": account_generation_id,
        "account_model_digest": canonical_digest(account_model),
        "code_identity": code_identity,
        "producer_id": DEFAULT_PRODUCER_ID,
        "producer_method_identity": DEFAULT_PRODUCER_METHOD,
        "simulator_manifest_id": DEFAULT_SIMULATOR_MANIFEST,
        "lifecycle_method_identity": DEFAULT_LIFECYCLE_METHOD,
        "activated_at": float(time.time() if now is None else now),
        "paper_only": True,
        "execution_allowed": False,
        "legacy_authority": False,
    }
    payload["manifest_digest"] = _manifest_digest(payload)
    _write_json_atomic(marker, payload)
    return payload | {
        "manifest_path": str(marker),
        "database_path": str(database),
        "integrity_check": "ok",
    }


def rollback_cutover(
    private_root: Path | str,
    *,
    manifest_path: Path | str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Disable startup before runtime without deleting authority evidence."""
    root = Path(private_root)
    marker = _resolved_within(
        root,
        Path(manifest_path)
        if manifest_path is not None
        else root / DEFAULT_MANIFEST_RELATIVE,
    )
    payload = load_cutover_manifest(root, manifest_path=marker, require_active=False)
    if payload["status"] == "rolled_back":
        return payload | {"changed": 0, "manifest_path": str(marker)}
    payload = {
        **payload,
        "status": "rolled_back",
        "rolled_back_at": float(time.time() if now is None else now),
    }
    payload["manifest_digest"] = _manifest_digest(payload)
    _write_json_atomic(marker, payload)
    return payload | {"changed": 1, "manifest_path": str(marker)}


def load_cutover_manifest(
    private_root: Path | str,
    *,
    manifest_path: Path | str | None = None,
    require_active: bool = True,
) -> dict[str, Any]:
    root = Path(private_root)
    marker = _resolved_within(
        root,
        Path(manifest_path)
        if manifest_path is not None
        else root / DEFAULT_MANIFEST_RELATIVE,
    )
    if not marker.is_file():
        raise PaperEvidenceConflict("paper evidence cutover manifest is missing")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperEvidenceConflict(
            "paper evidence cutover manifest is unreadable"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema") != CUTOVER_SCHEMA:
        raise PaperEvidenceConflict("paper evidence cutover schema mismatch")
    if payload.get("manifest_digest") != _manifest_digest(payload):
        raise PaperEvidenceConflict("paper evidence cutover digest mismatch")
    if require_active and payload.get("status") != "active":
        raise PaperEvidenceConflict("paper evidence cutover is not active")
    if (
        payload.get("paper_only") is not True
        or payload.get("execution_allowed") is not False
        or payload.get("legacy_authority") is not False
    ):
        raise PaperEvidenceConflict(
            "paper evidence cutover crossed paper-only boundary"
        )
    required = {
        "account_generation_id",
        "account_model_digest",
        "code_identity",
        "producer_id",
        "producer_method_identity",
        "simulator_manifest_id",
        "lifecycle_method_identity",
    }
    if not all(str(payload.get(field) or "") for field in required):
        raise PaperEvidenceConflict("paper evidence cutover identity is incomplete")
    if (
        payload.get("authority_database_relative_path")
        != DEFAULT_DATABASE_RELATIVE.as_posix()
    ):
        raise PaperEvidenceConflict("paper evidence authority path is not canonical")
    return payload


def compare_shadow_parity(
    legacy_items: list[dict[str, Any]],
    v2_projection: dict[str, Any],
) -> dict[str, Any]:
    """Compare content fields while excluding v2-only immutable identities."""
    identity_fields = {
        "paper_generation_run_id",
        "paper_subject_generation_id",
        "allocation_lifecycle_event_id",
        "terminal_lifecycle_event_id",
        "account_generation_id",
        "paper_account_decision",
    }

    def normalized(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            [
                {
                    key: value
                    for key, value in item.items()
                    if key not in identity_fields
                }
                for item in items
            ],
            key=lambda item: str(item.get("source_signal_id") or ""),
        )

    legacy = normalized(legacy_items)
    v2 = normalized(
        [item for item in v2_projection.get("items") or [] if isinstance(item, dict)]
    )
    report = {
        "schema": SHADOW_PARITY_SCHEMA,
        "legacy_count": len(legacy),
        "v2_count": len(v2),
        "legacy_content_digest": canonical_digest(legacy),
        "v2_content_digest": canonical_digest(v2),
        "current_v2_generation": bool(v2_projection.get("current")),
        "paper_only": True,
        "execution_allowed": False,
    }
    return report | {
        "parity": bool(
            report["current_v2_generation"]
            and report["legacy_count"] == report["v2_count"]
            and report["legacy_content_digest"] == report["v2_content_digest"]
        )
    }


def run_forward_shadow_replay(
    source_private_root: Path | str,
    shadow_root: Path | str,
    *,
    provider: Any,
    owner_id: str,
    identity: ProcessIdentity,
    code_identity: str,
    validation_generation_id: str,
    now_ms: int,
) -> dict[str, Any]:
    """Replay authenticated active signals into an isolated forward-only v2 root.

    Only the canonical paper-signal ledger is copied. No credential/configuration
    file, historical database, delivery state, or recipient surface is discovered.
    The shadow root must be distinct and must not already contain a v2 database.
    """
    source_root = Path(source_private_root).resolve()
    target_root = Path(shadow_root).resolve()
    if source_root == target_root:
        raise PaperEvidenceConflict("shadow root must differ from source private root")
    source_ledger = _resolved_within(
        source_root,
        source_root / "state" / "derived" / "paper_signals.jsonl",
    )
    if not source_ledger.is_file():
        raise PaperEvidenceConflict("paper signal ledger is missing for shadow replay")
    target_database = target_root / DEFAULT_DATABASE_RELATIVE
    if target_database.exists():
        raise PaperEvidenceConflict("shadow paper evidence database already exists")
    target_ledger = target_root / "state" / "derived" / "paper_signals.jsonl"
    target_ledger.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_ledger, target_ledger)
    source_digest = hashlib.sha256(source_ledger.read_bytes()).hexdigest()
    if hashlib.sha256(target_ledger.read_bytes()).hexdigest() != source_digest:
        raise PaperEvidenceConflict("shadow paper signal copy digest mismatch")

    activated = activate_cutover(
        target_root,
        owner_id=f"{owner_id}-activation",
        identity=identity,
        code_identity=code_identity,
    )
    runtime = CanonicalPaperGenerationRuntime.open_required(
        target_root,
        owner_id=owner_id,
        identity=identity,
    )
    try:
        generation = runtime.run(
            provider=provider,
            now_ms=now_ms,
            validation_generation_id=validation_generation_id,
        )
        parity = compare_shadow_parity(
            list(generation["trades"].get("items") or []),
            generation["projection"],
        )
    finally:
        runtime.close()
    return {
        "schema": "PaperEvidenceForwardShadowReplay.v2",
        "source_ledger_sha256": source_digest,
        "source_rows": int(generation["bridge"].get("source_rows") or 0),
        "run_id": str(generation["run_id"]),
        "producer_generation_id": str(generation["producer_generation_id"]),
        "account_generation_id": str(generation["account_generation_id"]),
        "database_path": str(target_database),
        "manifest_digest": str(activated["manifest_digest"]),
        "parity": parity,
        "paper_only": True,
        "execution_allowed": False,
    }


class _PaperWriterHeartbeat:
    def __init__(
        self,
        store: PaperEvidenceStore,
        lease: PaperWriterLease,
        *,
        lease_seconds: float,
        interval_seconds: float,
        on_failure: Callable[[BaseException, dict[str, Any]], None] | None,
    ) -> None:
        self.store = store
        self.lease = lease
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self.on_failure = on_failure
        self.failure: BaseException | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="paper-evidence-writer-heartbeat",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.lease = self.store.renew_writer(
                    self.lease,
                    lease_seconds=self.lease_seconds,
                )
            except BaseException as exc:  # noqa: BLE001 - fail-closed signal boundary
                self.failure = exc
                self._stop.set()
                if self.on_failure is not None:
                    self.on_failure(
                        exc,
                        {
                            "failure_kind": "paper_evidence_writer_lease",
                            "owner_id": self.lease.owner_id,
                            "fence": self.lease.fence,
                        },
                    )
                return

    def raise_if_failed(self) -> None:
        if self.failure is not None:
            raise PaperEvidenceConflict(
                "paper evidence writer heartbeat failed"
            ) from self.failure

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(5.0, self.interval_seconds + 1.0))
        if self._thread.is_alive():
            raise PaperEvidenceConflict("paper evidence writer heartbeat did not stop")


@dataclass
class CanonicalPaperGenerationRuntime:
    private_root: Path
    manifest: dict[str, Any]
    store: PaperEvidenceStore
    lease: PaperWriterLease
    heartbeat: _PaperWriterHeartbeat

    @classmethod
    def open_required(
        cls,
        private_root: Path | str,
        *,
        owner_id: str,
        identity: ProcessIdentity,
        manifest_path: Path | str | None = None,
        lease_seconds: float = 90.0,
        heartbeat_interval_seconds: float = 20.0,
        on_failure: Callable[[BaseException, dict[str, Any]], None] | None = None,
        expected_code_identity: str | None = None,
    ) -> "CanonicalPaperGenerationRuntime":
        root = Path(private_root)
        manifest = load_cutover_manifest(root, manifest_path=manifest_path)
        expected_revision = (
            expected_code_identity.strip().lower()
            if expected_code_identity is not None
            else current_checkout_revision()
        )
        if str(manifest["code_identity"]).strip().lower() != expected_revision:
            raise PaperEvidenceConflict(
                "paper evidence cutover revision does not match current checkout"
            )
        database = _resolved_within(
            root,
            root / str(manifest["authority_database_relative_path"]),
        )
        store = PaperEvidenceStore.open_existing(database)
        lease: PaperWriterLease | None = None
        try:
            lease = store.acquire_writer(
                owner_id=owner_id,
                identity=identity,
                lease_seconds=lease_seconds,
            )
            account_model = store.account_model(str(manifest["account_generation_id"]))
            if canonical_digest(account_model) != manifest["account_model_digest"]:
                raise PaperEvidenceConflict("paper account cutover digest mismatch")
            heartbeat = _PaperWriterHeartbeat(
                store,
                lease,
                lease_seconds=lease_seconds,
                interval_seconds=heartbeat_interval_seconds,
                on_failure=on_failure,
            )
            heartbeat.start()
            return cls(root, manifest, store, lease, heartbeat)
        except Exception:
            if lease is not None:
                try:
                    store.release_writer(lease)
                except PaperEvidenceConflict:
                    pass
            store.close()
            raise

    @property
    def database_path(self) -> Path:
        return self.store.path

    def raise_if_failed(self) -> None:
        self.heartbeat.raise_if_failed()

    def run(
        self,
        *,
        provider: Any,
        now_ms: int,
        validation_generation_id: str,
    ) -> dict[str, Any]:
        self.raise_if_failed()
        producer_id = str(self.manifest["producer_id"])
        cursor = self.store.latest_producer_cursor(producer_id)
        parent = str(cursor["producer_generation_id"] or "") or None
        result = run_paper_generation_v2(
            self.private_root,
            store=self.store,
            lease=self.lease,
            account_generation_id=str(self.manifest["account_generation_id"]),
            provider=provider,
            producer_id=producer_id,
            producer_sequence=int(cursor["producer_sequence"]) + 1,
            code_identity=str(self.manifest["code_identity"]),
            producer_method_identity=str(self.manifest["producer_method_identity"]),
            simulator_manifest_id=str(self.manifest["simulator_manifest_id"]),
            lifecycle_method_identity=str(self.manifest["lifecycle_method_identity"]),
            required_validation_generation_id=validation_generation_id,
            now_ms=now_ms,
            parent_producer_generation_id=parent,
        )
        self.raise_if_failed()
        projection = PaperEvidenceStore.read_completed_projection(
            self.database_path,
            "trades",
            expected_run_id=str(result["run_id"]),
        )
        if not projection.get("current"):
            raise PaperEvidenceConflict(
                "paper generation did not publish current projection"
            )
        return result | {"projection": projection}

    def close(self) -> None:
        heartbeat_error: BaseException | None = None
        try:
            self.heartbeat.stop()
        except BaseException as exc:  # noqa: BLE001 - close all owned resources first
            heartbeat_error = exc
        try:
            self.store.release_writer(self.lease)
        except PaperEvidenceConflict:
            if heartbeat_error is None and self.heartbeat.failure is None:
                raise
        finally:
            self.store.close()
        if heartbeat_error is not None:
            raise heartbeat_error
