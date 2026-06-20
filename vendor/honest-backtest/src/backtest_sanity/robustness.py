# -*- coding: utf-8 -*-
"""Layer 5 — robustness: a real edge is a plateau, not a single lucky spike.

param_sweep        : run a strategy over a parameter grid; a lone spike >> the median
                     is a tuning artifact, not a finding.
subperiod_stability: split the series into N chunks; an edge that only lives in one
                     chunk is fragile.
"""
from __future__ import annotations

import numpy as np


def param_sweep(strategy_fn, param_grid):
    """strategy_fn(param) -> scalar score (e.g. mean return). Returns the grid results
    plus spike_ratio = best/median (high = the 'best' param is probably overfit)."""
    results = [(p, float(strategy_fn(p))) for p in param_grid]
    vals = np.array([v for _, v in results], dtype=float)
    best = float(np.max(vals))
    median = float(np.median(vals))
    spike_ratio = (best / median) if median not in (0.0,) else float("inf")
    return {"results": results, "best": best, "median": median, "spike_ratio": spike_ratio}


def subperiod_stability(returns, n_periods: int = 4):
    """Split into n_periods chunks; report per-chunk mean and whether the sign is consistent."""
    arr = np.asarray(returns, dtype=float)
    means = [float(np.mean(c)) for c in np.array_split(arr, n_periods)]
    positive = sum(1 for m in means if m > 0)
    return {
        "period_means": means,
        "positive_periods": positive,
        "n_periods": n_periods,
        "consistent": positive in (0, n_periods),
    }
