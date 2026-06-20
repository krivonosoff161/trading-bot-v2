# -*- coding: utf-8 -*-
"""Layer 7 — AI adversarial review: make models try to KILL the finding, not bless it.

A single pass rationalizes; an adversarial panel refutes. Run N independent verifiers,
each prompted to REFUTE a claim (and to default to 'refuted' when unsure). If a majority
refute, drop the finding. Diversity beats redundancy: give each verifier a different lens
(correctness / does-it-reproduce / is-it-already-priced / cost-realism).

This module is LLM-agnostic: you inject verifier callables `verify(claim) -> bool`
(True = refuted). Wire them to the sibling `llm-router` for real LLM calls, or pass
deterministic fakes for offline tests.
"""
from __future__ import annotations

from typing import Callable, Sequence


def adversarial_review(claim: str,
                       verifiers: Sequence[Callable[[str], bool]],
                       threshold: float = 0.5) -> dict:
    """Each verifier returns True if it REFUTES `claim`. Reject if the refuted fraction
    reaches `threshold`. Returns the decision plus per-verifier votes."""
    if not verifiers:
        return {"claim": claim, "n": 0, "refuted": 0, "rejected": False, "votes": []}
    votes = [bool(v(claim)) for v in verifiers]
    refuted = sum(votes)
    return {
        "claim": claim,
        "n": len(verifiers),
        "refuted": refuted,
        "rejected": refuted >= threshold * len(verifiers),
        "votes": votes,
    }
