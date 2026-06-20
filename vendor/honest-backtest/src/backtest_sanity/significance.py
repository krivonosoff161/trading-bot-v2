# -*- coding: utf-8 -*-
"""Layer 3 — significance: is the result real, or luck?

bootstrap_ci  : confidence interval for a statistic via resampling.
permutation_test : null = the signal has no directional edge (random signs).
bonferroni / benjamini_hochberg : if you tested N things, correct for it.

Assumption: both bootstrap and the sign-flip permutation treat observations as
i.i.d. Real trading returns are autocorrelated and volatility-clustered, which
makes these p-values/CIs OPTIMISTIC on real data. Fine for teaching and for a
first cheap kill-test; for serious work use block bootstrap / stationary
bootstrap or account for dependence explicitly.
"""
from __future__ import annotations

import numpy as np


def bootstrap_ci(sample, statistic=np.mean, n_boot: int = 2000,
                 ci: float = 0.95, seed: int = 0):
    """Return (point_estimate, lo, hi). If `lo` spans below 0 for a mean-return, the
    'edge' is not distinguishable from zero."""
    arr = np.asarray(sample, dtype=float)
    n = len(arr)
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    stats = np.empty(n_boot)
    for i in range(n_boot):
        stats[i] = statistic(arr[rng.integers(0, n, n)])
    lo = float(np.percentile(stats, (1 - ci) / 2 * 100))
    hi = float(np.percentile(stats, (1 + ci) / 2 * 100))
    return float(statistic(arr)), lo, hi


def permutation_test(returns, n_perm: int = 2000, seed: int = 0):
    """Two-sided permutation test against the null 'no directional edge' (random sign flips).
    A small p means the mean return is hard to explain by chance."""
    arr = np.asarray(returns, dtype=float)
    rng = np.random.default_rng(seed)
    observed = float(np.mean(arr))
    null = np.empty(n_perm)
    for i in range(n_perm):
        signs = rng.choice([-1.0, 1.0], size=len(arr))
        null[i] = np.mean(arr * signs)
    # bias-corrected permutation p (Phipson & Smyth 2010): strictly positive,
    # never 0 — a finite permutation test cannot prove p == 0.
    p = float((np.sum(np.abs(null) >= abs(observed)) + 1) / (n_perm + 1))
    return {"observed_mean": observed, "p_value": p, "n_perm": n_perm}


def bonferroni(pvalues, alpha: float = 0.05):
    """Family-wise correction: reject only if p * m <= alpha."""
    m = len(pvalues)
    return [(float(p), bool(p * m <= alpha)) for p in pvalues]


def benjamini_hochberg(pvalues, alpha: float = 0.05):
    """FDR control. Returns [(p, reject), ...] in the input order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    max_rank = 0
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= rank / m * alpha:
            max_rank = rank
    cutoff = {order[r - 1] for r in range(1, max_rank + 1)}
    return [(float(pvalues[i]), i in cutoff) for i in range(m)]
