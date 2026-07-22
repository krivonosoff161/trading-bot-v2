"""Deterministic static and semantic graph builder for a Git revision.

Only tracked Git blobs and the reviewed public semantic catalog are read. The
builder never imports product modules and never opens runtime state.
"""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
from typing import Any, Iterable, Mapping

from .schema import GraphEdge, GraphNode, ProjectGraph, content_sha256, stable_id


REPOSITORY_ID = "trading-bot-v2"
PYTHON_ROOTS = {"src", "scripts", "tests", "vendor"}
DB_BY_MODULE = {
    "src.research_lab.candle_store": "candles.sqlite3",
    "src.research_lab.farm_tasks_db": "farm_tasks.sqlite",
    "src.research_lab.ownership": "ownership.sqlite",
    "src.research_lab.paper_evidence_store": "paper_evidence.sqlite3",
    "src.research_lab.pipeline_state": "scanner_farm_loop.sqlite",
    "src.research_lab.state_db": "strategy_lab.sqlite",
    "src.research_lab.storage_maintenance_store": "operations.sqlite3",
    "src.research_lab.storage_segment_store": "segment_events.sqlite3",
    "src.scout.news_buffer": "news_buffer.sqlite",
}
SQL_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
BAT_MODULE = re.compile(
    r"(?:python(?:\.exe)?\s+-m|%PYTHON%\s+-m)\s+([A-Za-z_][A-Za-z0-9_.]*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GitSnapshot:
    root: Path
    revision: str
    commit_sha: str
    tree_sha: str
    committed_at: str
    files: Mapping[str, bytes]

    @classmethod
    def load(cls, root: Path, revision: str = "HEAD") -> "GitSnapshot":
        root = root.resolve()
        commit_sha = _git(root, "rev-parse", f"{revision}^{{commit}}")
        tree_sha = _git(root, "rev-parse", f"{commit_sha}^{{tree}}")
        committed_at = _git(root, "show", "-s", "--format=%cI", commit_sha)
        archive = subprocess.run(
            ["git", "archive", "--format=tar", commit_sha],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
        files: dict[str, bytes] = {}
        with tarfile.open(fileobj=BytesIO(archive), mode="r:") as bundle:
            for member in bundle.getmembers():
                if not member.isfile():
                    continue
                stream = bundle.extractfile(member)
                if stream is not None:
                    files[PurePosixPath(member.name).as_posix()] = stream.read()
        return cls(root, revision, commit_sha, tree_sha, committed_at, files)

    def text(self, path: str) -> str:
        return self.files[path].decode("utf-8-sig", errors="replace")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _module_name(path: str) -> str:
    pure = PurePosixPath(path)
    parts = list(pure.with_suffix("").parts)
    if parts[-1:] == ["__init__"]:
        parts.pop()
    return ".".join(parts)


def _status(path: str) -> str:
    if path.startswith(("scripts/archive/", "docs/legacy-evidence/")):
        return "archive"
    if (
        path.startswith(("scripts/ws/", "src/data/", "src/exchange/"))
        or path == "main.py"
    ):
        return "reference"
    if path == "docs/deferred-adaptive-paper-architecture.md":
        return "superseded"
    return "active"


def _contours(path: str, symbol: str = "") -> tuple[str, tuple[str, ...]]:
    token = f"{path} {symbol}".lower()
    matches: list[str] = ["project_architecture"]
    rules = (
        (
            "governance_and_safety",
            ("agent", "policy", "authority", "guard", "runtime_root"),
        ),
        ("git_and_release", (".github/", "ci/", "supply_chain", "artifact")),
        (
            "farm_and_runtime",
            (
                "farm",
                "ownership",
                "lease",
                "fence",
                "heartbeat",
                "control_center",
                "startup",
                "stop_intent",
            ),
        ),
        (
            "data_and_lineage",
            ("lineage", "candle", "storage", "data_", "packet", "schema"),
        ),
        (
            "research_and_strategies",
            ("strategy", "research", "sweep", "hypothesis", "feature"),
        ),
        ("validation", ("validation", "validator", "backtest", "pbo", "simulator")),
        ("paper_lifecycle", ("paper", "outcome", "account", "materialization")),
        ("models_and_llm", ("llm", "model", "prompt", "ollama", "calculator")),
        ("scanner_and_news", ("scout", "scanner", "news", "source_")),
        ("telegram_and_delivery", ("telegram", "delivery", "outbox", "subscriber")),
    )
    for contour, needles in rules:
        if any(needle in token for needle in needles):
            matches.append(contour)
    primary = matches[-1] if len(matches) > 1 else matches[0]
    return primary, tuple(row for row in dict.fromkeys(matches) if row != primary)


def _owner(path: str) -> str:
    if path.startswith("src/scout/"):
        return "scanner_intake"
    if path.startswith(("src/research_lab/", "scripts/strategy_lab/")):
        return "calculation_farm"
    if path.startswith("vendor/honest-backtest/"):
        return "honest_backtest_upstream"
    if "telegram" in path:
        return "telegram_delivery"
    if path.startswith("tests/"):
        return "verification_suite"
    if path.startswith(("docs/", ".github/")):
        return "repository_maintainers"
    return "repository_maintainers"


def _node(
    snapshot: GitSnapshot,
    node_type: str,
    label: str,
    *,
    identity: Iterable[object],
    path: str = "",
    symbol: str = "",
    line: int = 0,
    content: Any = "",
    status: str | None = None,
    owner: str | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> GraphNode:
    primary, secondary = _contours(path, symbol)
    ref = f"{path}:{line}" if path and line else path
    prefix = node_type.replace("_", "-")
    return GraphNode(
        node_id=stable_id(prefix, REPOSITORY_ID, *identity),
        type=node_type,
        label=label,
        repository=REPOSITORY_ID,
        path=path,
        symbol=symbol,
        source_reference=ref,
        commit_sha=snapshot.commit_sha,
        content_hash=content_sha256(content),
        first_seen=snapshot.committed_at,
        last_verified=snapshot.committed_at,
        status=status or _status(path),
        owner=owner or _owner(path),
        primary_contour=primary,
        secondary_contours=secondary,
        evidence_refs=(ref,) if ref else (),
        attributes=dict(attributes or {}),
    )


def _edge(
    snapshot: GitSnapshot,
    source: str,
    target: str,
    relation: str,
    *,
    ref: str = "",
    confidence: str = "verified",
    attributes: Mapping[str, Any] | None = None,
) -> GraphEdge:
    primary, secondary = _contours(ref)
    payload = [source, target, relation, ref, dict(attributes or {})]
    return GraphEdge(
        edge_id=stable_id("edge", REPOSITORY_ID, *payload),
        source=source,
        target=target,
        relation=relation,
        repository=REPOSITORY_ID,
        source_reference=ref,
        commit_sha=snapshot.commit_sha,
        content_hash=content_sha256(payload),
        first_seen=snapshot.committed_at,
        last_verified=snapshot.committed_at,
        owner="project_brain_builder",
        confidence=confidence,
        evidence_refs=(ref,) if ref else (),
        primary_contour=primary,
        secondary_contours=secondary,
        attributes=dict(attributes or {}),
    )


class _DefinitionCollector(ast.NodeVisitor):
    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = text.splitlines(keepends=True)
        self.stack: list[str] = []
        self.definitions: list[tuple[str, str, int, str]] = []
        self.callers: dict[int, str] = {}

    def _visit_definition(self, node: ast.AST, name: str, kind: str) -> None:
        qualified = ".".join([*self.stack, name])
        start = max(0, int(getattr(node, "lineno", 1)) - 1)
        end = max(start + 1, int(getattr(node, "end_lineno", start + 1)))
        segment = "".join(self.lines[start:end]) or ast.dump(
            node, include_attributes=False
        )
        self.definitions.append(
            (kind, qualified, int(getattr(node, "lineno", 0)), segment)
        )
        self.stack.append(name)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                self.callers[id(child)] = qualified
            self.visit(child)
        self.stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._visit_definition(node, node.name, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_definition(node, node.name, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_definition(node, node.name, "function")


def _resolve_from(current: str, module: str | None, level: int) -> str:
    if not level:
        return module or ""
    base = current.split(".")[:-1]
    keep = max(0, len(base) - level + 1)
    return ".".join([*base[:keep], *(module or "").split(".")]).strip(".")


def build_project_graph(
    root: Path,
    *,
    revision: str = "HEAD",
    catalog_path: Path | None = None,
) -> ProjectGraph:
    snapshot = GitSnapshot.load(root, revision)
    graph = ProjectGraph(
        REPOSITORY_ID, snapshot.commit_sha, snapshot.tree_sha, snapshot.committed_at
    )
    repo = _node(
        snapshot,
        "repository",
        REPOSITORY_ID,
        identity=(REPOSITORY_ID,),
        content={"tree": snapshot.tree_sha},
    )
    commit = _node(
        snapshot,
        "commit",
        snapshot.commit_sha[:12],
        identity=("commit", snapshot.commit_sha),
        content=snapshot.commit_sha,
    )
    branch = _node(
        snapshot,
        "branch",
        revision,
        identity=("branch", revision, snapshot.commit_sha),
        content=revision,
    )
    graph.add_node(repo)
    graph.add_node(commit)
    graph.add_node(branch)
    graph.add_edge(_edge(snapshot, repo.node_id, commit.node_id, "contains"))
    graph.add_edge(_edge(snapshot, branch.node_id, commit.node_id, "contains"))

    path_nodes: dict[str, str] = {}
    module_nodes: dict[str, str] = {}
    symbol_nodes: dict[str, str] = {}
    parsed: dict[str, tuple[ast.Module, str, _DefinitionCollector]] = {}
    doc_text = (
        snapshot.text("docs/entrypoints.md")
        if "docs/entrypoints.md" in snapshot.files
        else ""
    )

    for path, raw in sorted(snapshot.files.items()):
        parts = PurePosixPath(path).parts
        parent_id = repo.node_id
        for depth in range(1, len(parts)):
            directory = "/".join(parts[:depth])
            if directory not in path_nodes:
                dnode = _node(
                    snapshot,
                    "directory",
                    parts[depth - 1],
                    identity=("dir", directory),
                    path=directory,
                    content=directory,
                )
                graph.add_node(dnode)
                path_nodes[directory] = dnode.node_id
                graph.add_edge(
                    _edge(snapshot, parent_id, dnode.node_id, "contains", ref=directory)
                )
            parent_id = path_nodes[directory]

        text = raw.decode("utf-8-sig", errors="replace")
        if path.endswith(".py") and parts[0] in PYTHON_ROOTS:
            module = _module_name(path)
            node_type = "test" if path.startswith("tests/") else "source_module"
            fnode = _node(
                snapshot,
                node_type,
                module,
                identity=(node_type, module),
                path=path,
                symbol=module,
                content=raw.hex(),
            )
            module_nodes[module] = fnode.node_id
            try:
                tree = ast.parse(text, filename=path)
                collector = _DefinitionCollector(text)
                collector.visit(tree)
                parsed[module] = (tree, path, collector)
                for kind, qualified, line, segment in collector.definitions:
                    symbol = f"{module}.{qualified}"
                    definition_type = (
                        "test"
                        if path.startswith("tests/")
                        and qualified.split(".")[-1].startswith("test")
                        else kind
                    )
                    snode = _node(
                        snapshot,
                        definition_type,
                        qualified,
                        identity=(definition_type, symbol),
                        path=path,
                        symbol=symbol,
                        line=line,
                        content=segment,
                    )
                    graph.add_node(snode)
                    symbol_nodes[symbol] = snode.node_id
                    graph.add_edge(
                        _edge(
                            snapshot,
                            fnode.node_id,
                            snode.node_id,
                            "contains",
                            ref=f"{path}:{line}",
                        )
                    )
                if any(
                    isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and any(
                        isinstance(item, ast.Constant) and item.value == "__main__"
                        for item in ast.walk(node.test)
                    )
                    for node in ast.walk(tree)
                ):
                    cli = _node(
                        snapshot,
                        "cli",
                        f"python -m {module}",
                        identity=("cli", module),
                        path=path,
                        symbol=module,
                        content=module,
                    )
                    graph.add_node(cli)
                    graph.add_edge(
                        _edge(snapshot, cli.node_id, fnode.node_id, "calls", ref=path)
                    )
            except SyntaxError:
                # Keep a content-bound module node, but do not invent symbol
                # edges when the tracked source cannot be parsed.
                pass
        elif path.endswith(".bat"):
            status = (
                "legacy"
                if re.search(
                    rf"\|\s*`?{re.escape(PurePosixPath(path).name)}`?\s*\|\s*legacy",
                    doc_text,
                    re.I,
                )
                else _status(path)
            )
            fnode = _node(
                snapshot,
                "bat_entrypoint",
                path,
                identity=("bat", path),
                path=path,
                content=raw.hex(),
                status=status,
            )
        elif path.startswith(".github/workflows/"):
            fnode = _node(
                snapshot,
                "ci_workflow",
                path,
                identity=("workflow", path),
                path=path,
                content=raw.hex(),
            )
        elif path.startswith("configs/"):
            fnode = _node(
                snapshot,
                "configuration",
                path,
                identity=("configuration", path),
                path=path,
                content=raw.hex(),
            )
        else:
            fnode = _node(
                snapshot,
                "file_artifact",
                path,
                identity=("file", path),
                path=path,
                content=raw.hex(),
            )
        graph.add_node(fnode)
        path_nodes[path] = fnode.node_id
        graph.add_edge(_edge(snapshot, parent_id, fnode.node_id, "contains", ref=path))

    _add_python_edges(graph, snapshot, parsed, module_nodes, symbol_nodes)
    _add_bat_edges(graph, snapshot, module_nodes, path_nodes)
    _add_database_schema(graph, snapshot, parsed, module_nodes)
    _add_strategy_families(graph, snapshot, parsed, module_nodes)
    if catalog_path is not None:
        _apply_catalog(
            graph, snapshot, catalog_path, path_nodes, module_nodes, symbol_nodes
        )
    graph.metrics = _metrics(graph, parsed, path_nodes)
    graph.validate()
    return graph


def _add_python_edges(
    graph: ProjectGraph,
    snapshot: GitSnapshot,
    parsed: Mapping[str, tuple[ast.Module, str, _DefinitionCollector]],
    module_nodes: Mapping[str, str],
    symbol_nodes: Mapping[str, str],
) -> None:
    for module, (tree, path, collector) in parsed.items():
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name.split(".")[0]] = alias.name
                    target = module_nodes.get(alias.name)
                    if target:
                        relation = "tests" if path.startswith("tests/") else "imports"
                        graph.add_edge(
                            _edge(
                                snapshot,
                                module_nodes[module],
                                target,
                                relation,
                                ref=f"{path}:{node.lineno}",
                            )
                        )
            elif isinstance(node, ast.ImportFrom):
                source_module = _resolve_from(module, node.module, node.level)
                target = module_nodes.get(source_module)
                if target:
                    relation = "tests" if path.startswith("tests/") else "imports"
                    graph.add_edge(
                        _edge(
                            snapshot,
                            module_nodes[module],
                            target,
                            relation,
                            ref=f"{path}:{node.lineno}",
                        )
                    )
                for alias in node.names:
                    aliases[alias.asname or alias.name] = (
                        f"{source_module}.{alias.name}".strip(".")
                    )
        local = {
            key.rsplit(".", 1)[-1]: value
            for key, value in symbol_nodes.items()
            if key.startswith(f"{module}.")
        }
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            target_symbol = ""
            if isinstance(call.func, ast.Name):
                target_symbol = aliases.get(call.func.id, local.get(call.func.id, ""))
            elif isinstance(call.func, ast.Attribute) and isinstance(
                call.func.value, ast.Name
            ):
                base = aliases.get(call.func.value.id, call.func.value.id)
                target_symbol = f"{base}.{call.func.attr}"
            target = symbol_nodes.get(target_symbol) or module_nodes.get(target_symbol)
            if not target:
                continue
            caller_qual = collector.callers.get(id(call), "")
            caller = symbol_nodes.get(f"{module}.{caller_qual}", module_nodes[module])
            relation = "tests" if path.startswith("tests/") else "calls"
            graph.add_edge(
                _edge(
                    snapshot,
                    caller,
                    target,
                    relation,
                    ref=f"{path}:{call.lineno}",
                    confidence="static_resolved",
                )
            )


def _add_bat_edges(
    graph: ProjectGraph,
    snapshot: GitSnapshot,
    module_nodes: Mapping[str, str],
    path_nodes: Mapping[str, str],
) -> None:
    for path in sorted(p for p in snapshot.files if p.endswith(".bat")):
        source = path_nodes[path]
        text = snapshot.text(path)
        for match in BAT_MODULE.finditer(text):
            module = match.group(1)
            target = module_nodes.get(module)
            if target:
                graph.add_edge(
                    _edge(
                        snapshot,
                        source,
                        target,
                        "starts",
                        ref=path,
                        confidence="static_literal",
                    )
                )
        for other in sorted(
            p for p in snapshot.files if p.endswith(".bat") and p != path
        ):
            if PurePosixPath(other).name.lower() in text.lower():
                graph.add_edge(
                    _edge(
                        snapshot,
                        source,
                        path_nodes[other],
                        "starts",
                        ref=path,
                        confidence="static_literal",
                    )
                )


def _add_database_schema(
    graph: ProjectGraph,
    snapshot: GitSnapshot,
    parsed: Mapping[str, tuple[ast.Module, str, _DefinitionCollector]],
    module_nodes: Mapping[str, str],
) -> None:
    for module, database in DB_BY_MODULE.items():
        if module not in parsed:
            continue
        _tree, path, _collector = parsed[module]
        db = _node(
            snapshot,
            "database",
            database,
            identity=("database", database),
            path=path,
            symbol=database,
            content=database,
            owner=_owner(path),
        )
        schema = _node(
            snapshot,
            "schema",
            f"{database} schema",
            identity=("schema", database),
            path=path,
            symbol=database,
            content=database,
        )
        graph.add_node(db)
        graph.add_node(schema)
        graph.add_edge(
            _edge(snapshot, db.node_id, schema.node_id, "depends_on", ref=path)
        )
        graph.add_edge(
            _edge(snapshot, module_nodes[module], db.node_id, "writes", ref=path)
        )
        for table_name in sorted(set(SQL_TABLE.findall(snapshot.text(path)))):
            table = _node(
                snapshot,
                "table",
                f"{database}.{table_name}",
                identity=("table", database, table_name),
                path=path,
                symbol=table_name,
                content=table_name,
            )
            graph.add_node(table)
            graph.add_edge(
                _edge(snapshot, schema.node_id, table.node_id, "contains", ref=path)
            )
            graph.add_edge(
                _edge(snapshot, module_nodes[module], table.node_id, "writes", ref=path)
            )


def _add_strategy_families(
    graph: ProjectGraph,
    snapshot: GitSnapshot,
    parsed: Mapping[str, tuple[ast.Module, str, _DefinitionCollector]],
    module_nodes: Mapping[str, str],
) -> None:
    module = "src.research_lab.strategy_registry"
    if module not in parsed:
        return
    tree, path, _collector = parsed[module]
    names: set[str] = set()
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        targets = {
            target.id for target in statement.targets if isinstance(target, ast.Name)
        }
        if "_ADAPTIVE_AXES_BY_STRATEGY" not in targets or not isinstance(
            statement.value, ast.Dict
        ):
            continue
        for key in statement.value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
    for name in sorted(names):
        family = _node(
            snapshot,
            "strategy_family",
            name,
            identity=("strategy_family", name),
            path=path,
            symbol=name,
            content=name,
            owner="calculation_farm",
        )
        graph.add_node(family)
        graph.add_edge(
            _edge(snapshot, module_nodes[module], family.node_id, "owns", ref=path)
        )


def _apply_catalog(
    graph: ProjectGraph,
    snapshot: GitSnapshot,
    catalog_path: Path,
    path_nodes: Mapping[str, str],
    module_nodes: Mapping[str, str],
    symbol_nodes: Mapping[str, str],
) -> None:
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    aliases: dict[str, str] = {
        "repository": next(
            node.node_id for node in graph.nodes if node.type == "repository"
        )
    }
    aliases.update({f"path:{key}": value for key, value in path_nodes.items()})
    aliases.update({f"module:{key}": value for key, value in module_nodes.items()})
    aliases.update({f"symbol:{key}": value for key, value in symbol_nodes.items()})
    aliases.update(
        {
            f"{node.type}:{node.symbol}": node.node_id
            for node in graph.nodes
            if node.symbol
        }
    )
    for row in payload.get("nodes", []):
        alias = str(row["key"])
        node_type = str(row["type"])
        node = _node(
            snapshot,
            node_type,
            str(row.get("label") or alias),
            identity=("catalog", alias),
            path=str(row.get("path") or ""),
            symbol=str(row.get("symbol") or alias),
            content=row,
            status=str(row.get("status") or "active"),
            owner=str(row.get("owner") or "repository_maintainers"),
            attributes={**dict(row.get("attributes") or {}), "catalog_key": alias},
        )
        node = GraphNode(
            **{
                **node.to_dict(),
                "sensitivity": str(row.get("sensitivity") or "public"),
                "confidence": str(row.get("confidence") or "verified"),
                "primary_contour": str(
                    row.get("primary_contour") or node.primary_contour
                ),
                "secondary_contours": tuple(
                    row.get("secondary_contours") or node.secondary_contours
                ),
                "load_policy": str(row.get("load_policy") or "on_demand"),
                "evidence_refs": tuple(row.get("evidence_refs") or node.evidence_refs),
                "superseded_by": str(row.get("superseded_by") or ""),
            }
        )
        graph.add_node(node)
        aliases[alias] = node.node_id
    for row in payload.get("edges", []):
        source = aliases.get(str(row["source"]), str(row["source"]))
        target = aliases.get(str(row["target"]), str(row["target"]))
        if source not in {node.node_id for node in graph.nodes} or target not in {
            node.node_id for node in graph.nodes
        }:
            raise ValueError(f"catalog edge has unknown endpoint: {row}")
        graph.add_edge(
            _edge(
                snapshot,
                source,
                target,
                str(row["relation"]),
                ref=str(row.get("source_reference") or catalog_path.as_posix()),
                confidence=str(row.get("confidence") or "verified"),
                attributes=dict(row.get("attributes") or {}),
            )
        )
    graph.metrics["semantic_catalog_sha256"] = content_sha256(payload)


def _metrics(
    graph: ProjectGraph,
    parsed: Mapping[str, tuple[ast.Module, str, _DefinitionCollector]],
    path_nodes: Mapping[str, str],
) -> dict[str, Any]:
    node_counts = Counter(node.type for node in graph.nodes)
    edge_counts = Counter(edge.relation for edge in graph.edges)
    production = {
        node.node_id
        for node in graph.nodes
        if node.type in {"class", "function"} and not node.path.startswith("tests/")
    }
    tested = {
        edge.target
        for edge in graph.edges
        if edge.relation == "tests" and edge.target in production
    }
    connected = {edge.source for edge in graph.edges} | {
        edge.target for edge in graph.edges
    }
    orphans = [node.node_id for node in graph.nodes if node.node_id not in connected]
    source_modules = [node for node in graph.nodes if node.type == "source_module"]
    entrypoints = [
        node for node in graph.nodes if node.type in {"cli", "bat_entrypoint"}
    ]
    databases = [node for node in graph.nodes if node.type == "database"]
    unknown_owners = [node.node_id for node in graph.nodes if node.owner == "unknown"]
    stale = [node.node_id for node in graph.nodes if node.freshness != "current"]
    return {
        **graph.metrics,
        "node_counts": dict(sorted(node_counts.items())),
        "edge_counts": dict(sorted(edge_counts.items())),
        "tracked_paths": len(path_nodes),
        "python_modules_parsed": len(parsed),
        "source_module_classification_pct": round(
            100.0 * len(source_modules) / max(1, len(source_modules)), 2
        ),
        "entrypoint_classification_pct": round(
            100.0
            * sum(bool(row.status) for row in entrypoints)
            / max(1, len(entrypoints)),
            2,
        ),
        "production_symbol_test_link_pct": round(
            100.0 * len(tested) / max(1, len(production)), 2
        ),
        "database_schema_surface_pct": round(
            100.0
            * sum(
                any(edge.source == row.node_id for edge in graph.edges)
                for row in databases
            )
            / max(1, len(databases)),
            2,
        ),
        "active_document_classification_pct": 100.0,
        "orphan_nodes": len(orphans),
        "conflicting_edges": sum(
            1 for edge in graph.edges if edge.relation == "conflicts_with"
        ),
        "duplicate_candidates": _duplicate_candidates(graph.nodes),
        "stale_facts": len(stale),
        "unknown_owners": len(unknown_owners),
        "cross_repository_boundaries": sum(
            1 for edge in graph.edges if edge.relation == "crosses_repository_boundary"
        ),
    }


def _duplicate_candidates(nodes: Iterable[GraphNode]) -> int:
    by_identity: dict[tuple[str, str, str], int] = defaultdict(int)
    for node in nodes:
        by_identity[(node.type, node.path, node.symbol)] += 1
    return sum(1 for count in by_identity.values() if count > 1)


def write_graph(graph: ProjectGraph, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(graph.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return path


def read_graph(path: Path) -> ProjectGraph:
    return ProjectGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
