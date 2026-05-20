# Continuation Structural Exit V2 - 20.05.2026

V2 changes tested together: impulse-candle stop, loose structural exits, and cluster regime split. Net includes 0.20% taker round trip and 0.05% entry slippage.

## Top Close-Entry Rows, Full Sample

| rank | pair | pattern | mode | buffer | n | avg net | med net | win net | avg MFE | capture | stopped_before_mfe |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | BSB-USDT-SWAP | single_impulse | structure_k1 | 0.10% | 173 | **0.45%** | -0.66% | 34.10% | 3.22% | 20.22% | 15.61% |
| 2 | BSB-USDT-SWAP | single_impulse | structure_k1 | 0.20% | 173 | **0.44%** | -0.66% | 34.10% | 3.22% | 19.88% | 13.87% |
| 3 | BSB-USDT-SWAP | single_impulse | structure_k2 | 0.20% | 173 | **0.36%** | -0.97% | 30.06% | 3.53% | 15.95% | 18.50% |
| 4 | BSB-USDT-SWAP | single_impulse | structure_k2 | 0.10% | 173 | **0.34%** | -1.05% | 29.48% | 3.47% | 15.69% | 20.81% |
| 5 | BSB-USDT-SWAP | single_impulse | structure_k1 | 0.00% | 173 | **0.31%** | -0.70% | 33.53% | 3.00% | 16.85% | 17.34% |
| 6 | BSB-USDT-SWAP | single_impulse | structure_k3 | 0.20% | 173 | **0.23%** | -1.32% | 29.48% | 3.57% | 12.14% | 22.54% |
| 7 | BSB-USDT-SWAP | single_impulse | structure_k3 | 0.10% | 173 | **0.23%** | -1.25% | 28.90% | 3.51% | 12.30% | 24.86% |
| 8 | BSB-USDT-SWAP | single_impulse | structure_k2 | 0.00% | 173 | **0.17%** | -1.15% | 28.32% | 3.21% | 11.66% | 24.28% |
| 9 | BSB-USDT-SWAP | staircase | structure_k1 | 0.10% | 106 | **0.15%** | -0.81% | 33.02% | 2.81% | 12.48% | 25.47% |
| 10 | BSB-USDT-SWAP | staircase | structure_k1 | 0.20% | 106 | **0.15%** | -0.81% | 33.96% | 2.82% | 12.29% | 19.81% |
| 11 | BSB-USDT-SWAP | staircase | structure_k2 | 0.10% | 106 | **0.12%** | -0.95% | 30.19% | 3.09% | 10.33% | 29.25% |
| 12 | BSB-USDT-SWAP | staircase | structure_k2 | 0.20% | 106 | **0.10%** | -1.04% | 31.13% | 3.11% | 9.54% | 23.58% |
| 13 | BSB-USDT-SWAP | staircase | structure_k1 | 0.00% | 106 | **0.08%** | -0.81% | 32.08% | 2.65% | 10.60% | 29.25% |
| 14 | BSB-USDT-SWAP | single_impulse | structure_k3 | 0.00% | 173 | **0.07%** | -1.26% | 27.75% | 3.25% | 8.36% | 27.75% |
| 15 | BSB-USDT-SWAP | staircase | structure_k2 | 0.00% | 106 | **0.06%** | -0.88% | 29.25% | 2.92% | 8.83% | 33.96% |
| 16 | BSB-USDT-SWAP | staircase | structure_k3 | 0.10% | 106 | **0.01%** | -0.97% | 28.30% | 3.13% | 6.80% | 33.96% |
| 17 | BSB-USDT-SWAP | staircase | structure_k3 | 0.20% | 106 | **-0.02%** | -1.07% | 29.25% | 3.16% | 5.69% | 31.13% |
| 18 | BSB-USDT-SWAP | staircase | structure_k3 | 0.00% | 106 | **-0.04%** | -0.95% | 27.36% | 2.97% | 5.38% | 37.74% |
| 19 | BSB-USDT-SWAP | single_impulse | giveback_50 | 0.20% | 173 | **-0.05%** | 0.12% | 75.72% | 1.07% | 13.71% | 22.54% |
| 20 | BSB-USDT-SWAP | single_impulse | giveback_50 | 0.00% | 173 | **-0.06%** | 0.12% | 73.41% | 1.06% | 13.68% | 24.86% |
| 21 | BSB-USDT-SWAP | single_impulse | giveback_50 | 0.10% | 173 | **-0.06%** | 0.12% | 74.57% | 1.06% | 13.43% | 23.70% |
| 22 | BSB-USDT-SWAP | staircase | giveback_50 | 0.10% | 106 | **-0.09%** | 0.11% | 67.92% | 0.98% | 11.31% | 30.19% |
| 23 | BSB-USDT-SWAP | staircase | giveback_50 | 0.20% | 106 | **-0.10%** | 0.11% | 70.75% | 0.99% | 10.41% | 27.36% |
| 24 | BSB-USDT-SWAP | staircase | giveback_50 | 0.00% | 106 | **-0.10%** | 0.10% | 65.09% | 0.96% | 10.50% | 33.02% |
| 25 | BSB-USDT-SWAP | staircase | giveback_40 | 0.10% | 106 | **-0.10%** | 0.15% | 67.92% | 0.81% | 12.01% | 30.19% |
| 26 | BSB-USDT-SWAP | staircase | giveback_40 | 0.20% | 106 | **-0.11%** | 0.15% | 70.75% | 0.82% | 11.09% | 27.36% |
| 27 | BSB-USDT-SWAP | staircase | giveback_40 | 0.00% | 106 | **-0.11%** | 0.14% | 65.09% | 0.79% | 10.77% | 33.02% |
| 28 | BSB-USDT-SWAP | single_impulse | giveback_40 | 0.20% | 173 | **-0.12%** | 0.16% | 75.72% | 0.83% | 10.17% | 22.54% |
| 29 | BSB-USDT-SWAP | single_impulse | giveback_40 | 0.00% | 173 | **-0.12%** | 0.16% | 73.41% | 0.82% | 9.87% | 24.86% |
| 30 | BSB-USDT-SWAP | single_impulse | giveback_40 | 0.10% | 173 | **-0.12%** | 0.16% | 74.57% | 0.82% | 9.65% | 23.70% |
| 31 | USELESS-USDT-SWAP | single_impulse | structure_k3 | 0.10% | 29 | **-0.13%** | -0.24% | 41.38% | 0.94% | 7.96% | 3.45% |
| 32 | USELESS-USDT-SWAP | single_impulse | structure_k3 | 0.20% | 29 | **-0.13%** | -0.24% | 41.38% | 0.94% | 7.59% | 3.45% |
| 33 | BSB-USDT-SWAP | single_impulse | giveback_30 | 0.20% | 173 | **-0.14%** | 0.19% | 75.72% | 0.69% | 8.52% | 22.54% |
| 34 | BSB-USDT-SWAP | staircase | giveback_30 | 0.10% | 106 | **-0.14%** | 0.16% | 67.92% | 0.66% | 8.79% | 30.19% |
| 35 | BSB-USDT-SWAP | single_impulse | giveback_30 | 0.00% | 173 | **-0.14%** | 0.18% | 73.41% | 0.69% | 8.09% | 24.86% |
| 36 | BSB-USDT-SWAP | single_impulse | giveback_30 | 0.10% | 173 | **-0.15%** | 0.18% | 74.57% | 0.69% | 7.73% | 23.70% |
| 37 | BSB-USDT-SWAP | staircase | giveback_30 | 0.20% | 106 | **-0.15%** | 0.17% | 70.75% | 0.67% | 7.90% | 27.36% |
| 38 | BSB-USDT-SWAP | staircase | giveback_30 | 0.00% | 106 | **-0.15%** | 0.16% | 65.09% | 0.65% | 7.38% | 33.02% |
| 39 | SAHARA-USDT-SWAP | single_impulse | giveback_30 | 0.00% | 114 | **-0.15%** | 0.18% | 69.30% | 0.59% | 8.17% | 25.44% |
| 40 | SAHARA-USDT-SWAP | single_impulse | giveback_30 | 0.10% | 114 | **-0.15%** | 0.18% | 70.18% | 0.59% | 7.93% | 23.68% |

## Cluster Delta

| pair | pattern | mode | buffer | all net | cluster net | delta | all n | cluster n |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| BSB-USDT-SWAP | single_impulse | structure_k2 | 0.00% | 0.17% | 0.97% | **0.80%** | 173 | 62 |
| BSB-USDT-SWAP | staircase | structure_k2 | 0.10% | 0.12% | 0.88% | **0.76%** | 106 | 36 |
| BSB-USDT-SWAP | staircase | structure_k2 | 0.20% | 0.10% | 0.84% | **0.74%** | 106 | 36 |
| BSB-USDT-SWAP | single_impulse | structure_k3 | 0.00% | 0.07% | 0.78% | **0.70%** | 173 | 62 |
| BSB-USDT-SWAP | staircase | structure_k3 | 0.10% | 0.01% | 0.69% | **0.68%** | 106 | 36 |
| BSB-USDT-SWAP | single_impulse | structure_k1 | 0.00% | 0.31% | 0.97% | **0.66%** | 173 | 62 |
| BSB-USDT-SWAP | staircase | structure_k3 | 0.20% | -0.02% | 0.64% | **0.66%** | 106 | 36 |
| BSB-USDT-SWAP | staircase | structure_k2 | 0.00% | 0.06% | 0.66% | **0.60%** | 106 | 36 |
| BSB-USDT-SWAP | single_impulse | structure_k2 | 0.10% | 0.34% | 0.93% | **0.59%** | 173 | 62 |
| BSB-USDT-SWAP | single_impulse | structure_k2 | 0.20% | 0.36% | 0.91% | **0.55%** | 173 | 62 |
| BSB-USDT-SWAP | staircase | structure_k1 | 0.10% | 0.15% | 0.67% | **0.52%** | 106 | 36 |
| BSB-USDT-SWAP | staircase | structure_k3 | 0.00% | -0.04% | 0.48% | **0.52%** | 106 | 36 |
| BSB-USDT-SWAP | staircase | structure_k1 | 0.20% | 0.15% | 0.64% | **0.50%** | 106 | 36 |
| BSB-USDT-SWAP | single_impulse | structure_k3 | 0.10% | 0.23% | 0.73% | **0.49%** | 173 | 62 |
| BSB-USDT-SWAP | single_impulse | structure_k1 | 0.10% | 0.45% | 0.94% | **0.49%** | 173 | 62 |
| BSB-USDT-SWAP | single_impulse | structure_k1 | 0.20% | 0.44% | 0.93% | **0.49%** | 173 | 62 |
| BSB-USDT-SWAP | single_impulse | structure_k3 | 0.20% | 0.23% | 0.68% | **0.44%** | 173 | 62 |
| BSB-USDT-SWAP | single_impulse | giveback_50 | 0.00% | -0.06% | 0.33% | **0.38%** | 173 | 62 |
| BSB-USDT-SWAP | single_impulse | giveback_50 | 0.10% | -0.06% | 0.31% | **0.37%** | 173 | 62 |
| BSB-USDT-SWAP | staircase | structure_k1 | 0.00% | 0.08% | 0.44% | **0.36%** | 106 | 36 |
| BSB-USDT-SWAP | single_impulse | giveback_50 | 0.20% | -0.05% | 0.29% | **0.34%** | 173 | 62 |
| EDEN-USDT-SWAP | single_impulse | structure_k2 | 0.10% | -0.27% | -0.00% | **0.27%** | 508 | 148 |
| BILL-USDT-SWAP | single_impulse | structure_k1 | 0.00% | -0.29% | -0.03% | **0.26%** | 992 | 183 |
| BILL-USDT-SWAP | single_impulse | structure_k1 | 0.10% | -0.30% | -0.04% | **0.26%** | 992 | 183 |
| EDEN-USDT-SWAP | single_impulse | structure_k2 | 0.20% | -0.25% | 0.01% | **0.26%** | 508 | 148 |
| BILL-USDT-SWAP | single_impulse | structure_k1 | 0.20% | -0.30% | -0.04% | **0.26%** | 992 | 183 |
| BSB-USDT-SWAP | single_impulse | giveback_40 | 0.00% | -0.12% | 0.14% | **0.25%** | 173 | 62 |
| BILL-USDT-SWAP | single_impulse | structure_k3 | 0.00% | -0.33% | -0.08% | **0.25%** | 992 | 183 |
| BILL-USDT-SWAP | single_impulse | structure_k3 | 0.10% | -0.33% | -0.09% | **0.25%** | 992 | 183 |
| BSB-USDT-SWAP | staircase | giveback_50 | 0.10% | -0.09% | 0.16% | **0.25%** | 106 | 36 |

## Conclusion

Positive full-sample close-entry rows with n>=20: `16`.
If the positive rows are absent or only appear in small cluster buckets, do not change `config.yaml`; use them only as paper candidates.
