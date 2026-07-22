"""Canonical public-safe schemas for the reproducible project graph.

The JSON graph is the canonical snapshot for one Git revision. SQLite, Markdown,
Mermaid, and context packets are indexes or projections and never override it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Iterable, Mapping


GRAPH_SCHEMA = "TradingProjectGraph.v1"
MEMORY_SCHEMA = "TradingProjectMemoryRecord.v1"

NODE_TYPES = frozenset(
    {
        "repository",
        "worktree",
        "branch",
        "commit",
        "directory",
        "source_module",
        "class",
        "function",
        "cli",
        "bat_entrypoint",
        "runtime_contour",
        "process",
        "process_owner",
        "port",
        "lease",
        "fence",
        "stop_intent",
        "database",
        "table",
        "schema",
        "data_source",
        "file_artifact",
        "evidence_artifact",
        "configuration",
        "strategy_family",
        "validation_method",
        "model_provider",
        "prompt_tool_contract",
        "telegram_surface",
        "test",
        "ci_workflow",
        "policy",
        "authority_gate",
        "decision",
        "hypothesis",
        "incident",
        "residual_risk",
        "external_repository",
        "dialogue_contour",
    }
)

RELATION_TYPES = frozenset(
    {
        "contains",
        "imports",
        "calls",
        "starts",
        "stops",
        "owns",
        "supervises",
        "reads",
        "writes",
        "produces",
        "consumes",
        "validates",
        "blocks",
        "permits",
        "depends_on",
        "routes_to",
        "notifies",
        "renders",
        "mirrors",
        "vendors",
        "tests",
        "guards",
        "supersedes",
        "conflicts_with",
        "caused",
        "fixed_by",
        "verified_by",
        "evidence_for",
        "belongs_to_contour",
        "crosses_repository_boundary",
    }
)

RECORD_TYPES = frozenset(
    {
        "observed",
        "derived",
        "hypothesis",
        "decision",
        "implementation",
        "verification",
        "blocked",
        "residual_risk",
        "superseded",
    }
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(prefix: str, *identity: object) -> str:
    digest = content_sha256(list(identity))[:24]
    return f"{prefix}:{digest}"


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    type: str
    label: str
    repository: str
    path: str = ""
    symbol: str = ""
    source_reference: str = ""
    commit_sha: str = ""
    content_hash: str = ""
    first_seen: str = ""
    last_verified: str = ""
    freshness: str = "current"
    status: str = "active"
    owner: str = "unknown"
    sensitivity: str = "public"
    confidence: str = "verified"
    evidence_refs: tuple[str, ...] = ()
    primary_contour: str = "project_architecture"
    secondary_contours: tuple[str, ...] = ()
    superseded_by: str = ""
    load_policy: str = "on_demand"
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in NODE_TYPES:
            raise ValueError(f"unsupported node type: {self.type}")
        if not self.node_id or not self.label or not self.repository:
            raise ValueError("node id, label, and repository are required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    source: str
    target: str
    relation: str
    repository: str
    source_reference: str = ""
    commit_sha: str = ""
    content_hash: str = ""
    first_seen: str = ""
    last_verified: str = ""
    freshness: str = "current"
    status: str = "active"
    owner: str = "unknown"
    sensitivity: str = "public"
    confidence: str = "verified"
    evidence_refs: tuple[str, ...] = ()
    primary_contour: str = "project_architecture"
    secondary_contours: tuple[str, ...] = ()
    superseded_by: str = ""
    load_policy: str = "on_demand"
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.relation not in RELATION_TYPES:
            raise ValueError(f"unsupported relation type: {self.relation}")
        if not self.source or not self.target:
            raise ValueError("edge endpoints are required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectGraph:
    repository: str
    commit_sha: str
    generated_from_tree: str
    verified_at: str
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    schema: str = GRAPH_SCHEMA
    _node_ids: set[str] = field(default_factory=set, init=False, repr=False)
    _edge_ids: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        self._node_ids = {node.node_id for node in self.nodes}
        self._edge_ids = {edge.edge_id for edge in self.edges}

    def add_node(self, node: GraphNode) -> None:
        if node.node_id in self._node_ids:
            return
        self.nodes.append(node)
        self._node_ids.add(node.node_id)

    def add_edge(self, edge: GraphEdge) -> None:
        if edge.edge_id in self._edge_ids:
            return
        self.edges.append(edge)
        self._edge_ids.add(edge.edge_id)

    def validate(self) -> None:
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate graph node id")
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("duplicate graph edge id")
        known = set(node_ids)
        dangling = [
            edge.edge_id
            for edge in self.edges
            if edge.source not in known or edge.target not in known
        ]
        if dangling:
            raise ValueError(f"dangling graph edges: {dangling[:5]}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "repository": self.repository,
            "commit_sha": self.commit_sha,
            "generated_from_tree": self.generated_from_tree,
            "verified_at": self.verified_at,
            "nodes": [
                node.to_dict()
                for node in sorted(self.nodes, key=lambda row: row.node_id)
            ],
            "edges": [
                edge.to_dict()
                for edge in sorted(self.edges, key=lambda row: row.edge_id)
            ],
            "metrics": self.metrics,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProjectGraph":
        if payload.get("schema") != GRAPH_SCHEMA:
            raise ValueError("unsupported project graph schema")
        graph = cls(
            repository=str(payload["repository"]),
            commit_sha=str(payload["commit_sha"]),
            generated_from_tree=str(payload["generated_from_tree"]),
            verified_at=str(payload["verified_at"]),
            nodes=[GraphNode(**row) for row in payload.get("nodes", [])],
            edges=[GraphEdge(**row) for row in payload.get("edges", [])],
            metrics=dict(payload.get("metrics") or {}),
        )
        graph.validate()
        return graph


def graph_digest(graph: ProjectGraph) -> str:
    return content_sha256(graph.to_dict())


def unique_records(rows: Iterable[Mapping[str, Any]], key: str) -> bool:
    values = [str(row.get(key) or "") for row in rows]
    return bool(values) and all(values) and len(values) == len(set(values))
