"""Sanitized status report for the full paper/research backbone."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.research_lab.dashboard_state import (
    load_backfill_summary,
    load_agent_role_review_cycle_summary,
    load_lineage_summary,
    load_pipeline_policy_summary,
    load_provider_bench_summary,
    load_ready_strategy_catalog_summary,
    load_vip_vision_smoke_summary,
)
from src.research_lab.human_feedback import feedback_summary
from src.research_lab.agent_role_registry import role_registry_summary
from src.research_lab.llm_role_reviews import review_summary as llm_role_review_summary
from src.research_lab.prompt_registry import prompt_registry_summary
from src.research_lab.provider_routes import provider_route_summary
from src.research_lab.validator_taxonomy import taxonomy_summary

SCHEMA = "PaperResearchStatus.v1"


def build_status(private_root: Path) -> dict[str, Any]:
    lineage = load_lineage_summary(private_root)
    backfill = load_backfill_summary(private_root)
    feedback = feedback_summary(private_root)
    routes = provider_route_summary()
    policy = load_pipeline_policy_summary()
    prompts = prompt_registry_summary()
    validator = taxonomy_summary(private_root)
    ready_catalog = load_ready_strategy_catalog_summary(private_root)
    roles = role_registry_summary()
    role_reviews = llm_role_review_summary(private_root)
    provider_bench = load_provider_bench_summary(private_root)
    review_cycle = load_agent_role_review_cycle_summary(private_root)
    vision_smoke = load_vip_vision_smoke_summary(private_root)
    return {
        "schema": SCHEMA,
        "private_root_label": "strategy-lab",
        "lineage": lineage,
        "backfill": backfill,
        "feedback": feedback,
        "provider_routes": routes,
        "agent_roles": roles,
        "llm_role_reviews": role_reviews,
        "provider_bench": provider_bench,
        "agent_role_review_cycle": review_cycle,
        "vip_vision_smoke": vision_smoke,
        "prompt_registry": prompts,
        "validator_taxonomy": validator,
        "ready_strategy_catalog": ready_catalog,
        "pipeline_policy": policy,
        "ready_flags": {
            "has_scanner_events": int((lineage.get("scanner_events") or {}).get("rows") or 0) > 0,
            "has_data_packets": int((lineage.get("data_packets") or {}).get("rows") or 0) > 0,
            "has_feature_packets": int((lineage.get("feature_packets") or {}).get("rows") or 0) > 0,
            "has_cycle_links": int((lineage.get("cycle_links") or {}).get("rows") or 0) > 0,
            "has_backfill": int(backfill.get("rows") or 0) > 0,
            "has_ready_strategy_catalog": int(ready_catalog.get("ready") or 0) > 0,
        },
        "paper_only": True,
        "execution_allowed": False,
        "secrets_exposed": False,
    }
