"""Shadow-mode routing evaluation; it never changes code, Git, or runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from .hooks import pre_tool_use
from .router import ContourSpec, build_context_packet, route_message
from .schema import ProjectGraph


def evaluate_shadow(
    graph: ProjectGraph,
    contours: Sequence[ContourSpec],
    golden_path: Path,
) -> dict[str, Any]:
    payload = json.loads(golden_path.read_text(encoding="utf-8"))
    if payload.get("schema") != "ProjectBrainGoldenQueries.v1":
        raise ValueError("unsupported golden query schema")
    cases: list[dict[str, Any]] = []
    true_positive = false_positive = false_negative = 0
    for expected in payload["queries"]:
        route = route_message(str(expected["query"]), contours)
        routed = {route.primary_contour, *route.secondary_contours}
        required = set(expected["required_contours"])
        forbidden = set(expected.get("forbidden_contours", []))
        missing = sorted(required - routed)
        unexpected = sorted(routed & forbidden)
        true_positive += len(required & routed)
        false_negative += len(missing)
        false_positive += len(unexpected)
        packet = build_context_packet(
            graph,
            route,
            str(expected["query"]),
            max_tokens=int(expected["max_tokens"]),
        )
        expected_action = str(expected.get("requested_action") or route.requested_action)
        effect = str(expected.get("effect") or "")
        effect_manifest = (
            pre_tool_use(graph, route, effect) if effect else None
        )
        denial_expected = bool(expected.get("denied_without_owner_manifest", False))
        cases.append(
            {
                "id": expected["id"],
                "route_id": route.route_id,
                "mode_expected": expected["mode"],
                "mode_actual": route.mode,
                "primary": route.primary_contour,
                "secondary": list(route.secondary_contours),
                "requested_action_expected": expected_action,
                "requested_action_actual": route.requested_action,
                "effect_allowed_without_owner_manifest": (
                    effect_manifest.allowed if effect_manifest else None
                ),
                "missing": missing,
                "unexpected": unexpected,
                "estimated_tokens": packet.context_budget["estimated_tokens"],
                "max_tokens": packet.context_budget["max_tokens"],
                "passed": not missing
                and not unexpected
                and route.mode == expected["mode"]
                and route.requested_action == expected_action
                and (
                    not denial_expected
                    or (effect_manifest is not None and not effect_manifest.allowed)
                )
                and packet.context_budget["estimated_tokens"]
                <= packet.context_budget["max_tokens"],
            }
        )
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    return {
        "schema": "ProjectBrainShadowEvaluation.v1",
        "cases": cases,
        "summary": {
            "cases": len(cases),
            "passed": sum(bool(case["passed"]) for case in cases),
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "authoritative": False,
        },
    }
