# -*- coding: utf-8 -*-
"""Synthetic data generators for the examples. NO real market data, ever — the point
is to demonstrate the *checks*, not to reveal any strategy."""
from __future__ import annotations

import numpy as np


def random_walk_prices(n: int = 1000, mu: float = 0.0, sigma: float = 0.01,
                       start: float = 100.0, seed: int = 0):
    """A driftless (or slightly drifting) geometric random walk + its log returns."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(mu, sigma, n)
    prices = start * np.exp(np.cumsum(rets))
    return prices, rets


def toy_returns(n: int = 1000, edge: float = 0.0, sigma: float = 0.01, seed: int = 0):
    """Per-period returns with a constant `edge` (mean) plus noise.
    edge=0.0 -> no real edge (any 'profit' is luck)."""
    rng = np.random.default_rng(seed)
    return rng.normal(edge, sigma, n)
