# Main Screener Entry Timing - 21.05.2026

Scope: `logs/signals/main_signals.jsonl` joined to `main_signals_labels.jsonl`, only `valid: true`. Price replay uses 5m candles from local screener cache when available, otherwise public OKX history candles. Alternative-entry results are approximate 5m OHLC replays with original SL/TP levels and 0.20% round-trip taker fee.

- valid joined signals analyzed: `77`
- signals skipped: `0`
- TIME avg consumed: `74.51%`; TP avg consumed: `66.67%`

## Entry Timing By Regime x Style

| regime | style | n | decisive WR | TIME | avg consumed | p50 consumed | TIME consumed | TP consumed | bars | MFE | MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DRIFT | FAST | 39 | 84.21% | 51.28% | 84.34% | 63.96% | 81.53% | 90.72% | 7.77 | 1.45% | -1.02% |
| RANGING | FAST | 7 | 75.00% | 42.86% | 20.86% | 12.72% | 22.11% | 22.32% | 5.71 | 1.65% | -1.30% |
| TRENDING | FAST | 11 | 66.67% | 45.45% | 54.27% | 58.30% | 69.91% | 35.91% | 5.27 | 1.10% | -0.84% |
| TRENDING | SWING | 20 | 68.42% | 5.00% | 59.84% | 83.75% | 114.17% | 56.76% | 6.95 | 5.34% | -3.39% |

## Entry Timing By Outcome

| outcome | n | avg consumed | p50 consumed | bars | MFE | MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| TP1 | 26 | 64.47% | 44.89% | 5.62 | 4.15% | -0.91% |
| TP2 | 10 | 72.38% | 62.26% | 6.50 | 4.14% | -0.82% |
| SL | 12 | 55.69% | 63.86% | 8.08 | 0.52% | -3.82% |
| TIME | 29 | 74.51% | 60.87% | 8.00 | 1.09% | -1.66% |

## Alternative Entries

| regime | style | mode | signals | filled | fill | avg net | WR | TIME | SL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DRIFT | FAST | breakout_confirmation | 39 | 32 | 82.05% | -0.02% | 53.12% | 40.62% | 6.25% |
| DRIFT | FAST | immediate | 39 | 39 | 100.00% | 0.03% | 43.59% | 48.72% | 7.69% |
| DRIFT | FAST | pullback | 39 | 27 | 69.23% | 0.82% | 29.63% | 59.26% | 11.11% |
| RANGING | FAST | breakout_confirmation | 7 | 6 | 85.71% | -0.30% | 83.33% | 16.67% | 0.00% |
| RANGING | FAST | immediate | 7 | 7 | 100.00% | -0.03% | 71.43% | 14.29% | 14.29% |
| RANGING | FAST | pullback | 7 | 3 | 42.86% | -0.03% | 66.67% | 33.33% | 0.00% |
| TRENDING | FAST | breakout_confirmation | 11 | 9 | 81.82% | 0.02% | 33.33% | 55.56% | 11.11% |
| TRENDING | FAST | immediate | 11 | 11 | 100.00% | -0.08% | 36.36% | 45.45% | 18.18% |
| TRENDING | FAST | pullback | 11 | 8 | 72.73% | 0.10% | 12.50% | 62.50% | 25.00% |
| TRENDING | SWING | breakout_confirmation | 20 | 18 | 90.00% | -0.30% | 83.33% | 5.56% | 11.11% |
| TRENDING | SWING | immediate | 20 | 20 | 100.00% | -0.18% | 75.00% | 5.00% | 20.00% |
| TRENDING | SWING | pullback | 20 | 11 | 55.00% | 0.87% | 72.73% | 9.09% | 18.18% |

## Recommended Entry Style

- pullback is a research candidate for DRIFT x FAST, not production-ready: avg net 0.82% but WR 29.63% and TIME 59.26%.
- inconclusive for RANGING x FAST: best filled mode is pullback but avg net is -0.03% with fill 42.86% (small sample).
- inconclusive for TRENDING x FAST: pullback is only weakly positive (0.10% avg net) on 11 signals.
- pullback for TRENDING x SWING: avg net 0.87%, WR 72.73%, TIME 9.09%, fill 55.00% (normal live sample).

Interpretation notes:

- `move_consumed_pct` is measured from detected trigger-start open to the live entry, divided by live entry-to-TP1 distance. Values above 100% mean the pre-entry move was already larger than the remaining TP1 path.
- `pullback` waits for EMA20 or 38.2% impulse retrace after the detected trigger. If there is no touch, it is counted as no-fill and the fill rate matters.
- `breakout_confirmation` waits for a small ATR-buffer break beyond the live signal candle. It is intentionally stricter and can reduce fills.

## GPT Hypotheses

| split | bucket | n | TP | TIME | SL | avg consumed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| session | Asia 00-06 | 27 | 48.15% | 22.22% | 29.63% | 69.94% |
| session | EU 07-15 | 31 | 61.29% | 32.26% | 6.45% | 57.47% |
| session | US 16-23 | 19 | 21.05% | 68.42% | 10.53% | 82.05% |
| adx_1h | 20-25 | 14 | 21.43% | 64.29% | 14.29% | 64.00% |
| adx_1h | 25-35 | 28 | 50.00% | 35.71% | 14.29% | 75.24% |
| adx_1h | 35+ | 16 | 50.00% | 25.00% | 25.00% | 59.10% |
| adx_1h | <20 | 19 | 57.89% | 31.58% | 10.53% | 67.41% |
| vol_ratio | 1-2 | 25 | 44.00% | 32.00% | 24.00% | 86.73% |
| vol_ratio | 2+ | 25 | 48.00% | 36.00% | 16.00% | 46.35% |
| vol_ratio | <1 | 27 | 48.15% | 44.44% | 7.41% | 70.45% |
| fvg | no | 70 | 48.57% | 35.71% | 15.71% | 67.19% |
| fvg | yes | 7 | 28.57% | 57.14% | 14.29% | 75.10% |
| side | long | 56 | 44.64% | 41.07% | 14.29% | 72.96% |
| side | short | 21 | 52.38% | 28.57% | 19.05% | 54.45% |

## Caveats

- This is a small live sample. RANGING is especially thin, so treat its recommendation as hypothesis-grade.
- 5m OHLC cannot know the true intrabar order when both SL and TP are inside one candle; the simulator uses conservative SL-first ordering.
- Missing local candles were fetched from OKX public history during this run; the saved summary does not include raw candle dumps.
