# Three Engines Polish - 22.05.2026

This is a separate polish sweep. It does not overwrite the previous three-engine report and does not touch production code.

## Coverage

- candle symbols loaded for trend/fade: `29`
- impulse tick root: `E:\trading-data\ticks`
- impulse pairs: `BSB-USDT-SWAP, EDEN-USDT-SWAP, RLS-USDT-SWAP, CHZ-USDT-SWAP, SPACE-USDT-SWAP, NOT-USDT-SWAP, TURBO-USDT-SWAP, BOME-USDT-SWAP`
- common tick dates across impulse pairs: `2026-05-20`
- tick bars loaded by pair: `{'BSB-USDT-SWAP': {'bars': 4252, 'tick_minutes': 4252}, 'EDEN-USDT-SWAP': {'bars': 6664, 'tick_minutes': 6663}, 'RLS-USDT-SWAP': {'bars': 5116, 'tick_minutes': 5116}, 'CHZ-USDT-SWAP': {'bars': 5851, 'tick_minutes': 5851}, 'SPACE-USDT-SWAP': {'bars': 7453, 'tick_minutes': 7453}, 'NOT-USDT-SWAP': {'bars': 14431, 'tick_minutes': 14431}, 'TURBO-USDT-SWAP': {'bars': 14392, 'tick_minutes': 14392}, 'BOME-USDT-SWAP': {'bars': 14450, 'tick_minutes': 14450}}`

## Direction Fix Check

- old structural trend direction match: `34.27%`
- new compute-signal-derived trend direction match: `44.87%`

## Impulse Detection Conditions

- Tape-only, selected volatile alts with real tick files; no candle proxy.
- Sweep: trigger windows 10/20/60/120/300 sec, min 1m move 0.6/0.8/1.0%, body ratio 1.2/1.5/2.0x, volume ratio 1.0/1.5/2.0x, exits structure k1/k2/k3 and giveback 30/40/50.

## Impulse Sweep Top

| window_sec | min_move | body_ratio_min | volume_ratio_min | exit | n | net | WR | capture | available | edge | hold | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 1.0 | 2.0 | 2.0 | structure_k3 | 194 | 3.03% | 84.02% | 49.13% | 8.09% | 100.00% | 10.96m | RESEARCH: split-check needed |
| 10 | 1.0 | 2.0 | 2.0 | structure_k2 | 194 | 3.02% | 86.60% | 52.22% | 8.09% | 100.00% | 8.85m | RESEARCH: split-check needed |
| 10 | 1.0 | 1.5 | 2.0 | structure_k3 | 207 | 2.96% | 82.61% | 48.30% | 8.12% | 100.00% | 10.80m | RESEARCH: split-check needed |
| 10 | 1.0 | 1.5 | 2.0 | structure_k2 | 207 | 2.94% | 85.02% | 51.31% | 8.12% | 100.00% | 8.78m | RESEARCH: split-check needed |
| 10 | 1.0 | 1.2 | 2.0 | structure_k3 | 210 | 2.92% | 81.90% | 47.68% | 8.19% | 100.00% | 10.70m | RESEARCH: split-check needed |
| 10 | 1.0 | 1.2 | 2.0 | structure_k2 | 210 | 2.90% | 84.29% | 50.74% | 8.19% | 100.00% | 8.69m | RESEARCH: split-check needed |
| 10 | 1.0 | 2.0 | 1.5 | structure_k2 | 246 | 2.82% | 85.77% | 51.85% | 8.08% | 100.00% | 8.67m | RESEARCH: split-check needed |
| 10 | 1.0 | 2.0 | 1.5 | structure_k3 | 246 | 2.81% | 82.11% | 47.81% | 8.08% | 100.00% | 10.83m | RESEARCH: split-check needed |
| 20 | 1.0 | 2.0 | 2.0 | structure_k2 | 258 | 2.80% | 86.82% | 52.68% | 7.62% | 100.00% | 8.71m | RESEARCH: split-check needed |
| 20 | 1.0 | 2.0 | 2.0 | structure_k3 | 258 | 2.80% | 84.88% | 49.63% | 7.62% | 100.00% | 11.02m | RESEARCH: split-check needed |
| 10 | 1.0 | 1.5 | 1.5 | structure_k2 | 267 | 2.77% | 83.90% | 50.81% | 8.15% | 100.00% | 8.55m | RESEARCH: split-check needed |
| 10 | 1.0 | 1.5 | 1.5 | structure_k3 | 267 | 2.76% | 80.15% | 46.80% | 8.15% | 100.00% | 10.64m | RESEARCH: split-check needed |
| 20 | 1.0 | 1.5 | 2.0 | structure_k3 | 272 | 2.74% | 83.46% | 48.79% | 7.65% | 100.00% | 10.86m | RESEARCH: split-check needed |
| 20 | 1.0 | 1.5 | 2.0 | structure_k2 | 272 | 2.74% | 85.66% | 51.84% | 7.65% | 100.00% | 8.63m | RESEARCH: split-check needed |
| 10 | 1.0 | 1.2 | 1.5 | structure_k2 | 276 | 2.74% | 83.70% | 50.50% | 8.27% | 100.00% | 8.52m | RESEARCH: split-check needed |
| 10 | 1.0 | 1.2 | 1.5 | structure_k3 | 276 | 2.71% | 78.99% | 46.06% | 8.27% | 100.00% | 10.56m | RESEARCH: split-check needed |
| 10 | 1.0 | 1.2 | 1.0 | structure_k3 | 410 | 2.71% | 77.56% | 45.42% | 9.26% | 100.00% | 10.32m | RESEARCH: split-check needed |
| 20 | 1.0 | 1.2 | 2.0 | structure_k3 | 276 | 2.71% | 82.97% | 48.37% | 7.71% | 100.00% | 10.82m | RESEARCH: split-check needed |
| 20 | 1.0 | 1.2 | 2.0 | structure_k2 | 276 | 2.70% | 84.78% | 51.22% | 7.71% | 100.00% | 8.55m | RESEARCH: split-check needed |
| 10 | 1.0 | 1.5 | 1.0 | structure_k3 | 385 | 2.67% | 78.18% | 45.88% | 8.92% | 100.00% | 10.33m | RESEARCH: split-check needed |

## Impulse Side Split Top

| side | window_sec | exit | n | net | WR | capture | available |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| long | 10 | structure_k2 | 4968 | 2.57% | 78.76% | 47.45% | 8.80% |
| long | 10 | structure_k3 | 4968 | 2.53% | 72.28% | 42.15% | 8.80% |
| long | 20 | structure_k2 | 6621 | 2.27% | 77.22% | 46.67% | 8.05% |
| long | 10 | structure_k1 | 4968 | 2.26% | 90.24% | 59.36% | 8.80% |
| long | 20 | structure_k3 | 6621 | 2.22% | 71.77% | 41.85% | 8.05% |
| short | 10 | structure_k1 | 5050 | 2.05% | 90.40% | 55.72% | 6.33% |
| long | 20 | structure_k1 | 6621 | 2.04% | 89.93% | 58.89% | 8.05% |
| short | 10 | structure_k3 | 5050 | 2.03% | 77.86% | 46.18% | 6.33% |
| short | 10 | structure_k2 | 5050 | 2.02% | 81.07% | 48.52% | 6.33% |
| long | 120 | structure_k2 | 9100 | 1.96% | 78.41% | 48.55% | 7.12% |
| long | 300 | structure_k2 | 9100 | 1.96% | 78.41% | 48.55% | 7.12% |
| long | 60 | structure_k2 | 9100 | 1.96% | 78.41% | 48.55% | 7.12% |
| long | 120 | structure_k3 | 9100 | 1.89% | 73.02% | 43.79% | 7.12% |
| long | 300 | structure_k3 | 9100 | 1.89% | 73.02% | 43.79% | 7.12% |
| long | 60 | structure_k3 | 9100 | 1.89% | 73.02% | 43.79% | 7.12% |
| short | 20 | structure_k1 | 6921 | 1.89% | 89.55% | 55.62% | 5.96% |

## Impulse Pair Split Top

| symbol | window_sec | exit | n | net | WR | capture | available |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BSB-USDT-SWAP | 10 | structure_k2 | 3002 | 3.68% | 82.18% | 49.81% | 12.09% |
| BSB-USDT-SWAP | 10 | structure_k3 | 3002 | 3.66% | 77.35% | 45.17% | 12.09% |
| BSB-USDT-SWAP | 10 | structure_k1 | 3002 | 3.56% | 92.04% | 59.10% | 12.09% |
| BSB-USDT-SWAP | 20 | structure_k2 | 3840 | 3.27% | 80.13% | 47.89% | 11.23% |
| BSB-USDT-SWAP | 20 | structure_k1 | 3840 | 3.22% | 91.61% | 57.57% | 11.23% |
| BSB-USDT-SWAP | 20 | structure_k3 | 3840 | 3.20% | 75.81% | 43.35% | 11.23% |
| BSB-USDT-SWAP | 120 | structure_k2 | 4804 | 3.02% | 78.81% | 46.97% | 10.66% |
| BSB-USDT-SWAP | 300 | structure_k2 | 4804 | 3.02% | 78.81% | 46.97% | 10.66% |
| BSB-USDT-SWAP | 60 | structure_k2 | 4804 | 3.02% | 78.81% | 46.97% | 10.66% |
| BSB-USDT-SWAP | 120 | structure_k1 | 4804 | 3.00% | 89.15% | 55.83% | 10.66% |
| BSB-USDT-SWAP | 300 | structure_k1 | 4804 | 3.00% | 89.15% | 55.83% | 10.66% |
| BSB-USDT-SWAP | 60 | structure_k1 | 4804 | 3.00% | 89.15% | 55.83% | 10.66% |
| BSB-USDT-SWAP | 120 | structure_k3 | 4804 | 2.89% | 73.04% | 41.81% | 10.66% |
| BSB-USDT-SWAP | 300 | structure_k3 | 4804 | 2.89% | 73.04% | 41.81% | 10.66% |
| BSB-USDT-SWAP | 60 | structure_k3 | 4804 | 2.89% | 73.04% | 41.81% | 10.66% |
| EDEN-USDT-SWAP | 10 | structure_k2 | 4609 | 2.10% | 80.93% | 49.21% | 6.61% |

## Trend Detection Conditions

- Corrected TRENDING swing/grind plus DRIFT FAST routed to trend.
- Direction is taken from replayed `compute_signal` side/bias/DI/slope, not from the previous structural reimplementation.
- Exit sweep: structure k1/k2/k3, ATR2/ATR3 wide trail, EMA20 break.

## Trend Sweep Top

| exit | n | net | WR | capture | available | edge | hold | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| structure_k3 | 1482 | -0.23% | 20.51% | 11.26% | 2.93% | 60.86% | 89.99m | NO-GO: net<=0 |
| structure_k1 | 1482 | -0.26% | 21.66% | 12.43% | 2.93% | 60.86% | 59.06m | NO-GO: net<=0 |
| structure_k2 | 1482 | -0.27% | 20.92% | 11.52% | 2.93% | 60.86% | 76.69m | NO-GO: net<=0 |
| atr3 | 1482 | -0.27% | 17.88% | 11.04% | 2.93% | 60.86% | 127.49m | NO-GO: net<=0 |
| atr2 | 1482 | -0.35% | 17.81% | 9.74% | 2.93% | 60.86% | 105.09m | NO-GO: net<=0 |
| ema20_break | 1482 | -0.43% | 16.13% | 11.50% | 2.93% | 60.86% | 69.69m | NO-GO: net<=0 |

## Trend Side Split

| side | exit | n | net | WR | capture | available |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| long | structure_k1 | 889 | -0.20% | 21.15% | 12.00% | 3.39% |
| long | structure_k3 | 889 | -0.22% | 20.02% | 10.41% | 3.39% |
| long | structure_k2 | 889 | -0.24% | 20.58% | 11.03% | 3.39% |
| long | atr3 | 889 | -0.24% | 17.89% | 10.41% | 3.39% |
| short | structure_k3 | 593 | -0.26% | 21.25% | 12.58% | 2.25% |
| short | atr3 | 593 | -0.32% | 17.88% | 12.02% | 2.25% |
| short | structure_k2 | 593 | -0.32% | 21.42% | 12.26% | 2.25% |
| long | atr2 | 889 | -0.33% | 18.22% | 9.41% | 3.39% |
| short | structure_k1 | 593 | -0.35% | 22.43% | 13.11% | 2.25% |
| short | atr2 | 593 | -0.38% | 17.20% | 10.23% | 2.25% |
| long | ema20_break | 889 | -0.39% | 15.97% | 12.23% | 3.39% |
| short | ema20_break | 593 | -0.50% | 16.36% | 10.38% | 2.25% |

## Trend Volatility Tier Split

| tier | exit | n | net | WR | capture | available |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| low_vol_alt | structure_k2 | 70 | -0.17% | 22.86% | 12.66% | 1.50% |
| major | structure_k2 | 175 | -0.18% | 21.14% | 15.01% | 1.36% |
| major | structure_k1 | 175 | -0.20% | 22.29% | 15.58% | 1.36% |
| major | structure_k3 | 175 | -0.21% | 21.71% | 14.22% | 1.36% |
| low_vol_alt | structure_k3 | 70 | -0.21% | 20.00% | 10.79% | 1.50% |
| mid_vol_alt | structure_k3 | 957 | -0.22% | 20.79% | 11.47% | 2.51% |
| major | atr3 | 175 | -0.23% | 22.29% | 14.59% | 1.36% |
| mid_vol_alt | atr3 | 957 | -0.24% | 17.55% | 10.88% | 2.51% |
| low_vol_alt | structure_k1 | 70 | -0.26% | 20.00% | 11.28% | 1.50% |
| mid_vol_alt | structure_k1 | 957 | -0.26% | 21.94% | 12.58% | 2.51% |
| mid_vol_alt | structure_k2 | 957 | -0.27% | 21.11% | 11.75% | 2.51% |
| low_vol_alt | atr3 | 70 | -0.27% | 15.71% | 10.58% | 1.50% |
| major | atr2 | 175 | -0.28% | 17.71% | 11.90% | 1.36% |
| low_vol_alt | atr2 | 70 | -0.30% | 18.57% | 9.35% | 1.50% |
| high_vol_alt | structure_k1 | 280 | -0.31% | 20.71% | 10.51% | 5.72% |
| mid_vol_alt | atr2 | 957 | -0.31% | 18.18% | 9.86% | 2.51% |

## Trend Early/Late Split

| period | exit | n | net | WR | capture | available |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| early | structure_k1 | 767 | -0.20% | 22.43% | 12.94% | 3.48% |
| early | structure_k3 | 767 | -0.22% | 20.47% | 10.57% | 3.48% |
| early | structure_k2 | 767 | -0.24% | 20.99% | 11.20% | 3.48% |
| late | structure_k3 | 715 | -0.25% | 20.56% | 11.99% | 2.35% |
| early | atr3 | 767 | -0.26% | 17.99% | 11.05% | 3.48% |
| late | atr3 | 715 | -0.28% | 17.76% | 11.04% | 2.35% |
| late | structure_k2 | 715 | -0.29% | 20.84% | 11.85% | 2.35% |
| late | structure_k1 | 715 | -0.32% | 20.84% | 11.90% | 2.35% |
| early | atr2 | 767 | -0.34% | 18.51% | 9.94% | 3.48% |
| late | atr2 | 715 | -0.36% | 17.06% | 9.52% | 2.35% |
| late | ema20_break | 715 | -0.43% | 16.36% | 11.85% | 2.35% |
| early | ema20_break | 767 | -0.44% | 15.91% | 11.18% | 3.48% |

## Fade Detection Conditions

- Corrected RANGING only; BB boundary touch tolerance sweep; target middle/opposite/giveback.
- Uses raw float levels and significant-value rendering; no fixed-decimal rounding on cheap coins.
- Activation sweep: ADX max, BB width max, boundary tolerance.

## Fade Sweep Top

| tol | adx_max | bb_width_max | target | n | net | WR | capture | available | edge | hold | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.05 | 22.0 | 3.5 | opposite | 10 | -0.05% | 30.00% | 20.41% | 1.39% | 30.00% | 49.50m | NO-GO: sample<20 |
| 0.05 | 22.0 | 5.0 | opposite | 10 | -0.05% | 30.00% | 20.41% | 1.39% | 30.00% | 49.50m | NO-GO: sample<20 |
| 0.05 | 26.0 | 3.5 | opposite | 10 | -0.05% | 30.00% | 20.41% | 1.39% | 30.00% | 49.50m | NO-GO: sample<20 |
| 0.05 | 26.0 | 5.0 | opposite | 10 | -0.05% | 30.00% | 20.41% | 1.39% | 30.00% | 49.50m | NO-GO: sample<20 |
| 0.05 | 30.0 | 3.5 | opposite | 10 | -0.05% | 30.00% | 20.41% | 1.39% | 30.00% | 49.50m | NO-GO: sample<20 |
| 0.05 | 30.0 | 5.0 | opposite | 10 | -0.05% | 30.00% | 20.41% | 1.39% | 30.00% | 49.50m | NO-GO: sample<20 |
| 0.1 | 22.0 | 3.5 | opposite | 12 | -0.08% | 25.00% | 18.83% | 1.38% | 25.00% | 53.75m | NO-GO: sample<20 |
| 0.1 | 22.0 | 5.0 | opposite | 12 | -0.08% | 25.00% | 18.83% | 1.38% | 25.00% | 53.75m | NO-GO: sample<20 |
| 0.1 | 26.0 | 3.5 | opposite | 12 | -0.08% | 25.00% | 18.83% | 1.38% | 25.00% | 53.75m | NO-GO: sample<20 |
| 0.1 | 26.0 | 5.0 | opposite | 12 | -0.08% | 25.00% | 18.83% | 1.38% | 25.00% | 53.75m | NO-GO: sample<20 |
| 0.1 | 30.0 | 3.5 | opposite | 12 | -0.08% | 25.00% | 18.83% | 1.38% | 25.00% | 53.75m | NO-GO: sample<20 |
| 0.1 | 30.0 | 5.0 | opposite | 12 | -0.08% | 25.00% | 18.83% | 1.38% | 25.00% | 53.75m | NO-GO: sample<20 |
| 0.2 | 22.0 | 2.5 | opposite | 16 | -0.17% | 31.25% | 24.64% | 1.10% | 31.25% | 50.62m | NO-GO: sample<20 |
| 0.05 | 22.0 | 3.5 | middle | 10 | -0.17% | 40.00% | 26.43% | 1.39% | 50.00% | 19.50m | NO-GO: sample<20 |
| 0.05 | 22.0 | 5.0 | middle | 10 | -0.17% | 40.00% | 26.43% | 1.39% | 50.00% | 19.50m | NO-GO: sample<20 |
| 0.05 | 26.0 | 3.5 | middle | 10 | -0.17% | 40.00% | 26.43% | 1.39% | 50.00% | 19.50m | NO-GO: sample<20 |
| 0.05 | 26.0 | 5.0 | middle | 10 | -0.17% | 40.00% | 26.43% | 1.39% | 50.00% | 19.50m | NO-GO: sample<20 |
| 0.05 | 30.0 | 3.5 | middle | 10 | -0.17% | 40.00% | 26.43% | 1.39% | 50.00% | 19.50m | NO-GO: sample<20 |
| 0.05 | 30.0 | 5.0 | middle | 10 | -0.17% | 40.00% | 26.43% | 1.39% | 50.00% | 19.50m | NO-GO: sample<20 |
| 0.2 | 26.0 | 2.5 | opposite | 17 | -0.18% | 29.41% | 23.19% | 1.10% | 29.41% | 49.41m | NO-GO: sample<20 |

## Fade Side Split

| side | tol | target | n | net | WR | capture | available |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| short | 0.05 | middle | 48 | 0.02% | 68.75% | 44.43% | 1.14% |
| short | 0.05 | opposite | 48 | 0.01% | 31.25% | 26.24% | 1.14% |
| short | 0.1 | middle | 57 | -0.00% | 57.89% | 39.48% | 1.04% |
| short | 0.1 | opposite | 57 | -0.00% | 26.32% | 25.55% | 1.04% |
| short | 0.2 | middle | 90 | -0.15% | 36.67% | 25.01% | 0.87% |
| long | 0.2 | opposite | 78 | -0.17% | 34.62% | 23.72% | 1.46% |
| short | 0.2 | opposite | 90 | -0.21% | 26.67% | 23.12% | 0.87% |
| long | 0.05 | opposite | 36 | -0.24% | 25.00% | 10.73% | 1.59% |
| long | 0.1 | opposite | 45 | -0.26% | 20.00% | 8.58% | 1.70% |
| long | 0.2 | giveback_40 | 78 | -0.28% | 11.54% | 12.64% | 1.46% |
| short | 0.05 | giveback_40 | 48 | -0.29% | 18.75% | 8.27% | 1.14% |
| short | 0.1 | giveback_40 | 57 | -0.29% | 15.79% | 6.97% | 1.04% |
| long | 0.2 | middle | 78 | -0.33% | 11.54% | 13.39% | 1.46% |
| short | 0.2 | giveback_40 | 90 | -0.33% | 10.00% | 11.37% | 0.87% |
| long | 0.1 | giveback_40 | 45 | -0.43% | 0.00% | 0.00% | 1.70% |
| long | 0.05 | giveback_40 | 36 | -0.44% | 0.00% | 0.00% | 1.59% |

## Fade Volatility Tier Split

| tier | tol | target | n | net | WR | capture | available |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| high_vol_alt | 0.05 | opposite | 27 | 0.21% | 66.67% | 46.77% | 1.13% |
| high_vol_alt | 0.1 | opposite | 27 | 0.21% | 66.67% | 46.77% | 1.13% |
| high_vol_alt | 0.2 | opposite | 27 | 0.21% | 66.67% | 46.77% | 1.13% |
| high_vol_alt | 0.05 | middle | 27 | -0.09% | 66.67% | 32.75% | 1.13% |
| high_vol_alt | 0.1 | middle | 27 | -0.09% | 66.67% | 32.75% | 1.13% |
| high_vol_alt | 0.2 | middle | 27 | -0.09% | 66.67% | 32.75% | 1.13% |
| mid_vol_alt | 0.2 | opposite | 114 | -0.19% | 21.05% | 17.93% | 1.31% |
| mid_vol_alt | 0.2 | middle | 114 | -0.23% | 21.05% | 21.14% | 1.31% |
| mid_vol_alt | 0.05 | middle | 57 | -0.23% | 26.32% | 21.90% | 1.43% |
| mid_vol_alt | 0.05 | opposite | 57 | -0.24% | 10.53% | 6.72% | 1.43% |
| mid_vol_alt | 0.1 | opposite | 75 | -0.24% | 8.00% | 7.73% | 1.40% |
| mid_vol_alt | 0.1 | middle | 75 | -0.24% | 20.00% | 18.22% | 1.40% |
| mid_vol_alt | 0.2 | giveback_40 | 114 | -0.27% | 7.89% | 9.71% | 1.31% |
| high_vol_alt | 0.05 | giveback_40 | 27 | -0.30% | 33.33% | 14.71% | 1.13% |
| high_vol_alt | 0.1 | giveback_40 | 27 | -0.30% | 33.33% | 14.71% | 1.13% |
| high_vol_alt | 0.2 | giveback_40 | 27 | -0.30% | 33.33% | 14.71% | 1.13% |

## Fade Early/Late Split

| period | tol | target | n | net | WR | capture | available |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| early | 0.05 | opposite | 48 | 0.21% | 31.25% | 26.24% | 1.74% |
| early | 0.1 | opposite | 66 | 0.08% | 22.73% | 22.07% | 1.63% |
| early | 0.2 | opposite | 90 | 0.04% | 36.67% | 31.55% | 1.43% |
| early | 0.05 | middle | 48 | -0.12% | 31.25% | 18.13% | 1.74% |
| early | 0.2 | middle | 90 | -0.12% | 26.67% | 19.68% | 1.43% |
| early | 0.1 | middle | 66 | -0.16% | 22.73% | 14.97% | 1.63% |
| early | 0.2 | giveback_40 | 90 | -0.28% | 10.00% | 11.18% | 1.43% |
| late | 0.05 | middle | 36 | -0.28% | 50.00% | 35.06% | 0.78% |
| late | 0.1 | middle | 36 | -0.28% | 50.00% | 35.06% | 0.78% |
| late | 0.2 | giveback_40 | 78 | -0.34% | 11.54% | 12.86% | 0.81% |
| late | 0.05 | giveback_40 | 36 | -0.34% | 25.00% | 11.03% | 0.78% |
| late | 0.1 | giveback_40 | 36 | -0.34% | 25.00% | 11.03% | 0.78% |
| early | 0.1 | giveback_40 | 66 | -0.35% | 0.00% | 0.00% | 1.63% |
| early | 0.05 | giveback_40 | 48 | -0.36% | 0.00% | 0.00% | 1.74% |
| late | 0.2 | middle | 78 | -0.36% | 23.08% | 19.53% | 0.81% |
| late | 0.2 | opposite | 78 | -0.46% | 23.08% | 14.00% | 0.81% |

## Verdict

- Impulse is now measured on the tick period where data exists. Treat rows with small n as research only even if net is positive.
- Trend direction improves only if the compute-signal-derived side beats the old structural side; the report shows both rates explicitly.
- Fade is the closest branch, but it still needs side/time stability and enough sample after tightening activation.

## GPT Hypotheses

- The profitable impulse rows should concentrate in shorter trigger windows and short side if the trader's hypothesis is right.
- Trend ride can have large winners, but direction quality is the binding constraint; exit tuning cannot fix wrong side.
- Fade should prefer mid-vol ranges with BB-middle targets; opposite-band targets likely raise capture but may lower win rate.
