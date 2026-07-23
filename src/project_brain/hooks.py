"""Shadow-only lifecycle hook adapters with an external authority boundary.

This module is not installed as a Codex hook configuration.  It models the
manifests an integration could exchange after a separate installation review.
Routing describes requested intent; only a trusted, separately supplied owner
manifest can authorize a state-changing effect.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime as dt
from typing import Any, Mapping

from scripts.ci.check_supply_chain_policy import reject_sensitive_data

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
CANONICAL_RCC_SCOPE = "canonical_paper_only_rcc"
CANONICAL_RCC_RESOURCES = frozenset(
    {"ollama", "public_news", "scanner", "paper_cards", "telegram_bot"}
)
MAX_AUTHORITY_LIFETIME = dt.timedelta(hours=1)
ABSOLUTE_DENIALS = frozenset(
    {
        "read_secret",
        "read_dotenv",
        "live_order",
        "private_exchange",
        "private_endpoint",
        "enable_auto_trade",
        "enable_execution_authority",
        "destructive_git",
        "git_merge",
        "push_main",
    }
)
STATE_CHANGING_EFFECTS = frozenset(
    {
        "start_process",
        "stop_process",
        "write_project_file",
        "write_memory",
        "git_commit",
        "git_push",
        "git_create_pr",
        "external_send",
        "database_mutation",
    }
)
READ_ONLY_EFFECTS = frozenset(
    {
        "answer",
        "read_public_code",
        "read_public_docs",
        "read_status",
        "inspect_git",
        "run_non_live_test",
        "build_context_packet",
    }
)


@dataclass(frozen=True)
class OwnerAuthorityManifest:
    """A capability supplied by a trusted owner channel, never by routing data."""

    action: str
    project_id: str
    contour: str
    exact_scope: str
    allowed_resources: tuple[str, ...]
    issued_at: str
    expires_at: str
    turn_id: str
    source: str = "external_owner_channel"
    schema: str = "OwnerAuthorityManifest.v1"

    @property
    def manifest_id(self) -> str:
        return stable_id("owner-authority", self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HookManifest:
    event: str
    project_id: str
    commit_sha: str
    route_id: str = ""
    primary_contour: str = ""
    secondary_contours: tuple[str, ...] = ()
    requested_action: str = ""
    authority_requirement: Mapping[str, Any] | None = None
    authority_manifest_id: str = ""
    checkpoint_record_id: str = ""
    evidence_pointer: str = ""
    result_hash: str = ""
    allowed: bool = True
    reason: str = "ok"
    schema: str = "ProjectBrainHookManifest.v2"

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
        requested_action="read_status",
        reason="resume_core_manifest" if resume else "new_session_core_manifest",
    )


def pre_compact(
    graph: ProjectGraph,
    store: ProjectBrainStore,
    *,
    branch: str,
    summary: str,
    evidence_refs: tuple[str, ...],
    owner_authority: OwnerAuthorityManifest | None = None,
    turn_id: str = "",
    now: str | None = None,
) -> HookManifest:
    allowed, reason, manifest_id = _validate_owner_authority(
        graph,
        action="write_memory",
        contour="active_work",
        resource="project_brain_private_store",
        owner_authority=owner_authority,
        turn_id=turn_id,
        now=now,
    )
    if not allowed:
        return HookManifest(
            event="PreCompact",
            project_id=graph.repository,
            commit_sha=graph.commit_sha,
            primary_contour="active_work",
            requested_action="write_memory",
            allowed=False,
            reason=reason,
        )
    record = store.append_record(
        contour="active_work",
        entity="conversation_checkpoint",
        record_type="verification",
        source="PreCompact",
        evidence_refs=evidence_refs,
        repository=graph.repository,
        branch=branch,
        commit_sha=graph.commit_sha,
        authority="external_owner_manifest",
        authority_id=manifest_id,
        summary=summary,
    )
    return HookManifest(
        event="PreCompact",
        project_id=graph.repository,
        commit_sha=graph.commit_sha,
        primary_contour="active_work",
        requested_action="write_memory",
        authority_manifest_id=manifest_id,
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
        requested_action="read_status",
        checkpoint_record_id=checkpoint_record_id,
        reason="load_manifest_not_transcript",
    )


def pre_tool_use(
    graph: ProjectGraph,
    route: RouteDecision,
    tool_effect: str,
    *,
    resource: str = "",
    owner_authority: OwnerAuthorityManifest | Mapping[str, Any] | None = None,
    turn_id: str = "",
    now: str | None = None,
) -> HookManifest:
    allowed, reason, manifest_id = _effect_allowed(
        graph,
        route,
        tool_effect,
        resource=resource,
        owner_authority=owner_authority,
        turn_id=turn_id,
        now=now,
    )
    return HookManifest(
        event="PreToolUse",
        project_id=graph.repository,
        commit_sha=graph.commit_sha,
        route_id=route.route_id,
        primary_contour=route.primary_contour,
        secondary_contours=route.secondary_contours,
        requested_action=route.requested_action,
        authority_requirement=route.authority_requirement,
        authority_manifest_id=manifest_id,
        allowed=allowed,
        reason=reason,
    )


def post_tool_use(
    graph: ProjectGraph,
    route: RouteDecision,
    *,
    evidence_pointer: str,
    result_hash: str,
    result_metadata: Mapping[str, Any] | None = None,
) -> HookManifest:
    reject_sensitive_data(
        {
            "evidence_pointer": evidence_pointer,
            "result_hash": result_hash,
            "result_metadata": dict(result_metadata or {}),
        }
    )
    return HookManifest(
        event="PostToolUse",
        project_id=graph.repository,
        commit_sha=graph.commit_sha,
        route_id=route.route_id,
        primary_contour=route.primary_contour,
        secondary_contours=route.secondary_contours,
        requested_action=route.requested_action,
        authority_requirement=route.authority_requirement,
        evidence_pointer=evidence_pointer,
        result_hash=result_hash,
        reason="pointer_and_hash_only_large_output_not_retained",
    )


def stop_hook(
    graph: ProjectGraph,
    store: ProjectBrainStore,
    *,
    branch: str,
    summary: str,
    evidence_refs: tuple[str, ...],
    owner_authority: OwnerAuthorityManifest | None = None,
    turn_id: str = "",
    now: str | None = None,
) -> HookManifest:
    allowed, reason, manifest_id = _validate_owner_authority(
        graph,
        action="write_memory",
        contour="active_work",
        resource="project_brain_private_store",
        owner_authority=owner_authority,
        turn_id=turn_id,
        now=now,
    )
    if not allowed:
        return HookManifest(
            event="Stop",
            project_id=graph.repository,
            commit_sha=graph.commit_sha,
            primary_contour="active_work",
            requested_action="write_memory",
            allowed=False,
            reason=reason,
        )
    record = store.append_record(
        contour="active_work",
        entity="turn_delta",
        record_type="verification",
        source="Stop",
        evidence_refs=evidence_refs,
        repository=graph.repository,
        branch=branch,
        commit_sha=graph.commit_sha,
        authority="external_owner_manifest",
        authority_id=manifest_id,
        summary=summary,
    )
    return HookManifest(
        event="Stop",
        project_id=graph.repository,
        commit_sha=graph.commit_sha,
        primary_contour="active_work",
        requested_action="write_memory",
        authority_manifest_id=manifest_id,
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
        requested_action=route.requested_action,
        authority_requirement=route.authority_requirement,
        reason="routing_only_no_operational_authority",
    )


def _effect_allowed(
    graph: ProjectGraph,
    route: RouteDecision,
    effect: str,
    *,
    resource: str,
    owner_authority: OwnerAuthorityManifest | Mapping[str, Any] | None,
    turn_id: str,
    now: str | None,
) -> tuple[bool, str, str]:
    normalized = effect.strip().lower()
    if normalized in ABSOLUTE_DENIALS:
        return False, "absolute_project_boundary", ""
    if normalized in READ_ONLY_EFFECTS:
        return True, "read_only_effect", ""
    if normalized not in STATE_CHANGING_EFFECTS:
        return False, "unclassified_effect_denied", ""
    if normalized != route.requested_action:
        return False, "requested_action_mismatch", ""
    return _validate_owner_authority(
        graph,
        action=normalized,
        contour=route.primary_contour,
        resource=resource,
        owner_authority=owner_authority,
        turn_id=turn_id,
        now=now,
    )


def _validate_owner_authority(
    graph: ProjectGraph,
    *,
    action: str,
    contour: str,
    resource: str,
    owner_authority: OwnerAuthorityManifest | Mapping[str, Any] | None,
    turn_id: str,
    now: str | None,
) -> tuple[bool, str, str]:
    if action in ABSOLUTE_DENIALS:
        return False, "absolute_project_boundary", ""
    if owner_authority is None:
        return False, "external_owner_manifest_missing", ""
    if not isinstance(owner_authority, OwnerAuthorityManifest):
        return False, "external_owner_manifest_untrusted", ""
    manifest = owner_authority
    reject_sensitive_data(manifest.to_dict())
    if manifest.source != "external_owner_channel":
        return False, "external_owner_manifest_untrusted", ""
    if manifest.action != action:
        return False, "owner_manifest_action_mismatch", ""
    if manifest.project_id != graph.repository:
        return False, "owner_manifest_project_mismatch", ""
    if manifest.contour != contour:
        return False, "owner_manifest_contour_mismatch", ""
    if not turn_id or manifest.turn_id != turn_id:
        return False, "owner_manifest_turn_mismatch", ""
    try:
        current = _utc_timestamp(now or dt.datetime.now(dt.timezone.utc).isoformat())
        issued = _utc_timestamp(manifest.issued_at)
        expires = _utc_timestamp(manifest.expires_at)
    except ValueError:
        return False, "owner_manifest_time_invalid", ""
    if issued > current or current >= expires:
        return False, "owner_manifest_stale", ""
    if expires <= issued or expires - issued > MAX_AUTHORITY_LIFETIME:
        return False, "owner_manifest_lifetime_invalid", ""
    if not resource or manifest.exact_scope != resource:
        return False, "owner_manifest_scope_mismatch", ""
    resources = frozenset(manifest.allowed_resources)
    if action in {"start_process", "stop_process"}:
        if manifest.exact_scope != CANONICAL_RCC_SCOPE:
            return False, "runtime_scope_not_canonical", ""
        if resources != CANONICAL_RCC_RESOURCES:
            return False, "runtime_profile_mismatch", ""
    elif resource not in resources and manifest.exact_scope not in resources:
        return False, "owner_manifest_resource_mismatch", ""
    return True, "exact_external_owner_manifest", manifest.manifest_id


def _utc_timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(dt.timezone.utc)


def hook_invocation_id(manifest: HookManifest) -> str:
    return stable_id(
        "hook",
        manifest.event,
        manifest.project_id,
        manifest.commit_sha,
        manifest.route_id,
        manifest.reason,
    )
