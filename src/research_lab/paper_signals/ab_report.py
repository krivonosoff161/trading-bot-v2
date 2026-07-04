# -*- coding: utf-8 -*-
"""A/B comparison artifacts for paper-signal exit modes.

This module is deliberately conservative: it only compares signals that share the
same symbol/timeframe/family/data boundary and differ by exit_mode. If there are
not enough matched pairs, the report says so instead of manufacturing evidence.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.paper_signals import store


def _pair_key(sig) -> tuple:
    return (
        sig.symbol,
        sig.timeframe,
        sig.setup_family,
        sig.side,
        sig.data_fingerprint,
        sig.boundary_ts,
        sig.mode,
    )


def _terminal(sig) -> bool:
    return sig.status in {"reviewed", "closed_paper", "expired"} and bool(sig.outcome)


def build_exit_mode_comparison(private_root: Path, *, baseline: str = "fixed",
                               challenger: str = "partial_be") -> dict[str, Any]:
    groups: dict[tuple, dict[str, Any]] = {}
    for sig in store.load_signals(Path(private_root)):
        if not _terminal(sig):
            continue
        groups.setdefault(_pair_key(sig), {})[sig.exit_mode] = sig

    pairs = []
    for key, modes in groups.items():
        if baseline not in modes or challenger not in modes:
            continue
        b = modes[baseline]
        c = modes[challenger]
        b_net = float((b.outcome or {}).get("net_pct") or 0.0)
        c_net = float((c.outcome or {}).get("net_pct") or 0.0)
        pairs.append({
            "key": {
                "symbol": key[0],
                "timeframe": key[1],
                "family": key[2],
                "side": key[3],
                "data_fingerprint": key[4],
                "boundary_ts": key[5],
                "mode": key[6],
            },
            baseline: {
                "signal_id": b.signal_id,
                "result": (b.outcome or {}).get("result"),
                "net_pct": b_net,
                "diagnosis": (b.review or {}).get("diagnosis"),
            },
            challenger: {
                "signal_id": c.signal_id,
                "result": (c.outcome or {}).get("result"),
                "net_pct": c_net,
                "diagnosis": (c.review or {}).get("diagnosis"),
            },
            "delta_net_pct": round(c_net - b_net, 6),
        })

    b_sum = round(sum(p[baseline]["net_pct"] for p in pairs), 6)
    c_sum = round(sum(p[challenger]["net_pct"] for p in pairs), 6)
    verdict = "insufficient_pairs" if not pairs else (
        "challenger_better" if c_sum > b_sum else "baseline_better_or_equal"
    )
    return {
        "schema": "paper_exit_ab_comparison.v1",
        "baseline": baseline,
        "challenger": challenger,
        "matched_pairs": len(pairs),
        "sum_net_pct": {baseline: b_sum, challenger: c_sum},
        "delta_sum_net_pct": round(c_sum - b_sum, 6),
        "verdict": verdict,
        "pairs": pairs,
        "disclaimer": (
            "Matched replay/paper comparison only. It is not forward proof, not an edge claim, "
            "and not permission to trade."
        ),
    }


def write_exit_mode_comparison(private_root: Path, *, baseline: str = "fixed",
                               challenger: str = "partial_be") -> Path:
    root = Path(private_root)
    out = root / "state" / "derived" / "paper_exit_ab_comparison.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = build_exit_mode_comparison(root, baseline=baseline, challenger=challenger)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out
