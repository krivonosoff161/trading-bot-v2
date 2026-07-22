"""Deterministic dialogue routing and bounded Context Packet construction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .schema import GraphEdge, GraphNode, ProjectGraph, content_sha256, stable_id


MODE_RULES = (
    ("runtime", ("запусти", "останови", "runtime", "process", "rcc")),
    ("git", ("создай pr", "commit", "merge", "мержи", "push", "ветк")),
    ("memory", ("запомни", "memory", "compaction", "resume", "контекст")),
    ("code_change", ("исправь", "сделай", "implement", "change code")),
    ("diagnosis", ("найди причину", "diagnos", "root cause", "почему слом")),
    ("research", ("исследуй", "hypothesis", "research")),
    ("status", ("что происходит", "status", "состояние", "срез")),
    ("question", ("объясни", "почему", "what", "how", "?")),
)

AUTHORITY_BY_MODE: Mapping[str, Mapping[str, Any]] = {
    "discussion": {
        "reads": ["public_code", "public_docs", "verified_memory"],
        "writes": [],
        "process": "none",
        "network": "none",
        "git": "none",
    },
    "question": {
        "reads": ["public_code", "public_docs", "verified_memory"],
        "writes": [],
        "process": "none",
        "network": "read_only_when_needed",
        "git": "none",
    },
    "status": {
        "reads": ["public_status", "authorized_runtime_status"],
        "writes": ["private_evidence_delta"],
        "process": "none",
        "network": "none",
        "git": "none",
    },
    "research": {
        "reads": ["public_code", "public_docs", "approved_sources"],
        "writes": ["task_worktree", "private_evidence_delta"],
        "process": "none",
        "network": "public_research_only",
        "git": "task_branch_only",
    },
    "diagnosis": {
        "reads": ["public_code", "authorized_evidence"],
        "writes": ["private_evidence_delta"],
        "process": "none",
        "network": "none",
        "git": "none",
    },
    "code_change": {
        "reads": ["public_code", "public_docs"],
        "writes": ["registered_task_worktree"],
        "process": "none",
        "network": "tests_only",
        "git": "task_branch_only",
    },
    "git": {
        "reads": ["git_metadata", "ci_metadata"],
        "writes": ["task_branch_if_authorized"],
        "process": "none",
        "network": "git_only_if_authorized",
        "git": "fresh_gate_required",
    },
    "runtime": {
        "reads": ["documented_status"],
        "writes": ["private_evidence_delta"],
        "process": "explicit_scope_required",
        "network": "documented_profile_only",
        "git": "none",
    },
    "memory": {
        "reads": ["project_graph", "verified_memory"],
        "writes": ["private_project_brain_delta"],
        "process": "none",
        "network": "none",
        "git": "none",
    },
}

FORBIDDEN_SURFACES = (
    "dotenv_contents",
    "credentials",
    "tokens",
    "recipient_ids",
    "cookie_values",
    "auto_trade",
    "execution_authority",
    "live_orders",
    "private_exchange_endpoints",
)


@dataclass(frozen=True)
class ContourSpec:
    id: str
    label: str
    load_policy: str
    max_tokens: int
    keywords: tuple[str, ...]
    forbidden_mix: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteDecision:
    mode: str
    primary_contour: str
    secondary_contours: tuple[str, ...]
    authority: Mapping[str, Any]
    forbidden_surfaces: tuple[str, ...]
    explanations: tuple[str, ...]
    route_id: str
    authority_id: str


@dataclass(frozen=True)
class ContextPacket:
    packet_id: str
    project_id: str
    commit_sha: str
    branch: str
    task_id: str
    intent: str
    mode: str
    primary_contour: str
    secondary_contours: tuple[str, ...]
    authority: Mapping[str, Any]
    authority_id: str
    verified_facts: tuple[Mapping[str, Any], ...]
    project_nodes: tuple[Mapping[str, Any], ...]
    causal_links: tuple[Mapping[str, Any], ...]
    open_questions: tuple[str, ...]
    freshness: Mapping[str, Any]
    context_budget: Mapping[str, int]
    evidence_refs: tuple[str, ...]
    forbidden_surfaces: tuple[str, ...]
    schema: str = "ProjectContextPacket.v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_contours(path: Path) -> tuple[ContourSpec, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "DialogueContourCatalog.v1":
        raise ValueError("unsupported dialogue contour catalog")
    return tuple(
        ContourSpec(
            id=str(row["id"]),
            label=str(row["label"]),
            load_policy=str(row["load_policy"]),
            max_tokens=int(row["max_tokens"]),
            keywords=tuple(str(item).lower() for item in row.get("keywords", [])),
            forbidden_mix=tuple(str(item) for item in row.get("forbidden_mix", [])),
        )
        for row in payload["contours"]
    )


def classify_mode(message: str) -> str:
    normalized = " ".join(message.lower().split())
    if any(token in normalized for token in ("обсуждаем", "пока не правь", "discuss")):
        return "discussion"
    if any(token in normalized for token in ("почему", "why")) and any(
        token in normalized
        for token in ("останов", "ошиб", "сбой", "fail", "incident", "broken")
    ):
        return "diagnosis"
    if normalized.strip() in {"проверь", "check", "verify"}:
        return "question"
    if (
        normalized.endswith("?")
        or any(
            normalized.startswith(prefix)
            for prefix in (
                "что ",
                "где ",
                "какие ",
                "как ",
                "what ",
                "where ",
                "which ",
                "how ",
            )
        )
    ) and not any(
        token in normalized
        for token in (
            "исследуй",
            "запусти",
            "останови",
            "исправь",
            "создай",
            "merge",
            "push",
        )
    ):
        return "question"
    for mode, needles in MODE_RULES:
        if any(needle in normalized for needle in needles):
            return mode
    return "discussion"


def route_message(message: str, contours: Sequence[ContourSpec]) -> RouteDecision:
    normalized = " ".join(message.lower().split())
    normalized_tokens = _tokens(normalized)
    mode = classify_mode(message)
    scores: list[tuple[int, str, ContourSpec, tuple[str, ...]]] = []
    mode_contour = {
        "runtime": "farm_and_runtime",
        "git": "git_and_release",
        "memory": "active_work",
        "code_change": "project_architecture",
        "diagnosis": "incidents_and_causality",
        "research": "research_and_strategies",
        "status": "farm_and_runtime",
    }.get(mode, "")
    for contour in contours:
        hits = tuple(
            keyword
            for keyword in contour.keywords
            if _keyword_hit(keyword, normalized, normalized_tokens)
        )
        bonus = (
            3
            if contour.id == "governance_and_safety"
            and mode in {"runtime", "git", "code_change"}
            else 0
        )
        if contour.id == mode_contour:
            bonus += 4
        scores.append((len(hits) + bonus, contour.id, contour, hits))
    scores.sort(key=lambda row: (-row[0], row[1]))
    positive = [row for row in scores if row[0] > 0]
    if positive:
        primary = positive[0][2]
        secondary = [row[2].id for row in positive[1:4]]
    else:
        primary = next(row for row in contours if row.id == "project_architecture")
        secondary = []
    for required in ("governance_and_safety", "active_work"):
        if required != primary.id and required not in secondary:
            secondary.append(required)
    explanations = tuple(
        f"{row[2].id}:{','.join(row[3]) or 'mode_boundary'}" for row in positive[:4]
    ) or ("project_architecture:default",)
    payload = [mode, primary.id, secondary, normalized]
    authority = dict(AUTHORITY_BY_MODE[mode])
    return RouteDecision(
        mode=mode,
        primary_contour=primary.id,
        secondary_contours=tuple(secondary),
        authority=authority,
        forbidden_surfaces=FORBIDDEN_SURFACES,
        explanations=explanations,
        route_id=stable_id("route", *payload),
        authority_id=stable_id("authority", mode, authority),
    )


def add_dialogue_graph(
    graph: ProjectGraph, contours: Sequence[ContourSpec]
) -> ProjectGraph:
    by_contour: dict[str, str] = {}
    repository_node = next(node for node in graph.nodes if node.type == "repository")
    for contour in contours:
        node_id = stable_id("dialogue-contour", graph.repository, contour.id)
        node = GraphNode(
            node_id=node_id,
            type="dialogue_contour",
            label=contour.label,
            repository=graph.repository,
            symbol=contour.id,
            source_reference="configs/project_brain/dialogue_contours.json",
            commit_sha=graph.commit_sha,
            content_hash=content_sha256(asdict(contour)),
            first_seen=graph.verified_at,
            last_verified=graph.verified_at,
            owner="project_orchestrator",
            primary_contour=contour.id,
            load_policy=contour.load_policy,
            attributes={
                "max_tokens": contour.max_tokens,
                "forbidden_mix": contour.forbidden_mix,
            },
        )
        graph.add_node(node)
        by_contour[contour.id] = node_id
        root_payload = [repository_node.node_id, node_id, "contains"]
        graph.add_edge(
            GraphEdge(
                edge_id=stable_id("edge", graph.repository, *root_payload),
                source=repository_node.node_id,
                target=node_id,
                relation="contains",
                repository=graph.repository,
                source_reference="configs/project_brain/dialogue_contours.json",
                commit_sha=graph.commit_sha,
                content_hash=content_sha256(root_payload),
                first_seen=graph.verified_at,
                last_verified=graph.verified_at,
                owner="project_brain_router",
                confidence="derived",
                primary_contour=contour.id,
            )
        )
    for node in list(graph.nodes):
        if node.node_id in by_contour.values():
            continue
        for contour_id in (node.primary_contour, *node.secondary_contours):
            target = by_contour.get(contour_id)
            if not target:
                continue
            payload = [node.node_id, target, "belongs_to_contour"]
            graph.add_edge(
                GraphEdge(
                    edge_id=stable_id("edge", graph.repository, *payload),
                    source=node.node_id,
                    target=target,
                    relation="belongs_to_contour",
                    repository=graph.repository,
                    source_reference=node.source_reference,
                    commit_sha=graph.commit_sha,
                    content_hash=content_sha256(payload),
                    first_seen=graph.verified_at,
                    last_verified=graph.verified_at,
                    owner="project_brain_router",
                    confidence="derived",
                    evidence_refs=node.evidence_refs,
                    primary_contour=contour_id,
                )
            )
    connected = {edge.source for edge in graph.edges} | {
        edge.target for edge in graph.edges
    }
    graph.metrics["orphan_nodes"] = sum(
        1 for node in graph.nodes if node.node_id not in connected
    )
    node_counts = dict(graph.metrics.get("node_counts") or {})
    node_counts["dialogue_contour"] = len(by_contour)
    graph.metrics["node_counts"] = dict(sorted(node_counts.items()))
    edge_counts = dict(graph.metrics.get("edge_counts") or {})
    edge_counts["contains"] = sum(
        1 for edge in graph.edges if edge.relation == "contains"
    )
    edge_counts["belongs_to_contour"] = sum(
        1 for edge in graph.edges if edge.relation == "belongs_to_contour"
    )
    graph.metrics["edge_counts"] = dict(sorted(edge_counts.items()))
    graph.validate()
    return graph


def build_context_packet(
    graph: ProjectGraph,
    route: RouteDecision,
    message: str,
    *,
    memory_records: Iterable[Mapping[str, Any]] = (),
    causal_links: Iterable[Mapping[str, Any]] = (),
    max_tokens: int = 2400,
    branch: str = "",
    task_id: str = "",
) -> ContextPacket:
    allowed_contours = {route.primary_contour, *route.secondary_contours}
    candidates = [
        node
        for node in graph.nodes
        if node.primary_contour in allowed_contours
        and node.sensitivity not in {"private", "protected_identity"}
        and node.load_policy not in {"logical_schema_only", "aggregate_only"}
        and (
            node.load_policy != "explicit_cross_project_only"
            or any(
                token in message.lower()
                for token in ("security", "agentic-security", "межпроект")
            )
        )
    ]
    query_tokens = _tokens(message)
    candidates.sort(
        key=lambda node: (
            -len(query_tokens & _tokens(f"{node.label} {node.path} {node.symbol}")),
            0 if node.load_policy == "always" else 1,
            node.node_id,
        )
    )
    selected_nodes: list[Mapping[str, Any]] = []
    selected_records: list[Mapping[str, Any]] = []
    used_chars = 0
    max_chars = max_tokens * 4
    for node in candidates:
        node_compact: dict[str, Any] = {
            "node_id": node.node_id,
            "type": node.type,
            "label": node.label,
            "path": node.path,
            "symbol": node.symbol,
            "status": node.status,
            "owner": node.owner,
            "freshness": node.freshness,
            "evidence_refs": node.evidence_refs[:3],
        }
        cost = len(json.dumps(node_compact, ensure_ascii=False))
        if used_chars + cost > max_chars or len(selected_nodes) >= 24:
            break
        selected_nodes.append(node_compact)
        used_chars += cost
    open_questions: list[str] = []
    for record in memory_records:
        if str(record.get("contour")) not in allowed_contours:
            continue
        if str(record.get("type")) in {"hypothesis", "blocked"}:
            summary = str(record.get("summary") or "").strip()
            if summary and summary not in open_questions and len(open_questions) < 8:
                open_questions.append(summary)
            continue
        if str(record.get("confidence")) not in {"verified", "high"}:
            continue
        record_compact: dict[str, Any] = {
            key: record.get(key)
            for key in (
                "record_id",
                "entity",
                "type",
                "summary",
                "evidence_refs",
                "commit_sha",
                "freshness",
                "confidence",
            )
        }
        cost = len(json.dumps(record_compact, ensure_ascii=False, default=str))
        if used_chars + cost > max_chars or len(selected_records) >= 12:
            break
        selected_records.append(record_compact)
        used_chars += cost
    causal = tuple(list(causal_links)[:12])
    evidence_set: set[str] = set()
    for selected_row in selected_nodes:
        refs = selected_row.get("evidence_refs") or ()
        if isinstance(refs, (list, tuple)):
            evidence_set.update(str(ref) for ref in refs if ref)
    evidence = sorted(evidence_set)
    payload = [
        graph.repository,
        graph.commit_sha,
        route.route_id,
        message,
        [row["node_id"] for row in selected_nodes],
    ]
    return ContextPacket(
        packet_id=stable_id("packet", *payload),
        project_id=graph.repository,
        commit_sha=graph.commit_sha,
        branch=branch,
        task_id=task_id,
        intent=message[:500],
        mode=route.mode,
        primary_contour=route.primary_contour,
        secondary_contours=route.secondary_contours,
        authority=route.authority,
        authority_id=route.authority_id,
        verified_facts=tuple(selected_records),
        project_nodes=tuple(selected_nodes),
        causal_links=causal,
        open_questions=tuple(open_questions),
        freshness={"graph": "current", "commit_sha": graph.commit_sha},
        context_budget={
            "max_tokens": max_tokens,
            "estimated_tokens": (used_chars + 3) // 4,
        },
        evidence_refs=tuple(evidence),
        forbidden_surfaces=route.forbidden_surfaces,
    )


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-zА-Яа-я0-9_]{3,}", text)}


def _keyword_hit(keyword: str, normalized: str, tokens: set[str]) -> bool:
    if " " in keyword:
        return keyword in normalized
    if len(keyword) <= 3:
        return keyword in tokens
    return any(token == keyword or token.startswith(keyword) for token in tokens)
