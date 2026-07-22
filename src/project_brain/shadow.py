"""Shadow-mode routing evaluation; it never changes code, Git, or runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

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
        cases.append(
            {
                "id": expected["id"],
                "route_id": route.route_id,
                "mode_expected": expected["mode"],
                "mode_actual": route.mode,
                "primary": route.primary_contour,
                "secondary": list(route.secondary_contours),
                "missing": missing,
                "unexpected": unexpected,
                "estimated_tokens": packet.context_budget["estimated_tokens"],
                "max_tokens": packet.context_budget["max_tokens"],
                "passed": not missing
                and not unexpected
                and route.mode == expected["mode"]
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
