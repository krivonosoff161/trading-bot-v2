from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

import pytest

from scripts.ci.check_tracked_artifacts import ALLOW_PATTERNS, DENY_PATTERNS, matches_any
from src.project_brain import codex_hooks as hooks
from src.project_brain.router import load_contours, route_message
from src.project_brain.schema import GraphNode, ProjectGraph, stable_id
from src.project_brain.store import ProjectBrainStore


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def _graph() -> ProjectGraph:
    nodes = [
        GraphNode(
            node_id=stable_id("repository", "trading-bot-v2"),
            type="repository",
            label="trading-bot-v2",
            repository="trading-bot-v2",
            commit_sha=SHA,
            content_hash=stable_id("hash", "repository"),
            first_seen="2026-01-01T00:00:00+00:00",
            last_verified="2026-01-01T00:00:00+00:00",
            owner="project_owner",
            primary_contour="project_architecture",
        ),
        GraphNode(
            node_id=stable_id("node", "farm-runtime"),
            type="runtime_contour",
            label="canonical paper farm",
            repository="trading-bot-v2",
            path="scripts/strategy_lab/farm_loop.py",
            symbol="farm_loop",
            source_reference="scripts/strategy_lab/farm_loop.py",
            commit_sha=SHA,
            content_hash=stable_id("hash", "farm-runtime"),
            first_seen="2026-01-01T00:00:00+00:00",
            last_verified="2026-01-01T00:00:00+00:00",
            owner="canonical_farm",
            primary_contour="farm_and_runtime",
        ),
    ]
    return ProjectGraph(
        repository="trading-bot-v2",
        commit_sha=SHA,
        generated_from_tree="b" * 40,
        verified_at="2026-01-01T00:00:00+00:00",
        nodes=nodes,
    )


def _store(tmp_path: Path, graph: ProjectGraph) -> ProjectBrainStore:
    store = ProjectBrainStore(
        tmp_path / "private-brain", repository_root=ROOT, allow_test_root=True
    )
    store.initialize(graph)
    return store


def test_project_hooks_use_only_official_supported_events_and_exact_trust_surface() -> None:
    payload = json.loads((ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    configured = payload["hooks"]
    assert set(configured) == hooks.SUPPORTED_EVENTS
    assert configured["SessionStart"][0]["matcher"] == "startup|resume|clear|compact"
    assert configured["PreCompact"][0]["matcher"] == "manual|auto"
    assert configured["PostCompact"][0]["matcher"] == "manual|auto"
    assert "matcher" not in configured["UserPromptSubmit"][0]
    assert "matcher" not in configured["Stop"][0]
    for groups in configured.values():
        for group in groups:
            for handler in group["hooks"]:
                assert handler["type"] == "command"
                assert "commandWindows" in handler
                assert "project_brain_hook.py" in handler["commandWindows"]


def test_project_brain_skill_has_discoverable_frontmatter() -> None:
    text = (ROOT / ".agents" / "skills" / "project-brain" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert text.startswith("---\n")
    frontmatter, body = text.removeprefix("---\n").split("\n---\n", maxsplit=1)
    metadata = dict(line.split(": ", maxsplit=1) for line in frontmatter.splitlines())
    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == "project-brain"
    assert "trading-bot-v2" in metadata["description"]
    assert body.startswith("\n# Project Brain Point Loader")


def test_public_artifact_guard_allows_only_reviewed_project_brain_client_files() -> None:
    allowed = {
        ".codex/hooks.json",
        ".agents/skills/project-brain/SKILL.md",
    }
    assert allowed <= set(ALLOW_PATTERNS)
    assert all(matches_any(path, DENY_PATTERNS) for path in allowed)
    assert not matches_any(".codex/local-state.json", ALLOW_PATTERNS)
    assert not matches_any(".agents/skills/unreviewed/SKILL.md", ALLOW_PATTERNS)


def test_historical_incident_word_does_not_request_process_stop() -> None:
    route = route_message(
        "Почему в прошлый раз остановилась ферма?",
        load_contours(ROOT / "configs" / "project_brain" / "dialogue_contours.json"),
    )
    assert route.mode == "diagnosis"
    assert route.requested_action == "answer"


def test_prompt_tool_checkpoint_and_resume_store_only_safe_deltas(tmp_path) -> None:
    graph = _graph()
    store = _store(tmp_path, graph)
    prompt_marker = "UNIQUE_RAW_PROMPT_MARKER"
    tool_marker = "UNIQUE_RAW_TOOL_RESULT_MARKER"

    output = hooks._on_prompt(
        {"prompt": f"Что происходит с farm runtime? {prompt_marker}"},
        ROOT,
        store,
        graph,
        "codex/project-brain-activation",
        "sessionhash",
        "turnhash",
    )
    hooks._on_post_tool(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "synthetic status"},
            "tool_response": {"text": tool_marker},
        },
        store,
        graph,
        "codex/project-brain-activation",
        "sessionhash",
        "turnhash",
    )
    hooks._checkpoint(
        store,
        graph,
        "codex/project-brain-activation",
        "sessionhash",
        "turnhash",
        "pre_compact",
    )
    resume = hooks._resume_context(store, graph, "codex/project-brain-activation")

    event_bytes = store.events_path.read_text(encoding="utf-8")
    assert prompt_marker not in event_bytes
    assert tool_marker not in event_bytes
    assert "input_sha256=" in event_bytes
    assert "result_sha256=" in event_bytes
    assert "checkpoint=pre_compact" in resume
    assert "last_tool=Bash" in resume
    context = output["hookSpecificOutput"]["additionalContext"]
    assert len(context) <= hooks.MAX_CONTEXT_TOKENS * 4
    assert "no_state_changing_authority" in context


def test_secret_like_prompt_is_rejected_without_memory_write(tmp_path) -> None:
    graph = _graph()
    store = _store(tmp_path, graph)
    before = store.events_path.read_bytes() if store.events_path.exists() else b""
    with pytest.raises(ValueError, match="sensitive value is forbidden"):
        hooks._on_prompt(
            {
                "prompt": "api_"
                + "key="
                + "synthetic_value_that_is_long_enough_123456789"
            },
            ROOT,
            store,
            graph,
            "codex/project-brain-activation",
            "sessionhash",
            "turnhash",
        )
    after = store.events_path.read_bytes() if store.events_path.exists() else b""
    assert after == before


def test_hook_failure_is_explicit_degraded_mode_and_fail_open(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        hooks, "_ensure_graph", lambda root, store: (_ for _ in ()).throw(TimeoutError())
    )
    result = hooks.run_hook(
        {
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(ROOT),
            "session_id": "synthetic",
            "turn_id": "synthetic",
            "prompt": "status",
        },
        repository_root=ROOT,
        store_root=tmp_path / "outside-repository",
    )
    assert result["continue"] is True
    assert "DEGRADED MEMORY MODE (store_busy)" in result["systemMessage"]


def test_concurrent_store_writes_are_complete_and_idempotent(tmp_path) -> None:
    graph = _graph()
    store = _store(tmp_path, graph)

    def append(index: int) -> str:
        return store.append_record(
            contour="active_work",
            entity=f"concurrent:{index}",
            record_type="observed",
            source="synthetic-concurrency-test",
            evidence_refs=(f"project-brain://concurrency/{index}",),
            repository="trading-bot-v2",
            branch="test",
            commit_sha=SHA,
            authority="synthetic",
            summary=f"completed synthetic chunk {index}",
        ).record_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        first = list(executor.map(append, range(24)))
        second = list(executor.map(append, range(24)))

    assert first == second
    assert len(store.records()) == 24
    lines = store.events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 24
    assert all(json.loads(line)["event"] == "record" for line in lines)


def test_windows_sharing_violation_is_treated_as_bounded_lock_contention(
    monkeypatch, tmp_path
) -> None:
    graph = _graph()
    store = _store(tmp_path, graph)
    real_open = os.open
    calls = 0

    def sharing_violation_then_open(path, flags, mode=0o777):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(13, "synthetic Windows sharing violation")
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", sharing_violation_then_open)
    record = store.append_record(
        contour="active_work",
        entity="windows-sharing-violation",
        record_type="observed",
        source="synthetic-windows-lock-test",
        evidence_refs=("project-brain://windows-lock/retry",),
        repository="trading-bot-v2",
        branch="test",
        commit_sha=SHA,
        authority="synthetic",
        summary="completed after bounded synthetic sharing violation",
    )
    assert record.entity == "windows-sharing-violation"
    assert calls >= 2
