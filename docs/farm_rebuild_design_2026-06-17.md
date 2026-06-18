# Calculation Farm Rebuild — Design Map (2026-06-17)

> **⚠️ HISTORICAL DESIGN MAP (superseded 2026-06-18).** Its DB/coordinator story
> (`scanner_farm_pipeline.run_cycle` / `scanner_farm_loop.sqlite`) predates the actual
> coordinator that shipped: `farm_coordinator.run_coordinator_cycle` over
> `farm_tasks.sqlite`. For the current architecture read
> [farm_loop_lifecycle.md](farm_loop_lifecycle.md). Kept as the rebuild's design history.

Status: **in progress.** This is the short design map that grounds the rebuild of
the strategy lab into a self-driving calculation farm. Paper/research only — no
order path, no `.env`, no `AUTO_TRADE`, no live engine changes. The old Main
engine is reused only as **extracted calculation** (features / strategy families),
never as an executor.

## What already exists (do not break)

A mature pipeline is already in place:

- `experiment.evaluate_spec` — sweep core (families × symbols × params), CPU or
  GPU, with honest `runtime_meta` (backend / fallback reasons).
- `worker_once.run_worker_once` — singleton-locked, cadence-throttled, variant-capped
  job runner with night-mode.
- `scanner_farm_pipeline.run_cycle` — coordinator: plan → prepare candles (private
  `market_data/<tf>/`) → compile+queue `SweepSpec` → checkpoint in
  `scanner_farm_loop.sqlite`.
- Two-stage validation: `validator.validate_candidate` (lite:
  REJECT/OBSERVE/REGIME_SPECIFIC/FORWARD_PAPER) → `honest_backtest_bridge`
  (hard: FAILED_COSTS/OOS/FRAGILITY/OVERFIT/REGIME_ONLY/PAPER_FORWARD_READY).
- Contracts: `CandidateForValidation`, `HardValidationVerdict`, `SetupCard`
  (with the safety invariant `main_engine_ready=False`).
- DBs: `strategy_lab.sqlite` (meta/runs/candidates/queue) + `scanner_farm_loop.sqlite`
  (processed_watches/queued_jobs/prepared_data/skips/cycles).

## The real "light" gaps (from the deep audit)

1. **Feature layer is minimal** — only sma/rsi/high/low/body. No ADX/ATR/BB/VWAP/
   swings/FVG/OI/funding/microstructure. → **Fixed: new `features/` package.**
2. **Strategy families are generic** — momentum/donchian/range/squeeze/retest/MR/
   rsi/volume. Not the rich families the spec asks for.
3. **Refill is news-first** — `--refill-universe=''` default OFF; universe backlog
   only added *after* watch jobs, capped to the same `max_jobs`. No first-class
   `universe × families × timeframes` grind loop. The farm waits for news.
4. **Loop does not drain the worker** by default — it only queues.
5. **No flow data** — only OHLCV is loaded; OI/funding never fetched.
6. **GPU is narrow** — only `momentum_breakout` is vectorized.

## Verdict guardrails (frame as research candidates, never proven edges)

The hard-validation layer is the guard; every new family is a **hypothesis
generator**, gated. The audit's closed-pattern list must not be re-shipped as a
money edge: 15m-scalp entry-timing & RR-geometry are *not* levers; DRIFT/RANGING-fade
are NO-GO; impulse "rivok" closed (BSB overfit) though its *detector* worked and the
*exit* was the disease; pump reversal fee-blocked; OI/funding verdicts are
single-regime/survivor-biased. Costs must always be modeled (the old no-fee sims
produced the documented "sub-cost edge" trap). Risk notes in the registry carry
this framing inline.

## Plan (logical commits)

1. **Feature layer** (`src/research_lab/features/`): trend / volatility / volume /
   structure / vwap / fvg / flow / microstructure + `feature_snapshot`. Pure-Python,
   parity-tested against `src/strategy/indicators.py`. *(built)*
2. **Strategy families** (`src/research_lab/strategies/` + registry): main_regime,
   range_volume_breakout, volatility_squeeze_breakout_v2, vwap_reclaim_reject,
   fvg_reclaim_reject, fractal_swing_break_retest, oi_funding_squeeze,
   oi_price_quadrant, bb_volume_fade, pump_dump_scalp, microstructure_confirmed_breakout.
   Each = `fn(candles, params) -> [{idx, side, reason}]`, no-lookahead.
3. **Universe-driven refill** (`universe_refill.py`): build a deterministic work-list
   from `configs/strategy_lab/universe.yaml` groups (+ OKX instruments / movers /
   candidate follow-ups / validation feedback) and fan out `families × timeframes`
   per asset; bounded; with retry backoff. Plus first-class worker draining.
4. **Flow data** (extend `data_prepare`/provider): optional OKX *public* OI + funding,
   merged into candle dicts as `oi`/`funding` for flow features. Bounded, no private
   endpoints.
5. **GPU**: add vectorized kernels for the rolling-window families + parity tests;
   honest CPU fallback already reported.
6. **Result classification + DB extension** (`state_db` migration, schema v3): add
   `farm_results`, `feature_snapshots`, `calculation_jobs`, `candidate_decisions`,
   `validation_exports`, `data_readiness`, `runtime_stats`. Non-destructive
   `_migrate_*` like the existing v1→v2. Decision states: REJECT / OBSERVE /
   REGIME_SPECIFIC / FORWARD_PAPER / PROMOTE_FOR_PRESSURE_TEST / NEEDS_HONEST_BACKTEST
   / MAIN_ENGINE_CANDIDATE (only post hard-validation).
7. **Validation wiring**: only `FORWARD_PAPER`/hard-validated objects become export
   candidates; `MAIN_ENGINE_CANDIDATE` only after `PAPER_FORWARD_READY`.
8. **Status report** (read-only): what was computed, which assets/TFs, CPU/GPU split,
   rejected/observed/promoted, why, what next.
9. **Tests** (features/parity/FVG/swings/OI/funding/range-vol/scheduler/DB) + ruff +
   dry-run + small real apply on 2-5 assets / 1-2 TFs.

## Boundaries (unchanged, enforced)

No order placement, no live path, no `.env`/`AUTO_TRADE`/live config edits, no
private OKX endpoints, no Telegram. New compute writes heavy artifacts to the private
HDD root; only indexes go to sqlite. `main_engine_ready` stays `False` everywhere.
