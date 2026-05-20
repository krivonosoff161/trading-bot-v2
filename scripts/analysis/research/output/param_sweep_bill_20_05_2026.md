# BILL Parameter Sweep - 20.05.2026

Method: tick-level replay on `BILL-USDT-SWAP`; entry is the close tick of the explosive 1m candle; trade direction is against the explosion.
Fee is always `0.20%` round trip. `net_pnl` follows the requested formula: `tp_rate * tp_pct - sl_rate * sl_pct - fee`; timeout mark-to-market is shown separately as `realized_net`.

- events: `983`

## Top 10 By net_pnl

| rank | SL | TP | hold | BE | TP% | SL% | BE% | timeout% | avg_MFE | net_pnl | realized_net |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1.20% | 0.70% | 5m | off | 55.24% | 28.79% | 0.00% | 15.97% | 0.52% | **-0.16%** | -0.16% |
| 2 | 1.20% | 0.70% | 5m | 0.70% | 55.24% | 28.79% | 0.00% | 15.97% | 0.52% | **-0.16%** | -0.16% |
| 3 | 1.20% | 0.80% | 5m | 0.70% | 47.51% | 28.79% | 6.41% | 17.29% | 0.57% | **-0.17%** | -0.17% |
| 4 | 1.20% | 0.70% | 10m | off | 61.95% | 33.27% | 0.00% | 4.78% | 0.54% | **-0.17%** | -0.17% |
| 5 | 1.20% | 0.70% | 10m | 0.70% | 61.95% | 33.27% | 0.00% | 4.78% | 0.54% | **-0.17%** | -0.17% |
| 6 | 1.20% | 0.80% | 15m | 0.70% | 56.15% | 34.59% | 7.63% | 1.63% | 0.60% | **-0.17%** | -0.17% |
| 7 | 1.20% | 0.80% | 10m | 0.70% | 54.12% | 33.27% | 7.53% | 5.09% | 0.60% | **-0.17%** | -0.17% |
| 8 | 1.20% | 0.70% | 15m | off | 63.99% | 34.59% | 0.00% | 1.42% | 0.55% | **-0.17%** | -0.17% |
| 9 | 1.20% | 0.70% | 15m | 0.70% | 63.99% | 34.59% | 0.00% | 1.42% | 0.55% | **-0.17%** | -0.17% |
| 10 | 1.20% | 1.50% | 15m | 0.70% | 29.70% | 34.59% | 32.15% | 3.56% | 0.88% | **-0.17%** | -0.17% |

## Current Params

| SL | TP | hold | BE | TP% | SL% | BE% | timeout% | avg_MFE | net_pnl | realized_net |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.80% | 1.50% | 15m | 0.50% | 20.04% | 39.17% | 39.67% | 1.12% | 0.71% | **-0.21%** | -0.21% |

## Conclusion

Positive requested-formula combinations: `0` / `320`.
Positive realized-net combinations including timeout mark-to-market: `0` / `320`.
Best formula result is SL `1.20%`, TP `0.70%`, hold `5m`, BE `off` with net_pnl `-0.16%`.
Current BILL params are not positive by the requested formula after fees.
Do not change engine parameters in production from this file alone; use these results as the next paper config candidate set.
