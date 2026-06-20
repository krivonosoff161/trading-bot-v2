# -*- coding: utf-8 -*-
"""backtest-sanity — a layered validation architecture for catching self-deception."""
from .adversarial import adversarial_review
from .costs import apply_costs
from .data_checks import lookahead_correlation, survivorship_note, timestamp_monotonic
from .forward import ForwardLog
from .overfit import (
    deflated_sharpe_ratio,
    minimum_track_record_length,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
)
from .robustness import param_sweep, subperiod_stability
from .significance import benjamini_hochberg, bonferroni, bootstrap_ci, permutation_test
from .splits import holdout, purged_kfold, walk_forward

__all__ = [
    # layer 1 — data integrity
    "lookahead_correlation", "timestamp_monotonic", "survivorship_note",
    # layer 2 — splits
    "holdout", "walk_forward", "purged_kfold",
    # layer 3 — significance
    "bootstrap_ci", "permutation_test", "bonferroni", "benjamini_hochberg",
    # layer 3 (extension) — named anti-overfitting statistics
    "probabilistic_sharpe_ratio", "deflated_sharpe_ratio",
    "minimum_track_record_length", "probability_of_backtest_overfitting",
    # layer 4 — costs
    "apply_costs",
    # layer 5 — robustness
    "param_sweep", "subperiod_stability",
    # layer 6 — forward
    "ForwardLog",
    # layer 7 — AI adversarial review
    "adversarial_review",
]
__version__ = "0.1.0"
