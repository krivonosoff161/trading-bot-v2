# Deep Analysis: SCANNER vs BB FADE

Source set:
- Local signal report: `scripts/analysis/backtest_runs/signal_analysis_2026-05-04_20-02.md`
- Local labels: `logs/signals/signal_labels.jsonl`
- Engine logic: `src/strategy/signal_engine.py`
- Analytics logic: `scripts/analysis/analyze_signal_log.py`, `scripts/analysis/label_outcomes.py`
- Backtest logic: `scripts/backtest/backtest_simulate.py`

## Executive Summary

- The main SCANNER problem is not raw win rate. It is payoff compression: live analytics score only `TP1`, while `DRIFT FAST` monetizes winners at just `+0.4R` and then accumulates too many negative `TIME_EXIT`.
- `DRIFT FAST` is mathematically dragged down by `TIME_EXIT`, not by stop frequency. In the labeled sample, its `TIME_EXIT` trades lose about `-0.241R` on average and contribute about `-0.083R` to subset average return.
- `BTC` is the weakest SCANNER symbol not because it stops out more, but because it produces too many stale `DRIFT FAST` entries. Those time exits have very low `MFE` and unusually high `vol_ratio`, which looks more like late entry into an exhausted impulse than "good move, but hold was too short".
- `BB FADE` is currently the cleaner edge. In `RANGING`, it exits mostly via TP/SL rather than time stop because its target is the Bollinger midline, which is much closer than SCANNER TP. Empirically it behaves like a fast mean-reversion trade, not a hold-and-hope trade.
- Highest-priority A/B work is not "make SCANNER hold longer". It is: reduce late `DRIFT FAST` entries, especially on `BTC`, and test tighter regime isolation for FADE rather than broadening it.

## SCANNER Analysis

### What the current measurement is actually scoring

The current live analytics do not score `TP2` as realized profit.

- `signal_engine.py` computes `tp1_price` and `tp2_price`.
- `telegram_bot.py` writes only `tp1_price` into `signal_log.jsonl` as `tp`.
- `label_outcomes.py` and `backtest_simulate.py` label results against one TP only.

Implication: the reported `avg_R` for SCANNER is driven by `TP1`, `SL`, and `TIME_EXIT`. The theoretical stretch target `TP2=1.5R` is not what current live stats are monetizing.

### Why DRIFT FAST is negative despite WR 75.5%

Sample: `DRIFT FAST n=81 WR=75.5% PF=1.23 avg_R=-0.047`

Outcome decomposition from labeled trades:

- `TP`: `40` trades, average realized return `+0.399R`
- `STOP`: `13` trades, average realized return `-1.000R`
- `TIME_EXIT`: `28` trades, average realized return `-0.241R`

Contribution to subset `avg_R`:

- TP contributes about `+0.197R`
- STOP contributes about `-0.160R`
- TIME_EXIT contributes about `-0.083R`
- Net: about `-0.047R`

Interpretation:

- Winners are too cheap. In `DRIFT`, the system books the primary win at only `+0.4R`.
- Losers are full-size at `-1R`.
- Time exits are frequent and usually negative.
- With current measurement, a high WR is not enough because the payoff ratio is compressed.

This is exactly the pattern you would expect from a system that catches many shallow continuation attempts but not enough clean follow-through to offset small wins and stale exits.

### TIME_EXIT is the core failure mode, not "almost winners"

All strategies combined:

- `TIME_EXIT: 40/168`
- `avg_mfe = +0.268R`
- `mfe >= 0.5R: 4/40`

For `DRIFT FAST` specifically, the picture is even weaker:

- `TIME_EXIT n=28`
- `avg_exit_r = -0.241R`
- `avg_mfe_r = +0.128R`
- `avg_elapsed = 186.9m`

Interpretation:

- These are mostly not "would have hit if we just waited a bit longer".
- They barely move in favor at all.
- The correct hypothesis is not "increase hold time". The stronger hypothesis is "entry quality is too late or too weak".

Increasing hold time is therefore low-priority for SCANNER and likely harmful for capital efficiency.

### BTC vs SOL: why BTC degrades and SOL survives

BTC overall:

- `n=40 WR=72.0% PF=1.34 avg_R=-0.022`
- `TIME_EXIT: 15/40`
- `avg TIME_EXIT exit_r = -0.219R`
- `avg TIME_EXIT mfe_r = +0.217R`

BTC `DRIFT FAST`:

- `n=19`
- `TP 8`, `STOP 1`, `TIME_EXIT 10`
- `TIME_EXIT avg_mfe_r = +0.099R`
- `TIME_EXIT avg vol_ratio ≈ 4.9`
- `TP avg vol_ratio ≈ 2.1`

SOL overall:

- `n=14 WR=88.9% PF=3.2 avg_R=+0.040`
- `TIME_EXIT: 5/14`

SOL `DRIFT FAST`:

- `n=13`
- `TP 8`, `STOP 1`, `TIME_EXIT 4`
- `TIME_EXIT avg_mfe_r = +0.115R`
- `TIME_EXIT avg vol_ratio ≈ 2.9`

Main difference:

- BTC does not fail because its time exits are individually much worse than SOL.
- BTC fails because it has many more of them, and they appear after very high `vol_ratio`.
- That pattern is consistent with late-entry continuation on a mature impulse: volume spike is large, but usable residual move is already gone.

On hours, BTC time exits cluster in weak windows rather than being evenly distributed. The existing report already shows global weakness around `05:00 UTC`, `14:00-15:00 UTC`, and `23:00 UTC`, and BTC contributes heavily to that `TIME_EXIT` inventory.

### SWING vs FAST inside TRENDING

Results:

- `TRENDING FAST n=12 WR=75.0% PF=2.43 avg_R=+0.196`
- `TRENDING SWING n=28 WR=75.0% PF=1.36 avg_R=+0.039`

Why FAST wins despite same WR:

- `FAST` TP1 is fixed around `0.8R`.
- `SWING` TP1 is capped by `min(1.0R, 0.5 * ATR_1H)`, so realized winner size is often smaller.
- In the sample, `TRENDING FAST` winners average about `+0.808R`, while `TRENDING SWING` winners average about `+0.452R`.
- `TRENDING SWING` time exits are also worse on average.

Interpretation:

- The trend exists, but the current SWING monetization is too conservative for the extra hold time it demands.
- FAST is harvesting the cleaner early move more efficiently than SWING is harvesting the later extension.

## BB FADE Analysis

### What the implementation actually does

Current BB FADE logic in `signal_engine.py`:

- `BB(20, 2.0)` on `5m`
- Requires low `1H` ADX (`adx_1h < 20`)
- Requires `vol_low`: last volume below `0.70 * vol_ma20`
- Requires `vol_declining`
- Requires `not_thrust`: candle range below `1.5 * ATR5`
- Requires slope fade confirmation: current 5m slope is weakening relative to previous
- TP is the Bollinger midline
- SL is outside the band by roughly `1 * ATR5`

This is important because it means the current production FADE is already more selective than a naive "touch outer band, fade immediately" implementation.

### Why FADE exits by TP/SL instead of TIME_EXIT

Legacy logging in `telegram_bot.py` stores FADE with `max_hold_min = 60`.

Observed behavior:

- `FADE n=47 WR=60.5% PF=1.77 avg_R=+0.291`
- `RANGING FADE n=27 WR=62.5% PF=1.98 avg_R=+0.318`
- `TIME_EXIT only 4` across all FADE

RANGING FADE decomposition:

- `TP 15`, `STOP 9`, `TIME_EXIT 3`
- `TP avg elapsed ≈ 20.3m`
- `TIME_EXIT avg elapsed ≈ 64m`
- `TIME_EXIT avg exit_r ≈ -0.08R`

Why this is structurally better than SCANNER:

- The target is close: price only needs to mean-revert to the mid-band, not extend to a continuation target.
- A successful fade resolves quickly.
- Time stops are rare because the trade idea is naturally short-duration and self-invalidating.

### Why RANGING FADE works and DRIFT FADE weakens

Results:

- `RANGING FADE n=27 WR=62.5% PF=1.98 avg_R=+0.318`
- `DRIFT FADE n=19 WR=55.6% PF=1.25 avg_R=+0.147`

Interpretation:

- In `RANGING`, fading the outer band is aligned with the dominant market behavior: reversion back into balance.
- In `DRIFT`, even if local `1H` ADX is not high, price is still more likely to keep crawling in one direction. Fading that move is effectively stepping in front of a slow continuation.
- That is why `DRIFT FADE` is weaker even though the trade definition is the same.

This also explains why broadening FADE into drift-like environments is lower-quality than keeping it concentrated in clean range states.

### Why XRP/SOL are disabled in legacy broadcasting

Current legacy broadcast gate in `telegram_bot.py` allows FADE only on:

- `BTC-USDT`
- `ETH-USDT`
- `DOGE-USDT`

Comment in code says `SOL/XRP` were disabled because a longer 35-day backtest delivered `WR < 30%` and `PF < 1` across tested ADX thresholds.

What the current labeled sample says:

- It still contains some historical `XRP` FADE (`n=8`), but not enough to override the longer-window decision.
- `SOL` does not appear in the current FADE sample at all.

Practical reading:

- The current small sample is not strong enough to justify re-enabling `XRP/SOL`.
- The existing production exclusion is defensible until a fresh pair-specific backtest says otherwise.

## Calibration Research

### Bollinger parameters for mean reversion

Open-source consensus is stronger on the default than on crypto-specific optimization.

- The canonical baseline remains `BB(20, 2.0)`. StockCharts and Bollinger material both treat it as the default reference setup.  
  Source: [StockCharts ChartSchool: Bollinger Bands](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/bollinger-bands), [Bollinger Band Rules](https://www.bollingerbands.com/bollinger-band-rules)
- John Bollinger's own rule of thumb is that shorter lookbacks should use slightly fewer standard deviations and longer lookbacks slightly more. Practical examples often cited are roughly `10-period -> about 1.5-1.9 sigma` and `50-period -> about 2.1-2.5 sigma`, depending on implementation.  
  Source: [Bollinger Band Rules](https://www.bollingerbands.com/bollinger-band-rules)
- Practical mean-reversion literature consistently frames `1.5 sigma` as a more frequent but noisier trigger and `2.5 sigma` as a rarer, stronger-extreme trigger.  
  Source: [CrossTrade: Bollinger Mean Reversion](https://crosstrade.io/learn/trading-strategies/bollinger-mean-reversion)

What is not well supported by robust open research:

- I did not find a strong primary-source or academic consensus specifically proving that `BB(10)`, `BB(14)`, or `BB(20)` is universally superior on `5m` crypto futures.

Usable conclusion for this system:

- `20,2.0` remains a reasonable baseline.
- If you want more signals, test shorter-period / slightly tighter bands.
- If you want cleaner extremes, test wider bands before changing the rest of the FADE stack.

### Volume filter for fade strategies

The current FADE logic uses the right directional idea: low/declining volume and no thrust bar.

Open-source support:

- ChartSchool and practical BB literature consistently treat strong-volume moves as more compatible with breakout/continuation than with fade.
- Low participation and failed expansion are more compatible with exhaustion and reversion than with discovery.
- Relative Volume references from StockCharts describe high RVOL as confirmation of interest and momentum; the absence of that confirmation is one reason not to trust continuation.  
  Source: [StockCharts: Relative Volume](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-volume-rvol)

Applied to your stack:

- `vol_low + vol_declining + not_thrust` is directionally correct.
- The stronger risk is not that FADE lacks a volume filter. The stronger risk is broadening FADE into conditions where volume is rising and continuation is more likely.

### Hold time for mean reversion

I did not find a robust open-source benchmark for "average minutes to return from outer Bollinger Band to mid-band on 5m crypto futures".

What practical literature does support:

- Mean-reversion fades are typically treated as short-horizon trades.
- Time stops are commonly used as invalidation when price does not revert quickly enough.  
  Source: [CrossTrade: Bollinger Mean Reversion](https://crosstrade.io/learn/trading-strategies/bollinger-mean-reversion)

Your own data is more valuable than generic literature here:

- `RANGING FADE` winners hit TP in about `20m`.
- The few FADE time exits occur around the `60m` cap.

Applied conclusion:

- A `60m` hold is not obviously too short.
- If anything, the data supports testing a slightly shorter FADE time stop before testing a longer one.

### Keltner vs Bollinger in adaptive volatility

Structural difference:

- Bollinger Bands widen and contract based on standard deviation.
- Keltner Channels use ATR, which usually makes them smoother and less sensitive to sudden variance bursts.  
  Source: [StockCharts: Keltner Channels](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/keltner-channels)

Practical implication:

- In choppy, variance-shifting crypto conditions, Keltner can reduce false "extreme" readings that are really just temporary volatility expansion.
- Bollinger is better at identifying statistical stretch; Keltner is often cleaner for volatility-normalized trend envelopes.

I did not find strong primary evidence that one is universally superior to the other for crypto mean reversion. This is an empirical A/B question, not a settled one.

### Why BTC/ETH/DOGE can be better FADE pairs than XRP/SOL

Open-source market microstructure work broadly supports the same intuition:

- More liquid instruments usually have tighter spreads and lower slippage.
- Mean-reversion edges are easier to monetize when execution friction is small and price discovery is deeper.
- More explosive altcoins can touch outer bands during genuine continuation rather than temporary overextension.  
  Source: [Makarov & Schoar, Trading and Arbitrage in Cryptocurrency Markets](https://www.sciencedirect.com/science/article/pii/S154461231931400X)

Applied conclusion:

- `BTC` and `ETH` are structurally more plausible FADE instruments because they revert with less execution friction.
- `DOGE` can still work because it is liquid enough and mean-reverts aggressively once thrust fails.
- `XRP` and `SOL` are more regime-sensitive: when they move, they often continue hard enough to break a simple band-fade.

## Hypotheses for A/B Testing

### 1. Tighten BTC DRIFT FAST late-entry filter

Hypothesis: reduce late BTC drift entries by adding an upper cap on `vol_ratio_sig` or a stricter stretch-from-base/vwap rule.

- Current value: BTC `DRIFT FAST` allows entries that later `TIME_EXIT` with `avg vol_ratio ≈ 4.9`
- Proposed: test BTC-specific veto such as `vol_ratio_sig <= 3.0-3.5` or stricter `BT_DRIFT_VWAP_STRETCH` / `BT_DRIFT_MOVE_FROM_BASE`
- Expected effect: fewer stale BTC entries after already-mature impulses; lower `TIME_EXIT` frequency
- Risk: may cut some real momentum winners
- Check via: `backtest_simulate.py` with BTC-only subsets and `BT_DRIFT_VWAP_STRETCH`, `BT_DRIFT_MOVE_FROM_BASE`, plus custom cap if exposed

### 2. Shorten DRIFT FAST hold before lengthening it

Hypothesis: reduce negative carry by closing stale `DRIFT FAST` trades sooner.

- Current value: `hold_fast_minutes=90`, but night FAST extends to `240m`
- Proposed: test `60m` and `75m` for DRIFT FAST, especially on BTC
- Expected effect: time exits currently show very low `MFE`, so earlier invalidation may improve `avg_R`
- Risk: some slower winners may be cut prematurely
- Check via: `backtest_simulate.py` with adjusted hold settings and pair-by-pair comparison

### 3. Raise DRIFT TP1 only after entry quality improves

Hypothesis: once stale entries are reduced, increase monetized winner size in DRIFT.

- Current value: `DRIFT TP1 = 0.4R`
- Proposed: test `0.5R` then `0.6R`
- Expected effect: current `+0.4R` wins are too small to absorb full-size stops and negative time exits; higher TP1 could materially lift payoff
- Risk: without cleaner entries this may simply convert current TPs into more time exits
- Check via: `backtest_simulate.py` with `BT_DRIFT_TP1_K=0.5` and `0.6`, but only after or alongside stricter entry filters

### 4. Keep FADE concentrated in RANGING; do not broaden DRIFT FADE blindly

Hypothesis: tighter regime isolation improves FADE quality more than parameter tweaking.

- Current value: FADE appears in both `RANGING` and `DRIFT`
- Proposed: test `RANGING-only` FADE or stricter `adx_1h` cap such as `<18` instead of `<20`
- Expected effect: current data already shows `RANGING FADE` materially stronger than `DRIFT FADE`
- Risk: signal count drops
- Check via: `backtest_simulate.py` / log replay with stricter ADX gate on FADE subset

### 5. Test shorter FADE time stop

Hypothesis: FADE that does not revert quickly is lower quality and should be invalidated sooner.

- Current value: `max_hold_min = 60`
- Proposed: test `45m` and `50m`
- Expected effect: winners already resolve in about `20m`; rare slow trades may not justify the extra hold
- Risk: may cut some acceptable late mean reversions
- Check via: FADE-only replay with altered `max_hold_min`

### 6. Test wider FADE bands before tighter ones

Hypothesis: wider bands improve selectivity and reduce continuation traps in crypto.

- Current value: `BB(20, 2.0)`
- Proposed: compare `BB(20, 2.5)` against baseline before testing `BB(20, 1.5)`
- Expected effect: open-source practice suggests `1.5 sigma` gives more frequent but noisier signals, while `2.5 sigma` isolates stronger extremes
- Risk: too few trades
- Check via: FADE-only backtest on current pair set, comparing count, PF, avg_R, and slippage sensitivity

### 7. Test shorter BB length with slightly reduced sigma

Hypothesis: shorter BB length may react better to 5m crypto micro-swings if sigma is reduced accordingly.

- Current value: `BB(20, 2.0)`
- Proposed: test `BB(14, 1.8-2.0)` and `BB(10, 1.7-1.9)`
- Expected effect: may catch earlier reversion points on fast liquid pairs
- Risk: more noise and more false fades during continuation
- Check via: FADE-only backtest, stratified by pair and regime

## Prioritized Next Steps

1. Re-run SCANNER backtest with focus on `BTC DRIFT FAST`, testing entry-quality filters before any hold extension.
2. Test `DRIFT FAST` hold reduction and `DRIFT TP1` increase as a paired experiment, not as isolated optimism.
3. Run FADE A/B on `RANGING-only` vs current regime mix.
4. Run FADE parameter grid on `BB(20,2.0)`, `BB(20,2.5)`, `BB(14,1.9)`, `BB(10,1.8)`.
5. Keep `SOL/XRP` FADE disabled until a fresh dedicated backtest proves otherwise.
6. If SCANNER remains negative after BTC drift cleanup, treat BB FADE as the higher-quality deployable edge and decide explicitly whether legacy SCANNER remains a product line or becomes a research branch.
