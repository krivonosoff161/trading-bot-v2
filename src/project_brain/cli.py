"""Offline CLI for graph build, bounded routing, store indexing, and shadow checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from .continuity import inspect_continuity_documents
from .graph_builder import build_project_graph, read_graph, write_graph
from .projection import write_projections
from .router import (
    add_dialogue_graph,
    build_context_packet,
    load_contours,
    route_message,
)
from .schema import graph_digest
from .shadow import evaluate_shadow
from .store import ProjectBrainStore


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = ROOT / "configs" / "project_brain" / "architecture.json"
DEFAULT_CONTOURS = ROOT / "configs" / "project_brain" / "dialogue_contours.json"
DEFAULT_GOLDEN = ROOT / "configs" / "project_brain" / "golden_queries.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Public-safe trading-bot-v2 project brain"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-graph", help="build a canonical revision-bound graph")
    build.add_argument("--root", type=Path, default=ROOT)
    build.add_argument("--revision", default="HEAD")
    build.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    build.add_argument("--contours", type=Path, default=DEFAULT_CONTOURS)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--projection-dir", type=Path)

    route = sub.add_parser("route", help="build a bounded Context Packet")
    route.add_argument("--graph", type=Path, required=True)
    route.add_argument("--query", required=True)
    route.add_argument("--contours", type=Path, default=DEFAULT_CONTOURS)
    route.add_argument("--max-tokens", type=int, default=2400)

    shadow = sub.add_parser(
        "shadow", help="evaluate deterministic routing without authority"
    )
    shadow.add_argument("--graph", type=Path, required=True)
    shadow.add_argument("--contours", type=Path, default=DEFAULT_CONTOURS)
    shadow.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    shadow.add_argument("--output", type=Path)

    init = sub.add_parser("init-store", help="create a private rebuildable index")
    init.add_argument("--graph", type=Path, required=True)
    init.add_argument("--store-root", type=Path, required=True)
    init.add_argument("--repository-root", type=Path, default=ROOT)

    status = sub.add_parser("status", help="check Git and continuity freshness")
    status.add_argument("--root", type=Path, default=ROOT)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build-graph":
        graph = build_project_graph(
            args.root, revision=args.revision, catalog_path=args.catalog
        )
        graph = add_dialogue_graph(graph, load_contours(args.contours))
        write_graph(graph, args.output)
        projections: list[str] = []
        if args.projection_dir:
            projections = [
                str(path) for path in write_projections(graph, args.projection_dir)
            ]
        print(
            json.dumps(
                {
                    "schema": graph.schema,
                    "commit_sha": graph.commit_sha,
                    "tree_sha": graph.generated_from_tree,
                    "nodes": len(graph.nodes),
                    "edges": len(graph.edges),
                    "graph_sha256": graph_digest(graph),
                    "output": str(args.output),
                    "projections": projections,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "route":
        graph = read_graph(args.graph)
        route = route_message(args.query, load_contours(args.contours))
        packet = build_context_packet(
            graph, route, args.query, max_tokens=args.max_tokens
        )
        print(json.dumps(packet.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "shadow":
        graph = read_graph(args.graph)
        report = evaluate_shadow(graph, load_contours(args.contours), args.golden)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(report["summary"], sort_keys=True))
        return 0 if report["summary"]["passed"] == report["summary"]["cases"] else 1
    if args.command == "init-store":
        graph = read_graph(args.graph)
        store = ProjectBrainStore(args.store_root, repository_root=args.repository_root)
        store.initialize(graph)
        print(
            json.dumps(
                {"store_root": str(store.root), "graph_sha256": graph_digest(graph)},
                sort_keys=True,
            )
        )
        return 0
    if args.command == "status":
        sha = _git(args.root, "rev-parse", "HEAD")
        branch = _git(args.root, "branch", "--show-current")
        dirty = bool(_git(args.root, "status", "--porcelain=v1"))
        continuity = [
            row.to_dict() for row in inspect_continuity_documents(args.root, sha)
        ]
        print(
            json.dumps(
                {
                    "project_id": "trading-bot-v2",
                    "commit_sha": sha,
                    "branch": branch,
                    "dirty": dirty,
                    "continuity": continuity,
                },
                sort_keys=True,
            )
        )
        return 0
    raise AssertionError(args.command)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
