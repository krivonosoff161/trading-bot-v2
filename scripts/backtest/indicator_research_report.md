# Indicator Research

Updated from the latest local run on 2026-05-08.

Run these scripts to refresh the report inputs:

```bash
python scripts/backtest/bt_bb_screener.py
python scripts/backtest/bt_breakeven.py
python scripts/backtest/bt_pump_trailing.py
```

Results are saved to:

- `scripts/backtest/bt_bb_screener_results.json`
- `scripts/backtest/bt_breakeven_results.json`
- `scripts/backtest/bt_pump_trailing_results.json`

Populate the table below from those JSON files after each run.

## BB Screener

| Mode | n | WR | PF | avg_R | Notes |
|---|---:|---:|---:|---:|---|
| 5m BB | 651 | 62.1% | 0.98 | -0.008 | baseline PF=1.98 on BTC/ETH/DOGE; broadening to 6 pairs diluted edge |
| 3m BB | 1720 | 66.2% | 0.79 | -0.069 | much more flow, worse quality |
| 1H BB | 161 | 41.6% | 0.90 | -0.051 | double-touch mode underperformed |

## Breakeven Sweep

| Config | n | WR | PF | avg_R | TIME_EXIT | BE_EXIT |
|---|---:|---:|---:|---:|---:|---:|
| tp1=0.5, BE=0.5, tp2=fvg | 452 | 37.4% | 1.63 | +0.093 | 292 | 123 |
| tp1=0.5, BE=0.5, tp2=3.0R | 452 | 36.9% | 1.63 | +0.092 | 298 | 125 |
| tp1=0.5, BE=0.5, tp2=2.5R | 452 | 36.9% | 1.62 | +0.091 | 292 | 125 |

Lowest `TIME_EXIT` was `tp1=0.3, BE=0.3, tp2=1.5R`: `TIME_EXIT=196`, but PF fell to `1.24` and `avg_R` to `+0.029`.

## Pump Trailing

Baseline from `pump_labels.jsonl`: `n=148`, `WR=43.2%`, `PF=1.68`, `avg_R=+0.541`, `avg_hold=13.0m`.

| Config | n | WR | PF | avg_R | BE_EXIT |
|---|---:|---:|---:|---:|---:|
| BE=0.0%, trail=0.5 ATR, TP=2.5 ATR | 145 | 39.3% | 1.41 | +0.055 | 71 |
| BE=0.0%, trail=1.0 ATR, TP=2.5 ATR | 145 | 22.1% | 0.38 | -0.211 | 31 |
| BE=1.0%, trail=1.0 ATR, TP=3.0 ATR | 145 | 27.6% | 0.37 | -0.463 | 35 |

## Conclusions

- Best BB timeframe: `5m` was the least bad on the 6-pair expansion. It still missed the old 3-pair baseline badly (`PF 0.98` vs `1.98`).
- `3m` increased signal count (`1720` vs `651`) but quality deteriorated; more flow did not translate into edge.
- The tested breakeven logic did not solve `TIME_EXIT` cleanly. The best-PF configs still left `292-298` time exits; the lowest-time config reduced them, but with noticeably worse PF and `avg_R`.
- FVG as TP2 helped only marginally: `PF 1.63` and `avg_R +0.093` vs `PF 1.63/+0.092` at `3.0R`. It is a small edge at best, not a decisive improvement.
- Pump trailing underperformed the current paper baseline. The only positive config was the tightest one (`BE 0%, trail 0.5 ATR`), but it still lagged baseline on WR, PF, and `avg_R`.
