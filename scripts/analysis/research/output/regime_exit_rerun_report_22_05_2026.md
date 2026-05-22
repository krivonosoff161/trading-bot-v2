# Regime Exit Re-run - 22.05.2026

Replay period: `2026-05-04T19:00:00Z` to `2026-05-14T19:00:00Z`. Entry, direction, regime labels, peak guard, universe, fee and slippage are kept from Phase B. Only exit and initial stop are changed.

## Exit Models Tested

- `old fixed TP`: yesterday's baseline, fixed `1.1R/1.4R` off ATR stop.
- `structure_k1/2/3`: structural stop behind impulse-bar extreme plus buffer, then ride until a closed 15m candle breaks the previous k-bar swing level.
- `giveback_30/40/50`: structural initial stop, then exit after giving back X% of best favorable excursion.
- `scaled_tp_50/75/100`: TP distance is scaled to the impulse candle body, not ATR.

## Best Exit Per Cell

| cell | model | best exit | filled | old net | new net | delta | old cap | new cap | entry lag | edge exists | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DRIFT | drift_fast | giveback_30 | 25 | -0.59% | -0.39% | 0.19% | 19.15% | 14.39% | 0.30% | 48.00% | NO-GO: net<=0 |
| RANGING | range_fade | structure_k2 | 29 | -0.11% | 0.37% | 0.48% | 51.31% | 31.71% | -0.26% | 48.28% | NO-GO: side split fails |
| TRENDING_IMPULSE | trend_impulse | structure_k1 | 55 | -0.04% | 0.01% | 0.05% | 40.77% | 25.55% | 0.87% | 81.82% | NO-GO: side split fails |

## Top Exit Grid

| cell | model | exit | filled | new net | delta | capture | WR | MAE before MFE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RANGING | range_fade | structure_k2 | 29 | 0.37% | 0.48% | 31.71% | 51.72% | 65.52% |
| RANGING | range_fade | structure_k3 | 29 | 0.36% | 0.47% | 30.92% | 44.83% | 65.52% |
| RANGING | range_fade | structure_k1 | 29 | 0.24% | 0.35% | 24.80% | 41.38% | 65.52% |
| RANGING | range_fade | giveback_50 | 29 | 0.02% | 0.13% | 19.51% | 24.14% | 65.52% |
| RANGING | range_fade | scaled_tp_100 | 29 | 0.01% | 0.12% | 53.54% | 72.41% | 65.52% |
| TRENDING_IMPULSE | trend_impulse | structure_k1 | 55 | 0.01% | 0.05% | 25.55% | 45.45% | 27.27% |
| RANGING | range_fade | giveback_40 | 29 | 0.01% | 0.11% | 18.80% | 24.14% | 65.52% |
| TRENDING_IMPULSE | trend_impulse | structure_k2 | 55 | -0.01% | 0.03% | 26.78% | 45.45% | 27.27% |
| TRENDING_IMPULSE | trend_impulse | structure_k3 | 55 | -0.01% | 0.03% | 26.78% | 45.45% | 27.27% |
| RANGING | range_fade | scaled_tp_75 | 29 | -0.02% | 0.08% | 51.52% | 72.41% | 65.52% |
| RANGING | range_fade | scaled_tp_50 | 29 | -0.05% | 0.06% | 52.38% | 72.41% | 65.52% |
| TRENDING_IMPULSE | trend_impulse | giveback_30 | 55 | -0.11% | -0.07% | 29.38% | 41.82% | 27.27% |
| RANGING | range_fade | giveback_30 | 29 | -0.15% | -0.04% | 24.09% | 31.03% | 65.52% |
| TRENDING_IMPULSE | trend_impulse | giveback_40 | 55 | -0.19% | -0.15% | 24.57% | 40.00% | 27.27% |
| TRENDING_IMPULSE | trend_impulse | giveback_50 | 55 | -0.20% | -0.16% | 22.61% | 40.00% | 27.27% |
| TRENDING_IMPULSE | trend_impulse | scaled_tp_100 | 55 | -0.37% | -0.32% | 30.14% | 47.27% | 27.27% |
| DRIFT | drift_fast | giveback_30 | 25 | -0.39% | 0.19% | 14.39% | 12.00% | 16.00% |
| DRIFT | drift_fast | giveback_40 | 25 | -0.39% | 0.19% | 14.39% | 12.00% | 16.00% |
| DRIFT | drift_fast | giveback_50 | 25 | -0.39% | 0.19% | 14.39% | 12.00% | 16.00% |
| TRENDING_IMPULSE | trend_impulse | scaled_tp_75 | 55 | -0.42% | -0.38% | 27.78% | 49.09% | 27.27% |

## Side Split For Best Exits

| cell | exit | side | filled | new net | delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| DRIFT | giveback_30 | long | 13 | -0.33% | 0.26% |
| DRIFT | giveback_30 | short | 12 | -0.46% | 0.12% |
| RANGING | structure_k2 | long | 11 | 0.68% | 0.69% |
| RANGING | structure_k2 | short | 18 | 0.19% | 0.35% |
| TRENDING_IMPULSE | structure_k1 | long | 23 | -0.67% | -0.08% |
| TRENDING_IMPULSE | structure_k1 | short | 32 | 0.50% | 0.15% |

## Early/Late Split For Best Exits

| cell | exit | period | filled | new net | delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| DRIFT | giveback_30 | early | 14 | -0.36% | 0.14% |
| DRIFT | giveback_30 | late | 11 | -0.44% | 0.25% |
| RANGING | structure_k2 | early | 12 | 0.51% | 0.73% |
| RANGING | structure_k2 | late | 17 | 0.28% | 0.30% |
| TRENDING_IMPULSE | structure_k1 | early | 35 | 0.07% | -0.13% |
| TRENDING_IMPULSE | structure_k1 | late | 20 | -0.10% | 0.37% |

## Volatility Tier Split For Best Exits

| cell | exit | tier | filled | new net | delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| DRIFT | giveback_30 | low_vol_alt | 1 | 0.12% | -0.20% |
| DRIFT | giveback_30 | major | 8 | -0.32% | 0.12% |
| DRIFT | giveback_30 | mid_vol_alt | 16 | -0.46% | 0.25% |
| RANGING | structure_k2 | high_vol_alt | 2 | -0.08% | 0.29% |
| RANGING | structure_k2 | low_vol_alt | 5 | 0.10% | 0.26% |
| RANGING | structure_k2 | major | 5 | 0.06% | 0.06% |
| RANGING | structure_k2 | mid_vol_alt | 17 | 0.60% | 0.69% |
| TRENDING_IMPULSE | structure_k1 | high_vol_alt | 15 | 0.87% | -0.00% |
| TRENDING_IMPULSE | structure_k1 | low_vol_alt | 2 | -0.27% | -0.05% |
| TRENDING_IMPULSE | structure_k1 | major | 2 | -0.43% | -0.19% |
| TRENDING_IMPULSE | structure_k1 | mid_vol_alt | 36 | -0.30% | 0.09% |

## Execution Diagnostics

- new-exit outcomes: `{'SL': 435, 'GIVEBACK_30': 71, 'GIVEBACK_40': 64, 'GIVEBACK_50': 61, 'STRUCT_K1': 35, 'TIME': 128, 'TP_MOVE_50': 56, 'TP_MOVE_75': 53, 'TP_MOVE_100': 51, 'STRUCT_K2': 17, 'STRUCT_K3': 10}`
- `entry lag` is directional move already passed from impulse-bar open to model entry close.
- `edge exists` separates cases where the movement existed from cases where there was not enough movement in the model side.
- `MAE before MFE` flags stop/noise arriving before the favorable move.

## Verdict

Ride-style exits improve the measurement in several cells, especially where the old TP clipped the first ATR-sized fragment. The strict production criterion is still applied without relaxing side or time stability. Cells that improve only on one side or one volatility tier are research candidates, not config changes.

## GPT Hypotheses

- If a cell has high `edge exists` but low capture, the problem is execution timing/exit, not absence of setup edge.
- Structure exits should help high-volatility impulse cells more than majors because their impulse size is larger than fee and stop noise.
- Giveback exits can over-hold flat/no-edge moves; those should be separated by `edge exists` and entry-lag diagnostics.
