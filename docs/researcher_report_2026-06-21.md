# Researcher's report — full day, 2026-06-21

A dense, honest account of one day's autonomous research on trading-bot-v2 / Strategy Lab. All
research-only; no live/orders/AUTO_TRADE/.env/private/Telegram touched. Test suite grew **1329 → 1600
passed (0 failed)**; ~16 new modules; public branch `feature/calc-farm` advanced ~30 commits; private
research repo advanced in lock-step.

## 1. Executive summary — what changed today
We stopped grinding a stale universe to zero and turned the contour into a self-driving, honestly-gated
search. The big reframe: the system was **honest but mis-aimed** (wrong universe / wrong exit / wrong
single validator / wrong breadth), not "market impossible". By day's end we have **two real leads**
(neither paper-ready) and a long list of avenues honestly CLOSED with numbers — which is what saves
months. Most importantly: every apparent win was stress-tested on a broad sample, and the machine now
self-guards against the small-sample mirages that produced fake leads twice.

## 2. Timeline of work (with commits + numbers)
| # | workstream | result | key numbers | commits |
|---|---|---|---|---|
| 1 | Overnight hardening (flake, shadow_oos, tactical_probe, revisit, LLM gov, OI opt-in) | done | suite 1329→1464 | 7f38d50..59ad707 |
| 2 | Forensic "why everything rejects" (3 subagents) | honest-but-mis-aimed ×4 | 4048 results mined | ea8fc84 |
| 3 | Discovery sprint (live universe, exit-first, SFP, tactical track, OI backfill, cycle) | re-aimed | farm blind to 15/20 movers; OI 12→22% | 0f73bfc..5749317 |
| 4 | Continuous contour (mover OOS, cycle, library, forward-watch) | headline killed | momentum 4h +5.37% IS → **flat OOS** | f4be371..06fdfae |
| 5 | Creative: hypothesis_search + exhaustion_fade | candidate killed | momentum/early_tp +1.64 (12 syms) → +0.19 (30 syms) | 2f27999, a5dd69d |
| 6 | Direction-filter probe | avenue CLOSED | overext/run/vol/funding all null (~0.5 win) | 0baa0bd |
| 7 | Funding carry (3 gates) | lead, fragile | +0.23%→+0.15% liquid; 50% liq at 3-5x | 9d17576, d45cc05 |
| 8 | Meme 1m/5m scalp | lead | vol_fade 5m +0.04% net, win 0.71, monotonic | c8bbbf4, 4974629 |
| 9 | Micro-scalp when×how-fast sweep | lead confirmed | 22 taker-positive cells; best +0.11 | 8d23e8f |
| 10 | Confirmed-fade (TA + candlestick + horizons) | TA adds nothing | bb +0.007 (noise); horizon hurts fast-fade | (today) |

## 3. What WORKED — the two leads (research-only, NOT edge)
**(A) Meme spike-fade + FAST exit (strongest, HF, "balance acceleration" shape).**
- range_fade (>=3x avg range) / body_fade / nbar_fade (exhaustion) on 5m/15m memes, exited FAST
  (tight tp_sl or first-green). 22 taker-positive cells across different triggers AND exits = robust grid,
  not one cell. Best: `range_fade x3 | tp_sl | 5m` = **net_taker +0.110, win 0.76, 128 OOS trades**.
- Mechanism: momentum is a coin-flip, but **overshoots/exhaustion mean-revert**; fade them, grab the
  snap-back. first-green exits hit 0.85-0.92 win (the micro-trade profile).
- Monotonic in the spike threshold (x2.5→x4 net +0.015→+0.05) = a real microstructure pattern.

**(B) Funding carry (directionless).**
- Delta-neutral harvest beats fees broadly (+0.23%/episode, 68% positive) and SURVIVES the liquidity gate
  (liquid +0.15%/episode, 67% positive, 78 episodes).
- But the short leg faces **50% liquidation at 3-5x** during the pumps that cause high funding → must run
  ~1x with full capital → modest and fragile, NOT a balance-accelerator.

## 4. What is a MIRAGE / honestly CLOSED (with numbers)
| avenue | verdict | number |
|---|---|---|
| momentum 4h movers | small-sample mirage | +5.37% (6 syms) → flat (22 syms) |
| momentum + early_tp | small-sample mirage | +1.64% (12) → +0.19% (30) |
| direction prediction from features | unfilterable coin-flip | overext/run/vol/funding all null, win ~0.5 |
| microstructure pressure (tape + orderbook) | no follow-through | 900 + 788 events, median ≈ cost |
| maker execution rescue | mostly mirage | 17.5% survival of the arithmetic unlock |
| TA / candlestick confirmation | no lift | bb +0.007 (noise), rsi/patterns ≤0 |
| exhaustion_fade family | weak on movers | negative |
| 1m scalping | cost-bound dead | all hypotheses net-negative |

## 5. What WORKED but needs REWORK (next gates, all unmodeled today)
1. **True forward** — EVERYTHING is held-out-tail pseudo-OOS, not genuine new-bar forward. The single most
   important missing test. The `true_forward` collector exists but was not run for the fade leads.
2. **Slippage into a spike** — the fade enters against a violent 5m bar; real fills are worse than the
   fixed taker cost used. Unmodeled; could erase the +0.11.
3. **Exit-mechanics sensitivity** — `tp_sl/hold4` gives +0.11 but `first_green/horizon24` gives -0.015.
   The lead is exit-sensitive; not fully reconciled (fast tight exit is essential, hold-to-horizon kills it).
4. **Multiple-testing on the 22 cells** — 22 positive cells out of a large grid will contain false
   positives; no Šidák/DSR deflation applied to the grid search.
5. **Walk-forward** — one held-out split, not rolling multi-window walk-forward (the overfit gold standard).
6. **Capacity / turnover / loss-tail** — small per-trade edge + 88% win can hide fat losers; no max-DD,
   capacity, or turnover-cost-at-size estimate.

## 6. Honest AUDIT — what I MISSED / did NOT do
- **Did not run a true forward** on the fade leads (the decisive test) — built the collector, didn't use it.
- **Did not model slippage** — used a flat taker cost everywhere; fading spikes is exactly where fills hurt.
- **Did not deflate the grid search** for multiple testing — 22/many positive cells needs a false-discovery
  correction before trusting any single one.
- **Did not do walk-forward** across multiple windows; one tail split is weak against regime/overfit.
- **Did not reconcile** the +0.11 (tp_sl) vs -0.015 (first_green/horizon) divergence into one robust spec.
- **Did not re-activate the LLM calculator** as the generator (the owner's tool) — I took the generator
  role myself but left the cheap-LLM loop disabled.
- **Did not test** two proposed directionless ideas: cross-sectional relative-value and fresh-listing
  vol-decay (only carry + fade were pursued).
- **Did not regime-condition** the fade (does it work only in certain BTC-vol regimes / sessions?).
- **Did not characterize the loss tail / drawdown** of the high-win-rate fade (88% win, fat 12%).

## 7. Concrete next steps (prioritized)
1. **TRUE-FORWARD the fade leads** — run `true_forward` on `range_fade x3 | tp_sl | 5m` + the nbar_fade
   first-green cells on NEW bars (days), the only honest test left.
2. **Slippage model** — re-price the fade entry with a spike-proportional slippage; see if +0.11 survives.
3. **Deflate the grid** — apply a multiple-testing correction to the 22-cell search; keep only what survives.
4. **Walk-forward** — rolling multi-window OOS on the fade instead of one tail split.
5. **Then, only if it survives 1-4** — a small/delta-neutral PAPER-forward (never live without a separate GO).
6. In parallel (cheap): test cross-sectional RV + fresh-listing vol-decay; re-activate the LLM loop as a
   second hypothesis generator behind the schema/RR/horizon/known-bad guards.

## 8. AUDIT CLOSED — the gates were run, and the leads DIED honestly (later same day)
The audit items above were not left as TODOs — they were executed rigorously (`fade_validation.py`,
`cross_sectional_probe.py`), and the result is decisive:

| gate | range_fade x3 / tp_sl / 5m | nbar_fade n4 / first_green / 15m |
|---|---|---|
| slippage (slip ∝ spike size) | **breaks even at 0.02** → realistic net **−0.006** | survives to 0.05; realistic net +0.010 |
| walk-forward (5 blocks) | **2/5 positive** (blocks 3-4 ≈ −0.14) | 3/5 positive (blocks 2-3 negative) |
| loss tail @ realistic slip | win 0.53, avg_win +0.45 / avg_loss −0.52, maxDD −9.3 | win 0.72 but avg_loss −0.70 vs +0.29 (2.4×), **maxDD −30.6** |
| significance + Šidák deflation | t −0.13, **p_adj 1.0** | t 0.44, **p_adj 1.0** |
| **verdict** | **dies_under_realistic_slippage** | **not_significant_after_deflation** |

**The meme fade is NOT an edge.** The +0.11 was a flat-cost / single-window / no-deflation artifact. The
fat loss tail I worried about (88% win hiding big losers) is real (avg loss 2.4× avg win on the 15m cell).

**New directionless avenue tested — cross-sectional relative-value (`cross_sectional_probe.py`):**
- momentum (long winners / short losers, 4h, market-neutral): spread **+0.26% net/rebalance, win 0.57,
  t 0.82** → `weak_or_zero` (positive, economically sensible, but underpowered on 79 rebalances).
- reversal: −0.46%, t −1.45 → `negative` (confirms momentum is the right sign).
This is the **least-dead** remaining idea — market-neutral, sensible sign, positive tilt — but not
significant yet. Honest next step is MORE history, not belief.

## Bottom line (final)
After running every gate the morning report flagged: **both fade leads die honestly** (slippage / tail /
deflation), funding carry stays modest-and-fragile, and the only survivor is a **weak, not-yet-significant
cross-sectional momentum tilt**. That is the real output of an honest day: not a green strategy, but a
shrunk search space — three more avenues closed with numbers, and one weak lead that needs data, not faith.
Nothing paper-ready. The discipline held: every apparent edge was killed by its own validation, which is
exactly what keeps this project alive.
