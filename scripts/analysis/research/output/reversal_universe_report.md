# Reversal Scalper Universe Scan

Universe source: `E:\trading-data\ticks`. Each directory is treated as one pair.
Explosive 1m candle threshold: `abs(price_change_pct) >= 0.8%`.
Reversal test: after an explosive candle closes, wait one full 1m bar, enter against the explosion at that bar close, then measure forward return.

- scanned pairs: `46`
- eligible pairs (`days >= 3` and `explosions >= 10`): `13`
- excluded pairs: `33`

## Universe Table

| pair | days | bars | explosions | exp/day | long_pct | osc_median | rev3_n | rev3_WR | rev3_avg | best_hold | verdict | note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| BILL-USDT-SWAP | 9 | 10234 | 895 | 99.44 | 48.83% | 11.00m | 895 | 54.53% | 0.11% | 10m | noise | usable |
| EDEN-USDT-SWAP | 3 | 2363 | 248 | 82.67 | 50.40% | 10.00m | 248 | 50.81% | 0.07% | 2m | weak_reversal | usable |
| TRUTH-USDT-SWAP | 9 | 10231 | 692 | 76.89 | 48.84% | 9.00m | 689 | 51.96% | 0.08% | 1m | weak_reversal | usable |
| UB-USDT-SWAP | 7 | 6802 | 389 | 55.57 | 50.64% | 12.00m | 389 | 51.67% | 0.08% | 10m | weak_reversal | usable |
| AI-USDT-SWAP | 5 | 5509 | 220 | 44.00 | 49.55% | 11.00m | 220 | 49.09% | -0.02% | 10m | noise | usable |
| SPACE-USDT-SWAP | 3 | 3151 | 93 | 31.00 | 49.46% | 23.00m | 93 | 46.24% | 0.05% | 3m | noise | usable |
| SAHARA-USDT-SWAP | 7 | 7080 | 108 | 15.43 | 43.52% | 48.50m | 108 | 50.93% | 0.01% | 5m | weak_reversal | usable |
| JELLYJELLY-USDT-SWAP | 3 | 2226 | 36 | 12.00 | 44.44% | 84.00m | 36 | 55.56% | 0.11% | 10m | strong_reversal | usable |
| BASED-USDT-SWAP | 7 | 5818 | 71 | 10.14 | 43.66% | 78.50m | 70 | 42.86% | -0.08% | 10m | continuation | usable |
| TURBO-USDT-SWAP | 9 | 10145 | 26 | 2.89 | 34.62% | 16.00m | 26 | 46.15% | -0.15% | 10m | noise | usable |
| NOT-USDT-SWAP | 9 | 10163 | 19 | 2.11 | 42.11% | 148.50m | 19 | 63.16% | 0.25% | 10m | strong_reversal | preliminary |
| BOME-USDT-SWAP | 9 | 10162 | 17 | 1.89 | 29.41% | 164.50m | 17 | 58.82% | 0.09% | 10m | weak_reversal | preliminary |
| NEIRO-USDT-SWAP | 9 | 10213 | 10 | 1.11 | 50.00% | 359.00m | 10 | 50.00% | 0.01% | 10m | noise | preliminary |

## Excluded Pairs

| pair | days | explosions | reason |
| --- | ---: | ---: | --- |
| BONK-USDT-SWAP | 9 | 9 | explosions<10 |
| SATS-USDT-SWAP | 9 | 9 | explosions<10 |
| FLOKI-USDT-SWAP | 9 | 8 | explosions<10 |
| HMSTR-USDT-SWAP | 9 | 8 | explosions<10 |
| MEME-USDT-SWAP | 9 | 7 | explosions<10 |
| MEW-USDT-SWAP | 9 | 7 | explosions<10 |
| PENGU-USDT-SWAP | 9 | 7 | explosions<10 |
| PUMP-USDT-SWAP | 9 | 7 | explosions<10 |
| SOL-USDT-SWAP | 9 | 6 | explosions<10 |
| ADA-USDT-SWAP | 9 | 5 | explosions<10 |
| DOGE-USDT-SWAP | 9 | 5 | explosions<10 |
| GALA-USDT-SWAP | 9 | 5 | explosions<10 |
| LINEA-USDT-SWAP | 9 | 5 | explosions<10 |
| PEPE-USDT-SWAP | 9 | 5 | explosions<10 |
| SHIB-USDT-SWAP | 9 | 4 | explosions<10 |
| ETH-USDT-SWAP | 9 | 3 | explosions<10 |
| XRP-USDT-SWAP | 9 | 2 | explosions<10 |
| BTC-USDT-SWAP | 9 | 0 | explosions<10 |
| KAT-USDT-SWAP | 5 | 0 | explosions<10 |
| CHZ-USDT-SWAP | 4 | 9 | explosions<10 |
| BABY-USDT-SWAP | 4 | 6 | explosions<10 |
| PEOPLE-USDT-SWAP | 3 | 1 | explosions<10 |
| BSB-USDT-SWAP | 2 | 97 | days<3 |
| OFC-USDT-SWAP | 2 | 39 | days<3 |
| USELESS-USDT-SWAP | 2 | 29 | days<3 |
| MOVE-USDT-SWAP | 2 | 22 | days<3 |
| GPS-USDT-SWAP | 2 | 18 | days<3 |
| AZTEC-USDT-SWAP | 2 | 9 | days<3, explosions<10 |
| CHIP-USDT-SWAP | 2 | 5 | days<3, explosions<10 |
| LAYER-USDT-SWAP | 2 | 4 | days<3, explosions<10 |
| DOOD-USDT-SWAP | 2 | 0 | days<3, explosions<10 |
| RLS-USDT-SWAP | 1 | 14 | days<3 |
| BIO-USDT-SWAP | 1 | 2 | days<3, explosions<10 |

## Top 10 Reversal Candidates By 3m Avg Return

### NOT-USDT-SWAP

- verdict: `strong_reversal`
- days: `9`, explosions: `19`, exp/day: `2.11`
- explosion size avg/p75/p90: `1.29%` / `1.40%` / `1.88%`
- oscillation median next opposite: `148.50m`

| hold | n | WR | avg_return |
| ---: | ---: | ---: | ---: |
| 1m | 19 | 47.37% | 0.16% |
| 2m | 19 | 52.63% | 0.18% |
| 3m | 19 | 63.16% | 0.25% |
| 5m | 19 | 63.16% | 0.41% |
| 10m | 19 | 68.42% | 0.56% |

UTC hour explosion counts: `1: 1, 5: 2, 6: 4, 7: 1, 9: 1, 11: 1, 12: 1, 13: 1, 16: 2, 19: 1, 23: 4`

### JELLYJELLY-USDT-SWAP

- verdict: `strong_reversal`
- days: `3`, explosions: `36`, exp/day: `12.00`
- explosion size avg/p75/p90: `1.18%` / `1.21%` / `1.90%`
- oscillation median next opposite: `84.00m`

| hold | n | WR | avg_return |
| ---: | ---: | ---: | ---: |
| 1m | 36 | 63.89% | 0.14% |
| 2m | 36 | 58.33% | 0.16% |
| 3m | 36 | 55.56% | 0.11% |
| 5m | 36 | 58.33% | 0.19% |
| 10m | 36 | 47.22% | 0.32% |

UTC hour explosion counts: `1: 1, 4: 1, 5: 3, 6: 5, 7: 2, 8: 2, 10: 1, 12: 3, 13: 6, 14: 4, 15: 3, 16: 3, 23: 2`

### BILL-USDT-SWAP

- verdict: `noise`
- days: `9`, explosions: `895`, exp/day: `99.44`
- explosion size avg/p75/p90: `1.28%` / `1.39%` / `2.00%`
- oscillation median next opposite: `11.00m`

| hold | n | WR | avg_return |
| ---: | ---: | ---: | ---: |
| 1m | 895 | 54.64% | 0.08% |
| 2m | 895 | 52.18% | 0.07% |
| 3m | 895 | 54.53% | 0.11% |
| 5m | 893 | 55.21% | 0.17% |
| 10m | 887 | 54.11% | 0.20% |

UTC hour explosion counts: `0: 18, 1: 28, 2: 19, 3: 32, 4: 15, 5: 40, 6: 41, 7: 65, 8: 59, 9: 64, 10: 60, 11: 51, 12: 60, 13: 46, 14: 36, 15: 51, 16: 60, 17: 37, 18: 22, 19: 33, 20: 9, 21: 21, 22: 11, 23: 17`

### BOME-USDT-SWAP

- verdict: `weak_reversal`
- days: `9`, explosions: `17`, exp/day: `1.89`
- explosion size avg/p75/p90: `1.20%` / `1.26%` / `1.85%`
- oscillation median next opposite: `164.50m`

| hold | n | WR | avg_return |
| ---: | ---: | ---: | ---: |
| 1m | 17 | 58.82% | 0.13% |
| 2m | 17 | 64.71% | 0.12% |
| 3m | 17 | 58.82% | 0.09% |
| 5m | 17 | 58.82% | 0.08% |
| 10m | 17 | 64.71% | 0.38% |

UTC hour explosion counts: `7: 2, 8: 1, 10: 1, 11: 2, 12: 1, 13: 3, 14: 1, 15: 1, 19: 1, 23: 4`

### UB-USDT-SWAP

- verdict: `weak_reversal`
- days: `7`, explosions: `389`, exp/day: `55.57`
- explosion size avg/p75/p90: `1.25%` / `1.34%` / `1.88%`
- oscillation median next opposite: `12.00m`

| hold | n | WR | avg_return |
| ---: | ---: | ---: | ---: |
| 1m | 389 | 52.70% | 0.06% |
| 2m | 389 | 54.76% | 0.05% |
| 3m | 389 | 51.67% | 0.08% |
| 5m | 389 | 55.78% | 0.16% |
| 10m | 389 | 55.27% | 0.28% |

UTC hour explosion counts: `0: 17, 1: 12, 2: 18, 3: 14, 4: 10, 5: 6, 6: 28, 7: 9, 8: 19, 9: 27, 10: 22, 11: 35, 12: 24, 13: 20, 14: 19, 15: 10, 16: 17, 17: 22, 18: 11, 19: 8, 20: 11, 21: 11, 22: 6, 23: 13`

### TRUTH-USDT-SWAP

- verdict: `weak_reversal`
- days: `9`, explosions: `692`, exp/day: `76.89`
- explosion size avg/p75/p90: `1.28%` / `1.42%` / `1.88%`
- oscillation median next opposite: `9.00m`

| hold | n | WR | avg_return |
| ---: | ---: | ---: | ---: |
| 1m | 691 | 57.45% | 0.11% |
| 2m | 690 | 53.91% | 0.09% |
| 3m | 689 | 51.96% | 0.08% |
| 5m | 689 | 52.54% | 0.10% |
| 10m | 689 | 52.10% | 0.10% |

UTC hour explosion counts: `0: 22, 1: 7, 2: 28, 3: 35, 4: 41, 5: 43, 6: 34, 7: 36, 8: 30, 9: 39, 10: 35, 11: 32, 12: 36, 13: 35, 14: 42, 15: 32, 16: 54, 17: 29, 18: 28, 19: 7, 20: 12, 21: 10, 22: 15, 23: 10`

### EDEN-USDT-SWAP

- verdict: `weak_reversal`
- days: `3`, explosions: `248`, exp/day: `82.67`
- explosion size avg/p75/p90: `1.21%` / `1.30%` / `1.74%`
- oscillation median next opposite: `10.00m`

| hold | n | WR | avg_return |
| ---: | ---: | ---: | ---: |
| 1m | 248 | 56.85% | 0.06% |
| 2m | 248 | 54.84% | 0.09% |
| 3m | 248 | 50.81% | 0.07% |
| 5m | 248 | 51.21% | 0.03% |
| 10m | 248 | 52.42% | 0.07% |

UTC hour explosion counts: `0: 10, 1: 10, 2: 8, 3: 16, 4: 20, 5: 18, 6: 16, 7: 21, 8: 23, 9: 19, 10: 16, 11: 14, 12: 13, 13: 8, 14: 1, 15: 2, 19: 4, 20: 9, 21: 3, 22: 5, 23: 12`

### SPACE-USDT-SWAP

- verdict: `noise`
- days: `3`, explosions: `93`, exp/day: `31.00`
- explosion size avg/p75/p90: `1.22%` / `1.36%` / `1.82%`
- oscillation median next opposite: `23.00m`

| hold | n | WR | avg_return |
| ---: | ---: | ---: | ---: |
| 1m | 93 | 51.61% | 0.02% |
| 2m | 93 | 46.24% | 0.05% |
| 3m | 93 | 46.24% | 0.05% |
| 5m | 93 | 51.61% | 0.05% |
| 10m | 93 | 52.69% | 0.04% |

UTC hour explosion counts: `0: 1, 1: 3, 4: 1, 5: 1, 6: 3, 7: 9, 8: 8, 9: 3, 10: 4, 11: 10, 12: 9, 13: 5, 14: 3, 15: 14, 16: 4, 17: 5, 18: 3, 19: 1, 21: 1, 22: 1, 23: 4`

### SAHARA-USDT-SWAP

- verdict: `weak_reversal`
- days: `7`, explosions: `108`, exp/day: `15.43`
- explosion size avg/p75/p90: `1.15%` / `1.24%` / `1.69%`
- oscillation median next opposite: `48.50m`

| hold | n | WR | avg_return |
| ---: | ---: | ---: | ---: |
| 1m | 108 | 53.70% | 0.04% |
| 2m | 108 | 57.41% | 0.06% |
| 3m | 108 | 50.93% | 0.01% |
| 5m | 108 | 58.33% | 0.11% |
| 10m | 106 | 51.89% | 0.09% |

UTC hour explosion counts: `0: 5, 1: 3, 2: 1, 3: 2, 4: 1, 5: 6, 6: 5, 7: 9, 8: 5, 9: 10, 10: 13, 11: 6, 12: 4, 13: 9, 14: 9, 15: 3, 17: 1, 18: 5, 19: 4, 20: 1, 21: 2, 22: 3, 23: 1`

### NEIRO-USDT-SWAP

- verdict: `noise`
- days: `9`, explosions: `10`, exp/day: `1.11`
- explosion size avg/p75/p90: `1.46%` / `1.50%` / `2.54%`
- oscillation median next opposite: `359.00m`

| hold | n | WR | avg_return |
| ---: | ---: | ---: | ---: |
| 1m | 10 | 50.00% | 0.12% |
| 2m | 10 | 40.00% | 0.01% |
| 3m | 10 | 50.00% | 0.01% |
| 5m | 10 | 50.00% | 0.11% |
| 10m | 10 | 60.00% | 0.25% |

UTC hour explosion counts: `11: 2, 12: 1, 13: 2, 19: 1, 23: 4`

## Intra-Candle Entry Research

Method: for every explosive 1m candle, inspect ticks inside that candle. Trigger price is the first tick where the move from candle open reaches the threshold in the final explosion direction.

Tested triggers:

- `move_0p5_20s`: price moved at least `0.5%` within first `20s`.
- `move_0p5_20s_vol2x`: same, plus first-20s volume is at least `2x` the pair's average 20s volume.
- `move_0p3_10s`: aggressive trigger, price moved at least `0.3%` within first `10s`.

Returns are measured in the explosion direction. `edge_vs_close` compares early entry with entering at the same candle close on the same event set.

### Trigger Summary By Pair

| pair | trigger | n | fire_pct | avg_sec | to_close | intra_1m | close_1m | edge_1m | intra_3m | close_3m | edge_3m |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BILL-USDT-SWAP | move_0p5_20s | 501 | 55.98% | 9.15s | 0.86% | 0.84% | -0.01% | 0.86% | 0.74% | -0.12% | 0.86% |
| BILL-USDT-SWAP | move_0p5_20s_vol2x | 284 | 31.73% | 7.62s | 1.01% | 0.95% | -0.06% | 1.01% | 0.90% | -0.11% | 1.01% |
| BILL-USDT-SWAP | move_0p3_10s | 501 | 55.98% | 4.39s | 0.99% | 0.95% | -0.04% | 0.99% | 0.85% | -0.14% | 0.99% |
| EDEN-USDT-SWAP | move_0p5_20s | 157 | 63.31% | 8.45s | 0.75% | 0.82% | 0.06% | 0.75% | 0.75% | -0.00% | 0.75% |
| EDEN-USDT-SWAP | move_0p5_20s_vol2x | 68 | 27.42% | 6.04s | 0.91% | 0.96% | 0.04% | 0.92% | 0.98% | 0.06% | 0.91% |
| EDEN-USDT-SWAP | move_0p3_10s | 163 | 65.73% | 3.67s | 0.93% | 0.97% | 0.04% | 0.93% | 0.90% | -0.03% | 0.93% |
| TRUTH-USDT-SWAP | move_0p5_20s | 447 | 64.60% | 7.43s | 0.81% | 0.76% | -0.05% | 0.81% | 0.69% | -0.12% | 0.81% |
| TRUTH-USDT-SWAP | move_0p5_20s_vol2x | 307 | 44.36% | 6.59s | 0.90% | 0.84% | -0.07% | 0.90% | 0.75% | -0.16% | 0.91% |
| TRUTH-USDT-SWAP | move_0p3_10s | 455 | 65.75% | 2.98s | 1.00% | 0.99% | -0.01% | 1.00% | 0.87% | -0.13% | 1.00% |
| UB-USDT-SWAP | move_0p5_20s | 221 | 56.81% | 8.54s | 0.85% | 0.96% | 0.11% | 0.85% | 0.93% | 0.08% | 0.85% |
| UB-USDT-SWAP | move_0p5_20s_vol2x | 130 | 33.42% | 6.90s | 1.01% | 1.16% | 0.16% | 1.01% | 1.27% | 0.27% | 1.00% |
| UB-USDT-SWAP | move_0p3_10s | 226 | 58.10% | 3.76s | 1.00% | 1.12% | 0.12% | 1.00% | 1.06% | 0.07% | 0.99% |
| AI-USDT-SWAP | move_0p5_20s | 120 | 54.55% | 11.08s | 0.84% | 0.75% | -0.09% | 0.84% | 0.78% | -0.06% | 0.84% |
| AI-USDT-SWAP | move_0p5_20s_vol2x | 93 | 42.27% | 10.24s | 0.94% | 0.79% | -0.14% | 0.94% | 0.83% | -0.11% | 0.94% |
| AI-USDT-SWAP | move_0p3_10s | 99 | 45.00% | 4.55s | 1.02% | 0.90% | -0.12% | 1.02% | 0.97% | -0.05% | 1.02% |
| SPACE-USDT-SWAP | move_0p5_20s | 56 | 60.22% | 8.13s | 0.78% | 0.68% | -0.10% | 0.78% | 0.66% | -0.12% | 0.77% |
| SPACE-USDT-SWAP | move_0p5_20s_vol2x | 47 | 50.54% | 7.46s | 0.84% | 0.75% | -0.09% | 0.84% | 0.76% | -0.08% | 0.84% |
| SPACE-USDT-SWAP | move_0p3_10s | 45 | 48.39% | 3.14s | 1.04% | 0.94% | -0.11% | 1.04% | 0.93% | -0.11% | 1.04% |
| SAHARA-USDT-SWAP | move_0p5_20s | 54 | 50.00% | 10.87s | 0.68% | 0.80% | 0.12% | 0.68% | 0.73% | 0.05% | 0.68% |
| SAHARA-USDT-SWAP | move_0p5_20s_vol2x | 50 | 46.30% | 10.60s | 0.70% | 0.80% | 0.10% | 0.70% | 0.75% | 0.04% | 0.70% |
| SAHARA-USDT-SWAP | move_0p3_10s | 44 | 40.74% | 4.75s | 0.84% | 0.94% | 0.10% | 0.84% | 0.93% | 0.09% | 0.84% |
| JELLYJELLY-USDT-SWAP | move_0p5_20s | 13 | 36.11% | 9.40s | 0.77% | 0.85% | 0.08% | 0.77% | 0.73% | -0.04% | 0.77% |
| JELLYJELLY-USDT-SWAP | move_0p5_20s_vol2x | 13 | 36.11% | 9.40s | 0.77% | 0.85% | 0.08% | 0.77% | 0.73% | -0.04% | 0.77% |
| JELLYJELLY-USDT-SWAP | move_0p3_10s | 17 | 47.22% | 4.90s | 0.96% | 1.29% | 0.32% | 0.96% | 1.19% | 0.23% | 0.96% |
| BASED-USDT-SWAP | move_0p5_20s | 31 | 43.66% | 11.35s | 0.94% | 1.15% | 0.22% | 0.94% | 1.26% | 0.32% | 0.94% |
| BASED-USDT-SWAP | move_0p5_20s_vol2x | 26 | 36.62% | 10.89s | 1.02% | 1.30% | 0.29% | 1.01% | 1.41% | 0.40% | 1.01% |
| BASED-USDT-SWAP | move_0p3_10s | 25 | 35.21% | 4.40s | 1.17% | 1.20% | 0.03% | 1.17% | 1.18% | 0.01% | 1.17% |
| TURBO-USDT-SWAP | move_0p5_20s | 11 | 42.31% | 9.13s | 0.72% | 0.51% | -0.21% | 0.72% | 0.82% | 0.10% | 0.72% |
| TURBO-USDT-SWAP | move_0p5_20s_vol2x | 11 | 42.31% | 9.13s | 0.72% | 0.51% | -0.21% | 0.72% | 0.82% | 0.10% | 0.72% |
| TURBO-USDT-SWAP | move_0p3_10s | 11 | 42.31% | 4.34s | 0.81% | 0.58% | -0.23% | 0.81% | 0.73% | -0.08% | 0.81% |
| NOT-USDT-SWAP | move_0p5_20s | 10 | 52.63% | 9.94s | 0.76% | 0.50% | -0.26% | 0.76% | 0.18% | -0.58% | 0.76% |
| NOT-USDT-SWAP | move_0p5_20s_vol2x | 10 | 52.63% | 9.94s | 0.76% | 0.50% | -0.26% | 0.76% | 0.18% | -0.58% | 0.76% |
| NOT-USDT-SWAP | move_0p3_10s | 6 | 31.58% | 3.03s | 1.09% | 0.77% | -0.31% | 1.08% | 0.33% | -0.76% | 1.08% |
| BOME-USDT-SWAP | move_0p5_20s | 5 | 29.41% | 7.21s | 0.76% | 0.51% | -0.25% | 0.76% | 0.33% | -0.43% | 0.76% |
| BOME-USDT-SWAP | move_0p5_20s_vol2x | 5 | 29.41% | 7.21s | 0.76% | 0.51% | -0.25% | 0.76% | 0.33% | -0.43% | 0.76% |
| BOME-USDT-SWAP | move_0p3_10s | 5 | 29.41% | 2.76s | 0.96% | 0.70% | -0.25% | 0.96% | 0.53% | -0.43% | 0.96% |
| NEIRO-USDT-SWAP | move_0p5_20s | 5 | 50.00% | 9.23s | 1.25% | 0.51% | -0.76% | 1.26% | 0.50% | -0.76% | 1.26% |
| NEIRO-USDT-SWAP | move_0p5_20s_vol2x | 5 | 50.00% | 9.23s | 1.25% | 0.51% | -0.76% | 1.26% | 0.50% | -0.76% | 1.26% |
| NEIRO-USDT-SWAP | move_0p3_10s | 5 | 50.00% | 2.60s | 1.45% | 0.71% | -0.76% | 1.46% | 0.70% | -0.76% | 1.46% |

### Threshold Hit Timing

| pair | threshold | n | median_sec | p75_sec | p90_sec |
| --- | ---: | ---: | ---: | ---: | ---: |
| BILL-USDT-SWAP | 0.3% | 895 | 8.39s | 19.20s | 33.19s |
| BILL-USDT-SWAP | 0.5% | 895 | 16.85s | 30.10s | 43.60s |
| BILL-USDT-SWAP | 0.7% | 895 | 24.82s | 39.79s | 50.40s |
| EDEN-USDT-SWAP | 0.3% | 248 | 5.94s | 15.19s | 33.23s |
| EDEN-USDT-SWAP | 0.5% | 248 | 14.27s | 27.56s | 41.31s |
| EDEN-USDT-SWAP | 0.7% | 248 | 24.16s | 38.04s | 49.09s |
| TRUTH-USDT-SWAP | 0.3% | 692 | 4.97s | 15.43s | 28.37s |
| TRUTH-USDT-SWAP | 0.5% | 692 | 12.18s | 26.22s | 42.04s |
| TRUTH-USDT-SWAP | 0.7% | 692 | 21.40s | 38.09s | 49.96s |
| UB-USDT-SWAP | 0.3% | 389 | 7.36s | 18.29s | 30.52s |
| UB-USDT-SWAP | 0.5% | 389 | 16.12s | 30.26s | 41.14s |
| UB-USDT-SWAP | 0.7% | 389 | 27.21s | 40.98s | 51.26s |
| AI-USDT-SWAP | 0.3% | 220 | 12.00s | 21.52s | 33.17s |
| AI-USDT-SWAP | 0.5% | 220 | 18.57s | 28.85s | 40.48s |
| AI-USDT-SWAP | 0.7% | 220 | 26.24s | 39.09s | 49.52s |
| SPACE-USDT-SWAP | 0.3% | 93 | 10.15s | 21.14s | 39.89s |
| SPACE-USDT-SWAP | 0.5% | 93 | 14.37s | 30.28s | 47.54s |
| SPACE-USDT-SWAP | 0.7% | 93 | 23.21s | 40.19s | 52.44s |
| SAHARA-USDT-SWAP | 0.3% | 108 | 13.93s | 24.54s | 41.61s |
| SAHARA-USDT-SWAP | 0.5% | 108 | 20.11s | 33.79s | 50.80s |
| SAHARA-USDT-SWAP | 0.7% | 108 | 28.59s | 41.13s | 54.51s |
| JELLYJELLY-USDT-SWAP | 0.3% | 36 | 15.37s | 22.66s | 42.77s |
| JELLYJELLY-USDT-SWAP | 0.5% | 36 | 21.49s | 39.65s | 49.17s |
| JELLYJELLY-USDT-SWAP | 0.7% | 36 | 30.15s | 46.15s | 54.38s |
| BASED-USDT-SWAP | 0.3% | 71 | 14.28s | 27.00s | 36.71s |
| BASED-USDT-SWAP | 0.5% | 71 | 23.44s | 40.72s | 49.14s |
| BASED-USDT-SWAP | 0.7% | 71 | 31.71s | 43.42s | 49.93s |
| TURBO-USDT-SWAP | 0.3% | 26 | 11.92s | 24.15s | 32.42s |
| TURBO-USDT-SWAP | 0.5% | 26 | 22.65s | 31.80s | 35.00s |
| TURBO-USDT-SWAP | 0.7% | 26 | 29.37s | 45.63s | 49.25s |
| NOT-USDT-SWAP | 0.3% | 19 | 17.55s | 22.81s | 35.17s |
| NOT-USDT-SWAP | 0.5% | 19 | 19.04s | 30.19s | 40.10s |
| NOT-USDT-SWAP | 0.7% | 19 | 26.57s | 40.72s | 56.49s |
| BOME-USDT-SWAP | 0.3% | 17 | 16.24s | 26.28s | 41.50s |
| BOME-USDT-SWAP | 0.5% | 17 | 27.49s | 40.38s | 52.27s |
| BOME-USDT-SWAP | 0.7% | 17 | 32.42s | 48.64s | 54.67s |
| NEIRO-USDT-SWAP | 0.3% | 10 | 14.97s | 26.04s | 28.33s |
| NEIRO-USDT-SWAP | 0.5% | 10 | 21.92s | 32.34s | 46.37s |
| NEIRO-USDT-SWAP | 0.7% | 10 | 25.97s | 38.93s | 48.56s |

### Dynamic SL Promotion

Question tested: after an intra-candle entry has positive MFE, can the stop be promoted to BE or positive lock so a later stop-out closes flat/green instead of red. This approximates the user's slippage concern: if the promoted stop is above entry for longs or below entry for shorts, execution can still be positive even when the stop is hit.
MFE and promoted-stop hits are evaluated in tick order after the trigger, not from full candle high/low.

Rules tested on the first 5 minutes after trigger:

- `be_after_0p5`: after MFE reaches `+0.5%`, promote SL to `0.0%`.
- `lock_0p2_after_0p7`: after MFE reaches `+0.7%`, promote SL to `+0.2%`.
- `lock_0p4_after_1p0`: after MFE reaches `+1.0%`, promote SL to `+0.4%`.

| pair | trigger | rule | hit_n | hit_pct | would_stop_n | would_stop_pct | stop_result | mfe_0_5m_avg |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BILL-USDT-SWAP | move_0p5_20s | be_after_0p5 | 497 | 99.20% | 317 | 63.27% | 0.00% | 2.12% |
| BILL-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 475 | 94.81% | 333 | 66.47% | 0.20% | 2.12% |
| BILL-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 414 | 82.63% | 283 | 56.49% | 0.40% | 2.12% |
| BILL-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 282 | 99.30% | 177 | 62.32% | 0.00% | 2.46% |
| BILL-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 277 | 97.54% | 198 | 69.72% | 0.20% | 2.46% |
| BILL-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 253 | 89.08% | 177 | 62.32% | 0.40% | 2.46% |
| BILL-USDT-SWAP | move_0p3_10s | be_after_0p5 | 501 | 100.00% | 283 | 56.49% | 0.00% | 2.28% |
| BILL-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 497 | 99.20% | 327 | 65.27% | 0.20% | 2.28% |
| BILL-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 456 | 91.02% | 302 | 60.28% | 0.40% | 2.28% |
| EDEN-USDT-SWAP | move_0p5_20s | be_after_0p5 | 154 | 98.09% | 90 | 57.32% | 0.00% | 1.99% |
| EDEN-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 146 | 92.99% | 96 | 61.15% | 0.20% | 1.99% |
| EDEN-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 128 | 81.53% | 83 | 52.87% | 0.40% | 1.99% |
| EDEN-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 68 | 100.00% | 37 | 54.41% | 0.00% | 2.28% |
| EDEN-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 67 | 98.53% | 46 | 67.65% | 0.20% | 2.28% |
| EDEN-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 62 | 91.18% | 45 | 66.18% | 0.40% | 2.28% |
| EDEN-USDT-SWAP | move_0p3_10s | be_after_0p5 | 163 | 100.00% | 89 | 54.60% | 0.00% | 2.12% |
| EDEN-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 159 | 97.55% | 97 | 59.51% | 0.20% | 2.12% |
| EDEN-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 145 | 88.96% | 96 | 58.90% | 0.40% | 2.12% |
| TRUTH-USDT-SWAP | move_0p5_20s | be_after_0p5 | 440 | 98.43% | 293 | 65.55% | 0.00% | 2.17% |
| TRUTH-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 415 | 92.84% | 295 | 66.00% | 0.20% | 2.17% |
| TRUTH-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 363 | 81.21% | 251 | 56.15% | 0.40% | 2.17% |
| TRUTH-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 305 | 99.35% | 206 | 67.10% | 0.00% | 2.39% |
| TRUTH-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 297 | 96.74% | 223 | 72.64% | 0.20% | 2.39% |
| TRUTH-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 267 | 86.97% | 198 | 64.50% | 0.40% | 2.39% |
| TRUTH-USDT-SWAP | move_0p3_10s | be_after_0p5 | 455 | 100.00% | 261 | 57.36% | 0.00% | 2.33% |
| TRUTH-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 447 | 98.24% | 293 | 64.40% | 0.20% | 2.33% |
| TRUTH-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 409 | 89.89% | 277 | 60.88% | 0.40% | 2.33% |
| UB-USDT-SWAP | move_0p5_20s | be_after_0p5 | 220 | 99.55% | 135 | 61.09% | 0.00% | 2.40% |
| UB-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 206 | 93.21% | 137 | 61.99% | 0.20% | 2.40% |
| UB-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 178 | 80.54% | 123 | 55.66% | 0.40% | 2.40% |
| UB-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 129 | 99.23% | 83 | 63.85% | 0.00% | 2.89% |
| UB-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 122 | 93.85% | 88 | 67.69% | 0.20% | 2.89% |
| UB-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 110 | 84.62% | 84 | 64.62% | 0.40% | 2.89% |
| UB-USDT-SWAP | move_0p3_10s | be_after_0p5 | 226 | 100.00% | 132 | 58.41% | 0.00% | 2.50% |
| UB-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 224 | 99.12% | 144 | 63.72% | 0.20% | 2.50% |
| UB-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 196 | 86.73% | 128 | 56.64% | 0.40% | 2.50% |
| AI-USDT-SWAP | move_0p5_20s | be_after_0p5 | 118 | 98.33% | 73 | 60.83% | 0.00% | 2.09% |
| AI-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 110 | 91.67% | 77 | 64.17% | 0.20% | 2.09% |
| AI-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 94 | 78.33% | 63 | 52.50% | 0.40% | 2.09% |
| AI-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 91 | 97.85% | 59 | 63.44% | 0.00% | 2.26% |
| AI-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 86 | 92.47% | 60 | 64.52% | 0.20% | 2.26% |
| AI-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 75 | 80.65% | 50 | 53.76% | 0.40% | 2.26% |
| AI-USDT-SWAP | move_0p3_10s | be_after_0p5 | 99 | 100.00% | 55 | 55.56% | 0.00% | 2.29% |
| AI-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 97 | 97.98% | 60 | 60.61% | 0.20% | 2.29% |
| AI-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 84 | 84.85% | 56 | 56.57% | 0.40% | 2.29% |
| SPACE-USDT-SWAP | move_0p5_20s | be_after_0p5 | 54 | 96.43% | 25 | 44.64% | 0.00% | 1.71% |
| SPACE-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 51 | 91.07% | 29 | 51.79% | 0.20% | 1.71% |
| SPACE-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 43 | 76.79% | 23 | 41.07% | 0.40% | 1.71% |
| SPACE-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 46 | 97.87% | 20 | 42.55% | 0.00% | 1.87% |
| SPACE-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 45 | 95.74% | 25 | 53.19% | 0.20% | 1.87% |
| SPACE-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 39 | 82.98% | 19 | 40.43% | 0.40% | 1.87% |
| SPACE-USDT-SWAP | move_0p3_10s | be_after_0p5 | 45 | 100.00% | 18 | 40.00% | 0.00% | 2.09% |
| SPACE-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 43 | 95.56% | 18 | 40.00% | 0.20% | 2.09% |
| SPACE-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 40 | 88.89% | 19 | 42.22% | 0.40% | 2.09% |
| SAHARA-USDT-SWAP | move_0p5_20s | be_after_0p5 | 52 | 96.30% | 19 | 35.19% | 0.00% | 1.76% |
| SAHARA-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 50 | 92.59% | 25 | 46.30% | 0.20% | 1.76% |
| SAHARA-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 41 | 75.93% | 20 | 37.04% | 0.40% | 1.76% |
| SAHARA-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 48 | 96.00% | 18 | 36.00% | 0.00% | 1.81% |
| SAHARA-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 47 | 94.00% | 24 | 48.00% | 0.20% | 1.81% |
| SAHARA-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 39 | 78.00% | 20 | 40.00% | 0.40% | 1.81% |
| SAHARA-USDT-SWAP | move_0p3_10s | be_after_0p5 | 44 | 100.00% | 15 | 34.09% | 0.00% | 1.89% |
| SAHARA-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 40 | 90.91% | 16 | 36.36% | 0.20% | 1.89% |
| SAHARA-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 36 | 81.82% | 18 | 40.91% | 0.40% | 1.89% |
| JELLYJELLY-USDT-SWAP | move_0p5_20s | be_after_0p5 | 13 | 100.00% | 3 | 23.08% | 0.00% | 1.86% |
| JELLYJELLY-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 11 | 84.62% | 3 | 23.08% | 0.20% | 1.86% |
| JELLYJELLY-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 10 | 76.92% | 3 | 23.08% | 0.40% | 1.86% |
| JELLYJELLY-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 13 | 100.00% | 3 | 23.08% | 0.00% | 1.86% |
| JELLYJELLY-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 11 | 84.62% | 3 | 23.08% | 0.20% | 1.86% |
| JELLYJELLY-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 10 | 76.92% | 3 | 23.08% | 0.40% | 1.86% |
| JELLYJELLY-USDT-SWAP | move_0p3_10s | be_after_0p5 | 17 | 100.00% | 4 | 23.53% | 0.00% | 2.15% |
| JELLYJELLY-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 17 | 100.00% | 4 | 23.53% | 0.20% | 2.15% |
| JELLYJELLY-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 14 | 82.35% | 3 | 17.65% | 0.40% | 2.15% |
| BASED-USDT-SWAP | move_0p5_20s | be_after_0p5 | 30 | 96.77% | 12 | 38.71% | 0.00% | 2.47% |
| BASED-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 25 | 80.65% | 12 | 38.71% | 0.20% | 2.47% |
| BASED-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 24 | 77.42% | 13 | 41.94% | 0.40% | 2.47% |
| BASED-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 26 | 100.00% | 10 | 38.46% | 0.00% | 2.77% |
| BASED-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 23 | 88.46% | 11 | 42.31% | 0.20% | 2.77% |
| BASED-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 22 | 84.62% | 12 | 46.15% | 0.40% | 2.77% |
| BASED-USDT-SWAP | move_0p3_10s | be_after_0p5 | 25 | 100.00% | 7 | 28.00% | 0.00% | 2.39% |
| BASED-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 25 | 100.00% | 9 | 36.00% | 0.20% | 2.39% |
| BASED-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 21 | 84.00% | 10 | 40.00% | 0.40% | 2.39% |
| TURBO-USDT-SWAP | move_0p5_20s | be_after_0p5 | 11 | 100.00% | 6 | 54.55% | 0.00% | 1.78% |
| TURBO-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 9 | 81.82% | 7 | 63.64% | 0.20% | 1.78% |
| TURBO-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 8 | 72.73% | 5 | 45.45% | 0.40% | 1.78% |
| TURBO-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 11 | 100.00% | 6 | 54.55% | 0.00% | 1.78% |
| TURBO-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 9 | 81.82% | 7 | 63.64% | 0.20% | 1.78% |
| TURBO-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 8 | 72.73% | 5 | 45.45% | 0.40% | 1.78% |
| TURBO-USDT-SWAP | move_0p3_10s | be_after_0p5 | 11 | 100.00% | 8 | 72.73% | 0.00% | 1.59% |
| TURBO-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 11 | 100.00% | 8 | 72.73% | 0.20% | 1.59% |
| TURBO-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 6 | 54.55% | 5 | 45.45% | 0.40% | 1.59% |
| NOT-USDT-SWAP | move_0p5_20s | be_after_0p5 | 9 | 90.00% | 7 | 70.00% | 0.00% | 1.06% |
| NOT-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 8 | 80.00% | 6 | 60.00% | 0.20% | 1.06% |
| NOT-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 4 | 40.00% | 3 | 30.00% | 0.40% | 1.06% |
| NOT-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 9 | 90.00% | 7 | 70.00% | 0.00% | 1.06% |
| NOT-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 8 | 80.00% | 6 | 60.00% | 0.20% | 1.06% |
| NOT-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 4 | 40.00% | 3 | 30.00% | 0.40% | 1.06% |
| NOT-USDT-SWAP | move_0p3_10s | be_after_0p5 | 6 | 100.00% | 5 | 83.33% | 0.00% | 1.39% |
| NOT-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 5 | 83.33% | 4 | 66.67% | 0.20% | 1.39% |
| NOT-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 5 | 83.33% | 4 | 66.67% | 0.40% | 1.39% |
| BOME-USDT-SWAP | move_0p5_20s | be_after_0p5 | 5 | 100.00% | 3 | 60.00% | 0.00% | 0.96% |
| BOME-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 3 | 60.00% | 1 | 20.00% | 0.20% | 0.96% |
| BOME-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 1 | 20.00% | 0 | 0.00% | n/a | 0.96% |
| BOME-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 5 | 100.00% | 3 | 60.00% | 0.00% | 0.96% |
| BOME-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 3 | 60.00% | 1 | 20.00% | 0.20% | 0.96% |
| BOME-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 1 | 20.00% | 0 | 0.00% | n/a | 0.96% |
| BOME-USDT-SWAP | move_0p3_10s | be_after_0p5 | 5 | 100.00% | 2 | 40.00% | 0.00% | 1.16% |
| BOME-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 5 | 100.00% | 3 | 60.00% | 0.20% | 1.16% |
| BOME-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 1 | 20.00% | 0 | 0.00% | n/a | 1.16% |
| NEIRO-USDT-SWAP | move_0p5_20s | be_after_0p5 | 4 | 80.00% | 2 | 40.00% | 0.00% | 1.43% |
| NEIRO-USDT-SWAP | move_0p5_20s | lock_0p2_after_0p7 | 3 | 60.00% | 1 | 20.00% | 0.20% | 1.43% |
| NEIRO-USDT-SWAP | move_0p5_20s | lock_0p4_after_1p0 | 2 | 40.00% | 0 | 0.00% | n/a | 1.43% |
| NEIRO-USDT-SWAP | move_0p5_20s_vol2x | be_after_0p5 | 4 | 80.00% | 2 | 40.00% | 0.00% | 1.43% |
| NEIRO-USDT-SWAP | move_0p5_20s_vol2x | lock_0p2_after_0p7 | 3 | 60.00% | 1 | 20.00% | 0.20% | 1.43% |
| NEIRO-USDT-SWAP | move_0p5_20s_vol2x | lock_0p4_after_1p0 | 2 | 40.00% | 0 | 0.00% | n/a | 1.43% |
| NEIRO-USDT-SWAP | move_0p3_10s | be_after_0p5 | 5 | 100.00% | 2 | 40.00% | 0.00% | 1.63% |
| NEIRO-USDT-SWAP | move_0p3_10s | lock_0p2_after_0p7 | 4 | 80.00% | 2 | 40.00% | 0.20% | 1.63% |
| NEIRO-USDT-SWAP | move_0p3_10s | lock_0p4_after_1p0 | 3 | 60.00% | 1 | 20.00% | 0.40% | 1.63% |

Interpretation: dynamic SL promotion is relevant when `hit_pct` is high. A promoted stop does not create the initial edge, but it can convert a later SL into BE or a small positive exit after the impulse has already paid enough MFE.

### WS Implementation Concept

Variant A - `confirm=0` candles:

- `ws_feed.py` already receives forming candle updates from OKX.
- Add a branch in `_on_candle_update()` for `confirm=0` updates.
- Track per-symbol current 1m candle open, latest close, elapsed seconds, and volume.
- Fire an early candidate when `abs(open -> current_close) >= threshold` and elapsed time is inside the tested window.
- Simpler integration, less moving state, and no separate trade-stream consumer.

Variant B - trades stream:

- Add a new component reading the same OKX trades channel as the recorder.
- Maintain rolling per-symbol 10s/20s open/current/volume state directly from trades.
- Fire when price move and volume trigger are met.
- More precise than `confirm=0`, but higher implementation risk: duplicate stream handling, state drift, race with recorder/orchestrator, and more noisy microstructure spikes.

Recommendation: start with Variant A. It is enough to test whether early entry adds more than `0.1%` edge over close-entry without adding a second real-time trades engine. Use Variant B only if `confirm=0` granularity is too slow or misses the measured trigger windows.

## Notes

- Pairs are never blended for verdicts; each verdict is per-pair.
- `preliminary` means the pair has fewer than 20 reversal rows even if it passed the minimum inclusion filter.
- A `continuation` verdict means the 3m reversal win rate is below 45%, so fading the explosion is probably the wrong side for that pair.
