# Regime Coverage Research - 21.05.2026

Replay period: `2026-05-04T19:00:00Z` to `2026-05-14T19:00:00Z` (`10` days).
Universe requested: `29` symbols; MTF loaded: `29`; decision-active: `28`; skipped: `0`.

This replay imports the real `src.strategy.signal_engine.compute_signal`. Funding, OI, order book, recent trades, and index-candle divergence are not reconstructed, so those fields are neutral/empty. The WS prefilter/cooldown/context gate are not replayed; this is engine recall at 15m closes.

Loaded but no replay decisions due insufficient warmup in the selected window: `BILL-USDT-SWAP`.

## Replay Decision Stream

| regime | n | ENTRY | ENTRY % | FAST entries | SWING entries | WAIT | NO_TRADE | avg vol | avg ADX |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CHOPPY | 598 | 0 | 0.00% | 0 | 0 | 0 | 598 | 1.11 | 22.47 |
| DRIFT | 7280 | 105 | 1.44% | 105 | 0 | 0 | 7175 | 1.22 | 22.41 |
| RANGING | 12066 | 1 | 0.01% | 1 | 0 | 0 | 12065 | 1.13 | 31.66 |
| TRENDING | 6964 | 15 | 0.22% | 0 | 15 | 0 | 6949 | 1.14 | 38.56 |

## Tradeable Movement Recall

| regime | type | moves | caught | recall | missed | WAIT | wrong side | avg move | p50 peak bars | top miss reasons |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CHOPPY | FAST | 7 | 0 | 0.00% | 7 | 0 | 0 | 3.67% | 3.00 | conditions_not_met:composite=3; conditions_not_met:low_vol_under_1=2; conditions_not_met:bias_1h_up=1 |
| CHOPPY | SWING | 48 | 0 | 0.00% | 48 | 0 | 0 | 5.03% | 13.00 | conditions_not_met:low_vol_under_1=22; conditions_not_met:bias_1h_down=9; conditions_not_met:composite=8 |
| DRIFT | FAST | 68 | 0 | 0.00% | 65 | 0 | 3 | 1.87% | 4.00 | conditions_not_met:low_vol_under_1=24; conditions_not_met:bias_1h_up=14; conditions_not_met:slope_not_down=10 |
| DRIFT | SWING | 408 | 9 | 2.21% | 386 | 0 | 13 | 3.04% | 13.00 | conditions_not_met:low_vol_under_1=126; conditions_not_met:bias_1h_up=70; conditions_not_met:bias_1h_down=49 |
| RANGING | FAST | 109 | 0 | 0.00% | 109 | 0 | 0 | 1.77% | 4.00 | conditions_not_met:bias_1h_up=29; conditions_not_met:low_vol_under_1=28; four_h_conflict=18 |
| RANGING | SWING | 698 | 0 | 0.00% | 698 | 0 | 0 | 3.02% | 13.00 | conditions_not_met:low_vol_under_1=184; conditions_not_met:bias_1h_up=132; four_h_conflict=123 |
| TRENDING | FAST | 67 | 0 | 0.00% | 66 | 0 | 1 | 2.72% | 4.00 | conditions_not_met:bias_1h_up=29; conditions_not_met:adx_not_rising=20; conditions_not_met:low_vol_under_1=11 |
| TRENDING | SWING | 446 | 3 | 0.67% | 441 | 0 | 2 | 3.86% | 13.00 | conditions_not_met:bias_1h_up=142; conditions_not_met:adx_not_rising=137; conditions_not_met:low_vol_under_1=73 |

## Top Silence Reasons On Missed Moves

| regime | type | reason | count | share |
| --- | ---: | ---: | ---: | ---: |
| CHOPPY | FAST | conditions_not_met:composite | 3 | 42.86% |
| CHOPPY | FAST | conditions_not_met:low_vol_under_1 | 2 | 28.57% |
| CHOPPY | FAST | conditions_not_met:bias_1h_up | 1 | 14.29% |
| CHOPPY | FAST | conditions_not_met:bias_1h_down | 1 | 14.29% |
| CHOPPY | SWING | conditions_not_met:low_vol_under_1 | 22 | 45.83% |
| CHOPPY | SWING | conditions_not_met:bias_1h_down | 9 | 18.75% |
| CHOPPY | SWING | conditions_not_met:composite | 8 | 16.67% |
| CHOPPY | SWING | conditions_not_met:bias_1h_up | 8 | 16.67% |
| CHOPPY | SWING | four_h_conflict | 1 | 2.08% |
| DRIFT | FAST | conditions_not_met:low_vol_under_1 | 24 | 35.29% |
| DRIFT | FAST | conditions_not_met:bias_1h_up | 14 | 20.59% |
| DRIFT | FAST | conditions_not_met:slope_not_down | 10 | 14.71% |
| DRIFT | FAST | conditions_not_met:bias_1h_down | 9 | 13.24% |
| DRIFT | FAST | conditions_not_met:composite | 4 | 5.88% |
| DRIFT | FAST | drift_adx1h_veto | 2 | 2.94% |
| DRIFT | SWING | conditions_not_met:low_vol_under_1 | 126 | 31.58% |
| DRIFT | SWING | conditions_not_met:bias_1h_up | 70 | 17.54% |
| DRIFT | SWING | conditions_not_met:bias_1h_down | 49 | 12.28% |
| DRIFT | SWING | conditions_not_met:slope_not_up | 43 | 10.78% |
| DRIFT | SWING | conditions_not_met:slope_not_down | 31 | 7.77% |
| DRIFT | SWING | drift_adx1h_veto | 27 | 6.77% |
| RANGING | FAST | conditions_not_met:bias_1h_up | 29 | 26.61% |
| RANGING | FAST | conditions_not_met:low_vol_under_1 | 28 | 25.69% |
| RANGING | FAST | four_h_conflict | 18 | 16.51% |
| RANGING | FAST | conditions_not_met:bias_1h_down | 11 | 10.09% |
| RANGING | FAST | conditions_not_met:bb_width_outside_corridor | 10 | 9.17% |
| RANGING | FAST | conditions_not_met:no_day_position | 10 | 9.17% |
| RANGING | SWING | conditions_not_met:low_vol_under_1 | 184 | 26.36% |
| RANGING | SWING | conditions_not_met:bias_1h_up | 132 | 18.91% |
| RANGING | SWING | four_h_conflict | 123 | 17.62% |
| RANGING | SWING | conditions_not_met:bias_1h_down | 109 | 15.62% |
| RANGING | SWING | conditions_not_met:no_day_position | 87 | 12.46% |
| RANGING | SWING | conditions_not_met:bb_width_outside_corridor | 36 | 5.16% |
| TRENDING | FAST | conditions_not_met:bias_1h_up | 29 | 43.28% |
| TRENDING | FAST | conditions_not_met:adx_not_rising | 20 | 29.85% |
| TRENDING | FAST | conditions_not_met:low_vol_under_1 | 11 | 16.42% |
| TRENDING | FAST | conditions_not_met:low_trend_vol | 2 | 2.99% |
| TRENDING | FAST | conditions_not_met:bias_1h_down | 2 | 2.99% |
| TRENDING | FAST | conditions_not_met:composite | 1 | 1.49% |
| TRENDING | SWING | conditions_not_met:bias_1h_up | 142 | 32.05% |
| TRENDING | SWING | conditions_not_met:adx_not_rising | 137 | 30.93% |
| TRENDING | SWING | conditions_not_met:low_vol_under_1 | 73 | 16.48% |
| TRENDING | SWING | conditions_not_met:bias_1h_down | 38 | 8.58% |
| TRENDING | SWING | conditions_not_met:low_trend_vol | 29 | 6.55% |
| TRENDING | SWING | conditions_not_met:composite | 10 | 2.26% |

## Live Signal Check In Replay Window

| cell | n | TP | SL | TIME | decisive WR | TIME |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DRIFT|FAST | 22 | 10 | 1 | 11 | 90.91% | 50.00% |
| RANGING|FAST | 2 | 1 | 0 | 1 | 100.00% | 50.00% |
| TRENDING|SWING | 14 | 8 | 5 | 1 | 61.54% | 7.14% |

## Benchmark Vs DRIFT x FAST

| bucket | n | avg vol | avg ADX | avg slope15 | avg day pos |
| --- | ---: | ---: | ---: | ---: | ---: |
| DRIFT FAST live TP signals | 10 | 1.68 | 20.88 | n/a | n/a |
| TRENDING missed moves | 510 | 1.27 | 38.72 | -0.25 | 0.55 |
| RANGING missed moves | 807 | 1.18 | 31.40 | 1.12 | 0.48 |

## What Each Cell Needs

- DRIFT x FAST: mostly silent (0.00% recall, usable sample); blockers: conditions_not_met:low_vol_under_1=24, conditions_not_met:bias_1h_up=14.
- DRIFT x SWING: mostly silent (2.21% recall, usable sample); blockers: conditions_not_met:low_vol_under_1=126, conditions_not_met:bias_1h_up=70.
- TRENDING x FAST: mostly silent (0.00% recall, usable sample); blockers: conditions_not_met:bias_1h_up=29, conditions_not_met:adx_not_rising=20.
- TRENDING x SWING: mostly silent (0.67% recall, usable sample); blockers: conditions_not_met:bias_1h_up=142, conditions_not_met:adx_not_rising=137.
- RANGING x FAST: mostly silent (0.00% recall, usable sample); blockers: conditions_not_met:bias_1h_up=29, conditions_not_met:low_vol_under_1=28.
- RANGING x SWING: mostly silent (0.00% recall, usable sample); blockers: conditions_not_met:low_vol_under_1=184, conditions_not_met:bias_1h_up=132.

## GPT Hypotheses

- `conditions_not_met` is the dominant silence bucket, so the post-hoc diagnostic split is more useful than raw `drop_reason` alone.
- TRENDING misses are mostly not a lack of movement; they are usually alignment/rising-ADX/volume/slope failures at the moment the move starts.
- RANGING movement exists, but the engine's ranging definition is intentionally narrow: BB corridor, falling ADX, day-position edge, and side-vs-VWAP all have to line up.
- If the trader marks missed PNGs as genuinely tradeable, the next phase should model a separate early-move detector per regime instead of loosening all filters globally.

## Caveats

- The independent movement detector is deliberately simple: it labels a move as FAST if the peak is inside 4 closed 15m bars and SWING if it is inside 16 bars; adverse excursion must stay below 75% of favorable excursion.
- This is recall research, not PnL backtest. A caught movement means engine side matched the movement start within a small near-start window; it does not guarantee the logged trade would hit TP after fees.
- `BB_FADE` 5m branch is not replayed here; the matrix requested DRIFT/TRENDING/RANGING x FAST/SWING, and the main `compute_signal` 15m decision stream is the source.
