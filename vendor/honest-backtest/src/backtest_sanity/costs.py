# -*- coding: utf-8 -*-
"""Layer 4 — costs: gross vs net. A thin edge that ignores costs is not an edge.

apply_costs : subtract per-period trading cost (fees + slippage) * turnover, and report
              gross vs net so the degradation from theory to reality is visible.
"""
from __future__ import annotations

import numpy as np


def apply_costs(returns, fee: float = 0.001, slippage: float = 0.0005,
                turnover: float = 1.0):
    """Returns a dict with gross/net mean, the per-period cost drag, and both series.
    `turnover` = fraction of capital traded per period (1.0 = fully re-trades each bar)."""
    arr = np.asarray(returns, dtype=float)
    cost = (fee + slippage) * turnover
    net = arr - cost
    return {
        "gross_mean": float(np.mean(arr)),
        "net_mean": float(np.mean(net)),
        "cost_drag": float(cost),
        "survives_costs": bool(np.mean(net) > 0),
        "gross": arr,
        "net": net,
    }
