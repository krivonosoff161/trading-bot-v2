# Volatility Scalper Tape Research

Scope: AI and EDEN are analyzed separately. GAP rows are skipped; missing 1m bars are not interpolated.
Explosive candle threshold: `abs(price_change_pct) >= 0.8%` on a 1m OHLCV bar.

Forward return is measured from the explosive candle close to the horizon close in the entry direction.

## AI-USDT-SWAP

- bars: `5509`
- explosive bars: `220` (usable)
- avg abs explosion size: `1.27%`
- optimal hold by avg forward return: `5m`, avg `-0.05%`

### Explosive Candles Per Day

| date | total | long | short | avg_abs_size |
| --- | ---: | ---: | ---: | ---: |
| 2026-05-15 | 102 | 52 | 50 | 1.18% |
| 2026-05-16 | 18 | 10 | 8 | 1.00% |
| 2026-05-17 | 29 | 15 | 14 | 1.22% |
| 2026-05-18 | 66 | 30 | 36 | 1.53% |
| 2026-05-19 | 5 | 2 | 3 | 1.02% |

### Forward After Explosive Candle

| hold | n | avg_forward_return | continued | reversed_gt_0.5 |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 220 | -0.06% | 40.91% | 17.73% |
| 2m | 220 | -0.12% | 39.09% | 25.91% |
| 3m | 220 | -0.09% | 44.55% | 28.64% |
| 5m | 220 | -0.05% | 46.36% | 36.36% |
| 10m | 219 | -0.12% | 38.36% | 45.21% |
| 15m | 218 | -0.21% | 42.20% | 45.41% |

### Oscillation Rhythm

- next opposite explosive candle: n=212, median=11.00m, avg=41.72m
- full opposite-back cycle: n=202, median=25.00m, avg=81.51m

### Pre-Explosion Predictors

Predictor WR means: when the predictor fires on minute `t`, did minute `t+1` become an explosive candle in the predicted direction. For `quiet_pre_5m_any_direction`, either direction counts.

| predictor | present_n | present_WR | absent_n | absent_next_explosion_rate |
| --- | ---: | ---: | ---: | ---: |
| pre_buy_ratio_directional | 845 | 0.59% | 4662 | 4.44% |
| pre_cvd_directional | 5504 | 2.03% | 3 | 0.00% |
| prev3_same_direction | 1068 | 1.31% | 4439 | 4.26% |
| quiet_pre_5m_any_direction | 3596 | 2.09% | 1911 | 7.59% |

### Real Pump Trade Cross-Check

- real pump trades in logs: `28`
- trades with entry-minute tape coverage: `16` (usable)
- covered avg label net: `0.76%`
- covered avg 3m tape exit before fees: `0.32%`
- covered avg optimal-hold tape exit before fees: `0.60%`

| signal_id | opened_at | dir | tape | explosion_bar | label | label_net | label_mfe | tape_mfe_1-5m | exit_3m | exit_opt | hold |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| b6255cd6-76e | 2026-05-09T10:24:00+00:00 | short | False | False | SL | -0.51% | n/a | n/a | n/a | n/a | 1.00m |
| 82aff5ec-ce2 | 2026-05-09T16:16:00+00:00 | long | False | False | SL | -0.98% | n/a | n/a | n/a | n/a | 3.00m |
| fc38da8b-574 | 2026-05-09T16:19:00+00:00 | long | False | False | TP | 1.60% | n/a | n/a | n/a | n/a | 12.00m |
| 14890695-7bf | 2026-05-11T11:19:00+00:00 | short | False | False | SL | -0.47% | n/a | n/a | n/a | n/a | 1.00m |
| a0ecb3cf-f5f | 2026-05-11T16:29:00+00:00 | short | False | False | SL | -0.34% | n/a | n/a | n/a | n/a | 1.00m |
| ff44c717-01f | 2026-05-11T16:40:00+00:00 | long | False | False | SL | -0.56% | n/a | n/a | n/a | n/a | 4.00m |
| 88730382-815 | 2026-05-11T16:49:00+00:00 | short | False | False | SL | -0.82% | n/a | n/a | n/a | n/a | 42.00m |
| 626bbc77-7e6 | 2026-05-11T21:19:00+00:00 | short | False | False | SL | -0.96% | n/a | n/a | n/a | n/a | 6.00m |
| 745f2734-a8b | 2026-05-12T22:13:00+00:00 | long | False | False | SL | -1.58% | n/a | n/a | n/a | n/a | 29.00m |
| e29dfa38-606 | 2026-05-14T09:00:00+00:00 | short | False | False | SL | -0.80% | 0.06% | n/a | n/a | n/a | 2.00m |
| e925445e-5d6 | 2026-05-15T06:10:00+00:00 | long | True | False | TP | 3.41% | 3.59% | 3.07% | 1.92% | 2.72% | 7.00m |
| 6f708fbb-cb7 | 2026-05-15T12:30:00+00:00 | short | True | False | TP | 2.25% | 2.89% | 2.89% | 1.77% | 1.34% | 2.00m |
| f2771c45-770 | 2026-05-15T22:14:00+00:00 | short | False | False | TP | 1.00% | 1.25% | n/a | n/a | n/a | 11.00m |
| 6c2b9ae4-6ae | 2026-05-16T00:25:00+00:00 | long | False | False | SL | -1.04% | 0.52% | n/a | n/a | n/a | 5.00m |
| 76d8cda0-2c1 | 2026-05-16T14:20:00+00:00 | short | True | False | SL | -0.49% | 0.06% | 0.06% | -0.27% | -0.38% | 3.00m |
| 0c37477e-2c3 | 2026-05-16T15:45:00+00:00 | long | True | False | SL | -0.57% | 0.20% | 0.20% | -0.31% | -0.40% | 2.00m |
| b2c449f5-c0e | 2026-05-16T16:03:00+00:00 | long | True | False | SL | -0.97% | 0.93% | 1.01% | -0.48% | 0.25% | 17.00m |
| 4d2ce0ed-dca | 2026-05-16T23:00:00+00:00 | short | True | False | TP | 0.90% | 1.12% | 0.55% | 0.08% | 0.08% | 6.00m |
| 200ec125-a58 | 2026-05-17T04:45:00+00:00 | long | True | True | TP | 1.57% | 1.78% | 1.78% | -0.19% | 0.42% | 2.00m |
| 7136a4b5-72f | 2026-05-17T05:00:00+00:00 | long | True | False | TP | 1.20% | 1.37% | 1.05% | 0.16% | 0.99% | 6.00m |
| 66ece4c5-775 | 2026-05-18T01:50:00+00:00 | long | True | False | SL | -0.51% | 0.67% | 0.32% | 0.09% | 0.26% | 7.00m |
| 989ab849-deb | 2026-05-18T02:15:00+00:00 | long | True | True | TP | 4.99% | 5.11% | 3.34% | 1.15% | 2.72% | 9.00m |
| efe85f58-1de | 2026-05-18T05:10:00+00:00 | short | True | False | TP | 3.31% | 3.58% | 1.05% | 0.76% | 0.32% | 37.00m |
| 66a4ccb6-e11 | 2026-05-18T06:00:00+00:00 | short | True | False | SL | -0.80% | 0.73% | 0.70% | 0.16% | 0.51% | 14.00m |
| 84a27ffe-a76 | 2026-05-18T07:10:00+00:00 | short | True | False | SL | -1.66% | 2.50% | 0.93% | 0.22% | 0.44% | 106.00m |
| c4edce7a-970 | 2026-05-18T09:17:00+00:00 | long | True | True | SL | -0.70% | 0.00% | 0.03% | -0.35% | -0.49% | 1.00m |
| 64710a51-252 | 2026-05-18T12:00:00+00:00 | long | True | False | TP | 0.90% | 1.01% | 1.01% | 0.49% | 0.85% | 5.00m |
| ac88c02a-11b | 2026-05-18T19:45:00+00:00 | short | True | False | SL | -0.72% | 0.30% | 0.30% | -0.05% | -0.11% | 16.00m |

## EDEN-USDT-SWAP

- bars: `2363`
- explosive bars: `248` (usable)
- avg abs explosion size: `1.21%`
- optimal hold by avg forward return: `1m`, avg `0.06%`

### Explosive Candles Per Day

| date | total | long | short | avg_abs_size |
| --- | ---: | ---: | ---: | ---: |
| 2026-05-17 | 27 | 12 | 15 | 1.05% |
| 2026-05-18 | 111 | 57 | 54 | 1.24% |
| 2026-05-19 | 110 | 56 | 54 | 1.21% |

### Forward After Explosive Candle

| hold | n | avg_forward_return | continued | reversed_gt_0.5 |
| ---: | ---: | ---: | ---: | ---: |
| 1m | 248 | 0.06% | 47.58% | 16.53% |
| 2m | 248 | -0.00% | 47.58% | 29.44% |
| 3m | 248 | -0.03% | 45.56% | 31.05% |
| 5m | 248 | 0.05% | 52.02% | 30.65% |
| 10m | 247 | -0.01% | 48.58% | 34.41% |
| 15m | 246 | 0.05% | 50.00% | 40.24% |

### Oscillation Rhythm

- next opposite explosive candle: n=241, median=10.00m, avg=25.17m
- full opposite-back cycle: n=237, median=22.00m, avg=40.75m

### Pre-Explosion Predictors

Predictor WR means: when the predictor fires on minute `t`, did minute `t+1` become an explosive candle in the predicted direction. For `quiet_pre_5m_any_direction`, either direction counts.

| predictor | present_n | present_WR | absent_n | absent_next_explosion_rate |
| --- | ---: | ---: | ---: | ---: |
| pre_buy_ratio_directional | 216 | 1.85% | 2145 | 11.14% |
| pre_cvd_directional | 2361 | 4.87% | 0 | n/a |
| prev3_same_direction | 477 | 5.24% | 1884 | 10.46% |
| quiet_pre_5m_any_direction | 1507 | 7.03% | 854 | 16.63% |

### Real Pump Trade Cross-Check

- real pump trades in logs: `12`
- trades with entry-minute tape coverage: `8` (preliminary)
- covered avg label net: `-1.00%`
- covered avg 3m tape exit before fees: `-0.09%`
- covered avg optimal-hold tape exit before fees: `0.06%`

| signal_id | opened_at | dir | tape | explosion_bar | label | label_net | label_mfe | tape_mfe_1-5m | exit_3m | exit_opt | hold |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 6e87c407-fb1 | 2026-05-11T15:29:00+00:00 | short | False | False | SL | -0.92% | n/a | n/a | n/a | n/a | 82.00m |
| f359a9cf-d57 | 2026-05-16T08:10:00+00:00 | long | False | False | SL | -0.80% | 0.28% | n/a | n/a | n/a | 4.00m |
| 0a7160cf-98d | 2026-05-16T08:45:00+00:00 | long | False | False | SL | -1.47% | 0.10% | n/a | n/a | n/a | 1.00m |
| 47d12c05-7c7 | 2026-05-17T14:15:00+00:00 | long | False | False | TP | 3.14% | 3.75% | n/a | n/a | n/a | 6.00m |
| 583cf868-4f3 | 2026-05-17T22:30:00+00:00 | short | True | False | SL | -0.71% | 0.02% | 0.02% | -1.36% | -0.85% | 1.00m |
| bdf06ddf-741 | 2026-05-18T09:15:00+00:00 | long | True | False | TP | 1.92% | 2.02% | 2.61% | 0.44% | 1.63% | 2.00m |
| c16d4df9-c05 | 2026-05-19T04:45:00+00:00 | short | True | False | SL | -1.69% | 0.25% | 0.25% | -0.82% | -0.13% | 6.00m |
| b0deec1f-246 | 2026-05-19T06:45:00+00:00 | short | True | False | SL | -1.79% | 1.57% | 1.57% | 0.67% | 0.36% | 6.00m |
| 25cac8ff-829 | 2026-05-19T07:30:00+00:00 | short | True | True | SL | -1.57% | 1.08% | 1.08% | 0.60% | 0.11% | 4.00m |
| 1bc023f4-141 | 2026-05-19T11:05:00+00:00 | short | True | False | SL | -1.83% | 0.40% | 0.40% | -0.22% | 0.15% | 5.00m |
| 946a9f4e-960 | 2026-05-19T11:10:00+00:00 | short | True | False | SL | -2.20% | 0.70% | 0.70% | -0.09% | -0.09% | 6.00m |
| 08f3176a-fe8 | 2026-05-19T13:20:00+00:00 | long | True | True | BE | -0.10% | 1.99% | 1.99% | 0.06% | -0.67% | 12.00m |

## Architecture Implications

- If the best forward horizon is short and next-opposite median is also short, the new engine should treat explosive bars as scalp events, not hold events.
- Add symmetric long/short handling: `PUMP` and `DUMP` waves are both first-class entries.
- Add re-entry logic after an opposite explosive candle instead of session-level suppression after the first wave.
- Use pre-event predictors only per-pair; AI and EDEN are not blended in this report.
