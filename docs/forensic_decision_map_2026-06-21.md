# Forensic discovery — decision map (2026-06-21)

Why the scanner→farm→validator→memory contour rejects almost everything. Built from: live OKX public
observer, validator gate forensic, rejected-cause breakdown (4048 results / 3564 candidates), unused
data/ideas inventory. Read-only. The conclusion is NOT "no edge" — it is "the system is honest but
mis-aimed in four compounding ways."

## Executive verdict

The farm is not a meat grinder proving the market is impossible. It is **aimed at the wrong universe,
exits at the wrong time, judged by the wrong (single) validator, and runs at a fraction of its breadth.**
Each is fixable without touching the live engine.

## Root causes (evidence)

| # | Root cause | Evidence |
|---|---|---|
| 1 | **Wrong universe** — stale + alphabetical + dead | data 2 days stale; intake reason = `universe_grind` (alphabetical: 0G,1INCH,APE,API3…); 4 symbols hold 216 candidates each, rest shallow; farm has data for only **5 of the top-20 live movers** (misses BICO +62%/$5.3B, RESOLV +49%, MET +22%, UB +23%, HMSTR $17B, PUMP $23B); universe polluted with tokenized stocks (AAPL/NVDA/AMZN/PLTR, 0.5–1.2% per-4h-bar); median farm symbol 4h range 2.49% vs live movers 17–62%/24h |
| 2 | **Wrong exit** — the #1 wall | **2220 candidates** have MFE>1% but capture<30% — the move happens, the exit gives it back. JSON-labeled wrong_exit=1054. Dominant momentum_breakout/1h |
| 3 | **Wrong (single) validator for tactical** | hard count-walls n<3→NEEDS_MORE_DATA, n<6→FAILED_OVERFIT, **n<10→FAILED_OOS** fire BEFORE any statistics → tactical 1–9-trade setups structurally un-passable and mislabeled; Šidák `1-(1-p)^n_trials` makes a max-significant signal un-passable at **≳51 variants** (deep sweep penalized); DSR present but unused; no separate tactical/shadow verdict class with its own bar |
| 4 | **Wrong breadth** — running 3 of 25 | only momentum_breakout/mean_reversion_fade/bb_volume_fade actively run; ~12 registered families never run (breakout_retest, donchian, rsi_reversal, volume_exhaustion_fade, impulse_continuation, trend_pullback, moving_average_reclaim, …); 115M-tick tape used only by micro replay; OI/funding data-starved (0% 15m/1d, 12% 1h) so OI families post 0 trades / 99 blocked; best leads (stat-arb Kalman Sharpe~0.6, cross-sectional, SFP) not in the per-symbol farm at all |

Cross-cutting: **1425 candidates were never even computed (n<3)** — half the "rejections" are non-events
(generators barely trigger on dead symbols), not losses. Only momentum_breakout is *confirmed* bad
(median net −0.25). Costs are a real but second-order killer (985 gross+/net−); maker does NOT rescue
it (Gate-1 honest re-sim: 17.5% survival).

## What is genuinely dead vs mislabeled

| verdict | reality |
|---|---|
| momentum_breakout on dead universe | genuinely confirmed-bad here (but untested on live movers) |
| OI families | data-starved, not tested (0 trades) — unknown, not dead |
| tactical 1–9-trade setups | mislabeled FAILED_OOS/OVERFIT — never actually judged |
| bb_volume_fade / MRF "median 0.0" | mostly never-triggered zeros, not neutral edge |
| tape / orderbook pressure (Theme 40) | genuinely null on collectable keyless data (proven) |

## The 5–10 hypotheses worth testing (ranked)

| rank | hypothesis | why it's promising | min test |
|---|---|---|---|
| 1 | **Movement-driven universe** — pick symbols by live volatility/volume, not alphabet | farm is blind to 20–60% movers; momentum/breakout needs movement to exist | re-sweep momentum_breakout on the top-N live-volatility symbols (fresh candles) vs the stale set |
| 2 | **Exit-first re-sim on the 2220 wrong_exit pool** — trailing/partial/early-TP/time-decay | move is real (MFE>1%), capture<30% — fixing exit, not signal | exit_phase2 grid over wrong_exit candidates that also have MFE>1% (we have the data) |
| 3 | **SFP / liquidity-sweep family** (single-symbol, candle-based) | only ICT idea that survived earlier hunts; no farm family exists | add family (swing pierce + reclaim) + sweep on movement universe |
| 4 | **Tactical/shadow verdict track** — small-n setups get a forward-watch class, not FAILED_OOS | n<10 mislabel hides every tactical setup; 595 already flagged "re-run with regime" | add verdict class; route n<10 positive to shadow, never paper-ready |
| 5 | **Regime-filtered re-validation of the 595** | pipeline already says "re-run with regime as spec filter" but never does | run the 595 REGIME_SPECIFIC with their regime as an entry gate |
| 6 | **fvg_reclaim_reject deeper** | only family with positive median OOS (+0.005), n=30 — under-explored | wider sweep + movement universe |
| 7 | **OI families with backfilled OI** | wired but data-starved; OKX OI history is keyless | backfill OI into 1h/4h candle rows, re-run the 3 OI families |
| 8 | **The few large-n IS−/OOS+ cases** (fractal/BTC n=203, main_fast_swing/ETH n=48 test+0.59, fractal/SOL n=287) | sign-flip with real n — possible wrong-side or regime | invert-side / regime-split re-sim, honest OOS |
| 9 | **Stat-arb Kalman (multi-symbol)** — the only prior anchor (Sharpe~0.6 walk-forward) | not in farm (needs pairs harness); strongest historical lead | run the standalone script forward-paper; build a pairs harness later |
| 10 | **Low-frequency 4h/1d cut** — costs bite least | 4h was healthiest in cost-mining (median 0.0 vs −0.09 on 15m) | restrict a clean sweep to 4h on movement universe |

## First 3 to run (lowest risk, highest information)

1. **#1 Movement-driven universe re-sweep** — the single biggest lever; everything else is downstream of
   testing on symbols that actually move. Read-only research sweep, bounded.
2. **#2 Exit-first re-sim on the wrong_exit pool** — we already have the data; directly attacks the #1
   wall; cheap.
3. **#3 SFP family** — the one survivor idea with no implementation; small candle-based add.

## What changes where

| component | change | risk |
|---|---|---|
| **scanner / discovery** | symbol selection by live volatility/volume/spread (public tickers), not alphabetical grind; feed movers into intake | research-only; no live engine |
| **farm** | enable the ~12 dormant families behind an opt-in research group; add SFP; movement universe | additive |
| **validator** | DO NOT loosen the gates. ADD a tactical/shadow verdict class so n<10 positives are routed to forward-watch instead of FAILED_OOS; use the proper DSR for multi-test instead of only Šidák-on-perm-p; relabel n<10 fails as `UNDERPOWERED` not `FAILED_OVERFIT` | additive; honesty preserved |
| **setup memory** | already has cost_class/tactical_class/next_action; wire the 595 "re-run with regime" + 99 "needs OI" into actual follow-up tasks instead of dead labels | additive |
| **paper / shadow** | paper loop barely used (3 trades); route shadow_forward + tactical candidates into a real forward collector | research-only |

## What NOT to do

- Do NOT loosen the validator gates — they are correct for statistical edge; add a parallel tactical
  track instead.
- Do NOT declare momentum_breakout dead globally — it is confirmed-bad ONLY on the stale dead universe;
  retest on movers first.
- Do NOT chase the 495 IS−/OOS+ as edge — mostly regime artifacts; only the 3 large-n cases merit a look.
- Do NOT revive maker-execution as the unlock (Gate-1 proved it a mirage).
- Do NOT pour compute into the stale universe — re-aim first.
- Do NOT touch live trading / orders / AUTO_TRADE / .env / private endpoints / Telegram.

## Questions for the owner

1. Universe policy: should the farm AUTODISCOVER its universe from live volatility/volume (top-N movers),
   or keep a curated list? (drives the #1 fix)
2. Should tokenized equities (AAPL/NVDA/AMZN/PLTR) be dropped from the crypto universe entirely?
3. Do you want a separate **tactical/one-shot track** (forward-watch, never paper-ready) as a first-class
   citizen, or keep everything under the single statistical validator?
4. OI backfill: OK to spend bounded keyless compute backfilling OI/funding into 1h/4h candles so the OI
   families can actually be tested?
5. Stat-arb (multi-symbol Kalman) is the strongest historical lead but needs a pairs harness — invest in
   that, or stay single-symbol?
