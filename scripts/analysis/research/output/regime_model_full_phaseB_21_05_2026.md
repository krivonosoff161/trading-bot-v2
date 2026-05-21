# Regime Model Full Phase B - 21.05.2026

Replay period: `2026-05-04T19:00:00Z` to `2026-05-14T19:00:00Z`. Net includes `0.20%` taker round trip and `0.03%` entry slippage.

## Formal Models Tested

- `trend_impulse`: structural momentum side, early 15m close entry, ATR stop, fixed-R TP. `adx_not_rising` is not required.
- `trend_grind_watch`: slow TRENDING is explicitly not traded in this pass; it is separated from impulse by speed.
- `range_fade`: side is fade of 24-bar range extreme, not default long. It only acts in corrected true RANGING.
- `drift_fast`: conservative momentum/VWAP-style fast entry with peak guard; DRIFT remains benchmarked against live DRIFT x FAST.
- `peak_guard`: skips entries already near local extreme after a large base move or ADX4H exhaustion reversal candle.

## EV By Cell

| cell | model | events | filled | fill | avg net | dir match | WR | TP | SL | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CHOPPY | no_model | 56 | 0 | 0.00% | n/a | n/a | n/a | n/a | n/a | NO-GO: sample<20 |
| DRIFT | drift_fast | 33 | 25 | 75.76% | -0.59% | 22.58% | 24.00% | 24.00% | 76.00% | NO-GO: net<=0 |
| DRIFT | no_model | 150 | 0 | 0.00% | n/a | n/a | n/a | n/a | n/a | NO-GO: sample<20 |
| RANGING | range_fade | 68 | 29 | 42.65% | -0.11% | 66.67% | 65.52% | 65.52% | 34.48% | NO-GO: net<=0 |
| TRENDING_GRIND | trend_grind_watch | 1228 | 0 | 0.00% | n/a | n/a | n/a | n/a | n/a | NO-GO: sample<20 |
| TRENDING_IMPULSE | trend_impulse | 91 | 55 | 60.44% | -0.04% | 47.78% | 47.27% | 47.27% | 50.91% | NO-GO: net<=0 |
| TRENDING_SWING | trend_grind_watch | 225 | 0 | 0.00% | n/a | n/a | n/a | n/a | n/a | NO-GO: sample<20 |

## Side Split

| cell | model | side | filled | avg net | WR |
| --- | ---: | ---: | ---: | ---: | ---: |
| DRIFT | drift_fast | long | 13 | -0.59% | 23.08% |
| DRIFT | drift_fast | short | 12 | -0.58% | 25.00% |
| RANGING | range_fade | long | 11 | -0.00% | 72.73% |
| RANGING | range_fade | short | 18 | -0.17% | 61.11% |
| TRENDING_IMPULSE | trend_impulse | long | 23 | -0.59% | 26.09% |
| TRENDING_IMPULSE | trend_impulse | short | 32 | 0.35% | 62.50% |

## Early/Late Split

| cell | model | period | filled | avg net | WR |
| --- | ---: | ---: | ---: | ---: | ---: |
| DRIFT | drift_fast | early | 14 | -0.50% | 28.57% |
| DRIFT | drift_fast | late | 11 | -0.69% | 18.18% |
| RANGING | range_fade | early | 12 | -0.22% | 50.00% |
| RANGING | range_fade | late | 17 | -0.03% | 76.47% |
| TRENDING_IMPULSE | trend_impulse | early | 35 | 0.20% | 48.57% |
| TRENDING_IMPULSE | trend_impulse | late | 20 | -0.47% | 45.00% |

## Volatility Tier Split

| cell | model | tier | filled | avg net | WR |
| --- | ---: | ---: | ---: | ---: | ---: |
| DRIFT | drift_fast | low_vol_alt | 1 | 0.32% | 100.00% |
| DRIFT | drift_fast | major | 8 | -0.44% | 25.00% |
| DRIFT | drift_fast | mid_vol_alt | 16 | -0.71% | 18.75% |
| RANGING | range_fade | high_vol_alt | 2 | -0.36% | 50.00% |
| RANGING | range_fade | low_vol_alt | 5 | -0.16% | 60.00% |
| RANGING | range_fade | major | 5 | 0.00% | 80.00% |
| RANGING | range_fade | mid_vol_alt | 17 | -0.09% | 64.71% |
| TRENDING_IMPULSE | trend_impulse | high_vol_alt | 15 | 0.87% | 60.00% |
| TRENDING_IMPULSE | trend_impulse | low_vol_alt | 2 | -0.22% | 50.00% |
| TRENDING_IMPULSE | trend_impulse | major | 2 | -0.24% | 50.00% |
| TRENDING_IMPULSE | trend_impulse | mid_vol_alt | 36 | -0.40% | 41.67% |

## Peak Guard

- model events skipped by peak guard: `48`
- `peak_guard:long_entry_near_range_high_after_large_move`: `21`
- `peak_guard:short_entry_near_range_low_after_large_move`: `19`
- `peak_guard:adx4h_exhaustion_reversal_candle`: `8`
- live valid signals caught by peak guard: `15`; outcomes: `{'TIME': 7, 'TP1': 7, 'SL': 1}`

## Tape Data

- tick root: `E:\trading-data\ticks`
- symbols with tick directories: `29` / `29` (100.00%)
- This pass uses candle/engine features for executable EV and records tape availability. The next implementation should add CVD/delta gates from these tick directories before any production config change.

## Data Needed By Regime

| cell | required data |
| --- | ---: |
| TRENDING_IMPULSE | 15m impulse speed, 1m/tape delta for earlier entry, distance from base, ADX4H exhaustion, structural stop |
| TRENDING_GRIND | slope persistence, pullback quality, low climax; current sample treated as watch/no-trade |
| RANGING | range high/low position, BB corridor, fade side, CVD exhaustion/divergence, tight range stop |
| DRIFT | VWAP walk, slope direction, base distance, trigger candle close location, peak guard |

## GO / NO-GO

The strict continuation-style criterion is positive net after fees on both long and short sides, stable early and late, with normal sample. Under that criterion no new regime cell is production-ready in this 10-day sample. High-volatility impulse cells remain research candidates; majors are mostly fee/size blocked.

## GPT Hypotheses

- The shared impulse component belongs to high-volatility alt regimes first; majors need a different threshold because 15m impulse size is close to fee and stop noise.
- RANGING should remain narrow and mean-reversion only. Sharp swings previously called RANGING are better handled by impulse/exhaustion logic.
- ADX4H above 40 is useful as an exhaustion/late-entry warning, not as a blanket trend confirmation.
