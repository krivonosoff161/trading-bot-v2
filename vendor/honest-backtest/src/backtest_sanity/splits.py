# -*- coding: utf-8 -*-
"""Layer 2 — validation splits: never judge on data you fitted to.

holdout       : one train/test cut.
walk_forward  : rolling train->test windows (the honest default for time series).
purged_kfold  : k-fold with a gap so train/test don't leak across the boundary.
"""
from __future__ import annotations


def holdout(n: int, test_frac: float = 0.3):
    """(train_idx, test_idx) with the LAST `test_frac` held out (no shuffling in time series)."""
    cut = int(n * (1 - test_frac))
    return list(range(cut)), list(range(cut, n))


def walk_forward(n: int, train_size: int, test_size: int, step: int | None = None):
    """List of (train_idx, test_idx) rolling forward. Each test window is strictly
    after its train window — the only way to mimic 'deciding before you see it'."""
    step = step or test_size
    out = []
    start = 0
    while start + train_size + test_size <= n:
        train = list(range(start, start + train_size))
        test = list(range(start + train_size, start + train_size + test_size))
        out.append((train, test))
        start += step
    return out


def purged_kfold(n: int, k: int = 5, purge: int = 10):
    """k-fold for time series with a `purge` gap removed from train around each test fold,
    so a sample right next to the test set can't leak into training."""
    fold = n // k
    out = []
    for i in range(k):
        lo = i * fold
        hi = (i + 1) * fold if i < k - 1 else n
        test = list(range(lo, hi))
        train = [j for j in range(n) if j < lo - purge or j >= hi + purge]
        out.append((train, test))
    return out
