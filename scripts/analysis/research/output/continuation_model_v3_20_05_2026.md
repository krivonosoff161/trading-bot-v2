# Continuation Formal Model V3 - 20.05.2026

## Fixed Base Model

This model is fixed from trading logic, not optimized over this run:

- Pattern: `single_impulse`.
- Signal: 1m impulse candle in continuation direction with `abs(open->close) >= 0.8%`.
- Body-strength filter: impulse body >= `1.5x` average absolute body of previous 4 completed 1m candles.
- Regime filter: at least `2` prior explosive candles in the last 5 minutes.
- Entry: first tick inside the impulse candle where move from candle open reaches `0.3%` within `10s`; taker slippage `0.05%` applied.
- Stop: impulse candle extreme +/- `0.1%` buffer.
- Exit: structure break `k=1`, meaning a completed candle closes beyond the previous completed bar's structural level.
- Max hold: `20m`; fee: `0.20%` round trip.

EV decomposition uses realized net values:

`E[net] = p_win * avg_win - p_loss * avg_loss`

Fees are already included in net returns.

## Base Model By Pair / Side / Time

| pair | period | side | n | avg net | median | std | IQR | win | avg win | avg loss | EV formula | avg MFE |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| AI-USDT-SWAP | all | both | 15 | 0.75% | 0.23% | 1.60% | 1.42% | 66.67% | 1.38% | 0.53% | 0.75% | 2.86% |
| AI-USDT-SWAP | all | long | 5 | 0.56% | 0.25% | 0.58% | 0.87% | 80.00% | 0.73% | 0.16% | 0.56% | 2.24% |
| AI-USDT-SWAP | all | short | 10 | 0.84% | 0.22% | 1.90% | 1.94% | 60.00% | 1.82% | 0.62% | 0.84% | 3.17% |
| AI-USDT-SWAP | early | both | 12 | 0.62% | 0.22% | 1.71% | 1.30% | 58.33% | 1.45% | 0.53% | 0.62% | 2.79% |
| AI-USDT-SWAP | early | long | 4 | 0.42% | 0.24% | 0.57% | 0.41% | 75.00% | 0.62% | 0.16% | 0.42% | 2.20% |
| AI-USDT-SWAP | early | short | 8 | 0.72% | -0.01% | 2.04% | 1.71% | 50.00% | 2.07% | 0.62% | 0.72% | 3.08% |
| AI-USDT-SWAP | late | both | 3 | 1.24% | 1.09% | 0.89% | 1.08% | 100.00% | 1.24% | n/a | n/a | 3.16% |
| AI-USDT-SWAP | late | long | 1 | 1.09% | 1.09% | 0.00% | 0.00% | 100.00% | 1.09% | n/a | n/a | 2.40% |
| AI-USDT-SWAP | late | short | 2 | 1.31% | 1.31% | 1.08% | 1.08% | 100.00% | 1.31% | n/a | n/a | 3.54% |
| BILL-USDT-SWAP | all | both | 59 | 1.09% | 0.74% | 1.67% | 1.98% | 71.19% | 1.76% | 0.55% | 1.09% | 3.02% |
| BILL-USDT-SWAP | all | long | 30 | 0.81% | 0.35% | 1.62% | 2.00% | 66.67% | 1.52% | 0.61% | 0.81% | 2.77% |
| BILL-USDT-SWAP | all | short | 29 | 1.38% | 0.93% | 1.67% | 2.54% | 75.86% | 1.97% | 0.47% | 1.38% | 3.28% |
| BILL-USDT-SWAP | early | both | 42 | 1.12% | 0.69% | 1.78% | 1.84% | 69.05% | 1.87% | 0.56% | 1.12% | 3.16% |
| BILL-USDT-SWAP | early | long | 23 | 0.90% | 0.47% | 1.74% | 1.80% | 65.22% | 1.70% | 0.59% | 0.90% | 2.94% |
| BILL-USDT-SWAP | early | short | 19 | 1.38% | 0.91% | 1.78% | 2.95% | 73.68% | 2.06% | 0.53% | 1.38% | 3.42% |
| BILL-USDT-SWAP | late | both | 17 | 1.02% | 0.85% | 1.37% | 1.89% | 76.47% | 1.49% | 0.51% | 1.02% | 2.68% |
| BILL-USDT-SWAP | late | long | 7 | 0.49% | 0.05% | 1.05% | 1.69% | 71.43% | 0.97% | 0.71% | 0.49% | 2.21% |
| BILL-USDT-SWAP | late | short | 10 | 1.40% | 0.95% | 1.44% | 1.68% | 80.00% | 1.82% | 0.32% | 1.40% | 3.02% |
| BOME-USDT-SWAP | all | both | 1 | 0.83% | 0.83% | 0.00% | 0.00% | 100.00% | 0.83% | n/a | n/a | 2.17% |
| BOME-USDT-SWAP | all | long | 1 | 0.83% | 0.83% | 0.00% | 0.00% | 100.00% | 0.83% | n/a | n/a | 2.17% |
| BOME-USDT-SWAP | late | both | 1 | 0.83% | 0.83% | 0.00% | 0.00% | 100.00% | 0.83% | n/a | n/a | 2.17% |
| BOME-USDT-SWAP | late | long | 1 | 0.83% | 0.83% | 0.00% | 0.00% | 100.00% | 0.83% | n/a | n/a | 2.17% |
| BSB-USDT-SWAP | all | both | 47 | 4.09% | 2.20% | 7.29% | 5.07% | 70.21% | 6.25% | 1.00% | 4.09% | 10.55% |
| BSB-USDT-SWAP | all | long | 30 | 3.86% | 2.28% | 6.79% | 4.22% | 70.00% | 5.97% | 1.07% | 3.86% | 10.86% |
| BSB-USDT-SWAP | all | short | 17 | 4.51% | 1.96% | 8.08% | 6.54% | 70.59% | 6.75% | 0.86% | 4.51% | 10.01% |
| BSB-USDT-SWAP | late | both | 47 | 4.09% | 2.20% | 7.29% | 5.07% | 70.21% | 6.25% | 1.00% | 4.09% | 10.55% |
| BSB-USDT-SWAP | late | long | 30 | 3.86% | 2.28% | 6.79% | 4.22% | 70.00% | 5.97% | 1.07% | 3.86% | 10.86% |
| BSB-USDT-SWAP | late | short | 17 | 4.51% | 1.96% | 8.08% | 6.54% | 70.59% | 6.75% | 0.86% | 4.51% | 10.01% |
| EDEN-USDT-SWAP | all | both | 61 | 2.05% | 0.87% | 3.41% | 2.30% | 77.05% | 2.85% | 0.62% | 2.05% | 5.46% |
| EDEN-USDT-SWAP | all | long | 35 | 2.87% | 1.40% | 4.15% | 4.45% | 77.14% | 3.91% | 0.65% | 2.87% | 7.10% |
| EDEN-USDT-SWAP | all | short | 26 | 0.95% | 0.66% | 1.42% | 1.25% | 76.92% | 1.41% | 0.59% | 0.95% | 3.24% |
| EDEN-USDT-SWAP | early | both | 4 | 0.29% | 0.25% | 1.03% | 2.02% | 50.00% | 1.32% | 0.74% | 0.29% | 2.60% |
| EDEN-USDT-SWAP | early | long | 3 | 0.63% | 1.24% | 0.97% | 1.07% | 66.67% | 1.32% | 0.74% | 0.63% | 3.05% |
| EDEN-USDT-SWAP | early | short | 1 | -0.74% | -0.74% | 0.00% | 0.00% | 0.00% | n/a | 0.74% | n/a | 1.23% |
| EDEN-USDT-SWAP | late | both | 57 | 2.17% | 0.87% | 3.48% | 2.38% | 78.95% | 2.91% | 0.60% | 2.17% | 5.66% |
| EDEN-USDT-SWAP | late | long | 32 | 3.08% | 1.75% | 4.27% | 5.13% | 78.12% | 4.12% | 0.64% | 3.08% | 7.48% |
| EDEN-USDT-SWAP | late | short | 25 | 1.02% | 0.69% | 1.41% | 1.26% | 80.00% | 1.41% | 0.56% | 1.02% | 3.32% |
| NOT-USDT-SWAP | all | both | 1 | 2.46% | 2.46% | 0.00% | 0.00% | 100.00% | 2.46% | n/a | n/a | 2.78% |
| NOT-USDT-SWAP | all | long | 1 | 2.46% | 2.46% | 0.00% | 0.00% | 100.00% | 2.46% | n/a | n/a | 2.78% |
| NOT-USDT-SWAP | late | both | 1 | 2.46% | 2.46% | 0.00% | 0.00% | 100.00% | 2.46% | n/a | n/a | 2.78% |
| NOT-USDT-SWAP | late | long | 1 | 2.46% | 2.46% | 0.00% | 0.00% | 100.00% | 2.46% | n/a | n/a | 2.78% |
| OFC-USDT-SWAP | all | both | 2 | 1.72% | 1.72% | 0.87% | 0.87% | 100.00% | 1.72% | n/a | n/a | 3.82% |
| OFC-USDT-SWAP | all | long | 2 | 1.72% | 1.72% | 0.87% | 0.87% | 100.00% | 1.72% | n/a | n/a | 3.82% |
| OFC-USDT-SWAP | early | both | 2 | 1.72% | 1.72% | 0.87% | 0.87% | 100.00% | 1.72% | n/a | n/a | 3.82% |
| OFC-USDT-SWAP | early | long | 2 | 1.72% | 1.72% | 0.87% | 0.87% | 100.00% | 1.72% | n/a | n/a | 3.82% |
| RLS-USDT-SWAP | all | both | 26 | 0.73% | 0.07% | 1.50% | 2.46% | 57.69% | 1.71% | 0.61% | 0.73% | 3.01% |
| RLS-USDT-SWAP | all | long | 16 | 0.54% | 0.03% | 1.28% | 2.11% | 56.25% | 1.39% | 0.55% | 0.54% | 3.06% |
| RLS-USDT-SWAP | all | short | 10 | 1.02% | 0.93% | 1.74% | 2.55% | 60.00% | 2.18% | 0.72% | 1.02% | 2.94% |
| RLS-USDT-SWAP | late | both | 26 | 0.73% | 0.07% | 1.50% | 2.46% | 57.69% | 1.71% | 0.61% | 0.73% | 3.01% |
| RLS-USDT-SWAP | late | long | 16 | 0.54% | 0.03% | 1.28% | 2.11% | 56.25% | 1.39% | 0.55% | 0.54% | 3.06% |
| RLS-USDT-SWAP | late | short | 10 | 1.02% | 0.93% | 1.74% | 2.55% | 60.00% | 2.18% | 0.72% | 1.02% | 2.94% |
| SAHARA-USDT-SWAP | all | both | 1 | 0.26% | 0.26% | 0.00% | 0.00% | 100.00% | 0.26% | n/a | n/a | 1.34% |
| SAHARA-USDT-SWAP | all | short | 1 | 0.26% | 0.26% | 0.00% | 0.00% | 100.00% | 0.26% | n/a | n/a | 1.34% |
| SAHARA-USDT-SWAP | early | both | 1 | 0.26% | 0.26% | 0.00% | 0.00% | 100.00% | 0.26% | n/a | n/a | 1.34% |
| SAHARA-USDT-SWAP | early | short | 1 | 0.26% | 0.26% | 0.00% | 0.00% | 100.00% | 0.26% | n/a | n/a | 1.34% |
| SPACE-USDT-SWAP | all | both | 6 | 0.73% | 0.49% | 0.75% | 1.40% | 83.33% | 0.89% | 0.07% | 0.73% | 2.91% |
| SPACE-USDT-SWAP | all | long | 3 | 0.28% | 0.06% | 0.40% | 0.46% | 66.67% | 0.45% | 0.07% | 0.28% | 2.50% |
| SPACE-USDT-SWAP | all | short | 3 | 1.19% | 1.69% | 0.74% | 0.79% | 100.00% | 1.19% | n/a | n/a | 3.32% |
| SPACE-USDT-SWAP | early | both | 2 | 1.29% | 1.29% | 0.44% | 0.44% | 100.00% | 1.29% | n/a | n/a | 2.66% |
| SPACE-USDT-SWAP | early | long | 1 | 0.84% | 0.84% | 0.00% | 0.00% | 100.00% | 0.84% | n/a | n/a | 2.48% |
| SPACE-USDT-SWAP | early | short | 1 | 1.73% | 1.73% | 0.00% | 0.00% | 100.00% | 1.73% | n/a | n/a | 2.84% |
| SPACE-USDT-SWAP | late | both | 4 | 0.46% | 0.10% | 0.72% | 0.50% | 75.00% | 0.63% | 0.07% | 0.46% | 3.04% |
| SPACE-USDT-SWAP | late | long | 2 | -0.00% | -0.00% | 0.07% | 0.07% | 50.00% | 0.06% | 0.07% | -0.00% | 2.52% |
| SPACE-USDT-SWAP | late | short | 2 | 0.92% | 0.92% | 0.77% | 0.77% | 100.00% | 0.92% | n/a | n/a | 3.57% |
| TRUTH-USDT-SWAP | all | both | 70 | 0.86% | 0.59% | 1.34% | 1.69% | 70.00% | 1.43% | 0.47% | 0.86% | 2.98% |
| TRUTH-USDT-SWAP | all | long | 33 | 0.98% | 0.62% | 1.30% | 1.64% | 78.79% | 1.38% | 0.50% | 0.98% | 2.83% |
| TRUTH-USDT-SWAP | all | short | 37 | 0.75% | 0.46% | 1.37% | 1.68% | 62.16% | 1.49% | 0.46% | 0.75% | 3.11% |
| TRUTH-USDT-SWAP | early | both | 69 | 0.87% | 0.62% | 1.35% | 1.70% | 69.57% | 1.45% | 0.47% | 0.87% | 2.99% |
| TRUTH-USDT-SWAP | early | long | 33 | 0.98% | 0.62% | 1.30% | 1.64% | 78.79% | 1.38% | 0.50% | 0.98% | 2.83% |
| TRUTH-USDT-SWAP | early | short | 36 | 0.76% | 0.56% | 1.39% | 1.70% | 61.11% | 1.54% | 0.46% | 0.76% | 3.14% |
| TRUTH-USDT-SWAP | late | both | 1 | 0.40% | 0.40% | 0.00% | 0.00% | 100.00% | 0.40% | n/a | n/a | 1.88% |
| TRUTH-USDT-SWAP | late | short | 1 | 0.40% | 0.40% | 0.00% | 0.00% | 100.00% | 0.40% | n/a | n/a | 1.88% |
| UB-USDT-SWAP | all | both | 32 | 1.28% | 0.62% | 2.30% | 2.20% | 65.62% | 2.29% | 0.64% | 1.28% | 3.81% |
| UB-USDT-SWAP | all | long | 12 | 0.94% | 0.05% | 2.02% | 2.69% | 50.00% | 2.48% | 0.59% | 0.94% | 3.58% |
| UB-USDT-SWAP | all | short | 20 | 1.49% | 0.81% | 2.44% | 1.91% | 75.00% | 2.22% | 0.70% | 1.49% | 3.95% |
| UB-USDT-SWAP | early | both | 18 | 1.59% | 0.60% | 2.63% | 2.23% | 72.22% | 2.44% | 0.62% | 1.59% | 4.24% |
| UB-USDT-SWAP | early | long | 6 | 1.20% | 0.50% | 1.78% | 1.98% | 66.67% | 1.99% | 0.36% | 1.20% | 3.14% |
| UB-USDT-SWAP | early | short | 12 | 1.78% | 0.60% | 2.95% | 2.51% | 75.00% | 2.64% | 0.79% | 1.78% | 4.79% |
| UB-USDT-SWAP | late | both | 14 | 0.89% | 0.66% | 1.72% | 2.45% | 57.14% | 2.05% | 0.66% | 0.89% | 3.27% |
| UB-USDT-SWAP | late | long | 6 | 0.68% | -0.40% | 2.20% | 2.09% | 33.33% | 3.46% | 0.71% | 0.68% | 4.02% |
| UB-USDT-SWAP | late | short | 8 | 1.05% | 0.95% | 1.22% | 1.62% | 75.00% | 1.58% | 0.56% | 1.05% | 2.70% |

## Stability Verdict

Stable positive pair/side/time cells: `3`.
- `BILL-USDT-SWAP` `both`: all `1.09%`, early `1.12%`, late `1.02%`.
- `BILL-USDT-SWAP` `short`: all `1.38%`, early `1.38%`, late `1.40%`.
- `UB-USDT-SWAP` `both`: all `1.28%`, early `1.59%`, late `0.89%`.

## Base-Model Verdict

No robust base model: no pair passed the fixed model with positive long and short sides across both early and late time splits with normal sample. The best BSB/early-entry rows are useful research candidates, not production config.

## GPT Hypotheses

- The v2 BSB edge is likely a directional micro-regime, not a universal continuation rule, unless long and short both stay positive.
- Cluster + early entry is still the correct direction for research, but the base needs more days or cross-pair confirmation.
- Median below mean implies fat-tail dependence; size/risk should be capped until more sample confirms the tail is repeatable.
