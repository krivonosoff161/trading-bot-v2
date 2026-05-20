# Continuation Patterns - 20.05.2026

Patterns tested: single impulse (`abs 1m open->close >= 0.8%`) and staircase (`2 of 3`, cumulative `>=1.2%`, max opposite body `0.5%`, final close in directional 40%).
Net returns subtract `0.20%` taker round trip; exit grid reports include entry slippage.

## Event Counts And Main MFE

| pair | days | single n | staircase n | single avg MFE | staircase avg MFE | eligible note |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| BILL-USDT-SWAP | 10 | 983 | 824 | 2.10% | 1.97% | eligible |
| EDEN-USDT-SWAP | 4 | 453 | 338 | 2.36% | 2.23% | eligible |
| TRUTH-USDT-SWAP | 10 | 693 | 523 | 2.25% | 1.92% | eligible |
| RLS-USDT-SWAP | 3 | 211 | 123 | 2.10% | 2.14% | eligible |
| UB-USDT-SWAP | 7 | 389 | 332 | 2.08% | 1.97% | eligible |
| AI-USDT-SWAP | 6 | 220 | 179 | 1.78% | 1.67% | eligible |
| BSB-USDT-SWAP | 3 | 119 | 78 | 2.91% | 3.07% | eligible |
| SPACE-USDT-SWAP | 4 | 125 | 125 | 1.58% | 1.61% | eligible |
| SAHARA-USDT-SWAP | 9 | 113 | 106 | 1.26% | 1.09% | eligible |
| JELLYJELLY-USDT-SWAP | 3 | 36 | 36 | 1.19% | 1.14% | eligible |
| NOT-USDT-SWAP | 10 | 20 | 17 | 0.71% | 0.82% | eligible |
| BOME-USDT-SWAP | 10 | 18 | 18 | 0.61% | 0.77% | watch/excluded |
| OFC-USDT-SWAP | 2 | 39 | 21 | 1.14% | 1.00% | watch/excluded |
| USELESS-USDT-SWAP | 2 | 29 | 33 | 1.15% | 1.01% | watch/excluded |
| LAYER-USDT-SWAP | 2 | 4 | 4 | 0.57% | 0.47% | watch/excluded |
| CHIP-USDT-SWAP | 2 | 5 | 5 | 0.96% | 0.60% | watch/excluded |
| ONT-USDT-SWAP | 2 | 3 | 6 | 1.04% | 0.98% | watch/excluded |
| FOGO-USDT-SWAP | 1 | 0 | 0 | n/a | n/a | watch/excluded |
| HOME-USDT-SWAP | 1 | 2 | 3 | 1.26% | 0.27% | watch/excluded |

## Staircase Variant Counts

| pair | top variant | count |
| --- | --- | ---: |
| BILL-USDT-SWAP | 3_of_4_cum_1.0_opp_0.7 | 1251 |
| EDEN-USDT-SWAP | 2_of_3_cum_1.0_opp_0.7 | 460 |
| TRUTH-USDT-SWAP | 3_of_4_cum_1.0_opp_0.7 | 817 |
| RLS-USDT-SWAP | 2_of_3_cum_1.0_opp_0.7 | 177 |
| UB-USDT-SWAP | 3_of_4_cum_1.0_opp_0.7 | 546 |
| AI-USDT-SWAP | 4_of_5_cum_1.0_opp_0.7 | 335 |
| BSB-USDT-SWAP | 2_of_3_cum_1.0_opp_0.7 | 116 |
| SPACE-USDT-SWAP | 3_of_4_cum_1.0_opp_0.7 | 222 |
| SAHARA-USDT-SWAP | 4_of_5_cum_1.0_opp_0.7 | 248 |
| JELLYJELLY-USDT-SWAP | 4_of_5_cum_1.0_opp_0.7 | 90 |
| NOT-USDT-SWAP | 4_of_5_cum_1.0_opp_0.5 | 59 |
| BOME-USDT-SWAP | 4_of_5_cum_1.0_opp_0.4 | 85 |
| OFC-USDT-SWAP | 3_of_4_cum_1.0_opp_0.7 | 40 |
| USELESS-USDT-SWAP | 3_of_4_cum_1.0_opp_0.7 | 95 |
| LAYER-USDT-SWAP | 4_of_5_cum_1.0_opp_0.4 | 27 |
| CHIP-USDT-SWAP | 4_of_5_cum_1.0_opp_0.4 | 14 |
| ONT-USDT-SWAP | 2_of_3_cum_1.0_opp_0.4 | 8 |
| FOGO-USDT-SWAP | 4_of_5_cum_1.0_opp_0.4 | 4 |
| HOME-USDT-SWAP | 3_of_4_cum_1.0_opp_0.5 | 9 |

## Conclusion

Pairs with at least one positive net exit-grid row at `0.05%` slippage: `none`.
Do not add pairs to `config.yaml` unless their positive rows are stable across slippage and not just one overfit exit setting.
