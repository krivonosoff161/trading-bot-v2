# Majors vs Alts Replay — 2026-05-19

## Methodology

Archive decisive majors trades were taken from the old scanner logs and labels, then replayed against the current `ws_main_screener` gating stack on the same timestamps using cached 5m/15m/1H/4H candles and current `compute_signal()`. The replay applies the outer 15m prefilter (`prefilter_vol_ratio_min=1.0`, `prefilter_adx_min=10`) before `compute_signal()`, then treats a trade as surviving only if current logic still emits `ENTRY` with the same regime/style/side bucket.

## AS-IS vs AFTER

### TRENDING x SWING

| Metric | AS-IS | AFTER NEW FILTERS | Delta |
| --- | ---: | ---: | ---: |
| n | 24 | 2 | -22 |
| WR | 75.00% | 50.00% | -25.00 pp |
| avg_R | 0.09 | -0.32 | -0.40 |
| std_R | 0.64 | 0.69 | 0.05 |
| PF | 1.36 | 0.37 | -0.99 |
| max_DD | -4.40 | -1.00 | 3.40 |

### DRIFT x FAST

| Metric | AS-IS | AFTER NEW FILTERS | Delta |
| --- | ---: | ---: | ---: |
| n | 53 | 23 | -30 |
| WR | 75.47% | 91.30% | 15.83 pp |
| avg_R | 0.06 | 0.27 | 0.22 |
| std_R | 0.60 | 0.39 | -0.21 |
| PF | 1.23 | 4.16 | 2.93 |
| max_DD | -5.39 | -2.00 | 3.39 |

### TRENDING x FAST

| Metric | AS-IS | AFTER NEW FILTERS | Delta |
| --- | ---: | ---: | ---: |
| n | 8 | 2 | -6 |
| WR | 75.00% | 100.00% | 25.00 pp |
| avg_R | 0.36 | 0.82 | 0.47 |
| std_R | 0.78 | 0.01 | -0.78 |
| PF | 2.43 | n/a | n/a |
| max_DD | -2.00 | 0.00 | 2.00 |

## Breakdown Filter Cuts

### TRENDING x SWING filter cuts

| Reason | Count |
| --- | ---: |
| min_vol_ratio_trending | 12 |
| conditions_not_met | 8 |
| reclassified_regime_RANGING | 1 |
| other | 1 |

### DRIFT x FAST filter cuts

| Reason | Count |
| --- | ---: |
| conditions_not_met | 22 |
| reclassified_regime_TRENDING | 5 |
| reclassified_regime_RANGING | 2 |
| min_vol_ratio_trending | 1 |

### TRENDING x FAST filter cuts

| Reason | Count |
| --- | ---: |
| min_vol_ratio_trending | 3 |
| conditions_not_met | 2 |
| reclassified_regime_RANGING | 1 |

## Dropped Trade Samples

### TRENDING x SWING dropped trades

| signal_id | symbol | side | outcome | reason | pre_vol | live_vol |
| --- | --- | --- | --- | --- | ---: | ---: |
| 1775734201000_BTC-USDT_buy_SWING | BTC-USDT | buy | TP | min_vol_ratio_trending | 0.76 | 0.90 |
| 1775756702000_BTC-USDT_buy_SWING | BTC-USDT | buy | TP | min_vol_ratio_trending | 0.30 | 0.92 |
| 1775775602000_BTC-USDT_buy_SWING | BTC-USDT | buy | TP | conditions_not_met | 1.72 | 3.58 |
| 1775775602000_ETH-USDT_buy_SWING | ETH-USDT | buy | SL | conditions_not_met | 2.24 | 3.32 |
| 1775782801000_BTC-USDT_buy_SWING | BTC-USDT | buy | TP | min_vol_ratio_trending | 0.56 | 0.72 |
| 1775797202000_BTC-USDT_buy_SWING | BTC-USDT | buy | SL | min_vol_ratio_trending | 0.63 | 0.84 |
| 1775803502000_BTC-USDT_buy_SWING | BTC-USDT | buy | SL | min_vol_ratio_trending | 1.22 | 0.77 |
| 1775871002000_DOGE-USDT_buy_SWING | DOGE-USDT | buy | TP | min_vol_ratio_trending | 1.21 | 1.25 |

### DRIFT x FAST dropped trades

| signal_id | symbol | side | outcome | reason | pre_vol | live_vol |
| --- | --- | --- | --- | --- | ---: | ---: |
| 1775733302000_XRP-USDT_sell_FAST | XRP-USDT | sell | TP | reclassified_regime_RANGING | 5.69 | 1.62 |
| 1775743202000_ETH-USDT_sell_FAST | ETH-USDT | sell | SL | reclassified_regime_TRENDING | 1.17 | 3.14 |
| 1775745001000_ETH-USDT_sell_FAST | ETH-USDT | sell | SL | reclassified_regime_TRENDING | 1.42 | 2.42 |
| 1775773801000_ETH-USDT_buy_FAST | ETH-USDT | buy | SL | conditions_not_met | 1.42 | 1.69 |
| 1775773801000_DOGE-USDT_buy_FAST | DOGE-USDT | buy | SL | min_vol_ratio_trending | 0.89 | 1.35 |
| 1775825102000_ETH-USDT_buy_FAST | ETH-USDT | buy | TP | reclassified_regime_TRENDING | 0.68 | 2.30 |
| 1775918702000_XRP-USDT_sell_FAST | XRP-USDT | sell | TP | conditions_not_met | 0.27 | 1.61 |
| 1776056401000_XRP-USDT_sell_FAST | XRP-USDT | sell | SL | conditions_not_met | 0.92 | 3.05 |

### TRENDING x FAST dropped trades

| signal_id | symbol | side | outcome | reason | pre_vol | live_vol |
| --- | --- | --- | --- | --- | ---: | ---: |
| 1775875501000_DOGE-USDT_buy_FAST | DOGE-USDT | buy | SL | reclassified_regime_RANGING | 0.61 | 1.19 |
| 1776479401000_BTC-USDT_buy_FAST | BTC-USDT | buy | SL | min_vol_ratio_trending | 0.68 | 0.92 |
| 1776860102000_ETH-USDT_buy_FAST | ETH-USDT | buy | TP | conditions_not_met | 2.31 | 2.27 |
| 1776863702000_BTC-USDT_buy_FAST | BTC-USDT | buy | TP | conditions_not_met | 1.18 | 1.94 |
| 1777371602000_XRP-USDT_sell_FAST | XRP-USDT | sell | TP | min_vol_ratio_trending | 0.63 | 1.04 |
| 1777565702000_XRP-USDT_sell_FAST | XRP-USDT | sell | TP | min_vol_ratio_trending | 0.15 | 0.89 |

## TRENDING x SWING `conditions_not_met` decomposition

| Sub-reason | Count | Trade IDs |
| --- | ---: | --- |
| slope_min veto | 6 | 1775775602000_BTC-USDT_buy_SWING, 1775775602000_ETH-USDT_buy_SWING, 1776150901000_ETH-USDT_buy_SWING, 1776157202000_BTC-USDT_buy_SWING, 1776335401000_XRP-USDT_buy_SWING, 1776862802000_ETH-USDT_buy_SWING |
| regime_reclassified (TRENDING->other) | 0 | - |
| 5m trigger mismatch | 0 | - |
| bias check fail | 0 | - |
| 4h conflict | 0 | - |
| other | 2 | 1777352402000_DOGE-USDT_buy_SWING, 1777761302000_BTC-USDT_buy_SWING |

### Overlap with `min_vol_ratio_trending`

| signal_id | sub-reason | also fails min_vol<1.5 | notes |
| --- | --- | --- | --- |
| 1775775602000_BTC-USDT_buy_SWING | slope_min_veto | no | slope_now=-34.7<min=35.0, slope_not_rising(now=-34.7,prev=11.9) |
| 1775775602000_ETH-USDT_buy_SWING | slope_min_veto | no | slope_now=-45.9<min=35.0, slope_not_rising(now=-45.9,prev=-27.5) |
| 1776150901000_ETH-USDT_buy_SWING | slope_min_veto | no | slope_now=31.1<min=35.0 |
| 1776157202000_BTC-USDT_buy_SWING | slope_min_veto | no | slope_now=30.7<min=35.0, slope_not_rising(now=30.7,prev=38.2) |
| 1776335401000_XRP-USDT_buy_SWING | slope_min_veto | no | slope_now=20.1<min=35.0, slope_not_rising(now=20.1,prev=29.5) |
| 1776862802000_ETH-USDT_buy_SWING | slope_min_veto | no | slope_not_rising(now=44.9,prev=46.0) |
| 1777352402000_DOGE-USDT_buy_SWING | other | no | adx_1h_rising=False |
| 1777761302000_BTC-USDT_buy_SWING | other | no | adx_1h_rising=False, bb_width=0.69<min=0.70 |

## Verdict

- TRENDING x SWING: scenario `B` — cuts 91.7% or avg_R turns non-positive.
- DRIFT x FAST: scenario `precision-positive` — cuts 56.6% but improves surviving subset quality.
- TRENDING x FAST: scenario `precision-positive` — cuts 75.0% but improves surviving subset quality.

## Concrete Next Experiment

- The next code-free experiment should isolate `min_vol_ratio_trending` on majors only, because that is the only filter with a plausible archive-edge cost inside `TRENDING x SWING`.
