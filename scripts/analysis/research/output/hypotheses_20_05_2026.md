# Hypotheses - 20.05.2026

All hypothesis tests below use `BILL-USDT-SWAP` unless stated otherwise. Forward-return tests use close-entry against the explosive candle; the parameter tests use tick replay.

## H1: Commission Trap

| test | n | WR | avg_gross | net_after_fee |
| --- | ---: | ---: | ---: | ---: |
| close-entry 3m | 983 | 52.80% | 0.04% | -0.16% |
| delayed-entry 3m | 983 | 53.71% | 0.09% | -0.11% |
| delayed-entry 10m | 974 | 54.00% | 0.18% | -0.02% |

Conclusion: the raw close-to-close reversal edge is mostly below the fee hurdle; positive WR alone is not enough.

## H2: BE Too Early

| BE trigger | best net_pnl | avg net_pnl | best realized_net |
| --- | ---: | ---: | ---: |
| off | -0.16% | -0.21% | -0.16% |
| 0.3% | -0.19% | -0.22% | -0.19% |
| 0.5% | -0.18% | -0.21% | -0.18% |
| 0.7% | -0.16% | -0.19% | -0.16% |

Current-param outcome mix: TP `20.04%`, SL `39.17%`, BE `39.67%`, timeout `1.12%`.
Current-param MFE avg/p50/p75/p90: `0.71%` / `0.63%` / `1.20%` / `1.50%`.
Conclusion: BE is useful only if its trigger improves net_pnl versus no-BE; otherwise it converts many trades with real MFE into zero-gross exits while fees remain.

## H3: TP Too Far

| TP | best net_pnl | avg net_pnl | best realized_net |
| ---: | ---: | ---: | ---: |
| 0.70% | -0.16% | -0.19% | -0.16% |
| 0.80% | -0.17% | -0.20% | -0.17% |
| 1.00% | -0.17% | -0.21% | -0.17% |
| 1.20% | -0.18% | -0.22% | -0.18% |
| 1.50% | -0.17% | -0.22% | -0.17% |

Conclusion: compare the best rows above with TP `1.5%`; if smaller TP dominates, current TP is beyond typical post-entry MFE.

## H4: Explosion Size Quantiles

Quantile cuts by absolute explosion size: Q25 `0.91%`, Q50 `1.08%`, Q75 `1.39%`.

| bucket | n | WR 3m | avg 3m | net 3m |
| --- | ---: | ---: | ---: | ---: |
| Q1 small | 246 | 41.46% | -0.16% | -0.36% |
| Q2 | 246 | 55.69% | 0.05% | -0.15% |
| Q3 | 245 | 56.73% | 0.13% | -0.07% |
| Q4 large | 246 | 57.32% | 0.13% | -0.07% |

## H5: Hour / Session

| session UTC | n | WR 3m | avg 3m | net 3m |
| --- | ---: | ---: | ---: | ---: |
| Asia 00-06 | 229 | 52.84% | 0.05% | -0.15% |
| EU 07-15 | 509 | 50.88% | 0.02% | -0.18% |
| US 16-23 | 245 | 56.73% | 0.05% | -0.15% |

## H6: Consecutive Explosions

| last 5m context | n | WR 3m | avg 3m | net 3m |
| --- | ---: | ---: | ---: | ---: |
| 0-1 prior explosions | 801 | 53.31% | 0.07% | -0.13% |
| 2+ prior explosions | 182 | 50.55% | -0.10% | -0.30% |

## H7: Direction Asymmetry

| side | n | WR 3m | avg 3m | net 3m |
| --- | ---: | ---: | ---: | ---: |
| fade up candle (short) | 481 | 51.56% | -0.02% | -0.22% |
| fade down candle (long) | 502 | 53.98% | 0.09% | -0.11% |

## H8: Entry Timing

| entry timing | n | WR 3m | avg 3m | net 3m |
| --- | ---: | ---: | ---: | ---: |
| close | 983 | 52.80% | 0.04% | -0.16% |
| after 1m | 983 | 53.71% | 0.09% | -0.11% |
| after 2m | 982 | 51.02% | 0.06% | -0.14% |

## Final Conclusion

The small profit is primarily a fee-hurdle and exit-shape problem: many BILL reversals are directionally correct but too small for `0.20%` round-trip fees plus a distant TP.
Next config experiment should prefer only parameter combinations with positive tick-replay net_pnl, and pair inclusion should require positive net edge, not just gross WR.
