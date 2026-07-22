"""Side-effect-bounded lifecycle hook contracts for Codex-style clients."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .router import RouteDecision, load_contours, route_message
from .schema import ProjectGraph, stable_id
from .store import ProjectBrainStore


HOOK_EVENTS = frozenset(
    {
        "UserPromptSubmit",
        "SessionStart",
        "PreCompact",
        "PostCompact",
        "PreToolUse",
        "PostToolUse",
        "Stop",
    }
)


@dataclass(frozen=True)
class HookManifest:
    event: str
    project_id: str
    commit_sha: str
    route_id: str = ""
    primary_contour: str = ""
    secondary_contours: tuple[str, ...] = ()
    authority: Mapping[str, Any] | None = None
    checkpoint_record_id: str = ""
    evidence_pointer: str = ""
    result_hash: str = ""
    allowed: bool = True
    reason: str = "ok"
    schema: str = "ProjectBrainHookManifest.v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def user_prompt_submit(
    graph: ProjectGraph, message: str, contour_catalog
) -> HookManifest:
    route = route_message(message, load_contours(contour_catalog))
    return _route_manifest("UserPromptSubmit", graph, route)


def session_start(graph: ProjectGraph, *, resume: bool = False) -> HookManifest:
    return HookManifest(
        event="SessionStart",
        project_id=graph.repository,
        commit_sha=graph.commit_sha,
        primary_contour="active_work",
        secondary_contours=("governance_and_safety", "project_architecture"),
        reason="resume_core_manifest" if resume else "new_session_core_manifest",
    )


def pre_compact(
    graph: ProjectGraph,
    store: ProjectBrainStore,
    *,
    branch: str,
    summary: str,
    evidence_refs: tuple[str, ...],
) -> HookManifest:
    record = store.append_record(
        contour="active_work",
        entity="conversation_checkpoint",
        record_type="verification",
        source="PreCompact",
        evidence_refs=evidence_refs,
        repository=graph.repository,
        branch=branch,
        commit_sha=graph.commit_sha,
        authority="memory_checkpoint_only",
        summary=summary,
    )
    return HookManifest(
        event="PreCompact",
        project_id=graph.repository,
        commit_sha=graph.commit_sha,
        primary_contour="active_work",
        checkpoint_record_id=record.record_id,
        evidence_pointer=str(store.events_path),
        reason="verified_delta_checkpointed",
    )


def post_compact(graph: ProjectGraph, checkpoint_record_id: str) -> HookManifest:
    return HookManifest(
        event="PostCompact",
        project_id=graph.repository,
        commit_sha=graph.commit_sha,
        primary_contour="active_work",
        secondary_contours=("governance_and_safety",),
        checkpoint_record_id=checkpoint_record_id,
        reason="load_manifest_not_transcript",
    )


def pre_tool_use(
    graph: ProjectGraph, route: RouteDecision, tool_effect: str
) -> HookManifest:
    allowed, reason = _effect_allowed(route, tool_effect)
    return HookManifest(
        event="PreToolUse",
        project_id=graph.repository,
        commit_sha=graph.commit_sha,
        route_id=route.route_id,
        primary_contour=route.primary_contour,
        secondary_contours=route.secondary_contours,
        authority=route.authority,
        allowed=allowed,
        reason=reason,
    )


def post_tool_use(
    graph: ProjectGraph,
    route: RouteDecision,
    *,
    evidence_pointer: str,
    result_hash: str,
) -> HookManifest:
    return HookManifest(
        event="PostToolUse",
        project_id=graph.repository,
        commit_sha=graph.commit_sha,
        route_id=route.route_id,
        primary_contour=route.primary_contour,
        secondary_contours=route.secondary_contours,
        authority=route.authority,
        evidence_pointer=evidence_pointer,
        result_hash=result_hash,
        reason="pointer_only_large_outputs_not_in_context",
    )


def stop_hook(
    graph: ProjectGraph,
    store: ProjectBrainStore,
    *,
    branch: str,
    summary: str,
    evidence_refs: tuple[str, ...],
) -> HookManifest:
    record = store.append_record(
        contour="active_work",
        entity="turn_delta",
        record_type="verification",
        source="Stop",
        evidence_refs=evidence_refs,
        repository=graph.repository,
        branch=branch,
        commit_sha=graph.commit_sha,
        authority="memory_delta_only",
        summary=summary,
    )
    return HookManifest(
        event="Stop",
        project_id=graph.repository,
        commit_sha=graph.commit_sha,
        primary_contour="active_work",
        checkpoint_record_id=record.record_id,
        evidence_pointer=str(store.events_path),
        reason="delta_recorded_session_untouched",
    )


def _route_manifest(
    event: str, graph: ProjectGraph, route: RouteDecision
) -> HookManifest:
    return HookManifest(
        event=event,
        project_id=graph.repository,
        commit_sha=graph.commit_sha,
        route_id=route.route_id,
        primary_contour=route.primary_contour,
        secondary_contours=route.secondary_contours,
        authority=route.authority,
    )


def _effect_allowed(route: RouteDecision, effect: str) -> tuple[bool, str]:
    normalized = effect.strip().lower()
    absolute_denials = {
        "read_secret",
        "read_dotenv",
        "live_order",
        "private_exchange",
        "enable_auto_trade",
        "enable_execution_authority",
        "destructive_git",
    }
    if normalized in absolute_denials:
        return False, "absolute_project_boundary"
    if normalized in {"start_process", "stop_process"} and route.mode != "runtime":
        return False, "runtime_authority_missing"
    if normalized in {"merge", "push_main"}:
        return False, "separate_git_gate_required"
    return True, "within_routed_manifest"


def hook_invocation_id(manifest: HookManifest) -> str:
    return stable_id(
        "hook",
        manifest.event,
        manifest.project_id,
        manifest.commit_sha,
        manifest.route_id,
        manifest.reason,
    )
