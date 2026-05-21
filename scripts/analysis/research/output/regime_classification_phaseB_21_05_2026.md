# Regime Classification Audit Phase B - 21.05.2026

Checkpoint result: only Part A is executed here. Per-regime model fitting, peak-entry filters, and impulse model tests are intentionally not run until the corrected regime labels are confirmed.

Replay period: `2026-05-04T19:00:00Z` to `2026-05-14T19:00:00Z`.
Universe: `29` requested, `28` decision-active symbols, `26908` engine decisions.

## Corrected Regime Definitions

- `RANGING`: low directional pressure, flat 15m slope, BB corridor, and limited daily range. This is the only true mean-reversion regime.
- `DRIFT`: moderate ADX/DI directional walk with contained BB width and daily range. This preserves the current DRIFT x FAST cell.
- `TRENDING`: directional or expanding swing/trend. High ADX/DI, high 15m/1h slope, wide BB, high daily range, or 4H conflict moves old false-RANGING labels out of range.
- `CHOPPY`: leftover/noise where neither range nor directional structure is clean enough.

## Old vs Corrected Counts

| regime | old count | corrected count | delta |
| --- | ---: | ---: | ---: |
| CHOPPY | 598 | 1178 | 580 |
| DRIFT | 7280 | 3278 | -4002 |
| RANGING | 12066 | 1388 | -10678 |
| TRENDING | 6964 | 21064 | 14100 |

## Old -> Corrected Crosstab

| old | corrected | count | share |
| --- | ---: | ---: | ---: |
| CHOPPY | RANGING | 1 | 0.00% |
| CHOPPY | TRENDING | 597 | 2.22% |
| DRIFT | CHOPPY | 20 | 0.07% |
| DRIFT | DRIFT | 3077 | 11.44% |
| DRIFT | RANGING | 463 | 1.72% |
| DRIFT | TRENDING | 3720 | 13.82% |
| RANGING | CHOPPY | 864 | 3.21% |
| RANGING | DRIFT | 23 | 0.09% |
| RANGING | RANGING | 922 | 3.43% |
| RANGING | TRENDING | 10257 | 38.12% |
| TRENDING | CHOPPY | 294 | 1.09% |
| TRENDING | DRIFT | 178 | 0.66% |
| TRENDING | RANGING | 2 | 0.01% |
| TRENDING | TRENDING | 6490 | 24.12% |

## Tradeable Movements: Old vs Corrected Regime

| regime | type | old moves | corrected moves | delta |
| --- | ---: | ---: | ---: | ---: |
| CHOPPY | FAST | 7 | 5 | -2 |
| CHOPPY | SWING | 48 | 51 | 3 |
| DRIFT | FAST | 68 | 33 | -35 |
| DRIFT | SWING | 408 | 150 | -258 |
| RANGING | FAST | 109 | 11 | -98 |
| RANGING | SWING | 698 | 57 | -641 |
| TRENDING | FAST | 67 | 202 | 135 |
| TRENDING | SWING | 446 | 1342 | 896 |

## Feature Signature

| bucket | n | avg ADX1H | avg DI1H | avg slope15 | avg BB width | avg day range | avg vol |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| old RANGING | 12066 | 31.66 | 10.67 | 1.09 | 3.02 | 4.31 | 1.13 |
| old RANGING -> corrected TRENDING | 10257 | 32.76 | 11.42 | 1.42 | 3.29 | 4.84 | 1.13 |
| corrected RANGING | 1388 | 18.08 | 3.96 | -0.87 | 1.58 | 1.14 | 1.15 |
| corrected TRENDING | 21064 | 32.64 | 14.09 | 0.69 | 3.83 | 5.32 | 1.17 |
| corrected DRIFT | 3278 | 22.09 | 10.63 | 1.03 | 1.90 | 1.48 | 1.15 |

## Problem Examples

| symbol | ts | type | side | move | old | corrected | reason |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BSB-USDT-SWAP | 2026-05-08T15:30:00Z | SWING | short | 28.05% | RANGING | TRENDING | directional_or_expanding_swing_not_range |
| BSB-USDT-SWAP | 2026-05-08T18:45:00Z | SWING | long | 26.19% | RANGING | TRENDING | directional_or_expanding_swing_not_range |
| BSB-USDT-SWAP | 2026-05-13T16:15:00Z | SWING | long | 14.70% | RANGING | TRENDING | directional_or_expanding_swing_not_range |
| BSB-USDT-SWAP | 2026-05-04T23:00:00Z | SWING | long | 13.80% | RANGING | TRENDING | directional_or_expanding_swing_not_range |
| BSB-USDT-SWAP | 2026-05-08T06:15:00Z | SWING | long | 13.60% | RANGING | TRENDING | directional_or_expanding_swing_not_range |
| BSB-USDT-SWAP | 2026-05-05T04:45:00Z | SWING | short | 13.39% | RANGING | TRENDING | directional_or_expanding_swing_not_range |
| BSB-USDT-SWAP | 2026-05-08T11:30:00Z | SWING | long | 12.60% | RANGING | TRENDING | directional_or_expanding_swing_not_range |
| BSB-USDT-SWAP | 2026-05-05T16:45:00Z | FAST | short | 12.57% | RANGING | TRENDING | expanding_move_fallback |

## Verdict For Checkpoint

- Old `RANGING` is too broad: `11144` of `12066` old-RANGING decisions are reclassified as directional/expanding, mostly `TRENDING`.
- The corrected `RANGING` definition is intentionally narrow. If the trader wants those sharp swings traded as fades, that should be a separate `TRENDING/IMPULSE` or exhaustion model, not the range bucket.
- The BSB-style sharp drop case class belongs outside `RANGING`; by the corrected definition it is directional expansion.

## Stop Here

Please review the corrected labels and PNG examples. If these regime labels are acceptable, Phase B can continue to per-regime entry/exit modelling. If not, adjust the definitions first.
