# Reversal Universe V2 - 20.05.2026

Source: `E:\trading-data\ticks`. Parallel scan with `ThreadPoolExecutor`; threshold `abs(1m open->close) >= 0.8%`.
Eligibility: `days >= 3` and `explosions >= 10`. Fee model: `0.20%` round trip.

Universe metrics below use the original research delayed-entry method for comparability: enter against the explosion one full 1m bar after the explosive candle, then measure forward close-to-close return.

- scanned pairs: `49`
- eligible pairs: `16`
- net-positive eligible pairs on at least one hold: `7`
- excluded pairs: `33`

## Eligible Universe

| pair | days | explosions | exp/day | old_exp | rev3_WR | rev3_avg | rev3_net | best_hold | best_net | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| EDEN-USDT-SWAP | 4 | 438 | 109.50 | 248 | 50.68% | -0.02% | -0.22% | 1m | -0.14% | noise |
| BILL-USDT-SWAP | 10 | 983 | 98.30 | 895 | 53.71% | 0.09% | -0.11% | 10m | -0.02% | weak_reversal_fee_blocked |
| TRUTH-USDT-SWAP | 10 | 692 | 69.20 | 692 | 51.96% | 0.08% | -0.12% | 1m | -0.09% | weak_reversal_fee_blocked |
| RLS-USDT-SWAP | 3 | 203 | 67.67 | 14 | 57.64% | 0.12% | -0.08% | 10m | -0.02% | gross_reversal_fee_blocked |
| UB-USDT-SWAP | 7 | 389 | 55.57 | 389 | 51.67% | 0.08% | -0.12% | 10m | **0.08%** | weak_reversal_fee_blocked |
| AI-USDT-SWAP | 6 | 220 | 36.67 | 220 | 49.09% | -0.02% | -0.22% | 10m | -0.10% | noise |
| BSB-USDT-SWAP | 3 | 98 | 32.67 | 97 | 56.12% | 0.07% | -0.13% | 1m | -0.03% | weak_reversal_fee_blocked |
| SPACE-USDT-SWAP | 4 | 125 | 31.25 | 93 | 50.40% | 0.04% | -0.16% | 3m | -0.16% | weak_reversal_fee_blocked |
| SAHARA-USDT-SWAP | 9 | 113 | 12.56 | 108 | 51.33% | 0.02% | -0.18% | 5m | -0.09% | weak_reversal_fee_blocked |
| JELLYJELLY-USDT-SWAP | 3 | 36 | 12.00 | 36 | 55.56% | 0.11% | -0.09% | 10m | **0.12%** | gross_reversal_fee_blocked |
| BASED-USDT-SWAP | 7 | 71 | 10.14 | 71 | 42.86% | -0.08% | -0.28% | 10m | -0.13% | continuation |
| TURBO-USDT-SWAP | 10 | 26 | 2.60 | 26 | 46.15% | -0.15% | -0.35% | 10m | **0.32%** | noise |
| CHZ-USDT-SWAP | 5 | 10 | 2.00 | 9 | 40.00% | -0.08% | -0.28% | 10m | **0.04%** | continuation |
| NOT-USDT-SWAP | 10 | 19 | 1.90 | 19 | 63.16% | 0.25% | **0.05%** | 10m | **0.36%** | net_positive_reversal |
| BOME-USDT-SWAP | 10 | 17 | 1.70 | 17 | 58.82% | 0.09% | -0.11% | 10m | **0.18%** | weak_reversal_fee_blocked |
| NEIRO-USDT-SWAP | 10 | 10 | 1.00 | 10 | 50.00% | 0.01% | -0.19% | 10m | **0.05%** | noise |

## Net Edge By Hold

### NOT-USDT-SWAP

- days: `10`, explosions: `19`, exp/day: `1.90`
- explosion size avg/p75/p90: `1.29%` / `1.40%` / `1.88%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 19 | 47.37% | 0.16% | -0.04% |
| 2m | 19 | 52.63% | 0.18% | -0.02% |
| 3m | 19 | 63.16% | 0.25% | **0.05%** |
| 5m | 19 | 63.16% | 0.41% | **0.21%** |
| 10m | 19 | 68.42% | 0.56% | **0.36%** |

### TURBO-USDT-SWAP

- days: `10`, explosions: `26`, exp/day: `2.60`
- explosion size avg/p75/p90: `1.22%` / `1.23%` / `1.61%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 26 | 53.85% | -0.04% | -0.24% |
| 2m | 26 | 38.46% | -0.18% | -0.38% |
| 3m | 26 | 46.15% | -0.15% | -0.35% |
| 5m | 26 | 50.00% | -0.04% | -0.24% |
| 10m | 26 | 73.08% | 0.52% | **0.32%** |

### BOME-USDT-SWAP

- days: `10`, explosions: `17`, exp/day: `1.70`
- explosion size avg/p75/p90: `1.20%` / `1.26%` / `1.85%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 17 | 58.82% | 0.13% | -0.07% |
| 2m | 17 | 64.71% | 0.12% | -0.08% |
| 3m | 17 | 58.82% | 0.09% | -0.11% |
| 5m | 17 | 58.82% | 0.08% | -0.12% |
| 10m | 17 | 64.71% | 0.38% | **0.18%** |

### JELLYJELLY-USDT-SWAP

- days: `3`, explosions: `36`, exp/day: `12.00`
- explosion size avg/p75/p90: `1.18%` / `1.21%` / `1.90%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 36 | 63.89% | 0.14% | -0.06% |
| 2m | 36 | 58.33% | 0.16% | -0.04% |
| 3m | 36 | 55.56% | 0.11% | -0.09% |
| 5m | 36 | 58.33% | 0.19% | -0.01% |
| 10m | 36 | 47.22% | 0.32% | **0.12%** |

### UB-USDT-SWAP

- days: `7`, explosions: `389`, exp/day: `55.57`
- explosion size avg/p75/p90: `1.25%` / `1.34%` / `1.88%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 389 | 52.70% | 0.06% | -0.14% |
| 2m | 389 | 54.76% | 0.05% | -0.15% |
| 3m | 389 | 51.67% | 0.08% | -0.12% |
| 5m | 389 | 55.78% | 0.16% | -0.04% |
| 10m | 389 | 55.27% | 0.28% | **0.08%** |

### NEIRO-USDT-SWAP

- days: `10`, explosions: `10`, exp/day: `1.00`
- explosion size avg/p75/p90: `1.46%` / `1.50%` / `2.54%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 10 | 50.00% | 0.12% | -0.08% |
| 2m | 10 | 40.00% | 0.01% | -0.19% |
| 3m | 10 | 50.00% | 0.01% | -0.19% |
| 5m | 10 | 50.00% | 0.11% | -0.09% |
| 10m | 10 | 60.00% | 0.25% | **0.05%** |

### CHZ-USDT-SWAP

- days: `5`, explosions: `10`, exp/day: `2.00`
- explosion size avg/p75/p90: `1.33%` / `1.24%` / `2.22%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 10 | 50.00% | 0.14% | -0.06% |
| 2m | 10 | 30.00% | -0.03% | -0.23% |
| 3m | 10 | 40.00% | -0.08% | -0.28% |
| 5m | 10 | 50.00% | -0.04% | -0.24% |
| 10m | 10 | 60.00% | 0.24% | **0.04%** |

### BILL-USDT-SWAP

- days: `10`, explosions: `983`, exp/day: `98.30`
- explosion size avg/p75/p90: `1.27%` / `1.39%` / `1.96%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 983 | 54.93% | 0.08% | -0.12% |
| 2m | 983 | 51.88% | 0.06% | -0.14% |
| 3m | 983 | 53.71% | 0.09% | -0.11% |
| 5m | 980 | 54.90% | 0.15% | -0.05% |
| 10m | 974 | 54.00% | 0.18% | -0.02% |

### RLS-USDT-SWAP

- days: `3`, explosions: `203`, exp/day: `67.67`
- explosion size avg/p75/p90: `1.41%` / `1.67%` / `2.19%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 203 | 50.25% | 0.11% | -0.09% |
| 2m | 203 | 58.13% | 0.06% | -0.14% |
| 3m | 203 | 57.64% | 0.12% | -0.08% |
| 5m | 203 | 54.68% | 0.14% | -0.06% |
| 10m | 202 | 60.89% | 0.18% | -0.02% |

### BSB-USDT-SWAP

- days: `3`, explosions: `98`, exp/day: `32.67`
- explosion size avg/p75/p90: `1.50%` / `1.52%` / `2.08%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 98 | 59.18% | 0.17% | -0.03% |
| 2m | 98 | 52.04% | 0.07% | -0.13% |
| 3m | 98 | 56.12% | 0.07% | -0.13% |
| 5m | 98 | 50.00% | -0.01% | -0.21% |
| 10m | 98 | 60.20% | -0.14% | -0.34% |

### TRUTH-USDT-SWAP

- days: `10`, explosions: `692`, exp/day: `69.20`
- explosion size avg/p75/p90: `1.28%` / `1.42%` / `1.88%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 691 | 57.45% | 0.11% | -0.09% |
| 2m | 690 | 53.91% | 0.09% | -0.11% |
| 3m | 689 | 51.96% | 0.08% | -0.12% |
| 5m | 689 | 52.54% | 0.10% | -0.10% |
| 10m | 689 | 52.10% | 0.10% | -0.10% |

### SAHARA-USDT-SWAP

- days: `9`, explosions: `113`, exp/day: `12.56`
- explosion size avg/p75/p90: `1.14%` / `1.24%` / `1.67%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 113 | 53.98% | 0.04% | -0.16% |
| 2m | 113 | 56.64% | 0.06% | -0.14% |
| 3m | 113 | 51.33% | 0.02% | -0.18% |
| 5m | 113 | 58.41% | 0.11% | -0.09% |
| 10m | 111 | 51.35% | 0.08% | -0.12% |

### AI-USDT-SWAP

- days: `6`, explosions: `220`, exp/day: `36.67`
- explosion size avg/p75/p90: `1.27%` / `1.28%` / `1.80%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 220 | 58.18% | 0.06% | -0.14% |
| 2m | 220 | 50.00% | 0.03% | -0.17% |
| 3m | 220 | 49.09% | -0.02% | -0.22% |
| 5m | 220 | 48.64% | -0.02% | -0.22% |
| 10m | 219 | 57.53% | 0.10% | -0.10% |

### BASED-USDT-SWAP

- days: `7`, explosions: `71`, exp/day: `10.14`
- explosion size avg/p75/p90: `1.24%` / `1.34%` / `1.77%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 70 | 47.14% | -0.07% | -0.27% |
| 2m | 70 | 41.43% | -0.09% | -0.29% |
| 3m | 70 | 42.86% | -0.08% | -0.28% |
| 5m | 70 | 52.86% | -0.04% | -0.24% |
| 10m | 70 | 51.43% | 0.07% | -0.13% |

### EDEN-USDT-SWAP

- days: `4`, explosions: `438`, exp/day: `109.50`
- explosion size avg/p75/p90: `1.31%` / `1.39%` / `1.91%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 438 | 56.16% | 0.06% | -0.14% |
| 2m | 438 | 51.83% | -0.00% | -0.20% |
| 3m | 438 | 50.68% | -0.02% | -0.22% |
| 5m | 438 | 50.23% | -0.12% | -0.32% |
| 10m | 437 | 51.26% | -0.06% | -0.26% |

### SPACE-USDT-SWAP

- days: `4`, explosions: `125`, exp/day: `31.25`
- explosion size avg/p75/p90: `1.25%` / `1.36%` / `1.85%`

| hold | n | WR | avg_return | net_return |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 125 | 53.60% | 0.04% | -0.16% |
| 2m | 125 | 52.00% | 0.04% | -0.16% |
| 3m | 125 | 50.40% | 0.04% | -0.16% |
| 5m | 125 | 51.20% | 0.03% | -0.17% |
| 10m | 125 | 55.20% | 0.02% | -0.18% |

## Priority Pair Check

| pair | status | days | explosions | rev3_net | best_hold | best_net | comment |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| FOGO-USDT-SWAP | excluded | 1 | 0 | n/a | n/a | n/a | not eligible; days<3; explosions<10 |
| HOME-USDT-SWAP | excluded | 1 | 0 | n/a | n/a | n/a | not eligible; days<3; explosions<10 |
| ONT-USDT-SWAP | excluded | 2 | 3 | -0.14% | 10m | -0.05% | not eligible; days<3; explosions<10 |
| USELESS-USDT-SWAP | excluded | 2 | 29 | -0.12% | 3m | -0.12% | not eligible; days<3 |
| CHIP-USDT-SWAP | excluded | 2 | 5 | -0.25% | 10m | -0.07% | not eligible; days<3; explosions<10 |
| LAYER-USDT-SWAP | excluded | 2 | 4 | 0.18% | 5m | 0.32% | not eligible; days<3; explosions<10 |
| BOME-USDT-SWAP | eligible | 10 | 17 | -0.11% | 10m | 0.18% | eligible |
| BSB-USDT-SWAP | eligible | 3 | 98 | -0.13% | 1m | -0.03% | eligible |
| OFC-USDT-SWAP | excluded | 2 | 39 | -0.10% | 5m | -0.06% | not eligible; days<3 |
| RLS-USDT-SWAP | eligible | 3 | 203 | -0.08% | 10m | -0.02% | eligible |

## Excluded Pairs

| pair | days | explosions | reason |
| --- | ---: | ---: | --- |
| BONK-USDT-SWAP | 10 | 9 | explosions<10 |
| SATS-USDT-SWAP | 10 | 9 | explosions<10 |
| FLOKI-USDT-SWAP | 10 | 8 | explosions<10 |
| HMSTR-USDT-SWAP | 10 | 8 | explosions<10 |
| MEME-USDT-SWAP | 10 | 7 | explosions<10 |
| MEW-USDT-SWAP | 10 | 7 | explosions<10 |
| PENGU-USDT-SWAP | 10 | 7 | explosions<10 |
| PUMP-USDT-SWAP | 10 | 7 | explosions<10 |
| SOL-USDT-SWAP | 10 | 6 | explosions<10 |
| ADA-USDT-SWAP | 10 | 5 | explosions<10 |
| DOGE-USDT-SWAP | 10 | 5 | explosions<10 |
| GALA-USDT-SWAP | 10 | 5 | explosions<10 |
| PEPE-USDT-SWAP | 10 | 5 | explosions<10 |
| SHIB-USDT-SWAP | 10 | 4 | explosions<10 |
| ETH-USDT-SWAP | 10 | 3 | explosions<10 |
| XRP-USDT-SWAP | 10 | 2 | explosions<10 |
| BTC-USDT-SWAP | 10 | 0 | explosions<10 |
| LINEA-USDT-SWAP | 9 | 5 | explosions<10 |
| BABY-USDT-SWAP | 5 | 6 | explosions<10 |
| KAT-USDT-SWAP | 5 | 0 | explosions<10 |
| PEOPLE-USDT-SWAP | 3 | 1 | explosions<10 |
| OFC-USDT-SWAP | 2 | 39 | days<3 |
| USELESS-USDT-SWAP | 2 | 29 | days<3 |
| MOVE-USDT-SWAP | 2 | 22 | days<3 |
| GPS-USDT-SWAP | 2 | 18 | days<3 |
| AZTEC-USDT-SWAP | 2 | 9 | days<3, explosions<10 |
| CHIP-USDT-SWAP | 2 | 5 | days<3, explosions<10 |
| LAYER-USDT-SWAP | 2 | 4 | days<3, explosions<10 |
| ONT-USDT-SWAP | 2 | 3 | days<3, explosions<10 |
| DOOD-USDT-SWAP | 2 | 0 | days<3, explosions<10 |
| BIO-USDT-SWAP | 1 | 2 | days<3, explosions<10 |
| FOGO-USDT-SWAP | 1 | 0 | days<3, explosions<10 |
| HOME-USDT-SWAP | 1 | 0 | days<3, explosions<10 |

## Conclusion

Do not expand `eligible_pairs` just because gross WR is above 50%; after the `0.20%` fee most gross edges disappear.
Config candidates with usable sample and positive best net: `JELLYJELLY-USDT-SWAP, UB-USDT-SWAP`.
Watch-only positive-net preliminary candidates: `BOME-USDT-SWAP, CHZ-USDT-SWAP, NEIRO-USDT-SWAP, NOT-USDT-SWAP, TURBO-USDT-SWAP`.
Pairs with negative best net should stay out of `config.yaml` until a parameter/filter test shows positive net after fees.
