"""Immutable, fenced paper lifecycle and account evidence.

The store is paper/research-only.  It deliberately has no provider, exchange,
credential, Telegram, or process-control imports.  Activation is explicit so
ordinary readers cannot migrate a legacy private root by opening a status view.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Callable

from src.research_lab.ownership import ProcessIdentity

SCHEMA_VERSION = "paper-evidence-store.v2"
WRITER_RESOURCE = "paper_evidence_writer"
STAGES = ("bridge", "consumer", "queue", "observer", "account", "projection")
MONEY_SCALE = 1_000_000
RATIO_SCALE = 1_000_000


class PaperEvidenceConflict(RuntimeError):
    """The requested mutation contradicts immutable or generation state."""


class StalePaperWriter(PaperEvidenceConflict):
    """The supplied owner/fence no longer has mutation authority."""


@dataclass(frozen=True)
class PaperWriterLease:
    owner_id: str
    identity: ProcessIdentity
    fence: int
    lease_expires_at: float


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _stable_id(namespace: str, value: Any, *, length: int = 32) -> str:
    return f"{namespace}_{_digest(value).split(':', 1)[1][:length]}"


def _scaled(value: Any, scale: int) -> int:
    return int(
        (Decimal(str(value)) * scale).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    )


def _unscaled(value: int, scale: int) -> float:
    return float(Decimal(int(value)) / scale)


def _pnl_delta_units(config: dict[str, Any], net_pct_units: int) -> int:
    return int(
        (
            Decimal(int(config["position_margin_microunits"]))
            * Decimal(int(config["leverage_microunits"]))
            * Decimal(net_pct_units)
            / RATIO_SCALE
            / RATIO_SCALE
            / 100
        ).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    )


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row is not None else {}


class PaperEvidenceStore:
    """One SQLite authority for writer fencing, lifecycle, and paper account state."""

    def __init__(
        self, path: Path | str, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise PaperEvidenceConflict(
                "paper evidence store is not explicitly activated"
            )
        return self._conn

    def activate(self) -> None:
        """Explicitly create/upgrade only the selected v2 database path."""
        with self._lock:
            if self._conn is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_writer_lease (
                    resource_id TEXT PRIMARY KEY CHECK(resource_id='paper_evidence_writer'),
                    owner_id TEXT,
                    pid INTEGER,
                    started_at REAL,
                    executable TEXT,
                    command_digest TEXT,
                    lease_expires_at REAL,
                    next_fence INTEGER NOT NULL DEFAULT 0 CHECK(next_fence >= 0),
                    mutation_seq INTEGER NOT NULL DEFAULT 0 CHECK(mutation_seq >= 0),
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS account_geneses (
                    generation_id TEXT PRIMARY KEY,
                    parent_generation_id TEXT REFERENCES account_geneses(generation_id),
                    config_digest TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    created_fence INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    paper_only INTEGER NOT NULL CHECK(paper_only=1),
                    execution_allowed INTEGER NOT NULL CHECK(execution_allowed=0)
                );
                CREATE TABLE IF NOT EXISTS producer_generations (
                    producer_generation_id TEXT PRIMARY KEY,
                    producer_id TEXT NOT NULL,
                    producer_sequence INTEGER NOT NULL,
                    parent_generation_id TEXT REFERENCES producer_generations(producer_generation_id),
                    status TEXT NOT NULL CHECK(status IN ('completed','failed','incomplete')),
                    manifest_digest TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    member_set_digest TEXT NOT NULL,
                    expected_member_count INTEGER NOT NULL,
                    created_fence INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(producer_id,producer_sequence)
                );
                CREATE TABLE IF NOT EXISTS producer_generation_members (
                    producer_generation_id TEXT NOT NULL REFERENCES producer_generations(producer_generation_id),
                    logical_id TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    source_validation_generation_id TEXT NOT NULL,
                    disposition TEXT NOT NULL CHECK(disposition IN ('active','withdrawn','rejected')),
                    PRIMARY KEY(producer_generation_id,logical_id)
                );
                CREATE TABLE IF NOT EXISTS paper_runs (
                    run_id TEXT PRIMARY KEY,
                    source_generation_id TEXT NOT NULL,
                    expected_ids_digest TEXT NOT NULL,
                    expected_ids_json TEXT NOT NULL,
                    source_completed INTEGER NOT NULL CHECK(source_completed IN (0,1)),
                    status TEXT NOT NULL CHECK(status IN ('pending','completed','failed')),
                    created_fence INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_run_stages (
                    run_id TEXT NOT NULL REFERENCES paper_runs(run_id),
                    stage TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','completed','failed')),
                    input_digest TEXT,
                    output_digest TEXT,
                    failure_reason TEXT,
                    PRIMARY KEY(run_id, stage)
                );
                CREATE TABLE IF NOT EXISTS paper_run_failures (
                    failure_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES paper_runs(run_id),
                    stage TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    writer_fence INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_current_run (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    run_id TEXT NOT NULL REFERENCES paper_runs(run_id),
                    promoted_fence INTEGER NOT NULL,
                    promoted_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_subjects (
                    subject_generation_id TEXT PRIMARY KEY,
                    logical_id TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES paper_runs(run_id),
                    payload_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    supersedes_generation_id TEXT REFERENCES paper_subjects(subject_generation_id),
                    state TEXT NOT NULL CHECK(state IN ('provisional','active','superseded','withdrawn')),
                    created_fence INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_subject_per_logical
                    ON paper_subjects(logical_id) WHERE state='active';
                CREATE TABLE IF NOT EXISTS observation_batches (
                    observation_id TEXT PRIMARY KEY,
                    subject_generation_id TEXT NOT NULL REFERENCES paper_subjects(subject_generation_id),
                    run_id TEXT NOT NULL REFERENCES paper_runs(run_id),
                    rows_digest TEXT NOT NULL,
                    rows_json TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    available_at REAL NOT NULL,
                    acquisition_id TEXT NOT NULL,
                    provider_identity TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    created_fence INTEGER NOT NULL,
                    CHECK(available_at >= observed_at)
                );
                CREATE TABLE IF NOT EXISTS subject_cursors (
                    subject_generation_id TEXT PRIMARY KEY REFERENCES paper_subjects(subject_generation_id),
                    state TEXT NOT NULL,
                    event_seq INTEGER NOT NULL,
                    last_event_hash TEXT NOT NULL,
                    last_observation_id TEXT,
                    updated_fence INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accepted_observations (
                    subject_generation_id TEXT NOT NULL REFERENCES paper_subjects(subject_generation_id),
                    accepted_seq INTEGER NOT NULL,
                    prior_observation_id TEXT,
                    observation_id TEXT NOT NULL UNIQUE REFERENCES observation_batches(observation_id),
                    run_id TEXT NOT NULL REFERENCES paper_runs(run_id),
                    writer_fence INTEGER NOT NULL,
                    accepted_at REAL NOT NULL,
                    PRIMARY KEY(subject_generation_id,accepted_seq)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_events (
                    lifecycle_event_id TEXT PRIMARY KEY,
                    subject_generation_id TEXT NOT NULL REFERENCES paper_subjects(subject_generation_id),
                    event_seq INTEGER NOT NULL,
                    prior_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL CHECK(event_type IN (
                        'position_opened','position_closed','outcome_revised',
                        'source_withdrawn','source_reintroduced'
                    )),
                    observation_id TEXT REFERENCES observation_batches(observation_id),
                    payload_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    supersedes_event_id TEXT REFERENCES lifecycle_events(lifecycle_event_id),
                    writer_fence INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    paper_only INTEGER NOT NULL CHECK(paper_only=1),
                    execution_allowed INTEGER NOT NULL CHECK(execution_allowed=0),
                    UNIQUE(subject_generation_id, event_seq)
                );
                CREATE TABLE IF NOT EXISTS account_events (
                    account_event_id TEXT PRIMARY KEY,
                    account_generation_id TEXT NOT NULL REFERENCES account_geneses(generation_id),
                    account_seq INTEGER NOT NULL,
                    prior_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL CHECK(event_type IN (
                        'position_opened','position_closed','pnl_adjustment',
                        'allocation_rejected','counterfactual_excluded','account_rebased'
                    )),
                    subject_generation_id TEXT REFERENCES paper_subjects(subject_generation_id),
                    lifecycle_event_id TEXT REFERENCES lifecycle_events(lifecycle_event_id),
                    account_model_digest TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    supersedes_account_event_id TEXT REFERENCES account_events(account_event_id),
                    writer_fence INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    paper_only INTEGER NOT NULL CHECK(paper_only=1),
                    execution_allowed INTEGER NOT NULL CHECK(execution_allowed=0),
                    UNIQUE(account_generation_id, account_seq),
                    CHECK(
                        (event_type='account_rebased' AND lifecycle_event_id IS NULL)
                        OR (event_type<>'account_rebased' AND lifecycle_event_id IS NOT NULL)
                    )
                );
                CREATE TABLE IF NOT EXISTS scheduling_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    cursor INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS projection_materializations (
                    projection_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES paper_runs(run_id),
                    projection_kind TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','completed','failed')),
                    high_water_mark TEXT NOT NULL,
                    input_digests_json TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    envelope_digest TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    generation_path TEXT NOT NULL,
                    account_generation_id TEXT,
                    subject_generation_ids_json TEXT NOT NULL,
                    writer_fence INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    completed_at REAL
                );
                CREATE TABLE IF NOT EXISTS paper_run_mutation_intents (
                    intent_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES paper_runs(run_id),
                    intent_order INTEGER NOT NULL,
                    intent_type TEXT NOT NULL,
                    subject_generation_id TEXT REFERENCES paper_subjects(subject_generation_id),
                    observation_id TEXT REFERENCES observation_batches(observation_id),
                    account_generation_id TEXT REFERENCES account_geneses(generation_id),
                    payload_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    supersedes_event_id TEXT,
                    expected_subject_seq INTEGER NOT NULL,
                    expected_subject_hash TEXT NOT NULL,
                    expected_account_seq INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('planned','applied','rejected')),
                    applied_lifecycle_event_id TEXT,
                    applied_account_event_id TEXT,
                    created_fence INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(run_id,intent_order)
                );
                CREATE TABLE IF NOT EXISTS paper_subject_membership_intents (
                    run_id TEXT NOT NULL REFERENCES paper_runs(run_id),
                    subject_generation_id TEXT NOT NULL REFERENCES paper_subjects(subject_generation_id),
                    intent_type TEXT NOT NULL CHECK(intent_type='reintroduce'),
                    expected_state TEXT NOT NULL CHECK(expected_state='withdrawn'),
                    created_fence INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(run_id,subject_generation_id,intent_type)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_completed_projection_per_kind_run
                    ON projection_materializations(run_id,projection_kind)
                    WHERE status='completed';
                CREATE TRIGGER IF NOT EXISTS immutable_account_geneses_update
                    BEFORE UPDATE ON account_geneses BEGIN
                    SELECT RAISE(ABORT, 'account genesis is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_account_geneses_delete
                    BEFORE DELETE ON account_geneses BEGIN
                    SELECT RAISE(ABORT, 'account genesis is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_producer_generations_update
                    BEFORE UPDATE ON producer_generations BEGIN
                    SELECT RAISE(ABORT, 'producer generation is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_producer_generations_delete
                    BEFORE DELETE ON producer_generations BEGIN
                    SELECT RAISE(ABORT, 'producer generation is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_producer_members_update
                    BEFORE UPDATE ON producer_generation_members BEGIN
                    SELECT RAISE(ABORT, 'producer generation member is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_producer_members_delete
                    BEFORE DELETE ON producer_generation_members BEGIN
                    SELECT RAISE(ABORT, 'producer generation member is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_observations_update
                    BEFORE UPDATE ON observation_batches BEGIN
                    SELECT RAISE(ABORT, 'observation batch is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_observations_delete
                    BEFORE DELETE ON observation_batches BEGIN
                    SELECT RAISE(ABORT, 'observation batch is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_accepted_observations_update
                    BEFORE UPDATE ON accepted_observations BEGIN
                    SELECT RAISE(ABORT, 'accepted observation is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_accepted_observations_delete
                    BEFORE DELETE ON accepted_observations BEGIN
                    SELECT RAISE(ABORT, 'accepted observation is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_lifecycle_events_update
                    BEFORE UPDATE ON lifecycle_events BEGIN
                    SELECT RAISE(ABORT, 'lifecycle event is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_lifecycle_events_delete
                    BEFORE DELETE ON lifecycle_events BEGIN
                    SELECT RAISE(ABORT, 'lifecycle event is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_account_events_update
                    BEFORE UPDATE ON account_events BEGIN
                    SELECT RAISE(ABORT, 'account event is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_account_events_delete
                    BEFORE DELETE ON account_events BEGIN
                    SELECT RAISE(ABORT, 'account event is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_run_failures_update
                    BEFORE UPDATE ON paper_run_failures BEGIN
                    SELECT RAISE(ABORT, 'run failure evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_run_failures_delete
                    BEFORE DELETE ON paper_run_failures BEGIN
                    SELECT RAISE(ABORT, 'run failure evidence is immutable'); END;
                INSERT OR IGNORE INTO scheduling_state(singleton, cursor) VALUES(1, 0);
                """
            )
            marker = conn.execute(
                "SELECT value FROM paper_meta WHERE key='schema_version'"
            ).fetchone()
            if marker is None:
                conn.execute(
                    "INSERT INTO paper_meta(key,value) VALUES('schema_version',?)",
                    (SCHEMA_VERSION,),
                )
            elif str(marker["value"]) != SCHEMA_VERSION:
                conn.close()
                raise PaperEvidenceConflict("unsupported paper evidence schema")
            conn.commit()
            self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _begin(self) -> sqlite3.Connection:
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        return conn

    @staticmethod
    def _valid_identity(identity: ProcessIdentity) -> bool:
        return bool(
            identity.pid > 0
            and identity.started_at > 0
            and identity.executable
            and identity.command_digest
        )

    def acquire_writer(
        self,
        *,
        owner_id: str,
        identity: ProcessIdentity,
        lease_seconds: float,
    ) -> PaperWriterLease:
        if not owner_id or not self._valid_identity(identity) or lease_seconds <= 0:
            raise ValueError("complete owner identity and positive lease are required")
        now = float(self._clock())
        with self._lock:
            conn = self._begin()
            try:
                row = conn.execute(
                    "SELECT * FROM paper_writer_lease WHERE resource_id=?",
                    (WRITER_RESOURCE,),
                ).fetchone()
                if row is None:
                    fence = 1
                    conn.execute(
                        "INSERT INTO paper_writer_lease(resource_id,next_fence,mutation_seq,updated_at) "
                        "VALUES(?,0,0,?)",
                        (WRITER_RESOURCE, now),
                    )
                else:
                    fence = int(row["next_fence"]) + 1
                    if (
                        row["owner_id"] is not None
                        and float(row["lease_expires_at"] or 0) > now
                    ):
                        raise PaperEvidenceConflict(
                            "paper evidence writer already owned"
                        )
                expires = now + float(lease_seconds)
                conn.execute(
                    """
                    UPDATE paper_writer_lease
                    SET owner_id=?,pid=?,started_at=?,executable=?,command_digest=?,
                        lease_expires_at=?,next_fence=?,updated_at=?
                    WHERE resource_id=?
                    """,
                    (
                        owner_id,
                        identity.pid,
                        identity.started_at,
                        identity.executable,
                        identity.command_digest,
                        expires,
                        fence,
                        now,
                        WRITER_RESOURCE,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return PaperWriterLease(owner_id, identity, fence, expires)

    def _assert_writer(
        self, conn: sqlite3.Connection, lease: PaperWriterLease
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM paper_writer_lease WHERE resource_id=?", (WRITER_RESOURCE,)
        ).fetchone()
        if row is None:
            raise StalePaperWriter("paper writer lease missing")
        same_identity = (
            int(row["pid"] or 0) == lease.identity.pid
            and float(row["started_at"] or 0) == lease.identity.started_at
            and str(row["executable"] or "") == lease.identity.executable
            and str(row["command_digest"] or "") == lease.identity.command_digest
        )
        if (
            str(row["owner_id"] or "") != lease.owner_id
            or int(row["next_fence"]) != lease.fence
            or float(row["lease_expires_at"] or 0) <= float(self._clock())
            or not same_identity
        ):
            raise StalePaperWriter("paper writer owner/fence/identity/expiry is stale")
        return row

    def _authorize(self, conn: sqlite3.Connection, lease: PaperWriterLease) -> None:
        row = self._assert_writer(conn, lease)
        updated = conn.execute(
            "UPDATE paper_writer_lease SET mutation_seq=mutation_seq+1,updated_at=? "
            "WHERE resource_id=? AND owner_id=? AND next_fence=? AND mutation_seq=?",
            (
                float(self._clock()),
                WRITER_RESOURCE,
                lease.owner_id,
                lease.fence,
                int(row["mutation_seq"]),
            ),
        )
        if updated.rowcount != 1:
            raise StalePaperWriter("paper writer mutation sequence changed")

    def renew_writer(
        self, lease: PaperWriterLease, *, lease_seconds: float
    ) -> PaperWriterLease:
        if lease_seconds <= 0:
            raise ValueError("positive writer lease required")
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                expires = float(self._clock()) + float(lease_seconds)
                updated = conn.execute(
                    "UPDATE paper_writer_lease SET lease_expires_at=?,updated_at=? "
                    "WHERE resource_id=? AND owner_id=? AND next_fence=?",
                    (
                        expires,
                        float(self._clock()),
                        WRITER_RESOURCE,
                        lease.owner_id,
                        lease.fence,
                    ),
                )
                if updated.rowcount != 1:
                    raise StalePaperWriter(
                        "paper writer renewal compare-and-set failed"
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return PaperWriterLease(lease.owner_id, lease.identity, lease.fence, expires)

    def release_writer(self, lease: PaperWriterLease) -> None:
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                updated = conn.execute(
                    "UPDATE paper_writer_lease SET owner_id=NULL,pid=NULL,started_at=NULL,"
                    "executable=NULL,command_digest=NULL,lease_expires_at=NULL,updated_at=? "
                    "WHERE resource_id=? AND owner_id=? AND next_fence=?",
                    (
                        float(self._clock()),
                        WRITER_RESOURCE,
                        lease.owner_id,
                        lease.fence,
                    ),
                )
                if updated.rowcount != 1:
                    raise StalePaperWriter(
                        "paper writer release compare-and-set failed"
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def create_account_genesis(
        self,
        lease: PaperWriterLease,
        config: dict[str, Any],
        *,
        parent_generation_id: str | None = None,
    ) -> str:
        required = {
            "currency",
            "deposit",
            "leverage",
            "position_margin",
            "allocation_policy",
            "cost_policy",
            "rounding_policy",
            "method",
        }
        if not required.issubset(config):
            raise ValueError("complete immutable paper account configuration required")
        if config.get("paper_only", True) is not True:
            raise ValueError("paper account genesis must be paper-only")
        if config.get("execution_allowed", False) is not False:
            raise ValueError("paper account genesis cannot allow execution")
        if (
            float(config["deposit"]) <= 0
            or float(config["position_margin"]) <= 0
            or float(config["leverage"]) <= 0
        ):
            raise ValueError("positive paper account values required")
        if (
            config["allocation_policy"] != "one-primary-per-scenario.v1"
            or config["cost_policy"] != "net-pct-cost-inclusive.v1"
            or config["rounding_policy"] != "integer-microunits-half-even.v1"
        ):
            raise ValueError("unsupported paper account policy identity")
        payload = {
            "schema": "PaperAccountGenesis.v2",
            **config,
            "paper_only": True,
            "execution_allowed": False,
            "money_scale": MONEY_SCALE,
            "ratio_scale": RATIO_SCALE,
            "deposit_microunits": _scaled(config["deposit"], MONEY_SCALE),
            "position_margin_microunits": _scaled(
                config["position_margin"], MONEY_SCALE
            ),
            "leverage_microunits": _scaled(config["leverage"], RATIO_SCALE),
        }
        config_digest = _digest(payload)
        generation_id = _stable_id(
            "paperaccountgeneration",
            {
                "config_digest": config_digest,
                "parent_generation_id": parent_generation_id or "",
            },
        )
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                existing = conn.execute(
                    "SELECT * FROM account_geneses WHERE generation_id=?",
                    (generation_id,),
                ).fetchone()
                if existing is not None:
                    conn.commit()
                    return generation_id
                roots = conn.execute(
                    "SELECT generation_id,config_digest FROM account_geneses "
                    "WHERE parent_generation_id IS NULL"
                ).fetchall()
                if parent_generation_id is None and roots:
                    same = next(
                        (row for row in roots if row["config_digest"] == config_digest),
                        None,
                    )
                    if same is not None:
                        conn.commit()
                        return str(same["generation_id"])
                    raise PaperEvidenceConflict(
                        "account config change requires an explicit parent generation"
                    )
                if parent_generation_id is not None:
                    parent = conn.execute(
                        "SELECT generation_id FROM account_geneses WHERE generation_id=?",
                        (parent_generation_id,),
                    ).fetchone()
                    if parent is None:
                        raise PaperEvidenceConflict("account parent generation missing")
                    parent_state = self._replay_account_conn(conn, parent_generation_id)
                    if parent_state["active_subjects"]:
                        raise PaperEvidenceConflict(
                            "account config generation cannot change with active positions"
                        )
                conn.execute(
                    "INSERT INTO account_geneses VALUES(?,?,?,?,?,?,?,?)",
                    (
                        generation_id,
                        parent_generation_id,
                        config_digest,
                        _canonical(payload),
                        lease.fence,
                        float(self._clock()),
                        1,
                        0,
                    ),
                )
                if parent_generation_id is not None:
                    rebase_payload = {
                        "opening_balance_microunits": int(
                            payload["deposit_microunits"]
                        ),
                        "parent_generation_id": parent_generation_id,
                        "reason": "explicit_account_generation_change",
                    }
                    self._insert_account_event(
                        conn,
                        lease,
                        generation_id,
                        "account_rebased",
                        rebase_payload,
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return generation_id

    def register_producer_generation(
        self,
        lease: PaperWriterLease,
        *,
        producer_id: str,
        producer_sequence: int,
        members: list[dict[str, str]],
        code_identity: str,
        method_identity: str,
        status: str = "completed",
        parent_generation_id: str | None = None,
    ) -> str:
        if (
            not producer_id
            or producer_sequence < 1
            or not code_identity
            or not method_identity
        ):
            raise ValueError("complete producer generation identity required")
        if status not in {"completed", "failed", "incomplete"}:
            raise ValueError("invalid producer generation status")
        normalized = sorted(
            [
                {
                    "logical_id": str(member["logical_id"]),
                    "payload_digest": str(member["payload_digest"]),
                    "source_validation_generation_id": str(
                        member["source_validation_generation_id"]
                    ),
                    "disposition": str(member.get("disposition") or "active"),
                }
                for member in members
            ],
            key=lambda member: member["logical_id"],
        )
        if len({member["logical_id"] for member in normalized}) != len(normalized):
            raise PaperEvidenceConflict(
                "producer generation contains duplicate logical IDs"
            )
        if any(
            not member["logical_id"]
            or not member["payload_digest"]
            or not member["source_validation_generation_id"]
            or member["disposition"] not in {"active", "withdrawn", "rejected"}
            for member in normalized
        ):
            raise PaperEvidenceConflict(
                "producer generation member identity is incomplete"
            )
        member_set_digest = _digest(normalized)
        manifest = {
            "schema": "PaperProducerGeneration.v2",
            "producer_id": producer_id,
            "producer_sequence": producer_sequence,
            "parent_generation_id": parent_generation_id or "",
            "status": status,
            "code_identity": code_identity,
            "method_identity": method_identity,
            "expected_member_count": len(normalized),
            "member_set_digest": member_set_digest,
        }
        manifest_digest = _digest(manifest)
        generation_id = _stable_id(
            "paperproducer",
            {
                "manifest_digest": manifest_digest,
                "member_set_digest": member_set_digest,
            },
        )
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                if parent_generation_id:
                    parent = conn.execute(
                        "SELECT * FROM producer_generations WHERE producer_generation_id=?",
                        (parent_generation_id,),
                    ).fetchone()
                    if parent is None or parent["producer_id"] != producer_id:
                        raise PaperEvidenceConflict(
                            "producer parent generation mismatch"
                        )
                    if int(parent["producer_sequence"]) >= producer_sequence:
                        raise PaperEvidenceConflict("producer sequence must increase")
                existing_sequence = conn.execute(
                    "SELECT * FROM producer_generations WHERE producer_id=? AND producer_sequence=?",
                    (producer_id, producer_sequence),
                ).fetchone()
                if existing_sequence is not None:
                    if existing_sequence["producer_generation_id"] != generation_id:
                        raise PaperEvidenceConflict(
                            "producer sequence identity reused with different content"
                        )
                    conn.commit()
                    return generation_id
                latest = conn.execute(
                    "SELECT * FROM producer_generations WHERE producer_id=? "
                    "ORDER BY producer_sequence DESC LIMIT 1",
                    (producer_id,),
                ).fetchone()
                if producer_sequence == 1:
                    if parent_generation_id is not None or latest is not None:
                        raise PaperEvidenceConflict(
                            "first producer generation cannot replace existing lineage"
                        )
                elif (
                    latest is None
                    or int(latest["producer_sequence"]) != producer_sequence - 1
                    or parent_generation_id != latest["producer_generation_id"]
                ):
                    raise PaperEvidenceConflict(
                        "producer generation requires its immediate authenticated parent"
                    )
                conn.execute(
                    "INSERT INTO producer_generations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        generation_id,
                        producer_id,
                        producer_sequence,
                        parent_generation_id,
                        status,
                        manifest_digest,
                        _canonical(manifest),
                        member_set_digest,
                        len(normalized),
                        lease.fence,
                        float(self._clock()),
                    ),
                )
                conn.executemany(
                    "INSERT INTO producer_generation_members VALUES(?,?,?,?,?)",
                    [
                        (
                            generation_id,
                            member["logical_id"],
                            member["payload_digest"],
                            member["source_validation_generation_id"],
                            member["disposition"],
                        )
                        for member in normalized
                    ],
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return generation_id

    def create_run(
        self,
        lease: PaperWriterLease,
        *,
        producer_generation_id: str,
    ) -> str:
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                producer = conn.execute(
                    "SELECT * FROM producer_generations WHERE producer_generation_id=?",
                    (producer_generation_id,),
                ).fetchone()
                if producer is None:
                    raise PaperEvidenceConflict("producer generation missing")
                manifest = json.loads(str(producer["manifest_json"]))
                member_rows = conn.execute(
                    "SELECT * FROM producer_generation_members WHERE producer_generation_id=? "
                    "ORDER BY logical_id",
                    (producer_generation_id,),
                ).fetchall()
                normalized = [
                    {
                        "logical_id": str(row["logical_id"]),
                        "payload_digest": str(row["payload_digest"]),
                        "source_validation_generation_id": str(
                            row["source_validation_generation_id"]
                        ),
                        "disposition": str(row["disposition"]),
                    }
                    for row in member_rows
                ]
                if (
                    _digest(manifest) != producer["manifest_digest"]
                    or manifest.get("member_set_digest")
                    != producer["member_set_digest"]
                    or int(manifest.get("expected_member_count", -1))
                    != int(producer["expected_member_count"])
                    or len(normalized) != int(producer["expected_member_count"])
                    or _digest(normalized) != producer["member_set_digest"]
                ):
                    raise PaperEvidenceConflict(
                        "producer generation manifest/member mismatch"
                    )
                expected = sorted(
                    member["logical_id"]
                    for member in normalized
                    if member["disposition"] == "active"
                )
                source_completed = str(producer["status"]) == "completed"
                payload = {
                    "schema": "PaperGenerationRun.v2",
                    "source_generation_id": producer_generation_id,
                    "source_manifest_digest": str(producer["manifest_digest"]),
                    "expected_logical_ids": expected,
                    "source_completed": source_completed,
                }
                run_id = _stable_id("paperrun", payload)
                existing = conn.execute(
                    "SELECT * FROM paper_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO paper_runs VALUES(?,?,?,?,?,?,?,?)",
                        (
                            run_id,
                            producer_generation_id,
                            _digest(expected),
                            _canonical(expected),
                            int(bool(source_completed)),
                            "pending",
                            lease.fence,
                            float(self._clock()),
                        ),
                    )
                    conn.executemany(
                        "INSERT INTO paper_run_stages(run_id,stage,ordinal,status) VALUES(?,?,?,'pending')",
                        [
                            (run_id, stage, ordinal)
                            for ordinal, stage in enumerate(STAGES)
                        ],
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return run_id

    def complete_stage(
        self,
        lease: PaperWriterLease,
        run_id: str,
        stage: str,
        *,
        input_digest: str,
        output_digest: str,
    ) -> None:
        if stage not in STAGES or not input_digest or not output_digest:
            raise ValueError("known stage and input/output digests required")
        ordinal = STAGES.index(stage)
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                run = conn.execute(
                    "SELECT * FROM paper_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                current = conn.execute(
                    "SELECT * FROM paper_run_stages WHERE run_id=? AND stage=?",
                    (run_id, stage),
                ).fetchone()
                if run is None or current is None or run["status"] != "pending":
                    raise PaperEvidenceConflict("paper run/stage is not pending")
                expected_input = str(run["source_generation_id"])
                if ordinal:
                    predecessor = conn.execute(
                        "SELECT * FROM paper_run_stages WHERE run_id=? AND ordinal=?",
                        (run_id, ordinal - 1),
                    ).fetchone()
                    if predecessor is None or predecessor["status"] != "completed":
                        raise PaperEvidenceConflict(
                            "paper stage predecessor is incomplete"
                        )
                    expected_input = str(predecessor["output_digest"])
                if input_digest != expected_input:
                    raise PaperEvidenceConflict("paper stage input digest mismatch")
                if current["status"] == "completed":
                    if (
                        current["input_digest"] == input_digest
                        and current["output_digest"] == output_digest
                    ):
                        conn.commit()
                        return
                    raise PaperEvidenceConflict(
                        "paper stage identity reused with different content"
                    )
                updated = conn.execute(
                    "UPDATE paper_run_stages SET status='completed',input_digest=?,output_digest=? "
                    "WHERE run_id=? AND stage=? AND status='pending'",
                    (input_digest, output_digest, run_id, stage),
                )
                if updated.rowcount != 1:
                    raise PaperEvidenceConflict("paper stage compare-and-set failed")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def fail_stage(
        self,
        lease: PaperWriterLease,
        run_id: str,
        stage: str,
        *,
        reason: str,
    ) -> None:
        if stage not in STAGES or not reason:
            raise ValueError("known stage and reason required")
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                updated = conn.execute(
                    "UPDATE paper_run_stages SET status='failed',failure_reason=? "
                    "WHERE run_id=? AND stage=? AND status='pending'",
                    (reason[:240], run_id, stage),
                )
                if updated.rowcount != 1:
                    raise PaperEvidenceConflict("paper stage is not pending")
                conn.execute(
                    "UPDATE paper_runs SET status='failed' WHERE run_id=?", (run_id,)
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def abort_run(
        self,
        lease: PaperWriterLease,
        run_id: str,
        *,
        stage: str,
        reason: str,
    ) -> None:
        """Append failure evidence when a completed stage cannot be finalized."""
        if stage not in STAGES or not reason:
            raise ValueError("known stage and reason required")
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                run = conn.execute(
                    "SELECT status FROM paper_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if run is None or run["status"] != "pending":
                    raise PaperEvidenceConflict(
                        "only a pending paper run can be aborted"
                    )
                identity = {"run_id": run_id, "stage": stage, "reason": reason[:240]}
                conn.execute(
                    "INSERT OR IGNORE INTO paper_run_failures VALUES(?,?,?,?,?,?)",
                    (
                        _stable_id("paperrunfailure", identity),
                        run_id,
                        stage,
                        reason[:240],
                        lease.fence,
                        float(self._clock()),
                    ),
                )
                conn.execute(
                    "UPDATE paper_runs SET status='failed' WHERE run_id=?", (run_id,)
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def promote_run(self, *_args: Any, **_kwargs: Any) -> None:
        raise PaperEvidenceConflict(
            "direct run promotion is forbidden; use finalize_run"
        )

    def finalize_run(self, lease: PaperWriterLease, run_id: str) -> dict[str, Any]:
        """Apply all planned authority and move current-run in one fenced commit."""
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                run = conn.execute(
                    "SELECT * FROM paper_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                stages = conn.execute(
                    "SELECT status FROM paper_run_stages WHERE run_id=? ORDER BY ordinal",
                    (run_id,),
                ).fetchall()
                if (
                    run is None
                    or not bool(run["source_completed"])
                    or len(stages) != len(STAGES)
                    or any(row["status"] != "completed" for row in stages)
                ):
                    raise PaperEvidenceConflict(
                        "only a complete paper generation run can be current"
                    )
                if run["status"] == "completed":
                    current = conn.execute(
                        "SELECT run_id FROM paper_current_run WHERE singleton=1"
                    ).fetchone()
                    if current is None or current["run_id"] != run_id:
                        raise PaperEvidenceConflict(
                            "completed run is not the current authoritative run"
                        )
                    applied_rows = conn.execute(
                        "SELECT * FROM paper_run_mutation_intents WHERE run_id=? ORDER BY intent_order",
                        (run_id,),
                    ).fetchall()
                    conn.commit()
                    return {
                        "run_id": run_id,
                        "applied_intents": len(applied_rows),
                        "current": True,
                    }
                if run["status"] != "pending":
                    raise PaperEvidenceConflict("failed paper run cannot be finalized")

                expected = set(json.loads(str(run["expected_ids_json"])))
                producer = conn.execute(
                    "SELECT * FROM producer_generations WHERE producer_generation_id=?",
                    (run["source_generation_id"],),
                ).fetchone()
                member_rows = conn.execute(
                    "SELECT * FROM producer_generation_members WHERE producer_generation_id=? "
                    "ORDER BY logical_id",
                    (run["source_generation_id"],),
                ).fetchall()
                normalized_members = [
                    {
                        "logical_id": str(row["logical_id"]),
                        "payload_digest": str(row["payload_digest"]),
                        "source_validation_generation_id": str(
                            row["source_validation_generation_id"]
                        ),
                        "disposition": str(row["disposition"]),
                    }
                    for row in member_rows
                ]
                producer_expected = {
                    member["logical_id"]
                    for member in normalized_members
                    if member["disposition"] == "active"
                }
                if (
                    producer is None
                    or producer["status"] != "completed"
                    or _digest(json.loads(str(producer["manifest_json"])))
                    != producer["manifest_digest"]
                    or len(normalized_members) != int(producer["expected_member_count"])
                    or _digest(normalized_members) != producer["member_set_digest"]
                    or producer_expected != expected
                    or _digest(sorted(expected)) != run["expected_ids_digest"]
                ):
                    raise PaperEvidenceConflict(
                        "paper run producer authority changed or mismatched"
                    )
                member_by_logical = {str(row["logical_id"]): row for row in member_rows}
                provisional = conn.execute(
                    "SELECT * FROM paper_subjects WHERE run_id=? AND state='provisional' "
                    "ORDER BY logical_id,subject_generation_id",
                    (run_id,),
                ).fetchall()
                for subject in provisional:
                    logical_id = str(subject["logical_id"])
                    active = conn.execute(
                        "SELECT * FROM paper_subjects WHERE logical_id=? AND state='active'",
                        (logical_id,),
                    ).fetchone()
                    supersedes = str(subject["supersedes_generation_id"] or "")
                    if active is not None:
                        if supersedes != str(active["subject_generation_id"]):
                            raise PaperEvidenceConflict(
                                "subject head changed before run finalization"
                            )
                        conn.execute(
                            "UPDATE paper_subjects SET state='superseded' "
                            "WHERE subject_generation_id=? AND state='active'",
                            (active["subject_generation_id"],),
                        )
                    elif supersedes:
                        prior = conn.execute(
                            "SELECT * FROM paper_subjects WHERE subject_generation_id=?",
                            (supersedes,),
                        ).fetchone()
                        if prior is None or prior["logical_id"] != logical_id:
                            raise PaperEvidenceConflict(
                                "subject reintroduction predecessor mismatch"
                            )
                    updated = conn.execute(
                        "UPDATE paper_subjects SET state='active' "
                        "WHERE subject_generation_id=? AND state='provisional'",
                        (subject["subject_generation_id"],),
                    )
                    if updated.rowcount != 1:
                        raise PaperEvidenceConflict(
                            "subject activation compare-and-set failed"
                        )
                    if supersedes and active is None:
                        self._append_system_lifecycle(
                            conn,
                            lease,
                            str(subject["subject_generation_id"]),
                            "source_reintroduced",
                            {
                                "supersedes_generation_id": supersedes,
                                "producer_run_id": run_id,
                            },
                            next_state="armed",
                        )

                reintroductions = conn.execute(
                    "SELECT * FROM paper_subject_membership_intents WHERE run_id=? "
                    "AND intent_type='reintroduce' ORDER BY subject_generation_id",
                    (run_id,),
                ).fetchall()
                for intent in reintroductions:
                    subject = conn.execute(
                        "SELECT * FROM paper_subjects WHERE subject_generation_id=?",
                        (intent["subject_generation_id"],),
                    ).fetchone()
                    if subject is None or subject["state"] != intent["expected_state"]:
                        raise PaperEvidenceConflict(
                            "subject reintroduction state changed before run finalization"
                        )
                    active = conn.execute(
                        "SELECT subject_generation_id FROM paper_subjects "
                        "WHERE logical_id=? AND state='active'",
                        (subject["logical_id"],),
                    ).fetchone()
                    if active is not None:
                        raise PaperEvidenceConflict(
                            "subject reintroduction conflicts with an active generation"
                        )
                    cursor = conn.execute(
                        "SELECT state FROM subject_cursors WHERE subject_generation_id=?",
                        (subject["subject_generation_id"],),
                    ).fetchone()
                    if cursor is None:
                        raise PaperEvidenceConflict(
                            "subject reintroduction cursor missing"
                        )
                    self._append_system_lifecycle(
                        conn,
                        lease,
                        str(subject["subject_generation_id"]),
                        "source_reintroduced",
                        {
                            "producer_run_id": run_id,
                            "subject_payload_digest": str(subject["payload_digest"]),
                        },
                        next_state=str(cursor["state"]),
                    )
                    updated = conn.execute(
                        "UPDATE paper_subjects SET state='active' "
                        "WHERE subject_generation_id=? AND state='withdrawn'",
                        (subject["subject_generation_id"],),
                    )
                    if updated.rowcount != 1:
                        raise PaperEvidenceConflict(
                            "subject reintroduction compare-and-set failed"
                        )

                active_rows = conn.execute(
                    "SELECT * FROM paper_subjects WHERE state='active'"
                ).fetchall()
                active_ids = {str(row["logical_id"]) for row in active_rows}
                if not expected.issubset(active_ids):
                    raise PaperEvidenceConflict(
                        "completed producer subject set is incomplete"
                    )
                for subject in active_rows:
                    logical_id = str(subject["logical_id"])
                    if logical_id not in expected:
                        continue
                    member = member_by_logical[logical_id]
                    subject_payload = json.loads(str(subject["payload_json"]))
                    if (
                        _digest(subject_payload) != str(subject["payload_digest"])
                        or str(
                            subject_payload.get("source_member_payload_digest") or ""
                        )
                        != str(member["payload_digest"])
                        or str(
                            subject_payload.get("source_validation_generation_id") or ""
                        )
                        != str(member["source_validation_generation_id"])
                    ):
                        raise PaperEvidenceConflict(
                            "active subject does not match completed producer member"
                        )
                withdrawn: list[str] = []
                for subject in active_rows:
                    if str(subject["logical_id"]) in expected:
                        continue
                    cursor = conn.execute(
                        "SELECT state FROM subject_cursors WHERE subject_generation_id=?",
                        (subject["subject_generation_id"],),
                    ).fetchone()
                    lifecycle_state = (
                        str(cursor["state"]) if cursor is not None else "armed"
                    )
                    self._append_system_lifecycle(
                        conn,
                        lease,
                        str(subject["subject_generation_id"]),
                        "source_withdrawn",
                        {
                            "producer_run_id": run_id,
                            "reason": "absent_from_complete_subject_set",
                        },
                        next_state=lifecycle_state,
                    )
                    conn.execute(
                        "UPDATE paper_subjects SET state='withdrawn' "
                        "WHERE subject_generation_id=? AND state='active'",
                        (subject["subject_generation_id"],),
                    )
                    withdrawn.append(str(subject["subject_generation_id"]))

                observations = conn.execute(
                    "SELECT * FROM observation_batches WHERE run_id=? "
                    "ORDER BY subject_generation_id,observed_at,available_at,observation_id",
                    (run_id,),
                ).fetchall()
                for observation in observations:
                    subject_generation_id = str(observation["subject_generation_id"])
                    cursor = conn.execute(
                        "SELECT * FROM subject_cursors WHERE subject_generation_id=?",
                        (subject_generation_id,),
                    ).fetchone()
                    if cursor is None:
                        raise PaperEvidenceConflict(
                            "accepted observation cursor missing"
                        )
                    accepted_seq = (
                        int(
                            conn.execute(
                                "SELECT COALESCE(MAX(accepted_seq),0) AS n "
                                "FROM accepted_observations WHERE subject_generation_id=?",
                                (subject_generation_id,),
                            ).fetchone()["n"]
                        )
                        + 1
                    )
                    conn.execute(
                        "INSERT INTO accepted_observations VALUES(?,?,?,?,?,?,?)",
                        (
                            subject_generation_id,
                            accepted_seq,
                            str(cursor["last_observation_id"] or "") or None,
                            observation["observation_id"],
                            run_id,
                            lease.fence,
                            float(self._clock()),
                        ),
                    )
                    updated = conn.execute(
                        "UPDATE subject_cursors SET last_observation_id=?,updated_fence=? "
                        "WHERE subject_generation_id=? AND "
                        "COALESCE(last_observation_id,'')=?",
                        (
                            observation["observation_id"],
                            lease.fence,
                            subject_generation_id,
                            str(cursor["last_observation_id"] or ""),
                        ),
                    )
                    if updated.rowcount != 1:
                        raise PaperEvidenceConflict(
                            "accepted observation compare-and-set failed"
                        )

                intents = conn.execute(
                    "SELECT * FROM paper_run_mutation_intents WHERE run_id=? "
                    "AND status='planned' ORDER BY intent_order",
                    (run_id,),
                ).fetchall()
                applied_intents: list[dict[str, str]] = []
                for intent in intents:
                    lifecycle_id, account_id, account_type = self._apply_planned_intent(
                        conn, lease, intent
                    )
                    applied_intents.append(
                        {
                            "intent_id": str(intent["intent_id"]),
                            "lifecycle_event_id": lifecycle_id,
                            "account_event_id": account_id,
                            "account_event_type": account_type,
                        }
                    )

                projections = conn.execute(
                    "SELECT * FROM projection_materializations WHERE run_id=? AND status='pending'",
                    (run_id,),
                ).fetchall()
                if not projections:
                    raise PaperEvidenceConflict(
                        "run finalization requires a prepared projection"
                    )
                high_water = self._high_water(conn)
                completed_projection_ids: list[str] = []
                for projection in projections:
                    draft = json.loads(str(projection["envelope_json"]))
                    envelope = {
                        **draft,
                        "materialization_status": "completed",
                        "store_high_water_mark": high_water,
                    }
                    self._verify_projection_references(conn, envelope)
                    envelope_digest = _digest(envelope)
                    conn.execute(
                        "UPDATE projection_materializations SET status='completed',high_water_mark=?,"
                        "envelope_digest=?,envelope_json=?,completed_at=? "
                        "WHERE projection_id=? AND status='pending'",
                        (
                            high_water,
                            envelope_digest,
                            _canonical(envelope),
                            float(self._clock()),
                            projection["projection_id"],
                        ),
                    )
                    completed_projection_ids.append(str(projection["projection_id"]))
                conn.execute(
                    "UPDATE paper_runs SET status='completed' WHERE run_id=?", (run_id,)
                )
                conn.execute(
                    "INSERT INTO paper_current_run(singleton,run_id,promoted_fence,promoted_at) "
                    "VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET "
                    "run_id=excluded.run_id,promoted_fence=excluded.promoted_fence,promoted_at=excluded.promoted_at",
                    (run_id, lease.fence, float(self._clock())),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "run_id": run_id,
            "applied_intents": applied_intents,
            "withdrawn_subject_generation_ids": withdrawn,
            "completed_projection_ids": completed_projection_ids,
            "current": True,
        }

    def current_run_id(self) -> str:
        row = self.connection.execute(
            "SELECT run_id FROM paper_current_run WHERE singleton=1"
        ).fetchone()
        return str(row["run_id"]) if row is not None else ""

    def register_subject(
        self,
        lease: PaperWriterLease,
        *,
        run_id: str,
        logical_id: str,
        payload: dict[str, Any],
        supersedes_generation_id: str | None = None,
    ) -> str:
        required_identity = {
            "source_member_payload_digest",
            "source_validation_generation_id",
            "simulator_manifest_id",
            "method_identity",
            "paper_only",
            "execution_allowed",
        }
        if not required_identity.issubset(payload):
            raise PaperEvidenceConflict(
                "paper subject generation identity is incomplete"
            )
        if (
            payload.get("paper_only") is not True
            or payload.get("execution_allowed") is not False
        ):
            raise PaperEvidenceConflict("paper subject crossed execution boundary")
        payload_digest = _digest(payload)
        generation_id = _stable_id(
            "papersubject",
            {"logical_id": logical_id, "payload_digest": payload_digest},
        )
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                run = conn.execute(
                    "SELECT * FROM paper_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if run is None or run["status"] != "pending":
                    raise PaperEvidenceConflict(
                        "subject generation requires a pending paper run"
                    )
                producer_member = conn.execute(
                    "SELECT * FROM producer_generation_members "
                    "WHERE producer_generation_id=? AND logical_id=?",
                    (run["source_generation_id"], logical_id),
                ).fetchone()
                if (
                    producer_member is None
                    or producer_member["disposition"] != "active"
                    or producer_member["payload_digest"]
                    != payload["source_member_payload_digest"]
                    or producer_member["source_validation_generation_id"]
                    != payload["source_validation_generation_id"]
                ):
                    raise PaperEvidenceConflict(
                        "paper subject does not match producer generation member"
                    )
                queue_predecessors = conn.execute(
                    "SELECT status FROM paper_run_stages WHERE run_id=? AND ordinal<=2",
                    (run_id,),
                ).fetchall()
                if len(queue_predecessors) != 3 or any(
                    row["status"] != "completed" for row in queue_predecessors
                ):
                    raise PaperEvidenceConflict(
                        "subject generation queue predecessors are incomplete"
                    )
                existing = conn.execute(
                    "SELECT * FROM paper_subjects WHERE subject_generation_id=?",
                    (generation_id,),
                ).fetchone()
                if existing is not None:
                    if existing["state"] == "withdrawn":
                        if supersedes_generation_id not in {None, generation_id}:
                            raise PaperEvidenceConflict(
                                "identical subject reintroduction target mismatch"
                            )
                        conn.execute(
                            "INSERT OR IGNORE INTO paper_subject_membership_intents "
                            "VALUES(?,?,'reintroduce','withdrawn',?,?)",
                            (run_id, generation_id, lease.fence, float(self._clock())),
                        )
                    elif existing["state"] not in {"active", "provisional"}:
                        raise PaperEvidenceConflict(
                            "historical subject generation requires explicit supersession"
                        )
                    conn.commit()
                    return generation_id
                active = conn.execute(
                    "SELECT * FROM paper_subjects WHERE logical_id=? AND state='active'",
                    (logical_id,),
                ).fetchone()
                if active is not None:
                    if supersedes_generation_id != active["subject_generation_id"]:
                        raise PaperEvidenceConflict(
                            "changed active subject requires explicit supersession"
                        )
                elif supersedes_generation_id is not None:
                    prior = conn.execute(
                        "SELECT * FROM paper_subjects WHERE subject_generation_id=?",
                        (supersedes_generation_id,),
                    ).fetchone()
                    if prior is None or prior["logical_id"] != logical_id:
                        raise PaperEvidenceConflict(
                            "subject supersession target mismatch"
                        )
                conn.execute(
                    "INSERT INTO paper_subjects VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        generation_id,
                        logical_id,
                        run_id,
                        payload_digest,
                        _canonical(payload),
                        supersedes_generation_id,
                        "provisional",
                        lease.fence,
                        float(self._clock()),
                    ),
                )
                conn.execute(
                    "INSERT INTO subject_cursors VALUES(?, 'armed', 0, '', NULL, ?)",
                    (generation_id, lease.fence),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return generation_id

    def subject(self, subject_generation_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM paper_subjects WHERE subject_generation_id=?",
            (subject_generation_id,),
        ).fetchone()
        if row is None:
            raise PaperEvidenceConflict("paper subject generation missing")
        return _row_dict(row)

    def active_subject(self, logical_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM paper_subjects WHERE logical_id=? AND state='active'",
            (logical_id,),
        ).fetchone()
        return _row_dict(row) if row is not None else None

    def latest_subject(self, logical_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM paper_subjects WHERE logical_id=? "
            "ORDER BY created_at DESC,subject_generation_id DESC LIMIT 1",
            (logical_id,),
        ).fetchone()
        return _row_dict(row) if row is not None else None

    def latest_terminal_event(
        self, subject_generation_id: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM lifecycle_events WHERE subject_generation_id=? "
            "AND event_type IN ('position_closed','outcome_revised') "
            "ORDER BY event_seq DESC LIMIT 1",
            (subject_generation_id,),
        ).fetchone()
        return _row_dict(row) if row is not None else None

    def record_observation(
        self,
        lease: PaperWriterLease,
        *,
        run_id: str,
        subject_generation_id: str,
        rows: list[dict[str, Any]],
        request: dict[str, Any],
        observed_at: float,
        available_at: float,
        acquisition_id: str,
        provider_identity: str,
        manifest_digest: str,
    ) -> str:
        if (
            not rows
            or not acquisition_id
            or not provider_identity
            or not manifest_digest
            or available_at < observed_at
        ):
            raise ValueError("complete point-in-time observation identity required")
        rows_digest = _digest(rows)
        request_digest = _digest(request)
        expected_acquisition_id = _digest(
            {
                "provider_identity": provider_identity,
                "request": request,
                "rows_digest": rows_digest,
                "observed_at_ms": int(round(float(observed_at) * 1000)),
            }
        )
        if acquisition_id != expected_acquisition_id:
            raise PaperEvidenceConflict("observation acquisition identity mismatch")
        expected_manifest_digest = _digest(
            {
                "schema": "CandleSnapshotManifest.v2",
                "request_digest": request_digest,
                "rows_digest": rows_digest,
                "observed_at_ms": int(round(float(observed_at) * 1000)),
                "available_at_ms": int(round(float(available_at) * 1000)),
                "provider_identity": provider_identity,
                "acquisition_id": acquisition_id,
            }
        )
        if manifest_digest != expected_manifest_digest:
            raise PaperEvidenceConflict("observation manifest digest mismatch")
        observation_id = _stable_id(
            "paperobservation",
            {
                "subject_generation_id": subject_generation_id,
                "run_id": run_id,
                "rows_digest": rows_digest,
                "request_digest": request_digest,
                "observed_at": float(observed_at),
                "available_at": float(available_at),
                "acquisition_id": acquisition_id,
                "provider_identity": provider_identity,
                "manifest_digest": manifest_digest,
            },
        )
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                subject = conn.execute(
                    "SELECT * FROM paper_subjects WHERE subject_generation_id=?",
                    (subject_generation_id,),
                ).fetchone()
                run = conn.execute(
                    "SELECT * FROM paper_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                observer_predecessors = conn.execute(
                    "SELECT status FROM paper_run_stages WHERE run_id=? AND ordinal<=2",
                    (run_id,),
                ).fetchall()
                if subject is None or subject["state"] not in {"active", "provisional"}:
                    raise PaperEvidenceConflict(
                        "observation requires active or provisional subject generation"
                    )
                if (
                    run is None
                    or run["status"] != "pending"
                    or len(observer_predecessors) != 3
                    or any(
                        row["status"] != "completed" for row in observer_predecessors
                    )
                    or (
                        subject["state"] == "provisional"
                        and subject["run_id"] != run_id
                    )
                ):
                    raise PaperEvidenceConflict(
                        "observation run predecessors are incomplete or mismatched"
                    )
                existing = conn.execute(
                    "SELECT * FROM observation_batches WHERE observation_id=?",
                    (observation_id,),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO observation_batches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            observation_id,
                            subject_generation_id,
                            run_id,
                            rows_digest,
                            _canonical(rows),
                            request_digest,
                            _canonical(request),
                            float(observed_at),
                            float(available_at),
                            acquisition_id,
                            provider_identity,
                            manifest_digest,
                            lease.fence,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return observation_id

    def observation(self, observation_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM observation_batches WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        if row is None:
            raise PaperEvidenceConflict("paper observation batch missing")
        rows = json.loads(str(row["rows_json"]))
        request = json.loads(str(row["request_json"]))
        expected_acquisition_id = _digest(
            {
                "provider_identity": str(row["provider_identity"]),
                "request": request,
                "rows_digest": str(row["rows_digest"]),
                "observed_at_ms": int(round(float(row["observed_at"]) * 1000)),
            }
        )
        expected_manifest_digest = _digest(
            {
                "schema": "CandleSnapshotManifest.v2",
                "request_digest": str(row["request_digest"]),
                "rows_digest": str(row["rows_digest"]),
                "observed_at_ms": int(round(float(row["observed_at"]) * 1000)),
                "available_at_ms": int(round(float(row["available_at"]) * 1000)),
                "provider_identity": str(row["provider_identity"]),
                "acquisition_id": str(row["acquisition_id"]),
            }
        )
        if (
            _digest(rows) != row["rows_digest"]
            or _digest(request) != row["request_digest"]
            or expected_acquisition_id != row["acquisition_id"]
            or expected_manifest_digest != row["manifest_digest"]
        ):
            raise PaperEvidenceConflict("paper observation content binding mismatch")
        return {
            **_row_dict(row),
            "rows": rows,
            "request": request,
        }

    def _account_config(
        self, conn: sqlite3.Connection, generation_id: str
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM account_geneses WHERE generation_id=?", (generation_id,)
        ).fetchone()
        if row is None:
            raise PaperEvidenceConflict("paper account generation missing")
        config = json.loads(str(row["config_json"]))
        if _digest(config) != row["config_digest"]:
            raise PaperEvidenceConflict("paper account genesis digest mismatch")
        if (
            config.get("paper_only") is not True
            or config.get("execution_allowed") is not False
            or int(row["paper_only"]) != 1
            or int(row["execution_allowed"]) != 0
        ):
            raise PaperEvidenceConflict("unsafe paper account genesis boundary")
        if (
            int(config.get("money_scale", 0)) != MONEY_SCALE
            or int(config.get("ratio_scale", 0)) != RATIO_SCALE
            or int(config.get("deposit_microunits", -1))
            != _scaled(config.get("deposit"), MONEY_SCALE)
            or int(config.get("position_margin_microunits", -1))
            != _scaled(config.get("position_margin"), MONEY_SCALE)
            or int(config.get("leverage_microunits", -1))
            != _scaled(config.get("leverage"), RATIO_SCALE)
        ):
            raise PaperEvidenceConflict("paper account genesis unit binding mismatch")
        return config

    def _replay_account_conn(
        self, conn: sqlite3.Connection, generation_id: str
    ) -> dict[str, Any]:
        config = self._account_config(conn, generation_id)
        model_digest = _digest(config)
        balance_units = int(config["deposit_microunits"])
        margin_units = int(config["position_margin_microunits"])
        reserved: dict[str, int] = {}
        owned_scenario_sets: set[str] = set()
        expected_seq = 0
        prior_hash = ""
        rows = conn.execute(
            "SELECT * FROM account_events WHERE account_generation_id=? ORDER BY account_seq",
            (generation_id,),
        ).fetchall()
        for row in rows:
            expected_seq += 1
            if int(row["account_seq"]) != expected_seq:
                raise PaperEvidenceConflict("paper account event sequence gap")
            payload = json.loads(str(row["payload_json"]))
            if (
                _digest(payload) != row["payload_digest"]
                or str(row["account_model_digest"]) != model_digest
                or str(row["prior_event_hash"]) != prior_hash
            ):
                raise PaperEvidenceConflict("paper account event digest mismatch")
            event_type = str(row["event_type"])
            subject = str(row["subject_generation_id"] or "")
            lifecycle_event_id = str(row["lifecycle_event_id"] or "")
            identity = {
                "account_generation_id": generation_id,
                "account_seq": expected_seq,
                "prior_event_hash": prior_hash,
                "event_type": event_type,
                "subject_generation_id": subject,
                "lifecycle_event_id": lifecycle_event_id,
                "account_model_digest": model_digest,
                "payload_digest": str(row["payload_digest"]),
                "supersedes_account_event_id": str(
                    row["supersedes_account_event_id"] or ""
                ),
            }
            if _digest(identity) != row["event_hash"]:
                raise PaperEvidenceConflict("paper account event hash mismatch")
            if lifecycle_event_id:
                lifecycle = conn.execute(
                    "SELECT * FROM lifecycle_events WHERE lifecycle_event_id=?",
                    (lifecycle_event_id,),
                ).fetchone()
                if (
                    lifecycle is None
                    or str(lifecycle["subject_generation_id"]) != subject
                ):
                    raise PaperEvidenceConflict(
                        "paper account source-event identity mismatch"
                    )
                lifecycle_payload = json.loads(str(lifecycle["payload_json"]))
                if _digest(lifecycle_payload) != lifecycle["payload_digest"]:
                    raise PaperEvidenceConflict(
                        "paper account lifecycle payload mismatch"
                    )
            elif event_type != "account_rebased":
                raise PaperEvidenceConflict("paper account source event is missing")
            expected_payload: dict[str, Any]
            if event_type == "position_opened":
                scenario_id = str(lifecycle_payload.get("scenario_id") or "")
                candidates = sorted(
                    {
                        str(candidate)
                        for candidate in lifecycle_payload.get("scenario_candidates")
                        or [scenario_id]
                        if str(candidate)
                    }
                )
                scenario_set_digest = _digest(candidates)
                expected_payload = {
                    "margin_microunits": margin_units,
                    "scenario_id": scenario_id,
                    "scenario_candidates": candidates,
                    "scenario_set_digest": scenario_set_digest,
                }
                if (
                    lifecycle["event_type"] != "position_opened"
                    or subject in reserved
                    or not scenario_id
                    or scenario_id not in candidates
                    or scenario_id != candidates[0]
                    or scenario_set_digest in owned_scenario_sets
                    or balance_units - sum(reserved.values()) < margin_units
                    or payload != expected_payload
                ):
                    raise PaperEvidenceConflict(
                        "paper account open arithmetic mismatch"
                    )
                reserved[subject] = margin_units
                owned_scenario_sets.add(scenario_set_digest)
            elif event_type == "position_closed":
                net_pct_units = _scaled(lifecycle_payload.get("net_pct"), RATIO_SCALE)
                expected_payload = {
                    "net_pct_microunits": net_pct_units,
                    "net_pct": _unscaled(net_pct_units, RATIO_SCALE),
                    "pnl_delta_microunits": _pnl_delta_units(config, net_pct_units),
                }
                if (
                    lifecycle["event_type"] != "position_closed"
                    or subject not in reserved
                    or payload != expected_payload
                    or row["supersedes_account_event_id"] is not None
                ):
                    raise PaperEvidenceConflict("paper close arithmetic mismatch")
                reserved.pop(subject)
                balance_units += int(payload["pnl_delta_microunits"])
            elif event_type == "pnl_adjustment":
                supersedes_account_event_id = str(
                    row["supersedes_account_event_id"] or ""
                )
                supersedes_lifecycle_event_id = str(
                    lifecycle["supersedes_event_id"] or ""
                )
                prior_account = conn.execute(
                    "SELECT * FROM account_events WHERE account_event_id=?",
                    (supersedes_account_event_id,),
                ).fetchone()
                if (
                    lifecycle["event_type"] != "outcome_revised"
                    or prior_account is None
                    or prior_account["lifecycle_event_id"]
                    != supersedes_lifecycle_event_id
                    or prior_account["subject_generation_id"] != subject
                    or prior_account["account_generation_id"] != generation_id
                    or prior_account["event_type"]
                    not in {"position_closed", "pnl_adjustment"}
                ):
                    raise PaperEvidenceConflict(
                        "paper adjustment supersession mismatch"
                    )
                prior_payload = json.loads(str(prior_account["payload_json"]))
                previous_net_units = int(prior_payload.get("net_pct_microunits", 0))
                new_net_units = _scaled(lifecycle_payload.get("net_pct"), RATIO_SCALE)
                expected_payload = {
                    "net_pct_microunits": new_net_units,
                    "net_pct": _unscaled(new_net_units, RATIO_SCALE),
                    "previous_net_pct_microunits": previous_net_units,
                    "previous_net_pct": _unscaled(previous_net_units, RATIO_SCALE),
                    "pnl_delta_microunits": _pnl_delta_units(
                        config, new_net_units - previous_net_units
                    ),
                }
                if payload != expected_payload:
                    raise PaperEvidenceConflict("paper adjustment arithmetic mismatch")
                balance_units += int(payload["pnl_delta_microunits"])
            elif event_type == "allocation_rejected":
                expected_payload = {
                    "required_margin_microunits": margin_units,
                    "available_margin_microunits": balance_units
                    - sum(reserved.values()),
                }
                if (
                    lifecycle["event_type"] != "position_opened"
                    or expected_payload["available_margin_microunits"] >= margin_units
                    or payload != expected_payload
                ):
                    raise PaperEvidenceConflict("paper allocation rejection mismatch")
            elif event_type == "counterfactual_excluded":
                scenario_id = str(lifecycle_payload.get("scenario_id") or "")
                candidates = sorted(
                    {
                        str(candidate)
                        for candidate in lifecycle_payload.get("scenario_candidates")
                        or [scenario_id]
                        if str(candidate)
                    }
                )
                scenario_set_digest = _digest(candidates)
                expected_payload = {
                    "scenario_id": scenario_id,
                    "scenario_candidates": candidates,
                    "scenario_set_digest": scenario_set_digest,
                    "reason": "non_primary_or_owned",
                }
                if (
                    lifecycle["event_type"] != "position_opened"
                    or scenario_id not in candidates
                    or (
                        scenario_id == candidates[0]
                        and scenario_set_digest not in owned_scenario_sets
                    )
                    or payload != expected_payload
                ):
                    raise PaperEvidenceConflict(
                        "paper counterfactual exclusion mismatch"
                    )
            elif event_type == "account_rebased":
                genesis = conn.execute(
                    "SELECT parent_generation_id FROM account_geneses WHERE generation_id=?",
                    (generation_id,),
                ).fetchone()
                expected_payload = {
                    "opening_balance_microunits": int(config["deposit_microunits"]),
                    "parent_generation_id": str(genesis["parent_generation_id"] or ""),
                    "reason": "explicit_account_generation_change",
                }
                if (
                    not expected_payload["parent_generation_id"]
                    or payload != expected_payload
                ):
                    raise PaperEvidenceConflict("paper account rebase mismatch")
            else:
                raise PaperEvidenceConflict("unknown paper account event type")
            prior_hash = str(row["event_hash"])
        reserved_units = sum(reserved.values())
        return {
            "account_generation_id": generation_id,
            "balance": _unscaled(balance_units, MONEY_SCALE),
            "balance_microunits": balance_units,
            "reserved_margin": _unscaled(reserved_units, MONEY_SCALE),
            "reserved_margin_microunits": reserved_units,
            "available_margin": _unscaled(balance_units - reserved_units, MONEY_SCALE),
            "available_margin_microunits": balance_units - reserved_units,
            "active_subjects": sorted(reserved),
            "events": len(rows),
            "position_margin": _unscaled(margin_units, MONEY_SCALE),
            "position_margin_microunits": margin_units,
            "paper_only": True,
            "execution_allowed": False,
        }

    def replay_account(self, generation_id: str) -> dict[str, Any]:
        with self._lock:
            return self._replay_account_conn(self.connection, generation_id)

    def account_model(self, generation_id: str) -> dict[str, Any]:
        with self._lock:
            self._replay_account_conn(self.connection, generation_id)
            return dict(self._account_config(self.connection, generation_id))

    def account_owned_scenario_sets(self, generation_id: str) -> set[str]:
        with self._lock:
            self._replay_account_conn(self.connection, generation_id)
            rows = self.connection.execute(
                "SELECT payload_json FROM account_events WHERE account_generation_id=? "
                "AND event_type='position_opened'",
                (generation_id,),
            ).fetchall()
            return {
                str(json.loads(str(row["payload_json"]))["scenario_set_digest"])
                for row in rows
            }

    def account_pnl_delta_microunits(self, generation_id: str, net_pct: Any) -> int:
        with self._lock:
            self._replay_account_conn(self.connection, generation_id)
            config = self._account_config(self.connection, generation_id)
            return _pnl_delta_units(config, _scaled(net_pct, RATIO_SCALE))

    def _insert_account_event(
        self,
        conn: sqlite3.Connection,
        lease: PaperWriterLease,
        generation_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        subject_generation_id: str | None = None,
        lifecycle_event_id: str | None = None,
        supersedes_account_event_id: str | None = None,
    ) -> str:
        seq = (
            int(
                conn.execute(
                    "SELECT COALESCE(MAX(account_seq),0) AS value FROM account_events "
                    "WHERE account_generation_id=?",
                    (generation_id,),
                ).fetchone()["value"]
            )
            + 1
        )
        prior_row = conn.execute(
            "SELECT event_hash FROM account_events WHERE account_generation_id=? "
            "ORDER BY account_seq DESC LIMIT 1",
            (generation_id,),
        ).fetchone()
        prior_hash = str(prior_row["event_hash"]) if prior_row is not None else ""
        config = self._account_config(conn, generation_id)
        model_digest = _digest(config)
        payload_digest = _digest(payload)
        identity = {
            "account_generation_id": generation_id,
            "account_seq": seq,
            "prior_event_hash": prior_hash,
            "event_type": event_type,
            "subject_generation_id": subject_generation_id or "",
            "lifecycle_event_id": lifecycle_event_id or "",
            "account_model_digest": model_digest,
            "payload_digest": payload_digest,
            "supersedes_account_event_id": supersedes_account_event_id or "",
        }
        event_hash = _digest(identity)
        event_id = _stable_id("paperaccountevent", identity)
        conn.execute(
            "INSERT INTO account_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                generation_id,
                seq,
                prior_hash,
                event_hash,
                event_type,
                subject_generation_id,
                lifecycle_event_id,
                model_digest,
                payload_digest,
                _canonical(payload),
                supersedes_account_event_id,
                lease.fence,
                float(self._clock()),
                1,
                0,
            ),
        )
        return event_id

    def plan_lifecycle(
        self,
        lease: PaperWriterLease,
        *,
        run_id: str,
        subject_generation_id: str,
        observation_id: str,
        event_type: str,
        payload: dict[str, Any],
        account_generation_id: str,
        supersedes_event_id: str | None = None,
    ) -> str:
        """Persist a non-authoritative intent; only ``finalize_run`` may apply it."""
        if event_type not in {"position_opened", "position_closed", "outcome_revised"}:
            raise ValueError("unsupported paper lifecycle event")
        if event_type == "position_opened" and not str(
            payload.get("scenario_id") or ""
        ):
            raise ValueError("paper open requires a scenario identity")
        if (
            event_type in {"position_closed", "outcome_revised"}
            and "net_pct" not in payload
        ):
            raise ValueError("paper terminal event requires net_pct")
        if "_evidence" in payload:
            raise PaperEvidenceConflict(
                "reserved lifecycle evidence field supplied by caller"
            )
        if (
            payload.get("execution_allowed") is True
            or payload.get("paper_only") is False
        ):
            raise PaperEvidenceConflict("paper lifecycle crossed execution boundary")
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                run = conn.execute(
                    "SELECT * FROM paper_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                subject = conn.execute(
                    "SELECT * FROM paper_subjects WHERE subject_generation_id=?",
                    (subject_generation_id,),
                ).fetchone()
                observation = conn.execute(
                    "SELECT * FROM observation_batches WHERE observation_id=?",
                    (observation_id,),
                ).fetchone()
                cursor = conn.execute(
                    "SELECT * FROM subject_cursors WHERE subject_generation_id=?",
                    (subject_generation_id,),
                ).fetchone()
                if (
                    run is None
                    or run["status"] != "pending"
                    or subject is None
                    or subject["state"] not in {"active", "provisional"}
                    or observation is None
                    or observation["subject_generation_id"] != subject_generation_id
                    or observation["run_id"] != run_id
                    or cursor is None
                ):
                    raise PaperEvidenceConflict(
                        "lifecycle intent generation join mismatch"
                    )
                subject_payload = json.loads(str(subject["payload_json"]))
                observation_rows = json.loads(str(observation["rows_json"]))
                observation_request = json.loads(str(observation["request_json"]))
                if (
                    _digest(subject_payload) != subject["payload_digest"]
                    or _digest(observation_rows) != observation["rows_digest"]
                    or _digest(observation_request) != observation["request_digest"]
                ):
                    raise PaperEvidenceConflict(
                        "lifecycle evidence input digest mismatch"
                    )
                payload = {
                    **payload,
                    "_evidence": {
                        "paper_subject_payload_digest": str(subject["payload_digest"]),
                        "source_validation_generation_id": str(
                            subject_payload["source_validation_generation_id"]
                        ),
                        "simulator_manifest_id": str(
                            subject_payload["simulator_manifest_id"]
                        ),
                        "method_identity": str(subject_payload["method_identity"]),
                        "observation_rows_digest": str(observation["rows_digest"]),
                        "observation_request_digest": str(
                            observation["request_digest"]
                        ),
                        "observation_acquisition_id": str(
                            observation["acquisition_id"]
                        ),
                        "observation_observed_at": float(observation["observed_at"]),
                        "observation_available_at": float(observation["available_at"]),
                    },
                }
                predecessor_stages = conn.execute(
                    "SELECT status FROM paper_run_stages WHERE run_id=? AND ordinal<=3",
                    (run_id,),
                ).fetchall()
                if len(predecessor_stages) != 4 or any(
                    row["status"] != "completed" for row in predecessor_stages
                ):
                    raise PaperEvidenceConflict(
                        "lifecycle intent predecessors are incomplete"
                    )
                payload_digest = _digest(payload)
                exact_retry = conn.execute(
                    "SELECT intent_id FROM paper_run_mutation_intents WHERE run_id=? "
                    "AND intent_type=? AND subject_generation_id=? AND observation_id=? "
                    "AND account_generation_id=? AND payload_digest=? "
                    "AND COALESCE(supersedes_event_id,'')=? AND status IN ('planned','applied')",
                    (
                        run_id,
                        event_type,
                        subject_generation_id,
                        observation_id,
                        account_generation_id,
                        payload_digest,
                        supersedes_event_id or "",
                    ),
                ).fetchone()
                if exact_retry is not None:
                    conn.commit()
                    return str(exact_retry["intent_id"])
                planned = conn.execute(
                    "SELECT * FROM paper_run_mutation_intents WHERE run_id=? "
                    "AND subject_generation_id=? AND status='planned' ORDER BY intent_order",
                    (run_id, subject_generation_id),
                ).fetchall()
                expected_seq = int(cursor["event_seq"])
                expected_hash = str(cursor["last_event_hash"])
                state = str(cursor["state"])
                for prior_intent in planned:
                    prior_payload = json.loads(str(prior_intent["payload_json"]))
                    expected_seq += 1
                    prior_identity = {
                        "subject_generation_id": subject_generation_id,
                        "event_seq": expected_seq,
                        "prior_event_hash": expected_hash,
                        "event_type": str(prior_intent["intent_type"]),
                        "observation_id": str(prior_intent["observation_id"]),
                        "payload_digest": _digest(prior_payload),
                        "supersedes_event_id": str(
                            prior_intent["supersedes_event_id"] or ""
                        ),
                    }
                    expected_hash = _digest(prior_identity)
                    state = {
                        "position_opened": "opened",
                        "position_closed": "closed",
                        "outcome_revised": "revised",
                    }[str(prior_intent["intent_type"])]
                if event_type == "position_opened" and state != "armed":
                    raise PaperEvidenceConflict("paper subject is not armed")
                if event_type == "position_closed" and state != "opened":
                    raise PaperEvidenceConflict("paper subject is not opened")
                if event_type == "outcome_revised":
                    if state not in {"closed", "revised"} or not supersedes_event_id:
                        raise PaperEvidenceConflict(
                            "paper revision requires terminal predecessor"
                        )
                    prior = conn.execute(
                        "SELECT * FROM lifecycle_events WHERE lifecycle_event_id=? "
                        "AND subject_generation_id=?",
                        (supersedes_event_id, subject_generation_id),
                    ).fetchone()
                    if prior is None or prior["event_type"] not in {
                        "position_closed",
                        "outcome_revised",
                    }:
                        raise PaperEvidenceConflict("paper revision target mismatch")
                self._account_config(conn, account_generation_id)
                base_account_seq = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(account_seq),0) AS n FROM account_events "
                        "WHERE account_generation_id=?",
                        (account_generation_id,),
                    ).fetchone()["n"]
                )
                prior_account_intents = int(
                    conn.execute(
                        "SELECT COUNT(*) AS n FROM paper_run_mutation_intents WHERE run_id=? "
                        "AND account_generation_id=? AND status='planned'",
                        (run_id, account_generation_id),
                    ).fetchone()["n"]
                )
                order = (
                    int(
                        conn.execute(
                            "SELECT COALESCE(MAX(intent_order),0) AS n FROM paper_run_mutation_intents "
                            "WHERE run_id=?",
                            (run_id,),
                        ).fetchone()["n"]
                    )
                    + 1
                )
                intent_identity = {
                    "run_id": run_id,
                    "intent_order": order,
                    "event_type": event_type,
                    "subject_generation_id": subject_generation_id,
                    "observation_id": observation_id,
                    "account_generation_id": account_generation_id,
                    "payload_digest": payload_digest,
                    "supersedes_event_id": supersedes_event_id or "",
                    "expected_subject_seq": expected_seq,
                    "expected_subject_hash": expected_hash,
                    "expected_account_seq": base_account_seq + prior_account_intents,
                }
                intent_id = _stable_id("paperintent", intent_identity)
                existing = conn.execute(
                    "SELECT * FROM paper_run_mutation_intents WHERE intent_id=?",
                    (intent_id,),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO paper_run_mutation_intents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            intent_id,
                            run_id,
                            order,
                            event_type,
                            subject_generation_id,
                            observation_id,
                            account_generation_id,
                            payload_digest,
                            _canonical(payload),
                            supersedes_event_id,
                            expected_seq,
                            expected_hash,
                            base_account_seq + prior_account_intents,
                            "planned",
                            None,
                            None,
                            lease.fence,
                            float(self._clock()),
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return intent_id

    def apply_lifecycle(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise PaperEvidenceConflict(
            "direct lifecycle mutation is forbidden; use plan_lifecycle and finalize_run"
        )

    def planned_lifecycle_event_id(self, intent_id: str) -> str:
        intent = self.connection.execute(
            "SELECT * FROM paper_run_mutation_intents WHERE intent_id=?",
            (intent_id,),
        ).fetchone()
        if intent is None:
            raise PaperEvidenceConflict("paper lifecycle intent is missing")
        identity = {
            "subject_generation_id": str(intent["subject_generation_id"]),
            "event_seq": int(intent["expected_subject_seq"]) + 1,
            "prior_event_hash": str(intent["expected_subject_hash"]),
            "event_type": str(intent["intent_type"]),
            "observation_id": str(intent["observation_id"]),
            "payload_digest": str(intent["payload_digest"]),
            "supersedes_event_id": str(intent["supersedes_event_id"] or ""),
        }
        return _stable_id("paperlifecycle", identity)

    def planned_lifecycle_event_type(self, intent_id: str) -> str:
        intent = self.connection.execute(
            "SELECT intent_type FROM paper_run_mutation_intents WHERE intent_id=?",
            (intent_id,),
        ).fetchone()
        if intent is None:
            raise PaperEvidenceConflict("paper lifecycle intent is missing")
        return str(intent["intent_type"])

    def _apply_planned_intent(
        self,
        conn: sqlite3.Connection,
        lease: PaperWriterLease,
        intent: sqlite3.Row,
    ) -> tuple[str, str, str]:
        subject_generation_id = str(intent["subject_generation_id"])
        observation_id = str(intent["observation_id"])
        event_type = str(intent["intent_type"])
        account_generation_id = str(intent["account_generation_id"])
        supersedes_event_id = str(intent["supersedes_event_id"] or "") or None
        payload = json.loads(str(intent["payload_json"]))
        cursor = conn.execute(
            "SELECT * FROM subject_cursors WHERE subject_generation_id=?",
            (subject_generation_id,),
        ).fetchone()
        observation = conn.execute(
            "SELECT * FROM observation_batches WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        if (
            cursor is None
            or observation is None
            or observation["subject_generation_id"] != subject_generation_id
            or int(cursor["event_seq"]) != int(intent["expected_subject_seq"])
            or str(cursor["last_event_hash"]) != str(intent["expected_subject_hash"])
        ):
            raise PaperEvidenceConflict(
                "planned lifecycle cursor/observation CAS failed"
            )
        account_seq = int(
            conn.execute(
                "SELECT COALESCE(MAX(account_seq),0) AS n FROM account_events "
                "WHERE account_generation_id=?",
                (account_generation_id,),
            ).fetchone()["n"]
        )
        if account_seq != int(intent["expected_account_seq"]):
            raise PaperEvidenceConflict("planned account cursor CAS failed")
        seq = int(cursor["event_seq"]) + 1
        prior_hash = str(cursor["last_event_hash"])
        event_identity = {
            "subject_generation_id": subject_generation_id,
            "event_seq": seq,
            "prior_event_hash": prior_hash,
            "event_type": event_type,
            "observation_id": observation_id,
            "payload_digest": str(intent["payload_digest"]),
            "supersedes_event_id": supersedes_event_id or "",
        }
        event_hash = _digest(event_identity)
        lifecycle_event_id = _stable_id("paperlifecycle", event_identity)
        conn.execute(
            "INSERT INTO lifecycle_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                lifecycle_event_id,
                subject_generation_id,
                seq,
                prior_hash,
                event_hash,
                event_type,
                observation_id,
                str(intent["payload_digest"]),
                str(intent["payload_json"]),
                supersedes_event_id,
                lease.fence,
                float(self._clock()),
                1,
                0,
            ),
        )
        account = self._replay_account_conn(conn, account_generation_id)
        config = self._account_config(conn, account_generation_id)
        supersedes_account_event_id = None
        account_payload: dict[str, Any]
        if event_type == "position_opened":
            margin_units = int(config["position_margin_microunits"])
            scenario_id = str(payload.get("scenario_id") or "")
            raw_candidates = payload.get("scenario_candidates") or [scenario_id]
            if not isinstance(raw_candidates, list):
                raise PaperEvidenceConflict(
                    "scenario candidates must be a complete list"
                )
            candidates = sorted(
                {str(candidate) for candidate in raw_candidates if str(candidate)}
            )
            if scenario_id not in candidates:
                raise PaperEvidenceConflict(
                    "scenario is absent from its complete candidate set"
                )
            scenario_set_digest = _digest(candidates)
            primary = scenario_id == candidates[0]
            scenario_owned = (
                any(
                    json.loads(str(row["payload_json"])).get("scenario_set_digest")
                    == scenario_set_digest
                    for row in conn.execute(
                        "SELECT payload_json FROM account_events WHERE account_generation_id=? "
                        "AND event_type='position_opened'",
                        (account_generation_id,),
                    ).fetchall()
                )
                if scenario_id
                else False
            )
            if not primary or scenario_owned:
                account_event_type = "counterfactual_excluded"
                account_payload = {
                    "scenario_id": scenario_id,
                    "scenario_candidates": candidates,
                    "scenario_set_digest": scenario_set_digest,
                    "reason": "non_primary_or_owned",
                }
                next_state = "counterfactual"
            elif int(account["available_margin_microunits"]) >= margin_units:
                account_event_type = "position_opened"
                account_payload = {
                    "margin_microunits": margin_units,
                    "scenario_id": scenario_id,
                    "scenario_candidates": candidates,
                    "scenario_set_digest": scenario_set_digest,
                }
                next_state = "opened"
            else:
                account_event_type = "allocation_rejected"
                account_payload = {
                    "required_margin_microunits": margin_units,
                    "available_margin_microunits": int(
                        account["available_margin_microunits"]
                    ),
                }
                next_state = "allocation_rejected"
        elif event_type == "position_closed":
            net_pct_units = _scaled(payload["net_pct"], RATIO_SCALE)
            pnl_units = _pnl_delta_units(config, net_pct_units)
            account_event_type = "position_closed"
            account_payload = {
                "net_pct_microunits": net_pct_units,
                "net_pct": _unscaled(net_pct_units, RATIO_SCALE),
                "pnl_delta_microunits": pnl_units,
            }
            next_state = "closed"
        else:
            prior_account = conn.execute(
                "SELECT * FROM account_events WHERE lifecycle_event_id=?",
                (supersedes_event_id,),
            ).fetchone()
            if prior_account is None or prior_account["event_type"] not in {
                "position_closed",
                "pnl_adjustment",
            }:
                raise PaperEvidenceConflict("paper revision lacks prior account effect")
            prior_payload = json.loads(str(prior_account["payload_json"]))
            new_net_units = _scaled(payload["net_pct"], RATIO_SCALE)
            previous_net_units = int(prior_payload.get("net_pct_microunits", 0))
            adjustment_units = _pnl_delta_units(
                config, new_net_units - previous_net_units
            )
            account_event_type = "pnl_adjustment"
            account_payload = {
                "net_pct_microunits": new_net_units,
                "net_pct": _unscaled(new_net_units, RATIO_SCALE),
                "previous_net_pct_microunits": previous_net_units,
                "previous_net_pct": _unscaled(previous_net_units, RATIO_SCALE),
                "pnl_delta_microunits": adjustment_units,
            }
            supersedes_account_event_id = str(prior_account["account_event_id"])
            next_state = "revised"
        account_event_id = self._insert_account_event(
            conn,
            lease,
            account_generation_id,
            account_event_type,
            account_payload,
            subject_generation_id=subject_generation_id,
            lifecycle_event_id=lifecycle_event_id,
            supersedes_account_event_id=supersedes_account_event_id,
        )
        updated = conn.execute(
            "UPDATE subject_cursors SET state=?,event_seq=?,last_event_hash=?,last_observation_id=?,"
            "updated_fence=? WHERE subject_generation_id=? AND event_seq=? AND last_event_hash=?",
            (
                next_state,
                seq,
                event_hash,
                observation_id,
                lease.fence,
                subject_generation_id,
                int(intent["expected_subject_seq"]),
                str(intent["expected_subject_hash"]),
            ),
        )
        if updated.rowcount != 1:
            raise PaperEvidenceConflict("paper lifecycle cursor compare-and-set failed")
        conn.execute(
            "UPDATE paper_run_mutation_intents SET status='applied',applied_lifecycle_event_id=?,"
            "applied_account_event_id=? WHERE intent_id=? AND status='planned'",
            (lifecycle_event_id, account_event_id, intent["intent_id"]),
        )
        return lifecycle_event_id, account_event_id, account_event_type

    def lifecycle_events(self, subject_generation_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM lifecycle_events WHERE subject_generation_id=? ORDER BY event_seq",
            (subject_generation_id,),
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def replay_lifecycle(self, subject_generation_id: str) -> dict[str, Any]:
        subject = self.connection.execute(
            "SELECT * FROM paper_subjects WHERE subject_generation_id=?",
            (subject_generation_id,),
        ).fetchone()
        if subject is None:
            raise PaperEvidenceConflict("paper lifecycle subject generation is missing")
        subject_payload = json.loads(str(subject["payload_json"]))
        if _digest(subject_payload) != subject["payload_digest"]:
            raise PaperEvidenceConflict("paper lifecycle subject digest mismatch")
        rows = self.connection.execute(
            "SELECT * FROM lifecycle_events WHERE subject_generation_id=? ORDER BY event_seq",
            (subject_generation_id,),
        ).fetchall()
        prior_hash = ""
        derived_state = "armed"
        for expected_seq, row in enumerate(rows, start=1):
            payload = json.loads(str(row["payload_json"]))
            if int(row["event_seq"]) != expected_seq:
                raise PaperEvidenceConflict("paper lifecycle event sequence gap")
            if (
                row["prior_event_hash"] != prior_hash
                or _digest(payload) != row["payload_digest"]
            ):
                raise PaperEvidenceConflict("paper lifecycle event chain mismatch")
            if row["observation_id"] is not None:
                observation = self.connection.execute(
                    "SELECT * FROM observation_batches WHERE observation_id=?",
                    (row["observation_id"],),
                ).fetchone()
                evidence = payload.get("_evidence") or {}
                if (
                    observation is None
                    or observation["subject_generation_id"] != subject_generation_id
                ):
                    raise PaperEvidenceConflict(
                        "paper lifecycle observation identity mismatch"
                    )
                observation_rows = json.loads(str(observation["rows_json"]))
                observation_request = json.loads(str(observation["request_json"]))
                expected_evidence = {
                    "paper_subject_payload_digest": str(subject["payload_digest"]),
                    "source_validation_generation_id": str(
                        subject_payload["source_validation_generation_id"]
                    ),
                    "simulator_manifest_id": str(
                        subject_payload["simulator_manifest_id"]
                    ),
                    "method_identity": str(subject_payload["method_identity"]),
                    "observation_rows_digest": str(observation["rows_digest"]),
                    "observation_request_digest": str(observation["request_digest"]),
                    "observation_acquisition_id": str(observation["acquisition_id"]),
                    "observation_observed_at": float(observation["observed_at"]),
                    "observation_available_at": float(observation["available_at"]),
                }
                if (
                    _digest(observation_rows) != observation["rows_digest"]
                    or _digest(observation_request) != observation["request_digest"]
                    or evidence != expected_evidence
                ):
                    raise PaperEvidenceConflict(
                        "paper lifecycle observation evidence mismatch"
                    )
            identity = {
                "subject_generation_id": subject_generation_id,
                "event_seq": expected_seq,
                "prior_event_hash": prior_hash,
                "event_type": str(row["event_type"]),
                "observation_id": str(row["observation_id"] or ""),
                "payload_digest": str(row["payload_digest"]),
                "supersedes_event_id": str(row["supersedes_event_id"] or ""),
            }
            if _digest(identity) != row["event_hash"]:
                raise PaperEvidenceConflict("paper lifecycle event hash mismatch")
            event_type = str(row["event_type"])
            if event_type == "position_opened":
                if derived_state != "armed":
                    raise PaperEvidenceConflict(
                        "paper lifecycle opened from invalid replay state"
                    )
                account = self.connection.execute(
                    "SELECT event_type FROM account_events WHERE lifecycle_event_id=?",
                    (row["lifecycle_event_id"],),
                ).fetchone()
                if account is None:
                    raise PaperEvidenceConflict(
                        "paper lifecycle open lacks account decision"
                    )
                derived_state = {
                    "position_opened": "opened",
                    "allocation_rejected": "allocation_rejected",
                    "counterfactual_excluded": "counterfactual",
                }.get(str(account["event_type"]), "")
                if not derived_state:
                    raise PaperEvidenceConflict(
                        "paper lifecycle open account decision is invalid"
                    )
            elif event_type == "position_closed":
                if derived_state != "opened":
                    raise PaperEvidenceConflict(
                        "paper lifecycle closed without replayed open"
                    )
                derived_state = "closed"
            elif event_type == "outcome_revised":
                if derived_state not in {"closed", "revised"}:
                    raise PaperEvidenceConflict(
                        "paper lifecycle revision lacks terminal state"
                    )
                derived_state = "revised"
            elif event_type not in {"source_withdrawn", "source_reintroduced"}:
                raise PaperEvidenceConflict(
                    "paper lifecycle replay event type is invalid"
                )
            prior_hash = str(row["event_hash"])
        accepted = self.connection.execute(
            "SELECT * FROM accepted_observations WHERE subject_generation_id=? "
            "ORDER BY accepted_seq",
            (subject_generation_id,),
        ).fetchall()
        prior_observation_id = ""
        for expected_accepted_seq, accepted_row in enumerate(accepted, start=1):
            if (
                int(accepted_row["accepted_seq"]) != expected_accepted_seq
                or str(accepted_row["prior_observation_id"] or "")
                != prior_observation_id
            ):
                raise PaperEvidenceConflict("accepted observation chain mismatch")
            observation = self.observation(str(accepted_row["observation_id"]))
            run = self.connection.execute(
                "SELECT status FROM paper_runs WHERE run_id=?",
                (accepted_row["run_id"],),
            ).fetchone()
            if (
                observation["subject_generation_id"] != subject_generation_id
                or observation["run_id"] != accepted_row["run_id"]
                or run is None
                or run["status"] != "completed"
            ):
                raise PaperEvidenceConflict("accepted observation authority mismatch")
            prior_observation_id = str(accepted_row["observation_id"])
        cursor = self.connection.execute(
            "SELECT * FROM subject_cursors WHERE subject_generation_id=?",
            (subject_generation_id,),
        ).fetchone()
        if (
            cursor is None
            or int(cursor["event_seq"]) != len(rows)
            or cursor["last_event_hash"] != prior_hash
            or str(cursor["state"]) != derived_state
            or str(cursor["last_observation_id"] or "") != prior_observation_id
        ):
            raise PaperEvidenceConflict(
                "paper lifecycle cursor does not match event chain"
            )
        return {
            "subject_generation_id": subject_generation_id,
            "events": len(rows),
            "state": derived_state,
            "last_observation_id": prior_observation_id,
            "last_event_hash": prior_hash,
            "paper_only": True,
            "execution_allowed": False,
        }

    def _append_system_lifecycle(
        self,
        conn: sqlite3.Connection,
        lease: PaperWriterLease,
        subject_generation_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        next_state: str,
    ) -> str:
        cursor = conn.execute(
            "SELECT * FROM subject_cursors WHERE subject_generation_id=?",
            (subject_generation_id,),
        ).fetchone()
        if cursor is None:
            raise PaperEvidenceConflict("paper lifecycle cursor missing")
        seq = int(cursor["event_seq"]) + 1
        prior_hash = str(cursor["last_event_hash"])
        payload_digest = _digest(payload)
        identity = {
            "subject_generation_id": subject_generation_id,
            "event_seq": seq,
            "prior_event_hash": prior_hash,
            "event_type": event_type,
            "observation_id": "",
            "payload_digest": payload_digest,
            "supersedes_event_id": "",
        }
        event_hash = _digest(identity)
        event_id = _stable_id("paperlifecycle", identity)
        conn.execute(
            "INSERT INTO lifecycle_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                subject_generation_id,
                seq,
                prior_hash,
                event_hash,
                event_type,
                None,
                payload_digest,
                _canonical(payload),
                None,
                lease.fence,
                float(self._clock()),
                1,
                0,
            ),
        )
        updated = conn.execute(
            "UPDATE subject_cursors SET state=?,event_seq=?,last_event_hash=?,updated_fence=? "
            "WHERE subject_generation_id=? AND event_seq=? AND last_event_hash=?",
            (
                next_state,
                seq,
                event_hash,
                lease.fence,
                subject_generation_id,
                int(cursor["event_seq"]),
                prior_hash,
            ),
        )
        if updated.rowcount != 1:
            raise PaperEvidenceConflict("paper system lifecycle compare-and-set failed")
        return event_id

    def withdraw_absent_subjects(
        self, lease: PaperWriterLease, run_id: str
    ) -> list[str]:
        del lease, run_id
        raise PaperEvidenceConflict(
            "direct withdrawal is forbidden; completed producer reconciliation runs in finalize_run"
        )

    def schedule_subjects(self, lease: PaperWriterLease, *, limit: int) -> list[str]:
        if limit < 0:
            raise ValueError("non-negative scheduling limit required")
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                rows = conn.execute(
                    "SELECT subject_generation_id FROM paper_subjects WHERE state='active' "
                    "ORDER BY created_at,subject_generation_id"
                ).fetchall()
                if not rows or limit == 0:
                    conn.commit()
                    return []
                cursor = int(
                    conn.execute(
                        "SELECT cursor FROM scheduling_state WHERE singleton=1"
                    ).fetchone()["cursor"]
                )
                count = min(limit, len(rows))
                selected = [
                    str(rows[(cursor + index) % len(rows)][0]) for index in range(count)
                ]
                conn.execute(
                    "UPDATE scheduling_state SET cursor=? WHERE singleton=1",
                    ((cursor + count) % len(rows),),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return selected

    @staticmethod
    def _high_water(conn: sqlite3.Connection) -> str:
        lifecycle = int(
            conn.execute("SELECT COUNT(*) AS n FROM lifecycle_events").fetchone()["n"]
        )
        account = int(
            conn.execute("SELECT COUNT(*) AS n FROM account_events").fetchone()["n"]
        )
        observations = int(
            conn.execute("SELECT COUNT(*) AS n FROM observation_batches").fetchone()[
                "n"
            ]
        )
        return _digest(
            {
                "lifecycle_events": lifecycle,
                "account_events": account,
                "observations": observations,
            }
        )

    @staticmethod
    def _verify_projection_references(
        conn: sqlite3.Connection, envelope: dict[str, Any]
    ) -> None:
        subject_ids = {
            str(value) for value in envelope.get("paper_subject_generation_ids") or []
        }
        account_generation_id = str(envelope.get("account_generation_id") or "")
        for subject_generation_id in subject_ids:
            subject = conn.execute(
                "SELECT subject_generation_id FROM paper_subjects "
                "WHERE subject_generation_id=?",
                (subject_generation_id,),
            ).fetchone()
            if subject is None:
                raise PaperEvidenceConflict("projection subject generation is missing")
        if account_generation_id:
            account = conn.execute(
                "SELECT generation_id FROM account_geneses WHERE generation_id=?",
                (account_generation_id,),
            ).fetchone()
            if account is None:
                raise PaperEvidenceConflict("projection account generation is missing")
        for item in envelope.get("items") or []:
            if not isinstance(item, dict):
                raise PaperEvidenceConflict("projection item is not an object")
            subject_generation_id = str(item.get("paper_subject_generation_id") or "")
            item_account_generation_id = str(item.get("account_generation_id") or "")
            allocation_event_id = str(item.get("allocation_lifecycle_event_id") or "")
            lifecycle_event_id = str(item.get("terminal_lifecycle_event_id") or "")
            account_decision = str(item.get("paper_account_decision") or "")
            allocation_decisions = {
                "position_opened",
                "allocation_rejected",
                "counterfactual_excluded",
            }
            terminal_decisions = {
                "position_closed",
                "pnl_adjustment",
                "terminal_unchanged",
            }
            if account_decision not in allocation_decisions | terminal_decisions | {""}:
                raise PaperEvidenceConflict("unknown paper account decision")
            if not subject_generation_id or subject_generation_id not in subject_ids:
                raise PaperEvidenceConflict(
                    "projection item subject reference mismatch"
                )
            if item_account_generation_id != account_generation_id:
                raise PaperEvidenceConflict(
                    "projection item account reference mismatch"
                )
            if account_decision in allocation_decisions and not allocation_event_id:
                raise PaperEvidenceConflict(
                    "paper account decision lacks allocation evidence"
                )
            if account_decision in terminal_decisions and allocation_event_id:
                raise PaperEvidenceConflict(
                    "terminal account decision has allocation evidence"
                )
            if not account_decision and (allocation_event_id or lifecycle_event_id):
                raise PaperEvidenceConflict(
                    "projection event reference lacks account decision"
                )
            if allocation_event_id:
                allocation_lifecycle = conn.execute(
                    "SELECT * FROM lifecycle_events WHERE lifecycle_event_id=?",
                    (allocation_event_id,),
                ).fetchone()
                allocation_account = conn.execute(
                    "SELECT * FROM account_events WHERE lifecycle_event_id=? "
                    "AND account_generation_id=?",
                    (allocation_event_id, account_generation_id),
                ).fetchone()
                if (
                    allocation_lifecycle is None
                    or allocation_lifecycle["subject_generation_id"]
                    != subject_generation_id
                    or allocation_lifecycle["event_type"] != "position_opened"
                    or allocation_account is None
                    or allocation_account["subject_generation_id"]
                    != subject_generation_id
                    or allocation_account["event_type"] != account_decision
                ):
                    raise PaperEvidenceConflict(
                        "projection allocation/account reference mismatch"
                    )
            terminal_looking = bool(
                str(item.get("signal_status") or "")
                in {"closed_paper", "expired", "reviewed", "invalidated"}
                or str(item.get("status") or "").startswith("closed_")
                or (item.get("outcome") or {}).get("net_pct") not in (None, "")
            )
            terminal_excluded = account_decision in {
                "allocation_rejected",
                "counterfactual_excluded",
            }
            if account_decision in terminal_decisions and not lifecycle_event_id:
                raise PaperEvidenceConflict(
                    "paper account decision lacks terminal evidence"
                )
            if terminal_looking and not terminal_excluded and not lifecycle_event_id:
                raise PaperEvidenceConflict(
                    "terminal projection item lacks terminal evidence"
                )
            if not lifecycle_event_id:
                continue
            lifecycle = conn.execute(
                "SELECT * FROM lifecycle_events WHERE lifecycle_event_id=?",
                (lifecycle_event_id,),
            ).fetchone()
            account_event = conn.execute(
                "SELECT * FROM account_events WHERE lifecycle_event_id=? "
                "AND account_generation_id=?",
                (lifecycle_event_id, account_generation_id),
            ).fetchone()
            if (
                lifecycle is None
                or lifecycle["subject_generation_id"] != subject_generation_id
                or lifecycle["event_type"] not in {"position_closed", "outcome_revised"}
                or account_event is None
                or account_event["subject_generation_id"] != subject_generation_id
                or account_event["event_type"]
                not in {"position_closed", "pnl_adjustment"}
            ):
                raise PaperEvidenceConflict(
                    "projection lifecycle/account reference mismatch"
                )
            if account_decision == "position_closed" and (
                lifecycle["event_type"] != "position_closed"
                or account_event["event_type"] != "position_closed"
            ):
                raise PaperEvidenceConflict(
                    "projection close decision reference mismatch"
                )
            if account_decision == "pnl_adjustment" and (
                lifecycle["event_type"] != "outcome_revised"
                or account_event["event_type"] != "pnl_adjustment"
            ):
                raise PaperEvidenceConflict(
                    "projection adjustment decision reference mismatch"
                )

    def prepare_projection(
        self,
        lease: PaperWriterLease,
        *,
        run_id: str,
        projection_kind: str,
        items: list[dict[str, Any]],
        input_projection_digests: dict[str, str],
        target_path: Path | str,
        subject_generation_ids: list[str] | None = None,
        account_generation_id: str = "",
    ) -> dict[str, Any]:
        """Persist projection bytes as a provisional run artifact, not authority."""
        if not projection_kind:
            raise ValueError("projection kind required")
        if any(
            item.get("paper_only") is not True
            or item.get("execution_allowed") is not False
            for item in items
        ):
            raise PaperEvidenceConflict("projection item crossed execution boundary")
        target_path = Path(target_path)
        subjects = sorted(set(subject_generation_ids or []))
        content_digest = _digest(items)
        with self._lock:
            conn = self._begin()
            try:
                self._authorize(conn, lease)
                run = conn.execute(
                    "SELECT * FROM paper_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                predecessors = conn.execute(
                    "SELECT status FROM paper_run_stages WHERE run_id=? AND ordinal<=4",
                    (run_id,),
                ).fetchall()
                if (
                    run is None
                    or run["status"] != "pending"
                    or len(predecessors) != 5
                    or any(row["status"] != "completed" for row in predecessors)
                ):
                    raise PaperEvidenceConflict(
                        "projection plan predecessors are incomplete"
                    )
                if account_generation_id:
                    self._account_config(conn, account_generation_id)
                identity = {
                    "run_id": run_id,
                    "projection_kind": projection_kind,
                    "input_projection_digests": input_projection_digests,
                    "content_digest": content_digest,
                    "subject_generation_ids": subjects,
                    "account_generation_id": account_generation_id,
                }
                projection_id = _stable_id("paperprojection", identity)
                generation_path = target_path.with_name(
                    f"{target_path.stem}.{projection_id}{target_path.suffix or '.json'}"
                )
                envelope = {
                    "schema": "PaperProjectionEnvelope.v2",
                    "paper_generation_run_id": run_id,
                    "projection_generation_id": projection_id,
                    "projection_kind": projection_kind,
                    "materialization_status": "pending",
                    "store_high_water_mark": "",
                    "input_projection_digests": dict(input_projection_digests),
                    "content_digest": content_digest,
                    "paper_subject_generation_ids": subjects,
                    "account_generation_id": account_generation_id,
                    "items": items,
                    "paper_only": True,
                    "execution_allowed": False,
                }
                existing = conn.execute(
                    "SELECT * FROM projection_materializations WHERE projection_id=?",
                    (projection_id,),
                ).fetchone()
                conflicting = conn.execute(
                    "SELECT projection_id FROM projection_materializations "
                    "WHERE run_id=? AND projection_kind=? AND projection_id<>?",
                    (run_id, projection_kind, projection_id),
                ).fetchone()
                if conflicting is not None:
                    raise PaperEvidenceConflict(
                        "projection kind already has different content in this run"
                    )
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO projection_materializations(
                            projection_id,run_id,projection_kind,status,high_water_mark,
                            input_digests_json,content_digest,envelope_digest,envelope_json,
                            generation_path,account_generation_id,subject_generation_ids_json,
                            writer_fence,created_at,completed_at
                        ) VALUES(?,?,?,'pending','',?,?,?, ?,?,?,?, ?,?,NULL)
                        """,
                        (
                            projection_id,
                            run_id,
                            projection_kind,
                            _canonical(input_projection_digests),
                            content_digest,
                            _digest(envelope),
                            _canonical(envelope),
                            str(generation_path),
                            account_generation_id or None,
                            _canonical(subjects),
                            lease.fence,
                            float(self._clock()),
                        ),
                    )
                elif any(
                    (
                        existing["run_id"] != run_id,
                        existing["projection_kind"] != projection_kind,
                        existing["content_digest"] != content_digest,
                        existing["envelope_digest"] != _digest(envelope),
                    )
                ):
                    raise PaperEvidenceConflict(
                        "projection identity reused with different content"
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        return envelope

    def publish_projection(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise PaperEvidenceConflict(
            "direct projection publication is forbidden; prepare it and finalize the run"
        )

    def export_completed_projection(
        self,
        projection_kind: str,
        target_path: Path | str,
        *,
        expected_run_id: str | None = None,
        fail_after_generation_file: bool = False,
    ) -> dict[str, Any]:
        """Write a non-authoritative content-addressed export of DB authority."""
        envelope = self.read_completed_projection(
            self.path, projection_kind, expected_run_id=expected_run_id
        )
        if not envelope.get("current"):
            raise PaperEvidenceConflict(
                "no completed projection is available for export"
            )
        exported = {
            key: value
            for key, value in envelope.items()
            if key not in {"current", "display_only", "generation_status"}
        }
        target_path = Path(target_path)
        generation_path = target_path.with_name(
            f"{target_path.stem}.{exported['projection_generation_id']}{target_path.suffix or '.json'}"
        )
        generation_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = generation_path.with_suffix(generation_path.suffix + ".pending")
        temporary.write_text(
            json.dumps(exported, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(generation_path)
        if fail_after_generation_file:
            raise PaperEvidenceConflict("synthetic projection export crash")
        pointer_tmp = target_path.with_suffix(target_path.suffix + ".pending")
        pointer_tmp.write_text(
            json.dumps(exported, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pointer_tmp.replace(target_path)
        return exported

    @staticmethod
    def read_completed_projection(
        database_path: Path | str,
        projection_kind: str,
        *,
        expected_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Resolve immutable current evidence without creating or migrating a store."""
        database_path = Path(database_path)
        unavailable = {
            "current": False,
            "display_only": True,
            "generation_status": "unavailable",
            "paper_only": True,
            "execution_allowed": False,
        }
        if not database_path.is_file():
            return unavailable
        uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            marker = conn.execute(
                "SELECT value FROM paper_meta WHERE key='schema_version'"
            ).fetchone()
            if marker is None or marker["value"] != SCHEMA_VERSION:
                return unavailable | {
                    "generation_status": "legacy_unversioned_projection"
                }
            current = conn.execute(
                "SELECT run_id FROM paper_current_run WHERE singleton=1"
            ).fetchone()
            if current is None:
                return unavailable
            run_id = str(current["run_id"])
            if expected_run_id is not None and expected_run_id != run_id:
                return unavailable | {"generation_status": "run_mismatch"}
            row = conn.execute(
                "SELECT * FROM projection_materializations WHERE run_id=? "
                "AND projection_kind=? AND status='completed' ORDER BY completed_at DESC LIMIT 1",
                (run_id, projection_kind),
            ).fetchone()
            if row is None:
                return unavailable | {"generation_status": "incomplete"}
            envelope = json.loads(str(row["envelope_json"]))
            expected_envelope = {
                "schema": "PaperProjectionEnvelope.v2",
                "paper_generation_run_id": run_id,
                "projection_generation_id": str(row["projection_id"]),
                "projection_kind": projection_kind,
                "materialization_status": "completed",
                "store_high_water_mark": str(row["high_water_mark"]),
                "input_projection_digests": json.loads(str(row["input_digests_json"])),
                "content_digest": str(row["content_digest"]),
                "paper_subject_generation_ids": json.loads(
                    str(row["subject_generation_ids_json"])
                ),
                "account_generation_id": str(row["account_generation_id"] or ""),
                "items": envelope.get("items"),
                "paper_only": True,
                "execution_allowed": False,
            }
            if (
                envelope != expected_envelope
                or _digest(envelope) != row["envelope_digest"]
                or _digest(envelope.get("items")) != row["content_digest"]
            ):
                return unavailable | {"generation_status": "digest_mismatch"}
            PaperEvidenceStore._verify_projection_references(conn, envelope)
            return envelope | {
                "current": True,
                "display_only": False,
                "generation_status": "completed",
            }
        except (
            OSError,
            sqlite3.Error,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            PaperEvidenceConflict,
        ):
            return unavailable | {"generation_status": "unreadable"}
        finally:
            if "conn" in locals():
                conn.close()

    @staticmethod
    def read_account_state(
        database_path: Path | str,
        *,
        account_generation_id: str = "",
    ) -> dict[str, Any]:
        """Replay one immutable account generation through a read-only connection."""
        database_path = Path(database_path)
        unavailable = {
            "valid": False,
            "generation_status": "unavailable",
            "paper_only": True,
            "execution_allowed": False,
        }
        if not database_path.is_file():
            return unavailable
        uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            marker = conn.execute(
                "SELECT value FROM paper_meta WHERE key='schema_version'"
            ).fetchone()
            if marker is None or marker["value"] != SCHEMA_VERSION:
                return unavailable | {
                    "generation_status": "legacy_unversioned_projection"
                }
            generation_id = account_generation_id
            if not generation_id:
                current = conn.execute(
                    "SELECT run_id FROM paper_current_run WHERE singleton=1"
                ).fetchone()
                if current is None:
                    return unavailable
                projection = conn.execute(
                    "SELECT account_generation_id FROM projection_materializations "
                    "WHERE run_id=? AND status='completed' AND account_generation_id IS NOT NULL "
                    "ORDER BY completed_at DESC LIMIT 1",
                    (current["run_id"],),
                ).fetchone()
                if projection is None:
                    return unavailable | {
                        "generation_status": "account_generation_missing"
                    }
                generation_id = str(projection["account_generation_id"])
            reader = PaperEvidenceStore(database_path)
            reader._conn = conn
            state = reader._replay_account_conn(conn, generation_id)
            return state | {
                "valid": True,
                "generation_status": "completed",
            }
        except (
            OSError,
            sqlite3.Error,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            PaperEvidenceConflict,
        ):
            return unavailable | {"generation_status": "unreadable"}
        finally:
            if "conn" in locals():
                conn.close()
