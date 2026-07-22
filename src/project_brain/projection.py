"""Small reproducible human projections from the canonical JSON graph."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from .schema import ProjectGraph


def markdown_projection(graph: ProjectGraph) -> str:
    nodes = Counter(node.type for node in graph.nodes)
    edges = Counter(edge.relation for edge in graph.edges)
    risks = [node for node in graph.nodes if node.type == "residual_risk"]
    externals = [node for node in graph.nodes if node.type == "external_repository"]
    databases = [node for node in graph.nodes if node.type == "database"]
    contours = [node for node in graph.nodes if node.type == "dialogue_contour"]
    lines = [
        "# Trading Bot V2 Project Graph",
        "",
        f"- schema: `{graph.schema}`",
        f"- repository: `{graph.repository}`",
        f"- commit: `{graph.commit_sha}`",
        f"- tree: `{graph.generated_from_tree}`",
        f"- nodes: {len(graph.nodes)}",
        f"- edges: {len(graph.edges)}",
        "",
        "## Coverage metrics",
        "",
    ]
    for key, value in sorted(graph.metrics.items()):
        if isinstance(value, dict) and {"numerator", "denominator", "method"} <= set(value):
            lines.append(
                f"- {key}: `{value['numerator']}/{value['denominator']}` "
                f"(`{value.get('pct', 0.0)}%`) — {value['method']}"
            )
            for extra_key in ("parse_failures", "unresolved", "orphan_count"):
                if extra_key in value:
                    lines.append(f"  - {extra_key}: `{value[extra_key]}`")
            continue
        if isinstance(value, dict):
            compact = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if len(compact) <= 500:
                lines.append(f"- {key}: `{compact}`")
            elif "group_count" in value:
                lines.append(
                    f"- {key}: groups=`{value['group_count']}`, "
                    f"candidate_nodes=`{value.get('candidate_node_count', 0)}` — "
                    f"{value.get('method', '')}"
                )
            continue
        if not isinstance(value, list):
            lines.append(f"- {key}: `{value}`")
    lines += ["", "## Node inventory", ""]
    lines.extend(f"- {key}: {value}" for key, value in sorted(nodes.items()))
    lines += ["", "## Relation inventory", ""]
    lines.extend(f"- {key}: {value}" for key, value in sorted(edges.items()))
    lines += ["", "## Database authorities", ""]
    lines.extend(
        f"- `{node.label}` — `{node.source_reference}` — owner `{node.owner}`"
        for node in sorted(databases, key=lambda row: row.label)
    )
    lines += ["", "## External boundaries", ""]
    lines.extend(
        f"- `{node.label}` — status `{node.status}`, load `{node.load_policy}`"
        for node in sorted(externals, key=lambda row: row.label)
    )
    if contours:
        lines += ["", "## Dialogue contours", ""]
        lines.extend(
            f"- `{node.symbol}` — {node.label}"
            for node in sorted(contours, key=lambda row: row.symbol)
        )
    lines += ["", "## Residual risks", ""]
    lines.extend(
        f"- {node.label} (`{node.source_reference}`)"
        for node in sorted(risks, key=lambda row: row.label)
    )
    return "\n".join(lines) + "\n"


def mermaid_projection(graph: ProjectGraph, *, max_nodes: int = 80) -> str:
    interesting = {
        node.node_id: node
        for node in graph.nodes
        if node.type
        in {
            "repository",
            "external_repository",
            "runtime_contour",
            "process",
            "database",
            "model_provider",
            "validation_method",
            "authority_gate",
            "telegram_surface",
            "dialogue_contour",
        }
    }
    selected = dict(list(sorted(interesting.items()))[:max_nodes])
    edges = [
        edge
        for edge in graph.edges
        if edge.source in selected and edge.target in selected
    ]
    lines = ["flowchart LR"]
    aliases = {node_id: f"N{index}" for index, node_id in enumerate(selected, start=1)}
    for node_id, node in selected.items():
        label = node.label.replace('"', "'")
        lines.append(f'  {aliases[node_id]}["{label}"]')
    for edge in edges:
        lines.append(
            f"  {aliases[edge.source]} -->|{edge.relation}| {aliases[edge.target]}"
        )
    return "\n".join(lines) + "\n"


def write_projections(graph: ProjectGraph, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    markdown = directory / "project_map.md"
    mermaid = directory / "project_map.mmd"
    markdown.write_text(markdown_projection(graph), encoding="utf-8", newline="\n")
    mermaid.write_text(mermaid_projection(graph), encoding="utf-8", newline="\n")
    return markdown, mermaid
