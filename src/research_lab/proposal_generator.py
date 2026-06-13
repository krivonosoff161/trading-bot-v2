# -*- coding: utf-8 -*-
"""Deterministic rule-based generator of next-experiment proposals.

Reads private candidate-registry entries and turns each non-REJECT research label
into one typed follow-up proposal. No LLM, no profitability claims. The mapping is
conservative: promote nothing, only request the next bounded test.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.proposal_schema import PROPOSED, Proposal, coerce_proposal
from src.research_lab.universe import Universe

REVIEW_STATUSES = {"FORWARD_PAPER", "REGIME_SPECIFIC", "OBSERVE"}
CAPTURE_LATE = 0.3
MAX_NEIGHBORS = 6
_INT_KEYS = {"lookback", "period", "ma", "trend_ma", "pullback_ma", "below_bars", "hold_bars", "retest_window", "move_bars"}


def generate_proposals_from_registry(
    entries: list[dict[str, Any]],
    *,
    universe: Universe,
    created_at: str,
    limit: int = 10,
) -> list[Proposal]:
    proposals: list[Proposal] = []
    seen: set[str] = set()
    for entry in entries:
        if str(entry.get("validation_status")) not in REVIEW_STATUSES:
            continue
        payload = _proposal_payload(entry, universe, created_at)
        if payload is None:
            continue
        proposal = coerce_proposal(payload)
        if proposal.proposal_id in seen:
            continue
        seen.add(proposal.proposal_id)
        proposals.append(proposal)
        if len(proposals) >= max(1, limit):
            break
    return proposals


def _proposal_payload(entry: dict[str, Any], universe: Universe, created_at: str) -> dict[str, Any] | None:
    symbol = str(entry.get("symbol") or "")
    family = str(entry.get("strategy_id") or "")
    params = entry.get("params")
    if not symbol or not family or not isinstance(params, dict):
        return None
    status = str(entry.get("validation_status"))
    reasons = [str(r) for r in (entry.get("validation_reasons") or [])]
    metrics = entry.get("metrics_summary") if isinstance(entry.get("metrics_summary"), dict) else {}
    entry_timing = metrics.get("entry_timing") if isinstance(metrics.get("entry_timing"), dict) else {}

    rule = _pick_rule(status, reasons, entry_timing)
    grid, symbols, timeframe, filters = _shape_for_rule(rule, params, symbol, family, entry, universe)
    payload = {
        "created_by": "rule_based",
        "hypothesis": _hypothesis(rule, family, symbol),
        "requested_universe": universe.group_of(symbol) or "",
        "requested_timeframe": timeframe,
        "setup_family": family,
        "symbols": symbols,
        "filters": filters,
        "parameter_grid": {family: grid},
        "max_variants": len(grid) * len(symbols),
        "reason_codes": [rule],
        "expected_validation": status,
        "private_notes": "rule-based follow-up; research label only, not a profitability claim",
        "source_run_id": str(entry.get("experiment_id") or ""),
        "status": PROPOSED,
        "created_at": created_at,
    }
    return payload


def _pick_rule(status: str, reasons: list[str], entry_timing: dict[str, Any]) -> str:
    if status == "FORWARD_PAPER":
        return "candidate_for_forward"
    if status == "REGIME_SPECIFIC":
        return "regime_dependent"
    if "too_few_trades" in reasons or "weak_oos_sample" in reasons:
        return "too_few_trades"
    cap = entry_timing.get("avg_capture_ratio")
    if cap is not None and float(cap) < CAPTURE_LATE:
        return "entry_late"
    if "oos_not_positive" in reasons or "negative_or_flat_average" in reasons:
        return "weak_edge"
    return "unstable_parameters"


def _shape_for_rule(rule, params, symbol, family, entry, universe):
    timeframe = "1d"
    filters: dict[str, list[str]] = {}
    symbols = [symbol]
    if rule == "regime_dependent":
        grid = [dict(params)]
        filters = _filters_from_entry(entry)
    elif rule == "entry_late":
        timeframe = "15m"
        grid = _neighborhood(params)
    elif rule == "too_few_trades":
        grid = [dict(params)]
        symbols = [symbol, *list(universe.peers(symbol))[:2]]
    elif rule == "weak_edge":
        grid = [dict(params)]  # reduced follow-up, single point
    else:  # candidate_for_forward, unstable_parameters
        grid = _neighborhood(params)
    return grid, symbols, timeframe, filters


def _hypothesis(rule: str, family: str, symbol: str) -> str:
    texts = {
        "candidate_for_forward": f"Re-test {family} on {symbol} across a parameter neighborhood to check stability.",
        "regime_dependent": f"Re-run {family} on {symbol} filtered to its strong regime bucket and re-test OOS strength.",
        "entry_late": f"Check whether {family} on {symbol} enters late; test a shorter timeframe / earlier trigger.",
        "unstable_parameters": f"Probe parameter neighbors of {family} on {symbol} for neighbor support, not a single best.",
        "too_few_trades": f"Broaden the symbol set for {family} (peers of {symbol}) to gather more trades before deciding.",
        "weak_edge": f"Reduced follow-up for {family} on {symbol}; weak OOS, expect rejection unless it strengthens.",
    }
    return texts.get(rule, f"Follow-up test for {family} on {symbol}.")


def _neighborhood(params: dict[str, Any]) -> list[dict[str, Any]]:
    variants = [dict(params)]
    for key in sorted(params):
        value = params[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        for mult in (0.8, 1.2):
            candidate = dict(params)
            candidate[key] = _scaled(key, value, mult)
            if candidate not in variants:
                variants.append(candidate)
            if len(variants) >= MAX_NEIGHBORS:
                return variants
    return variants


def _scaled(key: str, value: float, mult: float) -> Any:
    raw = float(value) * mult
    if key in _INT_KEYS:
        return max(1, int(round(raw)))
    return round(max(0.01, raw), 4)


def _filters_from_entry(entry: dict[str, Any]) -> dict[str, list[str]]:
    bucket = ""
    for reason in entry.get("validation_reasons") or []:
        text = str(reason)
        if text.startswith("strong_regime_bucket:"):
            bucket = text.split(":", 1)[1]
            break
    if not bucket:
        summary = entry.get("regime_summary")
        if isinstance(summary, dict):
            bucket = str(summary.get("dominant_bucket") or "")
    parts = [p for p in bucket.split("|") if p and p != "unknown"]
    keys = ["volatility", "trend", "volume"]
    return {key: [value] for key, value in zip(keys, parts) if value}
