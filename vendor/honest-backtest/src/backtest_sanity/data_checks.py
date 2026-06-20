# -*- coding: utf-8 -*-
"""Layer 1 — data integrity: the cheapest lies live here.

lookahead_correlation : a 'predictor' should not correlate implausibly with the very
                        bar it claims to predict — that's a look-ahead smell.
timestamp_monotonic   : out-of-order or duplicate bars = silent leakage.
survivorship_note     : a today-only universe quietly drops everything that died.
"""
from __future__ import annotations

import numpy as np


def lookahead_correlation(feature, contemporaneous_future_return):
    """|corr| between a feature available at time t and the SAME-bar future it must not see.
    A suspiciously high value is a classic look-ahead tell. Returns the correlation."""
    f = np.asarray(feature, dtype=float)
    r = np.asarray(contemporaneous_future_return, dtype=float)
    if len(f) != len(r) or len(f) < 3:
        return float("nan")
    return float(np.corrcoef(f, r)[0, 1])


def timestamp_monotonic(timestamps) -> bool:
    """True only if strictly increasing (no duplicates, no out-of-order bars)."""
    ts = list(timestamps)
    return all(ts[i] < ts[i + 1] for i in range(len(ts) - 1))


def survivorship_note() -> str:
    return ("Survivorship bias: if your universe is the assets that exist TODAY, you "
            "silently deleted the ones that delisted/died. Use a point-in-time universe.")
