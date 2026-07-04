# -*- coding: utf-8 -*-
"""Layer 3 (extension) -- named anti-overfitting statistics.

probabilistic_sharpe_ratio          : P(true Sharpe > benchmark), adjusting for
                                      skewness, kurtosis and sample length.
minimum_track_record_length         : how many observations are needed before
                                      the observed Sharpe clears a benchmark at
                                      a given confidence.
deflated_sharpe_ratio               : PSR against the Sharpe you would expect
                                      from the BEST of N tried variants under
                                      pure luck (multiple testing on Sharpe).
probability_of_backtest_overfitting : CSCV estimate of how often the in-sample
                                      winner underperforms out-of-sample.

Methods follow the published definitions (no code copied):
- Bailey & Lopez de Prado, "The Sharpe Ratio Efficient Frontier" (PSR, MinTRL).
- Bailey & Lopez de Prado, "The Deflated Sharpe Ratio" (DSR).
- Bailey, Borwein, Lopez de Prado & Zhu, "The Probability of Backtest
  Overfitting" (PBO via combinatorially symmetric cross-validation, CSCV).

Assumptions -- read before trusting a number:
- Sharpe ratios are PER-PERIOD (not annualized); the benchmark must be on the
  same scale. Moments are population moments (ddof=0).
- The PSR/DSR sampling distribution assumes returns are stationary and not
  strongly autocorrelated; real returns violate this, making the numbers
  OPTIMISTIC. Same caveat as the rest of layer 3.
- DSR needs the Sharpe ratios of ALL tried variants (including abandoned
  ones). Feeding it only the survivors understates the deflation -- garbage
  in, false confidence out.
- These statistics reduce false confidence. They do not prove profitability,
  and passing them does not certify a strategy. Kill-tests only.
"""
from __future__ import annotations

import math
from itertools import combinations

import numpy as np

_EULER_GAMMA = 0.5772156649015329


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation, ~1e-9
    relative error) -- keeps the package numpy-only, no scipy."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in the open interval (0, 1)")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        num = ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
        den = (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        return num / den
    if p > p_high:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        num = ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
        den = (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        return -num / den
    q = p - 0.5
    r = q * q
    num = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    den = ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0
    return num / den


def _moments(returns):
    """n, per-period Sharpe, skewness, kurtosis (non-excess; normal ~ 3)."""
    arr = np.asarray(returns, dtype=float)
    n = int(arr.size)
    if n < 3:
        raise ValueError("need at least 3 observations to estimate Sharpe "
                         "and higher moments -- a shorter series proves nothing")
    if not np.all(np.isfinite(arr)):
        raise ValueError("returns contain NaN or inf")
    mu = float(np.mean(arr))
    centered = arr - mu
    m2 = float(np.mean(centered ** 2))
    if m2 <= 0.0:
        raise ValueError("zero variance: Sharpe ratio is undefined for a flat "
                         "series -- nothing to validate")
    skew = float(np.mean(centered ** 3)) / m2 ** 1.5
    kurt = float(np.mean(centered ** 4)) / m2 ** 2
    return n, mu / math.sqrt(m2), skew, kurt


def _sharpe_variance_term(sr: float, skew: float, kurt: float) -> float:
    """Higher-moment-adjusted variance numerator of the Sharpe estimator."""
    term = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2
    if term <= 0.0:
        raise ValueError("non-positive Sharpe variance estimate (extreme "
                         "skew/kurtosis vs Sharpe) -- sample too pathological "
                         "to score; inspect the data instead")
    return term


def probabilistic_sharpe_ratio(returns, benchmark_sr: float = 0.0):
    """P(true Sharpe > benchmark_sr) given the observed track record.

    Adjusts the naive Sharpe for sample length, skewness and kurtosis: a short,
    fat-tailed track record gets less credit for the same point estimate.
    `benchmark_sr` must be per-period, like the computed Sharpe. PSR close to
    1.0 means "hard to explain by estimation noise alone" -- it does NOT mean
    the edge survives costs, regime change, or multiple testing (see
    `deflated_sharpe_ratio` for the latter).
    """
    n, sr, skew, kurt = _moments(returns)
    var_term = _sharpe_variance_term(sr, skew, kurt)
    z = (sr - benchmark_sr) * math.sqrt(n - 1.0) / math.sqrt(var_term)
    return {
        "psr": _norm_cdf(z),
        "sharpe": sr,
        "benchmark_sr": float(benchmark_sr),
        "n": n,
        "skew": skew,
        "kurtosis": kurt,
    }


def minimum_track_record_length(returns, benchmark_sr: float = 0.0,
                                confidence: float = 0.95):
    """How many observations would be needed for PSR(benchmark_sr) >= confidence,
    assuming the observed Sharpe/skew/kurtosis persist (a generous assumption).

    Returns `min_n = inf` when the observed Sharpe does not exceed the
    benchmark -- no track record length can rescue it. `sufficient` compares the
    actual sample size against `min_n`; an insufficient track record means
    "keep collecting forward evidence", never "trust it anyway".
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in the open interval (0, 1)")
    n, sr, skew, kurt = _moments(returns)
    if sr <= benchmark_sr:
        min_n = float("inf")
    else:
        var_term = _sharpe_variance_term(sr, skew, kurt)
        z = _norm_ppf(confidence)
        min_n = 1.0 + var_term * (z / (sr - benchmark_sr)) ** 2
    return {
        "min_n": min_n,
        "n": n,
        "sufficient": bool(n >= min_n),
        "sharpe": sr,
        "benchmark_sr": float(benchmark_sr),
        "confidence": float(confidence),
    }


def expected_max_sharpe(trial_sharpes):
    """Expected best Sharpe among N trials if EVERY trial were pure luck
    (true Sharpe 0), with luck-spread estimated from the trials themselves.

    `trial_sharpes` must contain the per-period Sharpe of ALL tried variants,
    not just the survivors -- selective reporting here defeats the whole point.
    """
    sharpes = np.asarray(trial_sharpes, dtype=float)
    if sharpes.size < 2:
        raise ValueError("need the Sharpe ratios of at least 2 trials; with "
                         "one trial there is no multiplicity to deflate for")
    if not np.all(np.isfinite(sharpes)):
        raise ValueError("trial_sharpes contain NaN or inf")
    n_trials = int(sharpes.size)
    spread = math.sqrt(float(np.var(sharpes)))
    return spread * ((1.0 - _EULER_GAMMA) * _norm_ppf(1.0 - 1.0 / n_trials)
                     + _EULER_GAMMA * _norm_ppf(1.0 - 1.0 / (n_trials * math.e)))


def deflated_sharpe_ratio(returns, trial_sharpes):
    """PSR of `returns` measured against the best Sharpe luck alone would
    produce across all tried variants (the multiple-testing-aware benchmark).

    `returns` is the candidate's per-period return series; `trial_sharpes` the
    Sharpe ratios of every variant tried during the search (including the
    candidate and every abandoned configuration). More trials, or a wider
    spread of trial outcomes, raise the luck benchmark and lower the DSR.
    A high DSR still proves nothing forward -- it only says the result is not
    trivially explained by trying many things.
    """
    sr_star = expected_max_sharpe(trial_sharpes)
    res = probabilistic_sharpe_ratio(returns, benchmark_sr=sr_star)
    return {
        "dsr": res["psr"],
        "sharpe": res["sharpe"],
        "expected_max_sharpe": sr_star,
        "n_trials": int(np.asarray(trial_sharpes).size),
        "n": res["n"],
    }


def _sharpe_score(returns) -> float:
    """Default CSCV ranking metric: per-period Sharpe; flat series score 0.0
    (neutral -- a zero-variance fold neither wins nor loses)."""
    arr = np.asarray(returns, dtype=float)
    sd = float(np.std(arr))
    if sd == 0.0:
        return 0.0
    return float(np.mean(arr)) / sd


def probability_of_backtest_overfitting(trial_returns, n_blocks: int = 8,
                                        metric=None,
                                        max_combinations: int = 200):
    """PBO via combinatorially symmetric cross-validation (CSCV).

    `trial_returns` is a 2-D array of shape (T, N): T per-period returns (or
    scores) for each of N strategy variants from the SAME search. Time is cut
    into `n_blocks` even blocks; for every way to pick half the blocks as
    in-sample, the in-sample winner (by `metric`, default per-period Sharpe) is
    ranked on the out-of-sample half. PBO is the fraction of splits where the
    in-sample winner lands in the bottom half out-of-sample -- i.e. how often
    "best in backtest" was just overfit.

    Reading the number: pure noise gives PBO around 0.5 (the winner is random
    out-of-sample); a genuinely dominant variant pulls PBO toward 0; PBO well
    above 0.5 means the selection actively favors what fails forward. A low
    PBO does not certify the strategy -- it only fails to convict the search.

    Block splitting preserves within-block time order but, like the rest of
    layer 3, ignores dependence ACROSS blocks; strong autocorrelation makes
    PBO optimistic. Ties in the out-of-sample ranking are counted against the
    candidate (conservative). When the number of combinations exceeds
    `max_combinations`, an evenly spaced deterministic subset is used and
    reported in `n_combinations`.
    """
    m = np.asarray(trial_returns, dtype=float)
    if m.ndim != 2:
        raise ValueError("trial_returns must be 2-D with shape (time, trials)")
    t, n = m.shape
    if n < 2:
        raise ValueError("need at least 2 trials (columns) to rank a winner")
    if n_blocks < 2 or n_blocks % 2 != 0:
        raise ValueError("n_blocks must be an even integer >= 2")
    if t < 2 * n_blocks:
        raise ValueError("too few rows: need at least 2 observations per "
                         "block (T >= 2 * n_blocks)")
    if not np.all(np.isfinite(m)):
        raise ValueError("trial_returns contain NaN or inf")
    if metric is None:
        metric = _sharpe_score
    if max_combinations < 1:
        raise ValueError("max_combinations must be >= 1")

    blocks = np.array_split(np.arange(t), n_blocks)
    combos = list(combinations(range(n_blocks), n_blocks // 2))
    if len(combos) > max_combinations:
        keep = np.linspace(0, len(combos) - 1, max_combinations).astype(int)
        combos = [combos[i] for i in keep]

    logits, oos_ranks = [], []
    for train_ids in combos:
        train_mask = np.zeros(t, dtype=bool)
        for b in train_ids:
            train_mask[blocks[b]] = True
        train, test = m[train_mask], m[~train_mask]
        is_scores = np.array([metric(train[:, j]) for j in range(n)])
        oos_scores = np.array([metric(test[:, j]) for j in range(n)])
        best = int(np.argmax(is_scores))
        # 1..n; ties count against the candidate (strictly-lower only)
        rank = float(np.sum(oos_scores < oos_scores[best]) + 1)
        omega = rank / (n + 1.0)
        logits.append(math.log(omega / (1.0 - omega)))
        oos_ranks.append(omega)

    pbo = float(np.mean([lam <= 0.0 for lam in logits]))
    return {
        "pbo": pbo,
        "n_combinations": len(combos),
        "n_blocks": n_blocks,
        "n_trials": n,
        "mean_logit": float(np.mean(logits)),
        "oos_relative_ranks": oos_ranks,
    }
