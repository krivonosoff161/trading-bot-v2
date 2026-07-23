"""Trusted project-local Codex hook runtime for bounded Project Brain memory.

The hook never reads transcripts, dotenv files, credential stores, runtime DBs,
or raw private rows. Raw prompt/tool payloads exist only in the hook process and
are reduced to deterministic routing fields and SHA-256 digests before writes.
Operational authority is deliberately absent.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping

from scripts.ci.check_supply_chain_policy import reject_sensitive_data

from .graph_builder import build_project_graph, read_graph
from .router import add_dialogue_graph, build_context_packet, load_contours, route_message
from .schema import ProjectGraph, content_sha256
from .store import (
    ProjectBrainStore,
    _atomic_write_text,
    _exclusive_file_lock,
)


PROJECT_ID = "trading-bot-v2"
MAX_CONTEXT_TOKENS = 1400
SUPPORTED_EVENTS = frozenset(
    {
        "SessionStart",
        "UserPromptSubmit",
        "PreCompact",
        "PostCompact",
        "PostToolUse",
        "Stop",
    }
)
CONTEXT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "PostCompact"})
SAFE_TOOL_NAME = re.compile(r"[^A-Za-z0-9_.:-]+")


def run_hook(
    payload: Mapping[str, Any],
    *,
    repository_root: Path | None = None,
    store_root: Path | None = None,
) -> dict[str, Any]:
    event = str(payload.get("hook_event_name") or "")
    if event not in SUPPORTED_EVENTS:
        return _degraded(event or "unknown", "unsupported_event")
    try:
        root = (repository_root or _git_root(Path(str(payload.get("cwd") or ".")))).resolve()
        if not (root / "configs" / "project_brain" / "architecture.json").is_file():
            return _degraded(event, "project_identity_unresolved")
        store = ProjectBrainStore(
            (store_root or default_store_root()).resolve(),
            repository_root=root,
        )
        graph = _ensure_graph(root, store)
        branch = _git(root, "branch", "--show-current") or "detached"
        session_key = _digest(str(payload.get("session_id") or "unknown"))[:16]
        turn_key = _digest(str(payload.get("turn_id") or "none"))[:16]

        if event == "UserPromptSubmit":
            return _on_prompt(payload, root, store, graph, branch, session_key, turn_key)
        if event == "SessionStart":
            return _on_session_start(payload, root, store, graph, branch, session_key)
        if event == "PreCompact":
            _checkpoint(store, graph, branch, session_key, turn_key, "pre_compact")
            return _success(event)
        if event == "PostCompact":
            return _context_output(event, _resume_context(store, graph, branch))
        if event == "PostToolUse":
            _on_post_tool(payload, store, graph, branch, session_key, turn_key)
            return _success(event)
        if event == "Stop":
            _checkpoint(store, graph, branch, session_key, turn_key, "turn_stop")
            return _success(event)
    except Exception as exc:  # Hooks must fail open without exposing payload values.
        return _degraded(event, _safe_error_code(exc))
    return _degraded(event, "unhandled_event")


def default_store_root() -> Path:
    override = os.environ.get("TRADING_PROJECT_BRAIN_HOME", "").strip()
    if override:
        return Path(override) / PROJECT_ID
    return Path.home() / ".codex" / "project_brain" / PROJECT_ID


def _on_prompt(
    payload: Mapping[str, Any],
    root: Path,
    store: ProjectBrainStore,
    graph: ProjectGraph,
    branch: str,
    session_key: str,
    turn_key: str,
) -> dict[str, Any]:
    message = str(payload.get("prompt") or payload.get("user_prompt") or "")
    contours = load_contours(root / "configs" / "project_brain" / "dialogue_contours.json")
    route = route_message(message, contours)
    packet = build_context_packet(
        graph,
        route,
        message,
        branch=branch,
        task_id=f"session:{session_key}",
        memory_records=store.records(
            contours={route.primary_contour, *route.secondary_contours}
        ),
        causal_links=store.causal_links(),
        max_tokens=MAX_CONTEXT_TOKENS,
    )
    safe_route = {
        "mode": route.mode,
        "primary_contour": route.primary_contour,
        "secondary_contours": list(route.secondary_contours),
        "requested_action": route.requested_action,
        "route_id": route.route_id,
        "intent_hash": packet.intent_hash,
        "turn_key": turn_key,
    }
    reject_sensitive_data(safe_route)
    _write_state(store, {"session_key": session_key, "route": safe_route})
    store.append_record(
        contour=route.primary_contour,
        entity="owner_request_route",
        record_type="observed",
        source="CodexHook:UserPromptSubmit",
        evidence_refs=(f"project-brain://route/{route.route_id}",),
        repository=PROJECT_ID,
        branch=branch,
        commit_sha=graph.commit_sha,
        authority="trusted_hook_safe_memory_only",
        authority_id=_activation_hash(root),
        summary=(
            f"mode={route.mode}; requested_action={route.requested_action or 'none'}; "
            f"primary_contour={route.primary_contour}"
        ),
        project_node_ids=tuple(row["node_id"] for row in packet.project_nodes),
        task_id=f"session:{session_key}",
    )
    return _context_output(event="UserPromptSubmit", context=_packet_context(packet.to_dict()))


def _on_session_start(
    payload: Mapping[str, Any],
    root: Path,
    store: ProjectBrainStore,
    graph: ProjectGraph,
    branch: str,
    session_key: str,
) -> dict[str, Any]:
    source = str(payload.get("source") or payload.get("matcher") or "startup")
    safe_source = source if source in {"startup", "resume", "clear", "compact"} else "startup"
    store.append_record(
        contour="active_work",
        entity="session_start",
        record_type="observed",
        source="CodexHook:SessionStart",
        evidence_refs=(f"project-brain://session/{session_key}",),
        repository=PROJECT_ID,
        branch=branch,
        commit_sha=graph.commit_sha,
        authority="trusted_hook_safe_memory_only",
        authority_id=_activation_hash(root),
        summary=f"session_source={safe_source}; exact_sha={graph.commit_sha}",
        task_id=f"session:{session_key}",
    )
    return _context_output("SessionStart", _resume_context(store, graph, branch))


def _on_post_tool(
    payload: Mapping[str, Any],
    store: ProjectBrainStore,
    graph: ProjectGraph,
    branch: str,
    session_key: str,
    turn_key: str,
) -> None:
    tool_name = SAFE_TOOL_NAME.sub("_", str(payload.get("tool_name") or "unknown"))[:80]
    tool_input_hash = content_sha256(payload.get("tool_input"))
    tool_result = payload.get("tool_response", payload.get("tool_result"))
    tool_result_hash = content_sha256(tool_result)
    outcome = "failed" if bool(payload.get("is_error")) else "completed"
    summary = (
        f"tool={tool_name}; outcome={outcome}; input_sha256={tool_input_hash}; "
        f"result_sha256={tool_result_hash}"
    )
    reject_sensitive_data(summary)
    store.append_record(
        contour="active_work",
        entity=f"tool:{tool_name}",
        record_type="observed",
        source="CodexHook:PostToolUse",
        evidence_refs=(f"project-brain://tool/{tool_result_hash[:20]}",),
        repository=PROJECT_ID,
        branch=branch,
        commit_sha=graph.commit_sha,
        authority="trusted_hook_safe_memory_only",
        summary=summary,
        task_id=f"session:{session_key}",
    )
    current = _read_state(store)
    _write_state(
        store,
        {
            **current,
            "session_key": session_key,
            "last_tool": {
                "name": tool_name,
                "outcome": outcome,
                "result_hash": tool_result_hash,
                "turn_key": turn_key,
            },
        },
    )


def _checkpoint(
    store: ProjectBrainStore,
    graph: ProjectGraph,
    branch: str,
    session_key: str,
    turn_key: str,
    reason: str,
) -> None:
    state = _read_state(store)
    route_value = state.get("route")
    route: Mapping[str, Any] = route_value if isinstance(route_value, Mapping) else {}
    tool_value = state.get("last_tool")
    last_tool: Mapping[str, Any] = (
        tool_value if isinstance(tool_value, Mapping) else {}
    )
    summary = (
        f"checkpoint={reason}; mode={route.get('mode', 'unknown')}; "
        f"primary_contour={route.get('primary_contour', 'active_work')}; "
        f"requested_action={route.get('requested_action', 'none') or 'none'}; "
        f"last_tool={last_tool.get('name', 'none')}; "
        f"last_tool_outcome={last_tool.get('outcome', 'none')}"
    )
    reject_sensitive_data(summary)
    store.append_record(
        contour="active_work",
        entity="conversation_checkpoint",
        record_type="verification",
        source=f"CodexHook:{reason}",
        evidence_refs=(f"project-brain://checkpoint/{session_key}/{turn_key}",),
        repository=PROJECT_ID,
        branch=branch,
        commit_sha=graph.commit_sha,
        authority="trusted_hook_safe_memory_only",
        summary=summary,
        task_id=f"session:{session_key}",
    )


def _resume_context(store: ProjectBrainStore, graph: ProjectGraph, branch: str) -> str:
    rows = store.records()[:8]
    safe_rows = [
        {
            "type": row.get("type"),
            "contour": row.get("contour"),
            "entity": row.get("entity"),
            "summary": row.get("summary"),
            "commit_sha": row.get("commit_sha"),
            "freshness": row.get("freshness"),
        }
        for row in rows
    ]
    payload = {
        "schema": "ProjectBrainResumeManifest.v1",
        "project_id": PROJECT_ID,
        "exact_sha": graph.commit_sha,
        "branch": branch,
        "authority": "context_only_no_operational_authority",
        "records": safe_rows,
    }
    reject_sensitive_data(payload)
    return _bounded_context("PROJECT BRAIN RESUME", payload)


def _packet_context(packet: Mapping[str, Any]) -> str:
    selected = {
        "schema": packet.get("schema"),
        "project_id": packet.get("project_id"),
        "commit_sha": packet.get("commit_sha"),
        "mode": packet.get("mode"),
        "primary_contour": packet.get("primary_contour"),
        "secondary_contours": packet.get("secondary_contours"),
        "requested_action": packet.get("requested_action"),
        "authority_requirement": packet.get("authority_requirement"),
        "verified_facts": list(packet.get("verified_facts") or []),
        "project_nodes": list(packet.get("project_nodes") or []),
        "open_questions": list(packet.get("open_questions") or []),
        "context_budget": packet.get("context_budget"),
        "authority": "routing_context_only_no_state_changing_authority",
    }
    reject_sensitive_data(selected)
    return _bounded_context("PROJECT BRAIN CONTEXT PACKET", selected)


def _bounded_context(title: str, payload: Mapping[str, Any]) -> str:
    bounded = dict(payload)
    encoded = json.dumps(bounded, ensure_ascii=False, sort_keys=True)
    text = f"{title}\n{encoded}"
    max_chars = MAX_CONTEXT_TOKENS * 4
    for key in ("project_nodes", "verified_facts", "records", "open_questions"):
        rows = list(bounded.get(key) or [])
        while len(text) > max_chars and rows:
            rows.pop()
            bounded[key] = rows
            encoded = json.dumps(bounded, ensure_ascii=False, sort_keys=True)
            text = f"{title}\n{encoded}"
    if len(text) > max_chars:
        core = {
            key: payload.get(key)
            for key in (
                "schema",
                "project_id",
                "commit_sha",
                "exact_sha",
                "branch",
                "mode",
                "primary_contour",
                "secondary_contours",
                "requested_action",
                "authority",
            )
            if key in payload
        }
        core.update(
            {
                "bounded": True,
                "omitted_payload_sha256": content_sha256(payload),
            }
        )
        text = f"{title}\n" + json.dumps(core, ensure_ascii=False, sort_keys=True)
    return text


def _context_output(event: str, context: str) -> dict[str, Any]:
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        },
    }


def _success(event: str) -> dict[str, Any]:
    del event
    return {}


def _degraded(event: str, reason: str) -> dict[str, Any]:
    message = f"Project Brain: DEGRADED MEMORY MODE ({reason}); Codex continues without memory writes."
    result: dict[str, Any] = {"systemMessage": message}
    if event in {"SessionStart", "PreCompact", "PostCompact", "UserPromptSubmit", "Stop"}:
        result["continue"] = True
    return result


def _ensure_graph(root: Path, store: ProjectBrainStore) -> ProjectGraph:
    current_sha = _git(root, "rev-parse", "HEAD")
    if store.graph_path.is_file():
        graph = read_graph(store.graph_path)
        if graph.commit_sha == current_sha:
            return graph
    build_lock = store.root / ".graph-build.lock"
    with _exclusive_file_lock(build_lock, timeout_seconds=20.0, stale_seconds=120.0):
        if store.graph_path.is_file():
            graph = read_graph(store.graph_path)
            if graph.commit_sha == current_sha:
                return graph
        graph = build_project_graph(
            root,
            revision=current_sha,
            catalog_path=root / "configs" / "project_brain" / "architecture.json",
        )
        graph = add_dialogue_graph(
            graph,
            load_contours(root / "configs" / "project_brain" / "dialogue_contours.json"),
        )
        store.initialize(graph)
        return graph


def _state_path(store: ProjectBrainStore) -> Path:
    return store.root / "hook_state.json"


def _read_state(store: ProjectBrainStore) -> dict[str, Any]:
    path = _state_path(store)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(store: ProjectBrainStore, value: Mapping[str, Any]) -> None:
    reject_sensitive_data(value)
    with _exclusive_file_lock(store.lock_path):
        _atomic_write_text(
            _state_path(store),
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )


def _activation_hash(root: Path) -> str:
    path = root / ".codex" / "hooks.json"
    payload = path.read_bytes() if path.is_file() else b"uninstalled"
    return hashlib.sha256(payload).hexdigest()


def _git_root(cwd: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "store_busy"
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return "invalid_safe_input"
    if isinstance(exc, (OSError, subprocess.SubprocessError)):
        return "local_io_unavailable"
    return "hook_internal_error"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be an object")
        output = run_hook(payload)
    except Exception as exc:
        output = _degraded("unknown", _safe_error_code(exc))
    json.dump(output, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0
