"""Private local project-brain event log and rebuildable SQLite index.

The append-only JSONL stream is memory evidence. SQLite is only a query index;
deleting it and calling ``rebuild_index`` must preserve the same logical state.
No store may be created inside the public repository unless a test explicitly
uses an isolated temporary root.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Mapping

from .schema import MEMORY_SCHEMA, RECORD_TYPES, ProjectGraph, content_sha256, stable_id


CAUSAL_STAGES = (
    "symptom",
    "timeline",
    "observation",
    "excluded_hypothesis",
    "root_cause",
    "decision",
    "change",
    "verification",
    "residual_risk",
)
DENIED_KEYS = frozenset(
    {
        "api_key",
        "secret",
        "password",
        "passwd",
        "token",
        "bot_token",
        "recipient_id",
        "chat_id",
        "cookie",
        "session_cookie",
        "credential",
    }
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


@dataclass(frozen=True)
class MemoryRecord:
    record_id: str
    contour: str
    entity: str
    type: str
    source: str
    evidence_refs: tuple[str, ...]
    repository: str
    branch: str
    commit_sha: str
    content_hash: str
    created_at: str
    verified_at: str
    freshness: str
    confidence: str
    authority: str
    supersedes: str = ""
    summary: str = ""
    project_node_ids: tuple[str, ...] = ()
    source_hashes: Mapping[str, str] | None = None
    causal_chain_id: str = ""
    causal_stage: str = ""
    task_id: str = ""
    authority_id: str = ""
    schema: str = MEMORY_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "evidence_refs": list(self.evidence_refs),
            "project_node_ids": list(self.project_node_ids),
            "source_hashes": dict(self.source_hashes or {}),
        }


class ProjectBrainStore:
    def __init__(
        self, root: Path, *, repository_root: Path, allow_test_root: bool = False
    ) -> None:
        self.root = root.resolve()
        self.repository_root = repository_root.resolve()
        if not allow_test_root and _is_within(self.root, self.repository_root):
            raise ValueError("project brain must live outside the public repository")
        self.events_path = self.root / "events.jsonl"
        self.index_path = self.root / "index.sqlite3"
        self.graph_path = self.root / "project_graph.json"

    def initialize(self, graph: ProjectGraph) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.graph_path.write_text(
            json.dumps(graph.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
        self._initialize_index()
        self.index_graph(graph)

    def append_record(
        self,
        *,
        contour: str,
        entity: str,
        record_type: str,
        source: str,
        evidence_refs: Iterable[str],
        repository: str,
        branch: str,
        commit_sha: str,
        authority: str,
        summary: str,
        confidence: str = "verified",
        supersedes: str = "",
        project_node_ids: Iterable[str] = (),
        source_hashes: Mapping[str, str] | None = None,
        causal_chain_id: str = "",
        causal_stage: str = "",
        task_id: str = "",
        authority_id: str = "",
        verified_at: str | None = None,
    ) -> MemoryRecord:
        if record_type not in RECORD_TYPES:
            raise ValueError(f"unsupported memory record type: {record_type}")
        if causal_stage and causal_stage not in CAUSAL_STAGES:
            raise ValueError(f"unsupported causal stage: {causal_stage}")
        if causal_stage == "root_cause" and record_type == "hypothesis":
            raise ValueError("a hypothesis cannot be recorded as root cause")
        refs = tuple(sorted(set(str(ref) for ref in evidence_refs if ref)))
        if causal_stage == "root_cause" and not refs:
            raise ValueError("root cause requires evidence references")
        payload = {
            "contour": contour,
            "entity": entity,
            "type": record_type,
            "source": source,
            "evidence_refs": refs,
            "repository": repository,
            "branch": branch,
            "commit_sha": commit_sha,
            "authority": authority,
            "summary": summary,
            "supersedes": supersedes,
            "project_node_ids": tuple(project_node_ids),
            "source_hashes": dict(source_hashes or {}),
            "causal_chain_id": causal_chain_id,
            "causal_stage": causal_stage,
            "task_id": task_id,
            "authority_id": authority_id,
        }
        _reject_sensitive_fields(payload)
        now = verified_at or utc_now()
        record = MemoryRecord(
            record_id=stable_id("memory", repository, content_sha256(payload)),
            contour=contour,
            entity=entity,
            type=record_type,
            source=source,
            evidence_refs=refs,
            repository=repository,
            branch=branch,
            commit_sha=commit_sha,
            content_hash=content_sha256(payload),
            created_at=now,
            verified_at=now,
            freshness="current",
            confidence=confidence,
            authority=authority,
            supersedes=supersedes,
            summary=summary,
            project_node_ids=tuple(project_node_ids),
            source_hashes=dict(source_hashes or {}),
            causal_chain_id=causal_chain_id,
            causal_stage=causal_stage,
            task_id=task_id,
            authority_id=authority_id,
        )
        if self._record_exists(record.record_id):
            return record
        self._append_event(
            {
                "event_schema": "ProjectBrainEvent.v1",
                "event": "record",
                "record": record.to_dict(),
            }
        )
        self._upsert_record(record)
        return record

    def append_causal_link(
        self,
        *,
        chain_id: str,
        source_record_id: str,
        target_record_id: str,
        relation: str,
        evidence_refs: Iterable[str],
    ) -> str:
        allowed = {
            "precedes",
            "supports",
            "excludes",
            "caused",
            "motivated",
            "fixed_by",
            "verified_by",
            "leaves_risk",
        }
        if relation not in allowed:
            raise ValueError(f"unsupported causal relation: {relation}")
        refs = tuple(sorted(set(evidence_refs)))
        if not refs:
            raise ValueError("causal links require evidence")
        link_id = stable_id(
            "causal", chain_id, source_record_id, target_record_id, relation
        )
        row = {
            "event_schema": "ProjectBrainEvent.v1",
            "event": "causal_link",
            "link_id": link_id,
            "chain_id": chain_id,
            "source_record_id": source_record_id,
            "target_record_id": target_record_id,
            "relation": relation,
            "evidence_refs": refs,
            "created_at": utc_now(),
        }
        self._append_event(row)
        self._initialize_index()
        with _connection(self.index_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO causal_links VALUES (?,?,?,?,?,?,?)",
                (
                    link_id,
                    chain_id,
                    source_record_id,
                    target_record_id,
                    relation,
                    json.dumps(refs),
                    row["created_at"],
                ),
            )
        return link_id

    def index_graph(self, graph: ProjectGraph) -> None:
        self._initialize_index()
        with _connection(self.index_path) as conn:
            conn.execute("DELETE FROM graph_nodes")
            conn.execute("DELETE FROM graph_edges")
            conn.executemany(
                "INSERT INTO graph_nodes VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        node.node_id,
                        node.type,
                        node.label,
                        node.path,
                        node.symbol,
                        node.commit_sha,
                        node.content_hash,
                        node.primary_contour,
                        json.dumps(node.to_dict(), ensure_ascii=False, sort_keys=True),
                    )
                    for node in graph.nodes
                ],
            )
            conn.executemany(
                "INSERT INTO graph_edges VALUES (?,?,?,?,?,?)",
                [
                    (
                        edge.edge_id,
                        edge.source,
                        edge.target,
                        edge.relation,
                        edge.commit_sha,
                        json.dumps(edge.to_dict(), ensure_ascii=False, sort_keys=True),
                    )
                    for edge in graph.edges
                ],
            )

    def assess_freshness(self, graph: ProjectGraph) -> dict[str, str]:
        by_id = {node.node_id: node for node in graph.nodes}
        result: dict[str, str] = {}
        for record in self.records():
            freshness = "current"
            if record["commit_sha"] != graph.commit_sha:
                freshness = "commit_stale"
            for node_id in record.get("project_node_ids", []):
                if node_id not in by_id:
                    freshness = "source_removed"
                    break
            for node_id, expected_hash in (record.get("source_hashes") or {}).items():
                node = by_id.get(node_id)
                if node is None:
                    freshness = "source_removed"
                    break
                if node.content_hash != expected_hash:
                    freshness = "source_changed"
                    break
            result[str(record["record_id"])] = freshness
        return result

    def records(self, *, contours: Iterable[str] = ()) -> list[dict[str, Any]]:
        self._initialize_index()
        wanted = tuple(contours)
        query = "SELECT payload FROM memory_records"
        params: tuple[Any, ...] = ()
        if wanted:
            query += f" WHERE contour IN ({','.join('?' for _ in wanted)})"
            params = wanted
        query += " ORDER BY verified_at DESC, record_id"
        with _connection(self.index_path) as conn:
            return [json.loads(row[0]) for row in conn.execute(query, params)]

    def causal_links(self, chain_id: str = "") -> list[dict[str, Any]]:
        self._initialize_index()
        query = "SELECT link_id,chain_id,source_record_id,target_record_id,relation,evidence_refs,created_at FROM causal_links"
        params: tuple[Any, ...] = ()
        if chain_id:
            query += " WHERE chain_id=?"
            params = (chain_id,)
        query += " ORDER BY created_at,link_id"
        with _connection(self.index_path) as conn:
            return [
                {
                    "link_id": row[0],
                    "chain_id": row[1],
                    "source_record_id": row[2],
                    "target_record_id": row[3],
                    "relation": row[4],
                    "evidence_refs": json.loads(row[5]),
                    "created_at": row[6],
                }
                for row in conn.execute(query, params)
            ]

    def rebuild_index(self, graph: ProjectGraph | None = None) -> None:
        if self.index_path.exists():
            self.index_path.unlink()
        self._initialize_index()
        if graph is not None:
            self.index_graph(graph)
        if not self.events_path.exists():
            return
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("event") == "record":
                self._upsert_record(MemoryRecord(**row["record"]))
            elif row.get("event") == "causal_link":
                with _connection(self.index_path) as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO causal_links VALUES (?,?,?,?,?,?,?)",
                        (
                            row["link_id"],
                            row["chain_id"],
                            row["source_record_id"],
                            row["target_record_id"],
                            row["relation"],
                            json.dumps(row["evidence_refs"]),
                            row["created_at"],
                        ),
                    )

    def _append_event(self, row: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n"
            )

    def _initialize_index(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with _connection(self.index_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_records(
                  record_id TEXT PRIMARY KEY, contour TEXT NOT NULL, entity TEXT NOT NULL,
                  type TEXT NOT NULL, commit_sha TEXT NOT NULL, verified_at TEXT NOT NULL,
                  freshness TEXT NOT NULL, supersedes TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_memory_contour ON memory_records(contour,verified_at);
                CREATE TABLE IF NOT EXISTS causal_links(
                  link_id TEXT PRIMARY KEY, chain_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
                  target_record_id TEXT NOT NULL, relation TEXT NOT NULL,
                  evidence_refs TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS graph_nodes(
                  node_id TEXT PRIMARY KEY, type TEXT NOT NULL, label TEXT NOT NULL,
                  path TEXT NOT NULL, symbol TEXT NOT NULL, commit_sha TEXT NOT NULL,
                  content_hash TEXT NOT NULL, contour TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS graph_edges(
                  edge_id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,
                  relation TEXT NOT NULL, commit_sha TEXT NOT NULL, payload TEXT NOT NULL);
                """
            )

    def _upsert_record(self, record: MemoryRecord) -> None:
        self._initialize_index()
        payload = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True)
        with _connection(self.index_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO memory_records VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    record.record_id,
                    record.contour,
                    record.entity,
                    record.type,
                    record.commit_sha,
                    record.verified_at,
                    record.freshness,
                    record.supersedes,
                    payload,
                ),
            )

    def _record_exists(self, record_id: str) -> bool:
        self._initialize_index()
        with _connection(self.index_path) as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM memory_records WHERE record_id=?",
                    (record_id,),
                ).fetchone()
                is not None
            )


def _reject_sensitive_fields(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in DENIED_KEYS:
                raise ValueError(
                    f"sensitive field is forbidden: {'.'.join([*path, normalized])}"
                )
            _reject_sensitive_fields(item, (*path, normalized))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_sensitive_fields(item, (*path, str(index)))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


@contextmanager
def _connection(path: Path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()
