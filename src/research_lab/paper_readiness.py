# -*- coding: utf-8 -*-
"""Operator-facing readiness report for the paper runtime.

The paper runtime is intentionally strict: it can only consume setup-library cards that
already passed hard validation. This module explains why the current setup library does
or does not contain runnable paper inputs.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable

from src.research_lab.candle_library import load_canonical_candles
from src.research_lab.hard_validation_contract import SetupCard
from src.research_lab.paper_contract import PaperPlanError, plan_from_setup_card
from src.research_lab.validation_generation import (
    CurrentGenerationSnapshot,
    load_current_generation_snapshot,
    read_current_setup_card,
)


def _short_reason(reason: str, *, max_len: int = 120) -> str:
    reason = " ".join(str(reason or "").split())
    return reason if len(reason) <= max_len else reason[: max_len - 3] + "..."


def _load_cards(
    private_root: Path,
    *,
    limit: int = 500,
    generation: CurrentGenerationSnapshot | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    check_active: Callable[[], None] | None = None,
) -> tuple[list[SetupCard], int, CurrentGenerationSnapshot]:
    generation = generation or load_current_generation_snapshot(
        private_root,
        progress=progress,
        check_active=check_active,
    )
    if generation.status != "legacy_absent":
        if not generation.usable:
            return [], generation.active_candidates, generation
        rows = list(generation.payloads.items())[: max(0, int(limit))]
        direct_cards: list[SetupCard] = []
        unreadable = 0
        total = len(rows)
        for completed, (_candidate_id, payloads) in enumerate(rows, start=1):
            if check_active is not None:
                check_active()
            try:
                direct_cards.append(SetupCard.from_dict(payloads["setup_card"][1]))
            except (KeyError, TypeError, ValueError):
                unreadable += 1
            if progress is not None:
                progress("paper_card_loaded", completed, total)
            if check_active is not None:
                check_active()
        return direct_cards, unreadable, generation

    cards_dir = Path(private_root) / "setup_library" / "cards"
    if not cards_dir.exists():
        return [], 0, generation
    legacy_cards: list[SetupCard] = []
    unreadable = 0
    paths = sorted(cards_dir.glob("*.json"))[: max(0, int(limit))]
    for completed, path in enumerate(paths, start=1):
        if check_active is not None:
            check_active()
        try:
            payload = read_current_setup_card(private_root, path)
            if payload is None:
                unreadable += 1
                continue
            legacy_cards.append(SetupCard.from_dict(payload))
        except (OSError, KeyError, TypeError, ValueError):
            unreadable += 1
        if progress is not None:
            progress("legacy_paper_card_loaded", completed, len(paths))
        if check_active is not None:
            check_active()
    return legacy_cards, unreadable, generation


def summarize_paper_readiness(
    private_root: Path,
    *,
    limit: int = 500,
    check_local_data: bool = True,
    generation: CurrentGenerationSnapshot | None = None,
    cards: list[SetupCard] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    check_active: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Summarize setup-library readiness without writing anything."""
    if cards is None:
        cards, unreadable, generation = _load_cards(
            Path(private_root),
            limit=limit,
            generation=generation,
            progress=progress,
            check_active=check_active,
        )
    else:
        unreadable = 0
        generation = generation or load_current_generation_snapshot(
            private_root,
            progress=progress,
            check_active=check_active,
        )
    hard: Counter[str] = Counter()
    lite: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    sample_blockers: list[dict[str, str]] = []
    ready_cards = 0
    plan_ready = 0
    local_data_ready = 0
    local_data_missing = 0
    local_data_unproven = 0
    data_manifests: dict[str, dict[str, Any]] = {}

    for completed, card in enumerate(cards, start=1):
        if check_active is not None:
            check_active()
        hard_status = str(card.hard_status or "missing").strip() or "missing"
        lite_status = str(card.lite_status or "missing").strip() or "missing"
        hard[hard_status] += 1
        lite[lite_status] += 1
        if not card.paper_forward_ready:
            reason = f"hard_status:{hard_status}"
            reasons[reason] += 1
            if len(sample_blockers) < 10:
                sample_blockers.append({
                    "setup_id": card.setup_id,
                    "symbol": card.symbol,
                    "timeframe": card.timeframe,
                    "reason": reason,
                })
            continue
        ready_cards += 1
        try:
            plan = plan_from_setup_card(card)
        except PaperPlanError as exc:
            reason = "invalid_plan:" + _short_reason(str(exc))
            reasons[reason] += 1
            if len(sample_blockers) < 10:
                sample_blockers.append({
                    "setup_id": card.setup_id,
                    "symbol": card.symbol,
                    "timeframe": card.timeframe,
                    "reason": reason,
                })
            continue
        plan_ready += 1
        if not check_local_data:
            continue
        selected = load_canonical_candles(
            Path(private_root), plan.symbol, plan.timeframe,
            purpose="paper_readiness", coverage_policy="gap_free",
        )
        data_manifests[card.setup_id] = {
            "snapshot_id": selected.manifest.snapshot_id,
            "evidence_hash": selected.manifest.evidence_hash,
            "provenance_status": selected.manifest.provenance_status,
            "coverage_status": selected.manifest.coverage_status,
        }
        if not selected.rows:
            local_data_missing += 1
            reason = "local_candles_missing"
            reasons[reason] += 1
            if len(sample_blockers) < 10:
                sample_blockers.append({
                    "setup_id": card.setup_id,
                    "symbol": card.symbol,
                    "timeframe": card.timeframe,
                    "reason": reason,
                })
        elif selected.manifest.provenance_status != "complete":
            local_data_unproven += 1
            reason = "local_candles_provenance_unknown"
            reasons[reason] += 1
            if len(sample_blockers) < 10:
                sample_blockers.append({
                    "setup_id": card.setup_id,
                    "symbol": card.symbol,
                    "timeframe": card.timeframe,
                    "reason": reason,
                })
        else:
            local_data_ready += 1
        if progress is not None:
            progress("paper_readiness_card_checked", completed, len(cards))
        if check_active is not None:
            check_active()

    return {
        "generation_status": generation.status,
        "generation_id": generation.generation_id,
        "generation_active_candidates": generation.active_candidates,
        "generation_invalid_candidates": len(generation.invalid_candidates),
        "checked_cards": len(cards),
        "unreadable_cards": unreadable,
        "paper_forward_ready": ready_cards,
        "plan_ready": plan_ready,
        "local_data_ready": local_data_ready,
        "local_data_missing": local_data_missing,
        "local_data_unproven": local_data_unproven,
        "data_manifests": data_manifests,
        "by_hard_status": dict(sorted(hard.items())),
        "by_lite_status": dict(sorted(lite.items())),
        "blocked_reasons": dict(sorted(reasons.items())),
        "sample_blockers": sample_blockers,
    }
