"""Deterministic static and semantic graph builder for a Git revision.

Only tracked Git blobs and the reviewed public semantic catalog are read. The
builder never imports product modules and never opens runtime state.
"""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import posixpath
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
DOCUMENT_LINK = re.compile(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)")
DOCUMENT_CODE_REF = re.compile(r"`([^`]+\.md)`")
STRUCTURAL_RELATIONS = frozenset({"contains", "belongs_to_contour"})
STRUCTURAL_NODE_TYPES = frozenset(
    {"repository", "worktree", "branch", "commit", "directory", "dialogue_contour"}
)
SEMANTIC_SURFACE_TYPES = frozenset(
    {
        "source_module",
        "cli",
        "bat_entrypoint",
        "runtime_contour",
        "process",
        "process_owner",
        "database",
        "data_source",
        "validation_method",
        "model_provider",
        "prompt_tool_contract",
        "telegram_surface",
        "policy",
        "authority_gate",
        "decision",
    }
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


def _status_with_method(path: str) -> tuple[str, str]:
    if path.startswith(("scripts/archive/", "docs/legacy-evidence/")):
        return "archive", "rule_derived"
    if (
        path.startswith(("scripts/ws/", "src/data/", "src/exchange/"))
        or path == "main.py"
    ):
        return "reference", "rule_derived"
    if path == "docs/deferred-adaptive-paper-architecture.md":
        return "superseded", "rule_derived"
    return "active", "fallback_default"


def _status(path: str) -> str:
    return _status_with_method(path)[0]


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


def _owner_with_method(path: str) -> tuple[str, str]:
    if path.startswith("src/scout/"):
        return "scanner_intake", "rule_derived_owner"
    if path.startswith(("src/research_lab/", "scripts/strategy_lab/")):
        return "calculation_farm", "rule_derived_owner"
    if path.startswith("vendor/honest-backtest/"):
        return "honest_backtest_upstream", "rule_derived_owner"
    if "telegram" in path:
        return "telegram_delivery", "rule_derived_owner"
    if path.startswith("tests/"):
        return "verification_suite", "rule_derived_owner"
    if path.startswith(("docs/", ".github/")):
        return "repository_maintainers", "rule_derived_owner"
    return "repository_maintainers", "fallback_owner"


def _owner(path: str) -> str:
    return _owner_with_method(path)[0]


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
    status_method: str | None = None,
    owner_method: str | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> GraphNode:
    primary, secondary = _contours(path, symbol)
    ref = f"{path}:{line}" if path and line else path
    prefix = node_type.replace("_", "-")
    derived_status, derived_status_method = _status_with_method(path)
    derived_owner, derived_owner_method = _owner_with_method(path)
    metadata = {
        "status_classification": status_method
        or ("verified" if status is not None else derived_status_method),
        "owner_classification": owner_method
        or ("verified_owner" if owner is not None else derived_owner_method),
        **dict(attributes or {}),
    }
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
        status=status or derived_status,
        owner=owner or derived_owner,
        primary_contour=primary,
        secondary_contours=secondary,
        evidence_refs=(ref,) if ref else (),
        attributes=metadata,
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
            catalog_match = re.search(
                rf"\|\s*`?{re.escape(PurePosixPath(path).name)}`?\s*\|\s*([^|]+)",
                doc_text,
                re.I,
            )
            status = (
                "legacy"
                if catalog_match and "legacy" in catalog_match.group(1).lower()
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
                status_method="verified" if catalog_match else _status_with_method(path)[1],
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
    graph.metrics = _metrics(graph, snapshot, parsed, path_nodes)
    graph.validate()
    return graph


def _add_python_edges(
    graph: ProjectGraph,
    snapshot: GitSnapshot,
    parsed: Mapping[str, tuple[ast.Module, str, _DefinitionCollector]],
    module_nodes: Mapping[str, str],
    symbol_nodes: Mapping[str, str],
) -> None:
    production_calls = 0
    resolved_production_calls = 0
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
            if not path.startswith("tests/"):
                production_calls += 1
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
            if not path.startswith("tests/"):
                resolved_production_calls += 1
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
    graph.metrics["static_call_resolution"] = _coverage_metric(
        resolved_production_calls,
        production_calls,
        "AST-resolved production calls; dynamic attribute, registry and dependency-injected dispatch remains unresolved",
        unresolved=production_calls - resolved_production_calls,
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
    pre_catalog_ids = {node.node_id for node in graph.nodes}
    catalog_verified_ids: set[str] = set()
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
        explicit_path = str(row.get("path") or "")
        catalog_verified_ids.update(
            node.node_id for node in graph.nodes if explicit_path and node.path == explicit_path
        )
        explicit_owner = row.get("owner")
        node = _node(
            snapshot,
            node_type,
            str(row.get("label") or alias),
            identity=("catalog", alias),
            path=explicit_path,
            symbol=str(row.get("symbol") or alias),
            content=row,
            status=str(row.get("status") or "active"),
            owner=str(explicit_owner) if explicit_owner else None,
            status_method="verified",
            owner_method="verified_owner" if explicit_owner else "fallback_owner",
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
                ref=str(
                    row.get("source_reference")
                    or "configs/project_brain/architecture.json"
                ),
                confidence=str(row.get("confidence") or "verified"),
                attributes=dict(row.get("attributes") or {}),
            )
        )
        if source in pre_catalog_ids:
            catalog_verified_ids.add(source)
        if target in pre_catalog_ids:
            catalog_verified_ids.add(target)
    for index, node in enumerate(graph.nodes):
        if node.node_id not in catalog_verified_ids:
            continue
        graph.nodes[index] = replace(
            node,
            attributes={**dict(node.attributes), "semantic_catalog_verified": True},
        )
    graph.metrics["semantic_catalog_sha256"] = content_sha256(payload)
    graph.metrics["semantic_catalog_verified_node_ids"] = sorted(catalog_verified_ids)
    graph.metrics["active_scope_catalog"] = _resolve_active_scope(
        payload.get("active_scope") or {}, aliases
    )


def _resolve_active_scope(
    scope: Mapping[str, Any], aliases: Mapping[str, str]
) -> dict[str, Any]:
    """Resolve reviewed active-scope aliases without inventing architecture."""

    resolved: dict[str, Any] = {"method": str(scope.get("method") or "")}
    for category in (
        "supported_entrypoints",
        "canonical_rcc_contours",
        "active_databases",
        "active_documents",
    ):
        rows: list[dict[str, Any]] = []
        for raw in scope.get(category, []):
            row = {"node": raw} if isinstance(raw, str) else dict(raw)
            alias = str(row.get("node") or "")
            node_id = aliases.get(alias)
            if not node_id:
                raise ValueError(f"active scope has unknown node alias: {alias}")
            required_links: list[dict[str, Any]] = []
            for link in row.get("required_links", []):
                if not isinstance(link, Mapping):
                    raise ValueError("active database link must be an object")
                link_row = dict(link)
                source_alias = str(link_row.get("source") or "")
                target_alias = str(link_row.get("target") or "")
                source_id = aliases.get(source_alias)
                target_id = aliases.get(target_alias)
                if not source_id or not target_id:
                    raise ValueError(
                        "active database link has unknown endpoint: "
                        f"{source_alias} -> {target_alias}"
                    )
                required_links.append(
                    {
                        **link_row,
                        "source_id": source_id,
                        "target_id": target_id,
                    }
                )
            if required_links:
                row["required_links"] = required_links
            rows.append({**row, "node_id": node_id})
        resolved[category] = rows
    dispositions: list[dict[str, Any]] = []
    for raw in scope.get("meaningful_orphan_dispositions", []):
        row = dict(raw)
        alias = str(row.get("node") or "")
        node_id = aliases.get(alias)
        if not node_id:
            raise ValueError(f"orphan disposition has unknown node alias: {alias}")
        disposition = str(row.get("disposition") or "").strip()
        evidence = str(row.get("evidence") or "").strip()
        if not disposition or not evidence:
            raise ValueError("orphan disposition requires disposition and evidence")
        dispositions.append({**row, "node_id": node_id})
    resolved["meaningful_orphan_dispositions"] = dispositions
    duplicate_dispositions: list[dict[str, str]] = []
    for raw in scope.get("semantic_duplicate_dispositions", []):
        row = {key: str(value).strip() for key, value in dict(raw).items()}
        if not all(row.get(key) for key in ("kind", "signature", "disposition", "evidence")):
            raise ValueError(
                "semantic duplicate disposition requires kind, signature, disposition, and evidence"
            )
        duplicate_dispositions.append(row)
    resolved["semantic_duplicate_dispositions"] = duplicate_dispositions
    return resolved


def _metrics(
    graph: ProjectGraph,
    snapshot: GitSnapshot,
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
    source_modules = [node for node in graph.nodes if node.type == "source_module"]
    entrypoints = [
        node for node in graph.nodes if node.type in {"cli", "bat_entrypoint"}
    ]
    databases = [node for node in graph.nodes if node.type == "database"]
    stale = [node.node_id for node in graph.nodes if node.freshness != "current"]
    catalog_verified_ids = set(
        graph.metrics.pop("semantic_catalog_verified_node_ids", [])
    )
    module_classification: Counter[str] = Counter()
    for node in source_modules:
        if node.node_id in catalog_verified_ids or node.attributes.get(
            "semantic_catalog_verified"
        ):
            module_classification["verified"] += 1
        elif node.attributes.get("status_classification") == "rule_derived":
            module_classification["rule_derived"] += 1
        elif node.status:
            module_classification["fallback"] += 1
        else:
            module_classification["unknown"] += 1
    classified_modules = (
        module_classification["verified"] + module_classification["rule_derived"]
    )
    discovered_python = [
        path
        for path in snapshot.files
        if path.endswith(".py") and PurePosixPath(path).parts[0] in PYTHON_ROOTS
    ]
    catalog_paths, documents, active_documents = _document_catalog_inventory(snapshot)
    catalogued_active = active_documents & catalog_paths
    owner_classification: Counter[str] = Counter()
    for node in graph.nodes:
        method = str(node.attributes.get("owner_classification") or "unknown_owner")
        if node.owner == "unknown":
            method = "unknown_owner"
        owner_classification[method] += 1
    semantic_nodes = [node for node in graph.nodes if node.type in SEMANTIC_SURFACE_TYPES]
    semantic_catalogued = [
        node
        for node in semantic_nodes
        if node.node_id in catalog_verified_ids
        or node.attributes.get("semantic_catalog_verified")
        or node.attributes.get("catalog_key")
    ]
    semantic_duplicates = _semantic_duplicate_candidates(graph)
    result = {
        **graph.metrics,
        "node_counts": dict(sorted(node_counts.items())),
        "edge_counts": dict(sorted(edge_counts.items())),
        "tracked_files": len(snapshot.files),
        "tracked_path_nodes": len(path_nodes),
        "syntactic_python_coverage": _coverage_metric(
            len(parsed),
            len(discovered_python),
            "AST parse success over every tracked Python file under src/scripts/tests/vendor",
            parse_failures=len(discovered_python) - len(parsed),
        ),
        "source_module_classification": _coverage_metric(
            classified_modules,
            len(source_modules),
            "semantic-catalog verified plus bounded path-rule classification; fallback active labels are excluded from numerator",
            breakdown=dict(sorted(module_classification.items())),
        ),
        "entrypoint_classification": _coverage_metric(
            sum(
                node.attributes.get("status_classification") != "fallback_default"
                for node in entrypoints
            ),
            len(entrypoints),
            "entrypoints explicitly catalogued or matched by bounded status rules; fallback active labels excluded",
        ),
        "production_symbol_test_link_coverage": _coverage_metric(
            len(tested),
            len(production),
            "static tests/calls links from tracked AST; not line or branch coverage",
        ),
        "database_schema_surface_coverage": _coverage_metric(
            sum(
                any(edge.source == row.node_id for edge in graph.edges)
                for row in databases
            ),
            len(databases),
            "declared database nodes with at least one statically or catalog-linked schema edge",
        ),
        "document_catalog_coverage": _coverage_metric(
            len(documents & catalog_paths),
            len(documents),
            "tracked Markdown paths explicitly named by docs/document-catalog.md",
        ),
        "active_document_catalog_coverage": _coverage_metric(
            len(catalogued_active),
            len(active_documents),
            "tracked Markdown declaring Status ACTIVE or CURRENT and explicitly named by docs/document-catalog.md",
        ),
        "semantic_catalog_coverage": _coverage_metric(
            len(semantic_catalogued),
            len(semantic_nodes),
            "bounded semantic surface nodes explicitly created, path-matched, or connected by architecture.json",
        ),
        "ownership_classification": {
            "denominator": len(graph.nodes),
            "verified_owner": owner_classification["verified_owner"],
            "rule_derived_owner": owner_classification["rule_derived_owner"],
            "fallback_owner": owner_classification["fallback_owner"],
            "unknown_owner": owner_classification["unknown_owner"],
            "method": "explicit catalog owner, bounded path rule, fallback label, or unknown; fallback is never counted as verified",
        },
        "verified_ownership_coverage": _coverage_metric(
            owner_classification["verified_owner"],
            len(graph.nodes),
            "nodes with an explicit reviewed catalog owner only",
        ),
        "conflicting_edges": sum(
            1 for edge in graph.edges if edge.relation == "conflicts_with"
        ),
        "exact_duplicate_candidates": _exact_duplicate_candidates(graph.nodes),
        "semantic_duplicate_candidates": semantic_duplicates,
        "stale_facts": len(stale),
        "cross_repository_boundaries": sum(
            1 for edge in graph.edges if edge.relation == "crosses_repository_boundary"
        ),
    }
    graph.metrics = result
    refresh_ownership_metrics(graph)
    refresh_connectivity_metrics(graph)
    _active_scope_metrics(graph, snapshot, semantic_duplicates)
    return graph.metrics


def _coverage_metric(
    numerator: int, denominator: int, method: str, **extra: Any
) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "pct": round(100.0 * numerator / denominator, 2) if denominator else 0.0,
        "method": method,
        **extra,
    }


def _document_catalog_inventory(
    snapshot: GitSnapshot,
) -> tuple[set[str], set[str], set[str]]:
    documents = {path for path in snapshot.files if path.lower().endswith(".md")}
    active_documents = {
        path
        for path in documents
        if re.search(
            r"(?im)^Status:\s*\*\*(?:ACTIVE|CURRENT)\*\*",
            snapshot.text(path),
        )
    }
    catalog_text = (
        snapshot.text("docs/document-catalog.md")
        if "docs/document-catalog.md" in snapshot.files
        else ""
    )
    raw_refs = set(DOCUMENT_LINK.findall(catalog_text)) | set(
        DOCUMENT_CODE_REF.findall(catalog_text)
    )
    catalog_paths: set[str] = {"docs/document-catalog.md"}
    for raw in raw_refs:
        candidate = posixpath.normpath(posixpath.join("docs", raw.strip()))
        if candidate in documents:
            catalog_paths.add(candidate)
            continue
        root_candidate = posixpath.normpath(raw.strip())
        if root_candidate in documents:
            catalog_paths.add(root_candidate)
            continue
        basename = PurePosixPath(raw.strip()).name
        matches = [path for path in documents if PurePosixPath(path).name == basename]
        if len(matches) == 1:
            catalog_paths.add(matches[0])
    return catalog_paths, documents, active_documents


def _exact_duplicate_candidates(nodes: Iterable[GraphNode]) -> dict[str, Any]:
    by_identity: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for node in nodes:
        by_identity[(node.type, node.path, node.symbol)].append(node.node_id)
    groups = [rows for rows in by_identity.values() if len(rows) > 1]
    return {
        "group_count": len(groups),
        "candidate_node_count": len({node_id for group in groups for node_id in group}),
        "method": "identical type/path/symbol identity",
    }


def _semantic_duplicate_candidates(graph: ProjectGraph) -> dict[str, Any]:
    node_by_id = {node.node_id: node for node in graph.nodes}
    groups: list[tuple[str, str, list[str]]] = []
    symbol_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    ignored_symbols = {"main", "run", "load", "save", "build", "parse", "close"}
    for node in graph.nodes:
        if node.type not in {"class", "function"} or node.path.startswith("tests/"):
            continue
        leaf = re.sub(r"[^a-z0-9]+", "_", node.symbol.rsplit(".", 1)[-1].lower()).strip("_")
        if len(leaf) < 6 or leaf in ignored_symbols:
            continue
        symbol_groups[(node.type, leaf)].append(node.node_id)
    for signature, rows in symbol_groups.items():
        if len(rows) > 1:
            groups.append(("similar_symbol", ":".join(signature), rows))

    launcher_targets: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        source = node_by_id.get(edge.source)
        if edge.relation == "starts" and source and source.type in {"bat_entrypoint", "cli"}:
            launcher_targets[edge.target].append(edge.source)
    for target, rows in launcher_targets.items():
        unique = sorted(set(rows))
        if len(unique) > 1:
            groups.append(("competing_launcher", target, unique))

    truth_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for node in graph.nodes:
        if node.type not in {
            "database",
            "schema",
            "policy",
            "decision",
            "configuration",
            "evidence_artifact",
        }:
            continue
        normalized = re.sub(r"[^a-z0-9]+", " ", node.label.lower()).strip()
        if normalized:
            truth_groups[(node.type, normalized)].append(node.node_id)
    for signature, rows in truth_groups.items():
        if len(rows) > 1:
            groups.append(("repeated_truth_surface", ":".join(signature), rows))

    outgoing: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in graph.edges:
        target_node = node_by_id.get(edge.target)
        if edge.relation in {
            "starts",
            "stops",
            "writes",
            "produces",
            "notifies",
            "routes_to",
        }:
            outgoing[edge.source].append(
                (edge.relation, target_node.type if target_node else "unknown")
            )
    role_groups: dict[
        tuple[str, str, str, tuple[tuple[str, str], ...]], list[str]
    ] = defaultdict(list)
    for node in graph.nodes:
        effects = tuple(sorted(set(outgoing.get(node.node_id, []))))
        if node.type not in {"bat_entrypoint", "cli", "runtime_contour"} or not effects:
            continue
        role_signature = (node.owner, node.primary_contour, node.status, effects)
        role_groups[role_signature].append(node.node_id)
    for role_signature, rows in role_groups.items():
        if len(rows) > 1:
            groups.append(
                (
                    "same_role_effect_owner",
                    content_sha256(role_signature)[:16],
                    rows,
                )
            )

    candidate_ids = {node_id for _, _, rows in groups for node_id in rows}
    breakdown = Counter(kind for kind, _, _ in groups)
    samples = [
        {
            "kind": kind,
            "signature": signature,
            "node_ids": sorted(set(rows))[:8],
        }
        for kind, signature, rows in sorted(groups)[:30]
    ]
    group_details = [
        {
            "kind": kind,
            "signature": signature,
            "node_ids": sorted(set(rows)),
        }
        for kind, signature, rows in sorted(groups)
    ]
    return {
        "group_count": len(groups),
        "candidate_node_count": len(candidate_ids),
        "breakdown": dict(sorted(breakdown.items())),
        "method": "bounded same-symbol, competing-launcher, repeated-truth and same role/effect/owner candidate scan; candidates require human review",
        "samples": samples,
        "groups": group_details,
    }


def _active_scope_metrics(
    graph: ProjectGraph,
    snapshot: GitSnapshot,
    semantic_duplicates: Mapping[str, Any],
) -> None:
    """Publish a strict gate for supported paths without claiming archive completeness."""

    scope = graph.metrics.pop("active_scope_catalog", {})
    categories = (
        "supported_entrypoints",
        "canonical_rcc_contours",
        "active_databases",
        "active_documents",
    )
    scoped = {
        str(row["node_id"])
        for category in categories
        for row in scope.get(category, [])
    }
    node_ids = {node.node_id for node in graph.nodes}
    edges = {(edge.source, edge.relation, edge.target) for edge in graph.edges}

    entrypoints = list(scope.get("supported_entrypoints", []))
    entrypoint_ok = [row for row in entrypoints if row["node_id"] in node_ids]
    contours = list(scope.get("canonical_rcc_contours", []))
    contour_ok = [
        row
        for row in contours
        if row["node_id"] in node_ids
        and any(
            relation == "belongs_to_contour" and target == row["node_id"]
            for _, relation, target in edges
        )
    ]
    databases = list(scope.get("active_databases", []))
    database_ok: list[dict[str, Any]] = []
    for row in databases:
        required = list(row.get("required_links") or [])
        if required and all(
            (
                str(link["source_id"]),
                str(link["relation"]),
                str(link["target_id"]),
            )
            in edges
            for link in required
        ):
            database_ok.append(row)
    documents = list(scope.get("active_documents", []))
    active_paths = _document_catalog_inventory(snapshot)[2]
    documented = [
        row
        for row in documents
        if str(row.get("path") or "") in active_paths
        and row["node_id"] in node_ids
    ]

    meaningful_connected = {
        endpoint
        for edge in graph.edges
        if edge.relation not in STRUCTURAL_RELATIONS
        for endpoint in (edge.source, edge.target)
    }
    disposition_ids = {
        str(row["node_id"])
        for row in scope.get("meaningful_orphan_dispositions", [])
    }
    resolved_orphans = scoped & (meaningful_connected | disposition_ids)
    duplicate_groups = [
        row
        for row in semantic_duplicates.get("groups", [])
        if scoped.intersection(row.get("node_ids", []))
    ]
    duplicate_dispositions = {
        (row["kind"], row["signature"]): row
        for row in scope.get("semantic_duplicate_dispositions", [])
    }
    resolved_duplicate_groups = [
        row
        for row in duplicate_groups
        if (row["kind"], row["signature"]) in duplicate_dispositions
    ]

    graph.metrics["active_scope"] = {
        "claim": "complete only for explicitly supported active paths; archive and dynamic dispatch remain backlog",
        "method": scope.get("method")
        or "reviewed active catalog bound to exact graph nodes and edges",
        "supported_entrypoint_coverage": _coverage_metric(
            len(entrypoint_ok),
            len(entrypoints),
            "reviewed supported entrypoints resolved to exact tracked graph nodes",
        ),
        "canonical_rcc_contour_coverage": _coverage_metric(
            len(contour_ok),
            len(contours),
            "reviewed RCC contours with an explicit process-to-contour relationship",
        ),
        "active_db_producer_consumer_coverage": _coverage_metric(
            len(database_ok),
            len(databases),
            "reviewed active databases with every declared producer/consumer edge present",
        ),
        "active_document_coverage": _coverage_metric(
            len(documented),
            len(documents),
            "reviewed documents that exist, declare CURRENT or ACTIVE, and resolve to graph nodes",
        ),
        "meaningful_orphan_disposition_coverage": _coverage_metric(
            len(resolved_orphans),
            len(scoped),
            "active-scope nodes have a non-structural edge or an explicit evidence-backed disposition",
            unresolved=sorted(scoped - resolved_orphans),
        ),
        "semantic_duplicate_disposition": {
            "numerator": len(resolved_duplicate_groups),
            "denominator": len(duplicate_groups),
            "pct": round(
                100.0 * len(resolved_duplicate_groups) / len(duplicate_groups), 2
            )
            if duplicate_groups
            else 100.0,
            "method": "bounded semantic duplicate groups intersecting the supported active scope with exact reviewed kind/signature dispositions; this is not a whole-repository uniqueness claim",
            "candidate_groups": duplicate_groups,
            "unresolved": [
                row
                for row in duplicate_groups
                if row not in resolved_duplicate_groups
            ],
        },
    }


def refresh_connectivity_metrics(graph: ProjectGraph) -> None:
    technical_connected = {edge.source for edge in graph.edges} | {
        edge.target for edge in graph.edges
    }
    meaningful_connected = {
        endpoint
        for edge in graph.edges
        if edge.relation not in STRUCTURAL_RELATIONS
        for endpoint in (edge.source, edge.target)
    }
    meaningful_nodes = [
        node for node in graph.nodes if node.type not in STRUCTURAL_NODE_TYPES
    ]
    technical_orphans = [
        node.node_id for node in graph.nodes if node.node_id not in technical_connected
    ]
    meaningful_orphans = [
        node.node_id
        for node in meaningful_nodes
        if node.node_id not in meaningful_connected
    ]
    graph.metrics["technical_connectivity"] = _coverage_metric(
        len(graph.nodes) - len(technical_orphans),
        len(graph.nodes),
        "any edge including containment and dialogue mapping",
        orphan_count=len(technical_orphans),
    )
    graph.metrics["meaningful_architectural_connectivity"] = _coverage_metric(
        len(meaningful_nodes) - len(meaningful_orphans),
        len(meaningful_nodes),
        "non-structural relation; contains and belongs_to_contour do not count",
        orphan_count=len(meaningful_orphans),
        orphan_samples=meaningful_orphans[:30],
    )


def refresh_ownership_metrics(graph: ProjectGraph) -> None:
    owner_classification: Counter[str] = Counter()
    for node in graph.nodes:
        method = str(node.attributes.get("owner_classification") or "unknown_owner")
        if node.owner == "unknown":
            method = "unknown_owner"
        owner_classification[method] += 1
    graph.metrics["ownership_classification"] = {
        "denominator": len(graph.nodes),
        "verified_owner": owner_classification["verified_owner"],
        "rule_derived_owner": owner_classification["rule_derived_owner"],
        "fallback_owner": owner_classification["fallback_owner"],
        "unknown_owner": owner_classification["unknown_owner"],
        "method": "explicit catalog owner, bounded path rule, fallback label, or unknown; fallback is never counted as verified",
    }
    graph.metrics["verified_ownership_coverage"] = _coverage_metric(
        owner_classification["verified_owner"],
        len(graph.nodes),
        "nodes with an explicit reviewed catalog owner only",
    )


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
