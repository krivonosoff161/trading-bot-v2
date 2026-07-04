# Calculation Farm Rebuild — Honest Status (2026-06-17, overnight)

Branch: `feature/calc-farm` (4 commits: `0ac466a`, `26dac73`, `a89d4ed`, `38fc41c`).
Paper/research only. No order path, `.env`, `AUTO_TRADE`, live engine, private OKX
endpoints, or Telegram were touched. `main_engine_ready` stays `False` everywhere.

## DONE (built, tested, and verified running)

1. **Unified feature layer** (`src/research_lab/features/`): trend (EMA, slope,
   ADX/DI, SuperTrend), volatility (ATR, ATR%, BB width/%B, squeeze), volume
   (vol_ratio, spike, decay, sideways-range accumulation), structure (swing/fractal
   pivots, range bounds, breakout quality), vwap (rolling VWAP, stretch,
   reclaim/reject, day_position), fvg (gaps + reaction), flow (OI delta/quadrant,
   funding extreme/z-score, perp-index basis), microstructure (OBI/spread/trade_delta).
   **Parity-tested 8/8 against `src/strategy/indicators.py`** (the live math, ported
   stdlib, no live-engine import). Pure no-lookahead.
2. **11 strategy families** wired into the registry as hypothesis generators:
   main_fast_swing_regime, range_volume_breakout, volatility_squeeze_breakout_v2,
   vwap_reclaim_reject, fvg_reclaim_reject, fractal_swing_break_retest,
   oi_funding_squeeze, oi_price_quadrant, bb_volume_fade, pump_dump_scalp,
   microstructure_confirmed_breakout. Flow/micro families degrade to NEEDS_DATA when
   data is absent. Audit verdicts (closed/dead patterns) carried in `risk_notes`.
3. **Universe-driven refill** (`universe_refill` + `universe_refill_runner` +
   `universe_farm_loop`): the farm no longer waits for news. Round-robin
   (group × timeframe × families) work-list, direction-aware family selection per
   group, prepare-on-demand with per-symbol backoff, multi-family research plans,
   idempotent queue, first-class loop that drains the worker so it COMPUTES.
4. **Result store + status** (schema v3): `farm_results` (asset_group/timeframe/
   backend/data_quality/metrics) + `runtime_stats` (CPU/GPU backend + fallback),
   additive migration. `farm_status_report` (read-only): what was computed, by
   group/family/timeframe, the CPU/GPU split, decisions/validation, what's next.
5. **GPU**: honest backend contract verified on this box (cupy 13.6 / GTX 1050).
   `--backend auto` runs the trade **simulation on GPU** (`sim=gpu`) while new-family
   signal generation honestly stays on CPU (`signal=cpu`, no kernel yet) — recorded
   and shown, never faked. `gpu` requested without a GPU → error, never silent CPU.
6. **OI/funding loading**: `okx_flow.OkxPublicFundingProvider` (public funding-rate-
   history, keyless), `flow_merge` (no-lookahead forward-fill), `enrich_flow_data`
   CLI. Verified live: 30 real BTC funding points fetched.
7. **Validation gate verified end-to-end on REAL OKX data**:
   universe refill → prepare (OKX public candles) → queue → worker (GPU sim) →
   classify (REJECT / OBSERVE / REGIME_SPECIFIC / PROMOTE_FOR_PRESSURE_TEST) →
   candidate registry → `export_hard_validation_requests` (Eligible 2 / Exported 2) →
   `honest_backtest_bridge` verdict (NEEDS_MORE_DATA on the thin 1d sample) →
   `SetupCard.main_engine_ready == False`. The honest layer did NOT promote weak
   candidates. Nothing reaches main directly.
8. **Tests**: +46 new (parity 8, families 4, feature units 12, universe refill 5,
   DB/status 4, flow 5, plus existing). 577 adjacent tests green; full-suite count in
   the session note.

## PARTIAL (works, but with a documented narrower scope than the spec's maximum)

- **GPU signal kernels**: only `momentum_breakout` has a vectorized signal kernel, so
  the new families' *signal generation* runs on CPU (their *simulation* runs on GPU).
  Honest and reported. Next: add vectorized kernels for the rolling-window families
  (range/squeeze) with a parity test — fiddly because their gates round to 4 dp, so a
  kernel must replicate the rounding exactly to stay bit-parity.
- **OI history**: funding is loaded for real; a keyless per-instId historical OI series
  is not reliably public, so `merge_oi` is a provider slot (interface + forward-fill
  ready), not a faked series. OI-quadrant/oi_funding families run on real data only
  once an OI provider is wired (current OKX `open-interest` is point-in-time only).
- **Microstructure families**: OBI/spread/trade_delta are not reconstructable from
  OHLCV, so `microstructure_confirmed_breakout` is wired + tested but reports
  NEEDS_DATA on historical sweeps (by design; no private endpoints).

## NOT DONE (deliberately out of scope tonight)

- Auto-enrichment of every prepared file with funding inside the main loop (kept as
  the separate bounded `enrich_flow_data` CLI to avoid N extra network calls/cycle).
- A continuous live run accumulating real candidates (the loop + worker drain are
  proven on small scopes; a long unattended run is the operator's call).
- New SQLite tables `calculation_jobs` / `feature_snapshots` / `candidate_decisions` /
  `validation_exports` / `data_readiness` as *separate* tables: these are covered by
  existing structures (queue / metrics.json features / candidates / hard_validation
  artifacts / pipeline_state.prepared_data). Added `farm_results` + `runtime_stats`
  where there was a real gap; the rest would be duplication.

## EXACT NEXT PATCHES (in priority order)

1. Vectorized GPU signal kernel for `range_volume_breakout` (+ parity test that
   matches the scalar rounding) → `signal=gpu` for that family.
2. Wire `enrich_flow_data` as an optional per-cycle step (flag-gated, bounded) so the
   flow families auto-run on real funding during the universe grind.
3. An OI history provider (or a manual OI CSV slot like the scanner's etf_flow) to
   light up the OI families on real data.
4. A small `--full` overnight universe run, then read `farm_status_report` and the
   LLM review pack to triage the first real OBSERVE/PROMOTE candidates.

## HOW TO RUN

```bash
# dry-run the universe grind (no network, no writes)
python -m scripts.strategy_lab.universe_farm_loop --once --dry-run

# small real apply (public OKX) + drain the worker, into a private root
TRADING_BOT_RESEARCH_ROOT=<private> python -m scripts.strategy_lab.universe_farm_loop \
  --once --apply --groups core_market --timeframes 1d --run-worker --max-worker-jobs-per-cycle 3

# operator status
python -m scripts.strategy_lab.farm_status_report

# export validated candidates to hard validation
python -m scripts.strategy_lab.export_hard_validation_requests --apply --include-regime-specific
```
