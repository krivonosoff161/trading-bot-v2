# Three Engines Research - 22.05.2026

Replay period: `2026-05-04T19:00:00Z` to `2026-05-14T19:00:00Z`. Universe is the fixed 29-symbol Phase B universe. Fee `0.20%`, slippage `0.03%`.

## Data Coverage

- candle symbols loaded: `29`
- tick directories present: `100.00%`
- tick files overlapping replay dates: `82.76%`
- symbols without replay-period tick files: `BSB-USDT-SWAP, CHZ-USDT-SWAP, EDEN-USDT-SWAP, RLS-USDT-SWAP, SPACE-USDT-SWAP`

## Detection Conditions

- `trend`: corrected TRENDING_SWING/TRENDING_GRIND plus DRIFT FAST; structural side; enter continuation at 15m signal close; structural stop behind impulse candle; ride with `structure_k3` for up to 32 bars.
- `impulse`: corrected TRENDING_IMPULSE; high-speed move; real tape trigger only, first tick reaching `0.30%` directional move within `300s` from 15m open; structural ride with `structure_k1`. No tick trigger means skipped, not approximated.
- `fade`: corrected RANGING; fade side near BB/range boundary; target BB middle; stop outside BB boundary plus buffer; short hold.

## Engine Metrics

| engine | events | filled | net | WR | capture | available | edge | hold | dir match | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fade | 68 | 63 | -0.10% | 38.10% | 35.84% | 1.15% | 66.67% | 29.76m | 82.35% | NO-GO: net<=0 |
| impulse | 91 | 2 | -0.34% | 50.00% | 7.68% | 1.42% | 100.00% | 52.50m | 48.35% | NO-GO: sample<20 |
| trend | 1486 | 1486 | -0.43% | 19.04% | 10.62% | 2.48% | 53.43% | 93.83m | 35.20% | NO-GO: net<=0 |

## Side Split

| engine | side | filled | net | WR | capture |
| --- | ---: | ---: | ---: | ---: | ---: |
| fade | long | 31 | -0.19% | 35.48% | 35.77% |
| fade | short | 32 | -0.02% | 40.62% | 35.90% |
| impulse | long | 1 | -0.69% | 0.00% | 0.00% |
| impulse | short | 1 | 0.00% | 100.00% | 15.36% |
| trend | long | 686 | -0.45% | 17.20% | 9.13% |
| trend | short | 800 | -0.41% | 20.62% | 11.91% |

## Early/Late Split

| engine | period | filled | net | WR | capture |
| --- | ---: | ---: | ---: | ---: | ---: |
| fade | early | 28 | -0.16% | 25.00% | 28.11% |
| fade | late | 35 | -0.06% | 48.57% | 41.75% |
| impulse | late | 2 | -0.34% | 50.00% | 7.68% |
| trend | early | 768 | -0.45% | 18.49% | 9.90% |
| trend | late | 718 | -0.41% | 19.64% | 11.37% |

## Volatility Tier Split

| engine | tier | filled | net | WR | capture |
| --- | ---: | ---: | ---: | ---: | ---: |
| fade | high_vol_alt | 4 | 0.00% | 50.00% | 59.73% |
| fade | low_vol_alt | 8 | -0.60% | 37.50% | 37.50% |
| fade | major | 14 | -0.16% | 21.43% | 24.05% |
| fade | mid_vol_alt | 37 | 0.01% | 43.24% | 38.13% |
| impulse | low_vol_alt | 1 | 0.00% | 100.00% | 15.36% |
| impulse | mid_vol_alt | 1 | -0.69% | 0.00% | 0.00% |
| trend | high_vol_alt | 281 | -0.78% | 17.44% | 7.51% |
| trend | low_vol_alt | 70 | -0.14% | 20.00% | 13.56% |
| trend | major | 175 | -0.35% | 18.29% | 11.65% |
| trend | mid_vol_alt | 960 | -0.37% | 19.58% | 11.18% |

## Per-Engine Notes

- Trend is judged by ride/capture and hold time, not by one-candle TP. It is still sensitive to side quality and late trend entries.
- Impulse is the only branch that requires tape. The report separates missing tick trigger/coverage from failed price action.
- Fade is judged by mean reversion to BB middle and range-side symmetry. It can pass net while still failing robustness if one side or period dominates.

## Verdict

The three engines should stay separated. The shared impulse detector is too blunt: trend needs ride logic, impulse needs tick-level entry, and range needs BB fade metrics. GO/NO-GO is kept strict; thin or asymmetric positives remain research candidates.

## GPT Hypotheses

- Edge-vs-capture is the right primary split: if `edge_exists` is high and capture is low, execution is the problem; if both are low, the setup has no edge in this sample.
- Impulse cannot be honestly evaluated for early entry where replay-period ticks are missing; those rows should not be converted to candle proxies.
- Fade looks structurally different from trend/impulse and should keep its own BB-middle target metrics.
