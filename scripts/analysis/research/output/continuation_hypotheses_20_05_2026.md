# Continuation Hypotheses - 20.05.2026

Uses saved continuation event MFE at `0.05%` entry slippage and `20m` horizon. `MFE-fee` is not executable PnL; it is a hurdle check against `0.20%` taker fees.

## C1/C2: Repeated Explosions As Continuation Regime

| group | n | avg MFE | p50 MFE | >=0.7 | >=1.0 | MFE-fee |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0-1 prior explosions in 5m | 4863 | 1.85% | 1.31% | 69.13% | 59.33% | 1.65% |
| 2+ prior explosions in 5m | 1370 | 2.54% | 1.87% | 78.83% | 71.09% | 2.34% |

## C3: Explosion Size

| group | n | avg MFE | p50 MFE | >=0.7 | >=1.0 | MFE-fee |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| small <1.1% | 1851 | 1.86% | 1.34% | 69.85% | 59.10% | 1.66% |
| medium 1.1-1.5% | 1986 | 1.79% | 1.29% | 69.13% | 59.11% | 1.59% |
| large >=1.5% | 2396 | 2.28% | 1.61% | 74.12% | 66.40% | 2.08% |

## C4: Single Impulse Close Location

| group | n | avg MFE | p50 MFE | >=0.7 | >=1.0 | MFE-fee |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| directional close ok | 2457 | 2.04% | 1.47% | 72.24% | 62.76% | 1.84% |
| rejection close | 1005 | 2.15% | 1.52% | 72.14% | 62.49% | 1.95% |

## C5: Staircase vs Single Spike

| group | n | avg MFE | p50 MFE | >=0.7 | >=1.0 | MFE-fee |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| single impulse | 3462 | 2.08% | 1.49% | 72.21% | 62.68% | 1.88% |
| staircase | 2771 | 1.91% | 1.36% | 70.08% | 60.95% | 1.71% |

## C6: Session Dependence

| group | n | avg MFE | p50 MFE | >=0.7 | >=1.0 | MFE-fee |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Asia 00-06 | 1838 | 2.18% | 1.56% | 73.61% | 65.07% | 1.98% |
| EU 07-15 | 2942 | 1.93% | 1.43% | 71.35% | 62.41% | 1.73% |
| US 16-23 | 1453 | 1.93% | 1.23% | 68.13% | 56.92% | 1.73% |

## C7: Network Alignment (BTC)

| group | n | avg MFE | p50 MFE | >=0.7 | >=1.0 | MFE-fee |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| isolated | 6174 | 2.00% | 1.43% | 71.36% | 62.07% | 1.80% |
| aligned | 57 | 1.56% | 0.89% | 59.65% | 43.86% | 1.36% |
| opposite | 2 | 4.19% | 4.19% | 100.00% | 100.00% | 3.99% |

## C7: Network Alignment (SOL)

| group | n | avg MFE | p50 MFE | >=0.7 | >=1.0 | MFE-fee |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| isolated | 6116 | 2.00% | 1.43% | 71.47% | 62.13% | 1.80% |
| aligned | 101 | 1.41% | 0.92% | 58.42% | 46.53% | 1.21% |
| opposite | 16 | 4.47% | 3.74% | 75.00% | 75.00% | 4.27% |

## C8: Entry Delay

Entry-delay replay was not re-run tick-by-tick in this pass. The actionable proxy is that MFE is large while executable exit grids are negative, so the next test should focus on pullback/confirmation entries rather than more exit tuning at signal close.

## Conclusion

Continuation has deeper MFE than reversal, but the tested signal-close entries and exit modes still fail after fee/slippage. The next research branch should test delayed/pullback entries, not immediate config changes.
